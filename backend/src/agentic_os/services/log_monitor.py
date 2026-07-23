"""
Log File Monitor - Watches log files or container stdout/stderr for regex patterns.

Three sources are supported:
  - "file"    — tails a log file inside the watcher container's filesystem
  - "docker"  — reads new lines from a named container via `docker logs --since`
  - "vcenter" — reads a log file inside a VM via vCenter guest exec (VMware Tools)

Configuration (from environment variable, JSON array):
  WATCHER_LOG_MONITORS=[
    {
      "name": "error_detector",
      "source": "file",
      "file": "/var/log/app.log",
      "pattern": "ERROR|CRITICAL|panic",
      "event_type": "log_error_detected",
      "severity": "warning",
      "min_occurrences": 1,
      "interval_sec": 30
    },
    {
      "name": "backend_errors",
      "source": "docker",
      "container": "agentic_os_backend",
      "pattern": "ERROR|CRITICAL",
      "event_type": "backend_log_error",
      "severity": "critical",
      "min_occurrences": 2,
      "interval_sec": 30
    },
    {
      "name": "vm_app_errors",
      "source": "vcenter",
      "vm_name": "prod-app-01",
      "file": "/var/log/app/app.log",
      "pattern": "ERROR|CRITICAL|Exception",
      "event_type": "vm_log_error",
      "severity": "high",
      "min_occurrences": 1,
      "interval_sec": 60
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
    source: str = "file"           # "file", "docker", or "vcenter"
    file: str = ""                 # log file path (file or vcenter mode)
    container: str = ""            # container name (docker mode)
    vm_name: str = ""              # VM name (vcenter mode)
    interval_sec: int = 30
    min_occurrences: int = 1       # min matching lines per poll to fire
    severity: str = "warning"      # critical | high | warning | info
    clear_after_polls: int = 3     # consecutive quiet polls before all-clear fires
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
            vm_name=d.get("vm_name", ""),
            interval_sec=d.get("interval_sec", 30),
            min_occurrences=max(1, int(d.get("min_occurrences", 1))),
            severity=d.get("severity", "warning"),
            clear_after_polls=max(0, int(d.get("clear_after_polls", 3))),
            enabled=d.get("enabled", True),
        )


@dataclass
class LogMatch:
    """A group of matched lines from one poll cycle for a single monitor."""
    monitor_name: str
    event_type: str
    matched_line: str          # first (or most representative) matching line
    timestamp: str
    match_count: int = 1       # total matching lines in this poll
    all_matched_lines: List[str] = field(default_factory=list)  # all matching lines (up to 20)


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
        self.min_interval = min((c.interval_sec for c in self.configs.values()), default=30)

        logger.info(f"[LOG-MONITOR] Initialized with {len(self.configs)} monitor(s)")
        for name, cfg in self.configs.items():
            if cfg.source == "docker":
                target = cfg.container
            elif cfg.source == "vcenter":
                target = f"{cfg.vm_name}:{cfg.file}"
            else:
                target = cfg.file
            logger.info(
                f"  • {name} [{cfg.source}]: {target} → {cfg.event_type} "
                f"(pattern={cfg.pattern[:50]}..., min={cfg.min_occurrences}, sev={cfg.severity})"
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

    def poll(self, vcenter_adapter=None) -> List[LogMatch]:
        """
        Check all monitored sources for new lines matching their patterns.

        Returns at most one LogMatch per monitor (when min_occurrences is met).
        Each LogMatch carries all matched lines for use in the incident description.

        Args:
            vcenter_adapter: Optional vCenterAdapter instance. Required for monitors
                             with source="vcenter"; vcenter monitors are skipped if None.
        """
        matches = []

        for monitor_name, config in self.configs.items():
            if not config.enabled:
                continue
            if config.source == "docker":
                result = self._poll_docker(monitor_name, config)
            elif config.source == "vcenter":
                if vcenter_adapter is None:
                    logger.debug(
                        f"[LOG-MONITOR] {monitor_name}: skipping vcenter source — "
                        f"no vcenter adapter available"
                    )
                    result = None
                else:
                    result = self._poll_vcenter(monitor_name, config, vcenter_adapter)
            else:
                result = self._poll_file(monitor_name, config)
            if result is not None:
                matches.append(result)

        if matches or self.positions:
            self._save_positions()

        return matches

    def _poll_file(self, monitor_name: str, config: LogMonitorConfig) -> Optional[LogMatch]:
        """Tail a log file inside the watcher container and match new lines."""
        file_path = Path(config.file)

        if not file_path.exists():
            logger.debug(f"[LOG-MONITOR] File not found: {config.file}")
            return None

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
            matched_lines: List[str] = []
            for line in new_lines:
                line_stripped = line.rstrip("\n")
                if pattern_re.search(line_stripped):
                    matched_lines.append(line_stripped)
                    if len(matched_lines) >= 20:
                        break

            self.positions[monitor_name] = new_pos

            if len(matched_lines) < config.min_occurrences:
                if matched_lines:
                    logger.debug(
                        f"[LOG-MONITOR] {monitor_name}: {len(matched_lines)} match(es), "
                        f"need {config.min_occurrences} — skipping"
                    )
                return None

            logger.info(
                f"[LOG-MONITOR] {monitor_name} match: {matched_lines[0][:100]}"
                + (f" (+{len(matched_lines)-1} more)" if len(matched_lines) > 1 else "")
            )
            return LogMatch(
                monitor_name=monitor_name,
                event_type=config.event_type,
                matched_line=matched_lines[0],
                timestamp=datetime.utcnow().isoformat(),
                match_count=len(matched_lines),
                all_matched_lines=matched_lines,
            )

        except Exception as e:
            logger.error(f"[LOG-MONITOR] Error monitoring {monitor_name}: {e}")
            return None

    def _poll_docker(self, monitor_name: str, config: LogMonitorConfig) -> Optional[LogMatch]:
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
            cmd += ["--since", str(int(last_ts))]
        else:
            cmd += ["--tail", "100"]
        cmd.append(config.container)

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
            matched_lines: List[str] = []
            for line in combined.splitlines():
                line = line.strip()
                if line and pattern_re.search(line):
                    matched_lines.append(line)
                    if len(matched_lines) >= 20:
                        break

            if len(matched_lines) < config.min_occurrences:
                if matched_lines:
                    logger.debug(
                        f"[LOG-MONITOR] docker:{monitor_name}: {len(matched_lines)} match(es), "
                        f"need {config.min_occurrences} — skipping"
                    )
                return None

            logger.info(
                f"[LOG-MONITOR] docker:{monitor_name} match: {matched_lines[0][:100]}"
                + (f" (+{len(matched_lines)-1} more)" if len(matched_lines) > 1 else "")
            )
            return LogMatch(
                monitor_name=monitor_name,
                event_type=config.event_type,
                matched_line=matched_lines[0],
                timestamp=datetime.utcnow().isoformat(),
                match_count=len(matched_lines),
                all_matched_lines=matched_lines,
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

        return None

    def _poll_vcenter(self, monitor_name: str, config: LogMonitorConfig, adapter) -> Optional[LogMatch]:
        """
        Read new log lines from a VM log file via vCenter guest exec.

        Uses line-count tracking to read only new lines each poll. On the first
        poll, reads the last 100 lines and records the current total line count.
        Subsequent polls use awk to read only lines past the saved line count.

        The vCenterAdapter.exec() runs a command in the VM via VMware Tools
        GuestProcessManager — no SSH or direct network access to the VM required.
        """
        pos_key = f"vcenter:{monitor_name}"
        last_line = self.positions.get(pos_key)

        log_file = config.file.replace("'", "'\\''")  # escape single quotes for shell

        if last_line is None:
            # First poll: tail recent history and capture current line count
            cmd = (
                f"NL=$(wc -l < '{log_file}' 2>/dev/null || echo 0); "
                f"tail -n 100 '{log_file}' 2>/dev/null; "
                f"echo \"__VCENTER_LINECOUNT__:$NL\""
            )
        else:
            # Subsequent polls: read only new lines and update count
            cmd = (
                f"NL=$(wc -l < '{log_file}' 2>/dev/null || echo 0); "
                f"awk 'NR > {int(last_line)}' '{log_file}' 2>/dev/null; "
                f"echo \"__VCENTER_LINECOUNT__:$NL\""
            )

        try:
            result = adapter.exec(config.vm_name, cmd, timeout=25)
        except Exception as exc:
            logger.error(f"[LOG-MONITOR] vcenter:{monitor_name}: exec failed: {exc}")
            return None

        if not result.success and not result.stdout.strip():
            logger.warning(
                f"[LOG-MONITOR] vcenter:{monitor_name}: command returned non-zero "
                f"on {config.vm_name} (rc={result.returncode}): {result.stderr[:200]}"
            )
            return None

        # Parse the sentinel line for the updated line count
        lines = result.stdout.splitlines()
        new_line_count: Optional[int] = None
        content_lines: List[str] = []
        for line in lines:
            if line.startswith("__VCENTER_LINECOUNT__:"):
                try:
                    new_line_count = int(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
            else:
                content_lines.append(line)

        # Persist updated line count so the next poll only reads new lines
        if new_line_count is not None:
            self.positions[pos_key] = new_line_count
        elif last_line is not None:
            self.positions[pos_key] = last_line  # keep previous on parse failure

        # Apply pattern matching
        pattern_re = re.compile(config.pattern, re.IGNORECASE | re.MULTILINE)
        matched_lines: List[str] = []
        for line in content_lines:
            line = line.strip()
            if line and pattern_re.search(line):
                matched_lines.append(line)
                if len(matched_lines) >= 20:
                    break

        if len(matched_lines) < config.min_occurrences:
            if matched_lines:
                logger.debug(
                    f"[LOG-MONITOR] vcenter:{monitor_name}: {len(matched_lines)} match(es), "
                    f"need {config.min_occurrences} — skipping"
                )
            return None

        logger.info(
            f"[LOG-MONITOR] vcenter:{monitor_name} match on {config.vm_name}: "
            f"{matched_lines[0][:100]}"
            + (f" (+{len(matched_lines)-1} more)" if len(matched_lines) > 1 else "")
        )
        return LogMatch(
            monitor_name=monitor_name,
            event_type=config.event_type,
            matched_line=matched_lines[0],
            timestamp=datetime.utcnow().isoformat(),
            match_count=len(matched_lines),
            all_matched_lines=matched_lines,
        )

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

                    # Preserve vcenter line count
                    vcenter_key = f"vcenter:{config.name}"
                    if (
                        config.source == "vcenter"
                        and old.source == "vcenter"
                        and old.vm_name == config.vm_name
                        and old.file == config.file
                        and vcenter_key in self.positions
                    ):
                        new_positions[vcenter_key] = self.positions[vcenter_key]
                        logger.debug(
                            f"[LOG-MONITOR] Preserved vcenter line count for {config.name}"
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
            if cfg.source == "docker":
                target = cfg.container
            elif cfg.source == "vcenter":
                target = f"{cfg.vm_name}:{cfg.file}"
            else:
                target = cfg.file
            logger.info(
                f"  • {name} [{cfg.source}]: {target} → {cfg.event_type} "
                f"(pattern={cfg.pattern[:50]}..., min={cfg.min_occurrences}, sev={cfg.severity})"
            )
