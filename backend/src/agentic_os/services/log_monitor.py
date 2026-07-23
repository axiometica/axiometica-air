"""
Log File Monitor - Watches log files or container stdout/stderr for regex patterns.

Two sources are supported:
  - "file"   — tails a log file inside the watcher container's filesystem
  - "docker" — reads new lines from a named container via `docker logs --since`

Configuration (from environment variable, JSON array):
  WATCHER_LOG_MONITORS=[
    {
      "name": "error_detector",
      "source": "file",
      "file": "/var/log/app.log",
      "pattern": "ERROR|CRITICAL|panic",
      "event_type": "log_error_detected",
      "interval_sec": 5
    },
    {
      "name": "backend_errors",
      "source": "docker",
      "container": "agentic_os_backend",
      "pattern": "ERROR|CRITICAL",
      "event_type": "backend_log_error",
      "interval_sec": 10
    }
  ]
"""

import os
import json
import re
import subprocess
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class LogMonitorConfig:
    """Configuration for a single log monitor"""
    name: str
    pattern: str
    event_type: str
    source: str = "file"       # "file" or "docker"
    file: str = ""             # log file path (file mode)
    container: str = ""        # container name (docker mode)
    interval_sec: int = 5
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "LogMonitorConfig":
        return cls(
            name=d.get("name", "unnamed"),
            pattern=d.get("pattern", ""),
            event_type=d.get("event_type", "custom"),
            source=d.get("source", "file"),
            file=d.get("file", ""),
            container=d.get("container", ""),
            interval_sec=d.get("interval_sec", 5),
            enabled=d.get("enabled", True),
        )


@dataclass
class LogMatch:
    """A matched line from a log file or container"""
    monitor_name: str
    event_type: str
    matched_line: str
    timestamp: str


