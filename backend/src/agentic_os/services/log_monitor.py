"""
Log File Monitor - Watches log files for regex patterns and emits custom events.

Tails one or more log files, matches lines against regex patterns, and emits
MonitoringEvent objects to the watcher for incident creation.

Configuration (from environment variable, JSON array):
  WATCHER_LOG_MONITORS=[
    {
      "name": "error_detector",
      "file": "/var/log/app.log",
      "pattern": "ERROR|CRITICAL|panic",
      "event_type": "log_error_detected",
      "interval_sec": 5
    }
  ]
"""

import os
import json
import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class LogMonitorConfig:
    """Configuration for a single log monitor"""
    name: str
    file: str
    pattern: str
    event_type: str
    interval_sec: int = 5
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "LogMonitorConfig":
        return cls(
            name=d.get("name", "unnamed"),
            file=d.get("file", ""),
            pattern=d.get("pattern", ""),
            event_type=d.get("event_type", "custom"),
            interval_sec=d.get("interval_sec", 5),
            enabled=d.get("enabled", True),
        )


@dataclass
class LogMatch:
    """A matched line from a log file"""
    monitor_name: str
    event_type: str
    matched_line: str
    timestamp: str


class LogMonitor:
    """
    Monitors one or more log files for patterns and emits events.

    Usage:
        monitor = LogMonitor.from_env()
        while True:
            matches = monitor.poll()  # Check for new matches
            for match in matches:
                # Create MonitoringEvent(event_type=match.event_type, ...)
            time.sleep(monitor.min_interval)
    """

    def __init__(self, configs: List[LogMonitorConfig], state_dir: str = "/app/.state"):
        """
        Initialize with a list of monitor configs.

        Args:
            configs: List of LogMonitorConfig objects
            state_dir: Directory to store file position tracking
        """
        self.configs = {c.name: c for c in configs if c.enabled}
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.positions_file = self.state_dir / "log_monitor_positions.json"
        self.positions = self._load_positions()
        self.min_interval = min((c.interval_sec for c in self.configs.values()), default=5)

        logger.info(f"[LOG-MONITOR] Initialized with {len(self.configs)} monitor(s)")
        for name, cfg in self.configs.items():
            logger.info(
                f"  • {name}: {cfg.file} → {cfg.event_type} (pattern={cfg.pattern[:50]}...)"
            )

    @classmethod
    def from_env(cls, state_dir: str = "/app/.state") -> "LogMonitor":
        """
        Load monitor configs from WATCHER_LOG_MONITORS environment variable.

        Expected format:
          WATCHER_LOG_MONITORS='[{"name":"...", "file":"...", ...}]'
        """
        raw = os.getenv("WATCHER_LOG_MONITORS", "[]")
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning(f"[LOG-MONITOR] WATCHER_LOG_MONITORS is not a list, ignoring")
                return cls([], state_dir)
            configs = [LogMonitorConfig.from_dict(d) for d in data]
            return cls(configs, state_dir)
        except json.JSONDecodeError as e:
            logger.error(f"[LOG-MONITOR] Failed to parse WATCHER_LOG_MONITORS: {e}")
            return cls([], state_dir)

    def _load_positions(self) -> Dict[str, int]:
        """Load last-read file positions from state file."""
        if not self.positions_file.exists():
            return {}
        try:
            with open(self.positions_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[LOG-MONITOR] Could not load positions: {e}")
            return {}

    def _save_positions(self) -> None:
        """Persist file positions to state file."""
        try:
            with open(self.positions_file, "w") as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            logger.error(f"[LOG-MONITOR] Failed to save positions: {e}")

    def poll(self) -> List[LogMatch]:
        """
        Check all monitored files for new lines matching their patterns.

        Returns:
            List of LogMatch objects for lines that matched
        """
        matches = []

        for monitor_name, config in self.configs.items():
            if not config.enabled:
                continue

            file_path = Path(config.file)

            # Skip if file doesn't exist
            if not file_path.exists():
                logger.debug(f"[LOG-MONITOR] File not found: {config.file}")
                continue

            try:
                # Get current file size
                current_size = file_path.stat().st_size
                last_pos = self.positions.get(monitor_name, 0)

                # If file was truncated or rotated, reset to start
                if current_size < last_pos:
                    logger.info(f"[LOG-MONITOR] File rotated: {monitor_name}, resetting position")
                    last_pos = 0

                # Read new lines
                with open(file_path, "r", errors="ignore") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    new_pos = f.tell()

                # Match lines against the pattern
                pattern_re = re.compile(config.pattern, re.IGNORECASE | re.MULTILINE)
                for line in new_lines:
                    line_stripped = line.rstrip("\n")
                    if pattern_re.search(line_stripped):
                        match = LogMatch(
                            monitor_name=monitor_name,
                            event_type=config.event_type,
                            matched_line=line_stripped,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                        matches.append(match)
                        logger.info(
                            f"[LOG-MONITOR] {monitor_name} match: {line_stripped[:100]}"
                        )

                # Update position
                self.positions[monitor_name] = new_pos

            except Exception as e:
                logger.error(f"[LOG-MONITOR] Error monitoring {monitor_name}: {e}")

        # Persist positions if any matches or position changes
        if matches or any(k in self.positions for k in self.configs.keys()):
            self._save_positions()

        return matches

    def is_enabled(self) -> bool:
        """Check if any monitors are enabled."""
        return len(self.configs) > 0

    def reload_configs(self, config_dicts: List[Dict[str, Any]]) -> None:
        """
        Reload monitor configurations at runtime without losing file position tracking.

        Preserves positions for monitors with the same name and file path.
        Stops monitoring deleted monitors gracefully.

        Args:
            config_dicts: List of config dictionaries from the API
        """
        if not isinstance(config_dicts, list):
            raise ValueError("config_dicts must be a list")

        # Build new configs
        new_configs = {}
        new_positions = {}

        for cfg_dict in config_dicts:
            try:
                config = LogMonitorConfig.from_dict(cfg_dict)
                if not config.enabled:
                    continue

                # Preserve position for monitors with same name and file
                if config.name in self.positions and config.name in self.configs:
                    old_config = self.configs[config.name]
                    if old_config.file == config.file:
                        new_positions[config.name] = self.positions[config.name]
                        logger.debug(
                            f"[LOG-MONITOR] Preserved position for {config.name}: "
                            f"pos={new_positions[config.name]}"
                        )

                new_configs[config.name] = config
            except Exception as e:
                logger.error(f"[LOG-MONITOR] Invalid config: {e}")

        # Replace configs and update positions
        old_count = len(self.configs)
        self.configs = new_configs
        self.positions = new_positions
        self.min_interval = min(
            (c.interval_sec for c in self.configs.values()), default=5
        )

        # Save updated positions
        self._save_positions()

        logger.info(
            f"[LOG-MONITOR] Reloaded: {old_count} → {len(self.configs)} monitor(s), "
            f"preserved {len(new_positions)} position(s)"
        )
        for name, cfg in self.configs.items():
            logger.info(
                f"  • {name}: {cfg.file} → {cfg.event_type} (pattern={cfg.pattern[:50]}...)"
            )