class LogMonitor:
    """
    Monitors log files and/or container stdout/stderr for patterns and emits events.

    Usage:
        monitor = LogMonitor.from_env()
        while True:
            matches = monitor.poll()
            for match in matches:
                # Create MonitoringEvent(event_type=match.event_type, ...)
            time.sleep(monitor.min_interval)
    """

    def __init__(self, configs: List[LogMonitorConfig], state_dir: str = "/app/.state"):
        self.configs = {c.name: c for c in configs if c.enabled}
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.positions_file = self.state_dir / "log_monitor_positions.json"
        self.positions = self._load_positions()
        self.min_interval = min((c.interval_sec for c in self.configs.values()), default=5)

        logger.info(f"[LOG-MONITOR] Initialized with {len(self.configs)} monitor(s)")
        for name, cfg in self.configs.items():
            target = cfg.container if cfg.source == "docker" else cfg.file
            logger.info(
                f"  • {name} [{cfg.source}]: {target} → {cfg.event_type} "
                f"(pattern={cfg.pattern[:50]}...)"
            )

    @classmethod
    def from_env(cls, state_dir: str = "/app/.state") -> "LogMonitor":
        """Load monitor configs from WATCHER_LOG_MONITORS environment variable."""
        raw = os.getenv("WATCHER_LOG_MONITORS", "[]")
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning("[LOG-MONITOR] WATCHER_LOG_MONITORS is not a list, ignoring")
                return cls([], state_dir)
            configs = [LogMonitorConfig.from_dict(d) for d in data]
            return cls(configs, state_dir)
        except json.JSONDecodeError as e:
            logger.error(f"[LOG-MONITOR] Failed to parse WATCHER_LOG_MONITORS: {e}")
            return cls([], state_dir)

    def _load_positions(self) -> Dict[str, Any]:
        """Load last-read positions from state file."""
        if not self.positions_file.exists():
            return {}
        try:
            with open(self.positions_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[LOG-MONITOR] Could not load positions: {e}")
            return {}

    def _save_positions(self) -> None:
        """Persist positions to state file."""
        try:
            with open(self.positions_file, "w") as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            logger.error(f"[LOG-MONITOR] Failed to save positions: {e}")

    def poll(self) -> List[LogMatch]:
        """
        Check all monitored sources for new lines matching their patterns.

        Returns:
            List of LogMatch objects for lines that matched
        """
        matches = []

        for monitor_name, config in self.configs.items():
            if not config.enabled:
                continue
            if config.source == "docker":
                matches.extend(self._poll_docker(monitor_name, config))
            else:
                matches.extend(self._poll_file(monitor_name, config))

        if matches or self.positions:
            self._save_positions()

        return matches

    def _poll_file(self, monitor_name: str, config: LogMonitorConfig) -> List[LogMatch]:
        """Tail a log file inside the watcher container and match new lines."""
        file_path = Path(config.file)

        if not file_path.exists():
            logger.debug(f"[LOG-MONITOR] File not found: {config.file}")
            return []

        matches = []
        try:
            current_size = file_path.stat().st_size
            last_pos = self.positions.get(monitor_name, 0)

            # Reset on log rotation
            if current_size < last_pos:
                logger.info(f"[LOG-MONITOR] File rotated: {monitor_name}, resetting position")
                last_pos = 0

            with open(file_path, "r", errors="ignore") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                new_pos = f.tell()

            pattern_re = re.compile(config.pattern, re.IGNORECASE | re.MULTILINE)
            for line in new_lines:
                line_stripped = line.rstrip("\n")
                if pattern_re.search(line_stripped):
                    matches.append(LogMatch(
                        monitor_name=monitor_name,
                        event_type=config.event_type,
                        matched_line=line_stripped,
                        timestamp=datetime.utcnow().isoformat(),
                    ))
                    logger.info(f"[LOG-MONITOR] {monitor_name} match: {line_stripped[:100]}")

            self.positions[monitor_name] = new_pos

        except Exception as e:
            logger.error(f"[LOG-MONITOR] Error monitoring {monitor_name}: {e}")

        return matches

    def _poll_docker(self, monitor_name: str, config: LogMonitorConfig) -> List[LogMatch]:
        """
        Fetch new log lines from a Docker container via `docker logs --since`.

        Position tracking uses a Unix timestamp stored under "docker:<name>" in
        the positions dict, keeping it separate from file byte-offset entries.
        On the first poll, falls back to --tail 100 to catch recent history
        without replaying the full container log.
        """
        pos_key = f"docker:{monitor_name}"
        last_ts = self.positions.get(pos_key)
        now_ts = time.time()

        cmd = ["docker", "logs"]
        if last_ts is not None:
            # Fetch only lines since the last poll timestamp
            cmd += ["--since", str(int(last_ts))]
        else:
            # First poll: last 100 lines to catch recent activity
            cmd += ["--tail", "100"]
        cmd.append(config.container)

        matches = []
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=15,
            )
            # docker logs writes container stdout → subprocess stdout,
            # container stderr → subprocess stderr; scan both.
            combined = result.stdout + result.stderr
            pattern_re = re.compile(config.pattern, re.IGNORECASE | re.MULTILINE)
            for line in combined.splitlines():
                line = line.strip()
                if line and pattern_re.search(line):
                    matches.append(LogMatch(
                        monitor_name=monitor_name,
                        event_type=config.event_type,
                        matched_line=line,
                        timestamp=datetime.utcnow().isoformat(),
                    ))
                    logger.info(
                        f"[LOG-MONITOR] docker:{monitor_name} match: {line[:100]}"
                    )

        except FileNotFoundError:
            logger.error(
                f"[LOG-MONITOR] docker CLI not found — docker source requires "
                f"docker in PATH (container: {config.container})"
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                f"[LOG-MONITOR] docker logs timed out for container '{config.container}'"
            )
        except Exception as e:
            logger.error(
                f"[LOG-MONITOR] Error reading docker logs for '{config.container}': {e}"
            )
        finally:
            # Always advance the timestamp so the next poll only sees new lines.
            self.positions[pos_key] = now_ts

        return matches

    def is_enabled(self) -> bool:
        """Check if any monitors are enabled."""
        return len(self.configs) > 0

    def reload_configs(self, config_dicts: List[Dict[str, Any]]) -> None:
        """
        Reload monitor configurations at runtime without losing position tracking.

        Preserves:
          - file byte offsets when name and file path are unchanged
          - docker timestamps when name and container are unchanged
        """
        if not isinstance(config_dicts, list):
            raise ValueError("config_dicts must be a list")

        new_configs = {}
        new_positions = {}

        for cfg_dict in config_dicts:
            try:
                config = LogMonitorConfig.from_dict(cfg_dict)
                if not config.enabled:
                    continue

                if config.name in self.configs:
                    old = self.configs[config.name]

                    # Preserve file byte offset
                    if (
                        config.source == "file"
                        and old.source == "file"
                        and old.file == config.file
                        and config.name in self.positions
                    ):
                        new_positions[config.name] = self.positions[config.name]
                        logger.debug(
                            f"[LOG-MONITOR] Preserved file position for {config.name}: "
                            f"pos={new_positions[config.name]}"
                        )

                    # Preserve docker timestamp
                    docker_key = f"docker:{config.name}"
                    if (
                        config.source == "docker"
                        and old.source == "docker"
                        and old.container == config.container
                        and docker_key in self.positions
                    ):
                        new_positions[docker_key] = self.positions[docker_key]
                        logger.debug(
                            f"[LOG-MONITOR] Preserved docker timestamp for {config.name}"
                        )

                new_configs[config.name] = config
            except Exception as e:
                logger.error(f"[LOG-MONITOR] Invalid config: {e}")

        old_count = len(self.configs)
        self.configs = new_configs
        self.positions = new_positions
        self.min_interval = min(
            (c.interval_sec for c in self.configs.values()), default=5
        )

        self._save_positions()

        logger.info(
            f"[LOG-MONITOR] Reloaded: {old_count} → {len(self.configs)} monitor(s), "
            f"preserved {len(new_positions)} position(s)"
        )
        for name, cfg in self.configs.items():
            target = cfg.container if cfg.source == "docker" else cfg.file
            logger.info(
                f"  • {name} [{cfg.source}]: {target} → {cfg.event_type} "
                f"(pattern={cfg.pattern[:50]}...)"
            )
