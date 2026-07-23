"""
Watcher Service - Brain-and-Senses Integration with Docker Stats & Advanced Monitoring
Consumes kernel-level syscall telemetry from Sentinel (eBPF monitor),
Docker container metrics, and advanced infrastructure monitoring for comprehensive anomaly detection.
"""

import subprocess
import json
import time
import os
import re
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from enum import Enum

from agentic_os.services.docker_stats_service import DockerStatsService, ContainerMetrics
from agentic_os.services.advanced_monitoring_service import (
    AdvancedMonitoringService,
    ExternalCheckConfig,
    ExternalCheckResult,
)
from agentic_os.services.discovery_service import DiscoveryService

logger = logging.getLogger(__name__)

WATCHER_VERSION = "1.0.0"


class AnomalyDetectionStrategy(str, Enum):
    """Strategy for anomaly detection"""
    HIGH_SYSCALL_INTENSITY = "high_syscall_intensity"
    CPU_SPIKE = "cpu_spike"
    MEMORY_SURGE = "memory_surge"
    DISK_FULL = "disk_full"
    HEALTH_CHECK_FAILED = "health_check_failed"
    CONNECTION_SPIKE = "connection_spike"
    LOG_ERROR = "log_error"
    METRICS_ANOMALY = "metrics_anomaly"
    PING_FAILED = "ping_failed"
    EXTERNAL_HTTP_FAILED = "external_http_failed"
    EXTERNAL_TCP_FAILED = "external_tcp_failed"
    DNS_FAILED = "dns_failed"
    TLS_EXPIRY = "tls_expiry"


class WatcherService:
    """
    Watcher Brain: Monitors telemetry from Sentinel and orchestrates incident response.

    Architecture:
    - Sentinel (Senses): eBPF kernel monitor, outputs JSON syscall telemetry
    - Watcher (Brain): Python agent, consumes telemetry, creates incidents, executes remediation
    """

    def __init__(
        self,
        sentinel_container: str = "sentinel_senses",
        api_base_url: str = "http://backend:8000",
        watcher_name: str = "watcher_brain",
        poll_interval: int = 10,
        anomaly_threshold: int = 5000,
        cpu_threshold: float = 80.0,
        memory_threshold: float = 90.0,
        disk_threshold: float = 90.0,
        connection_threshold: int = 1000,
        cooldown_seconds: int = 60,
        min_consecutive_polls: int = 3,
        synthetic_min_consecutive_fails: int = 1,
        discovery_interval_polls: int = 15,
        discovery_enabled: bool = True,
        syscall_sample_interval: int = 1,  # Phase 1: Sample syscalls every N polls
    ):
        """
        Initialize the Watcher service.

        Args:
            sentinel_container: Name of the Sentinel eBPF container
            api_base_url: Base URL of the agentic platform API
            poll_interval: Polling interval in seconds
            anomaly_threshold: Syscall count threshold for anomaly
            cpu_threshold: CPU percentage threshold (default: 80%)
            memory_threshold: Memory percentage threshold (default: 90%)
            disk_threshold: Disk usage percentage threshold (default: 90%)
            connection_threshold: Network connection count threshold (default: 1000)
            cooldown_seconds: Cooldown period between incident creations
        """
        self.sentinel_container = sentinel_container
        self.api_base_url = api_base_url
        self.watcher_name = watcher_name
        self.poll_interval = poll_interval
        self.anomaly_threshold = anomaly_threshold
        # Cache last known container per process — handles short-lived bursting
        # processes that exit before _find_process_container can run pgrep.
        self._process_container_cache: dict = {}

        # ── Environment detection ─────────────────────────────────────────────
        from agentic_os.services.environment_detector import detect_environment, ENV_LABELS
        from agentic_os.services.adapters.factory import create_adapter
        raw_env = os.getenv("WATCHER_ENVIRONMENT") or detect_environment()
        self.environment = raw_env.value if hasattr(raw_env, "value") else str(raw_env)
        self.environment_label = ENV_LABELS.get(self.environment, self.environment)
        logger.info(f"🌍 [ENV] Watcher environment: {self.environment_label} ({self.environment})")

        # ── Execution adapter ─────────────────────────────────────────────────
        # Adapter is the mode-specific backend for exec/kill/restart/metrics.
        # Kill-API handlers in watcher_main.py access it via _adapter_instance.
        self.adapter = create_adapter(self.environment)
        logger.info(f"🔌 [ADAPTER] Using: {self.adapter.adapter_name}")
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.disk_threshold = disk_threshold
        self.connection_threshold = connection_threshold
        self.cooldown_seconds = cooldown_seconds
        # Discovery agent settings
        self.discovery_interval_polls = discovery_interval_polls  # run every N polls
        self.discovery_enabled = discovery_enabled
        self.syscall_sample_interval = syscall_sample_interval  # Phase 1: sample every N polls
        self.poll_count: int = 0  # incremented every loop iteration
        # Sustained-duration gate: require this many consecutive polls showing
        # the same anomaly before opening an incident.  Filters transient spikes.
        self.min_consecutive_polls = min_consecutive_polls
        self.synthetic_min_consecutive_fails = synthetic_min_consecutive_fails
        # Hysteresis clear thresholds — a counter is reset only when the metric
        # drops THIS far below the alert threshold (prevents oscillation at boundary).
        self.cpu_clear_threshold: float = cpu_threshold * 0.80        # e.g. 64 % when alert=80 %
        self.memory_clear_threshold: float = memory_threshold * 0.80  # e.g. 72 % when alert=90 %

        # Auth header sent with every backend API call (X-API-Key for automation account)
        _api_key = os.getenv("WATCHER_API_KEY", "")
        self._api_headers: dict = {"X-API-Key": _api_key} if _api_key else {}
        if not _api_key:
            logger.warning("[WATCHER] WATCHER_API_KEY not set — backend API calls will be unauthenticated")

        # Public HTTPS URL of the NGINX endpoint this watcher registered through.
        # Set WATCHER_NGINX_URL env var when deploying outside the Docker network.
        self.nginx_url: str = os.getenv("WATCHER_NGINX_URL", "")

        # Kill-API callback URL — platform can push config updates to this address.
        # Defaults to http://<hostname>:8080 (the aiohttp Kill-API server).
        import socket as _socket_init
        _hostname = _socket_init.gethostname()
        _kill_api_port = os.getenv("WATCHER_KILL_API_PORT", "8080")
        self.kill_api_url: str = os.getenv(
            "WATCHER_KILL_API_URL",
            f"http://{_hostname}:{_kill_api_port}"
        )

        # Registration approval state — gate on this before submitting events.
        # Starts as None (unknown); updated on each registration heartbeat.
        # Values: "pending" | "approved" | "rejected"
        self._registration_status: Optional[str] = None

        self.state_dir = Path("/app/.state")
        self.state_dir.mkdir(exist_ok=True, parents=True)

        self.status_file = self.state_dir / "watcher_status.json"
        self.stats_file = self.state_dir / "container_stats.json"
        self.config_file = self.state_dir / "watcher_config.json"
        self.last_config_mtime: Optional[float] = None

        # Stable platform-assigned UUID for this watcher instance.
        # Persisted to .state/watcher_identity.json so it survives restarts.
        # None until first successful registration.
        self._watcher_id: Optional[str] = None
        self._identity_file = self.state_dir / "watcher_identity.json"
        self._load_identity()

        # Rolling metrics history — last 20 poll snapshots sent in each heartbeat.
        self._metrics_buffer: list = []
        self._METRICS_BUFFER_MAX = 20

        self.active_incident_id: Optional[str] = None
        self.last_anomaly_process: Optional[str] = None
        self.anomaly_start_time: Optional[datetime] = None
        # Legacy global cooldown kept for backward compat but no longer used internally.
        self.cooldown_until: Optional[datetime] = None

        # Initialize Docker stats and advanced monitoring services
        self.docker_stats = DockerStatsService()
        self.advanced_monitor = AdvancedMonitoringService()

        # ── Log File Monitoring ──────────────────────────────────────────────
        # Monitors log files for regex patterns and emits custom events.
        # Configuration via WATCHER_LOG_MONITORS environment variable (JSON array).
        from agentic_os.services.log_monitor import LogMonitor
        self.log_monitor = LogMonitor.from_env(state_dir=str(self.state_dir))
        if self.log_monitor.is_enabled():
            logger.info(f"📋 [LOG-MONITOR] Enabled with {len(self.log_monitor.configs)} monitor(s)")

        # ── External checks ──────────────────────────────────────────────────
        # Loaded from the platform DB at runtime (via kill-API config push).
        # No hardcoded defaults — configure probes in the UI under Watcher settings.
        # Supported types: http, tcp, dns, ping, tls
        self.external_checks: List[ExternalCheckConfig] = []
        # Map ExternalCheckConfig.name → last ExternalCheckResult for deduplication
        self._external_check_state: Dict[str, str] = {}  # name → last status

        # Event-type → default severity (info/warning/critical), loaded from the
        # Event Type Taxonomy via load_config_from_api(). Keyed by both a type's
        # canonical code and each of its aliases, so it can be looked up directly
        # with whatever short event-type string the watcher already uses (e.g.
        # "high_cpu"). Empty until the first successful settings fetch — anything
        # not present here falls back to the hardcoded criticality_map below.
        self.event_type_severity: Dict[str, str] = {}

        if self.external_checks:
            check_summary = ", ".join(
                f"{c.check_type.upper()} {c.name or c.target}"
                for c in self.external_checks
            )
            logger.info(
                f"🌐 [EXTERNAL] {len(self.external_checks)} external check(s) configured: {check_summary}"
            )

        # Discovery agent — Docker inspection only; Neo4j writes go via backend API.
        # Always initialise — DiscoveryService has no external deps (neo4j_uri=None).
        # The discovery_enabled flag gates whether it *runs*, not whether it exists.
        # This allows live enable/disable via the config API without a watcher restart.
        self.discovery = DiscoveryService(
            neo4j_uri=None,   # no direct Neo4j connection from watcher
            neo4j_user=None,
            neo4j_password=None,
        )

        # Track detected anomalies
        self.active_anomalies: Dict[str, Dict[str, Any]] = {}

        # Conditions currently being watched after firing an incident.
        # Maps resource_name → platform_event_type so we know what to clear.
        # Format: {"agentic_os_backend": "high_cpu", "agentic_os_neo4j": "high_syscall_intensity"}
        self.active_conditions: Dict[str, str] = {}

        # Parallel map: resource_name → workflow_id of the open incident.
        # Used by _reconcile_active_conditions_with_db() to check workflow state.
        self._active_workflow_ids: Dict[str, str] = {}

        # Consecutive-poll counters — key is "container_name:anomaly_type".
        # Incremented each poll the condition is detected; reset when it clears.
        self.consecutive_anomaly_counts: Dict[str, int] = {}

        # Synthetic monitor consecutive failure counters — keyed by monitor name.
        # Independent from consecutive_anomaly_counts / min_consecutive_polls.
        self.synthetic_fail_counts: Dict[str, int] = {}
        # Monitor names that have an active alert open (fired but not yet cleared).
        self.synthetic_alert_active: set = set()

        # Quiet-poll counters for log file monitors — keyed by "monitor_name:event_type".
        # Counts consecutive polls with no match.  All-clear fires only when the
        # counter reaches the monitor's clear_after_polls threshold.
        self._log_quiet_polls: Dict[str, int] = {}

        # Per-resource cooldown timers — prevent re-opening an incident for the
        # same resource immediately after cooldown expires.
        self.per_resource_cooldown: Dict[str, datetime] = {}

        # Tracks the known anomaly process for each resource that currently has
        # an active incident.  Used for correlation: if a second anomaly type
        # _active_process_by_resource removed — cross-anomaly correlation is now
        # handled by the backend dedup query (resource_name match only).
        # Watcher sends all anomaly events; backend decides if it is a new incident
        # or an additional signal for an already-open one.

        # Port→process discovery cache — updated every poll for containers with external checks.
        # Format: { "agentic_os_flower": { 5555: {"process": "celery", "pid": 847} } }
        self._port_process_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}

        # Metadata for external checks that just failed — enriches alert payloads.
        self._external_check_metadata: Dict[str, Dict[str, Any]] = {}

        # Load config from file (local override / legacy)
        self.load_config_from_file()

        # Timestamp of last successful API config fetch (epoch seconds)
        self._last_api_config_fetch: float = 0.0
        # How often to poll the platform API for settings changes (seconds)
        self._api_config_interval: float = 30.0

        logger.info(f"🚀 [INIT] Watcher Brain initialized for Sentinel: {sentinel_container}")
        logger.info(f"📊 [INIT] Docker Stats monitoring enabled (CPU: {cpu_threshold}%, Memory: {memory_threshold}%)")
        logger.info(f"💾 [INIT] Advanced monitoring enabled (Disk: {disk_threshold}%, Connections: {connection_threshold})")

    def load_config_from_file(self):
        """Load thresholds from watcher_config.json file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

                # Update thresholds from config
                self.poll_interval = config.get('poll_interval', self.poll_interval)
                self.cooldown_seconds = config.get('cooldown_seconds', self.cooldown_seconds)
                self.anomaly_threshold = config.get('syscall_threshold', self.anomaly_threshold)
                self.cpu_threshold = config.get('cpu_threshold', self.cpu_threshold)
                self.memory_threshold = config.get('memory_threshold', self.memory_threshold)
                self.disk_threshold = config.get('disk_threshold', self.disk_threshold)
                self.connection_threshold = config.get('connection_threshold', self.connection_threshold)
                self.min_consecutive_polls = config.get('min_consecutive_polls', self.min_consecutive_polls)
                self.discovery_interval_polls = config.get('discovery_interval_polls', self.discovery_interval_polls)
                self.discovery_enabled = config.get('discovery_enabled', self.discovery_enabled)
                # Recompute hysteresis thresholds if alert thresholds changed
                self.cpu_clear_threshold = config.get('cpu_clear_threshold', self.cpu_threshold * 0.80)
                self.memory_clear_threshold = config.get('memory_clear_threshold', self.memory_threshold * 0.80)

                # Track file modification time for hot-reload
                self.last_config_mtime = self.config_file.stat().st_mtime

                logger.info(f"⚙️  [CONFIG LOADED] Syscalls: {self.anomaly_threshold}, CPU: {self.cpu_threshold}%, Memory: {self.memory_threshold}%")
            else:
                logger.warning(f"⚠️  [CONFIG] File not found: {self.config_file}, using defaults")
        except Exception as e:
            logger.error(f"❌ [CONFIG ERROR] Failed to load config: {e}")

    def reload_config_if_changed(self):
        """Check if config file changed and reload if necessary (for hot-wiring)."""
        try:
            if self.config_file.exists():
                current_mtime = self.config_file.stat().st_mtime
                if self.last_config_mtime is None or current_mtime > self.last_config_mtime:
                    old_threshold = self.anomaly_threshold
                    old_cpu = self.cpu_threshold
                    self.load_config_from_file()
                    if old_threshold != self.anomaly_threshold or old_cpu != self.cpu_threshold:
                        logger.info(f"🔄 [CONFIG RELOADED] Syscalls: {old_threshold} → {self.anomaly_threshold}, CPU: {old_cpu}% → {self.cpu_threshold}%")
        except Exception as e:
            logger.warning(f"⚠️  [CONFIG RELOAD] Failed to check config: {e}")

    async def load_config_from_api(self) -> bool:
        """
        Fetch watcher thresholds from the platform's settings API (DB-backed).
        Returns True if any setting changed.
        """
        url = f"{self.api_base_url}/api/settings/watcher"
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=self._api_headers) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(f"[API CONFIG] Settings API returned {resp.status_code} — keeping current values")
                    return False

                data = resp.json()
                settings = {s["key"]: s["value"] for s in data.get("settings", [])}
                if not settings:
                    return False

                changed: list[str] = []

                def _apply(key: str, attr: str, cast):
                    nonlocal changed
                    if key in settings:
                        new = cast(settings[key])
                        if getattr(self, attr) != new:
                            changed.append(f"{attr}: {getattr(self, attr)} → {new}")
                            setattr(self, attr, new)

                _apply("watcher.poll_interval",          "poll_interval",          int)
                _apply("watcher.cooldown_seconds",       "cooldown_seconds",       int)
                _apply("watcher.syscall_threshold",      "anomaly_threshold",      int)
                _apply("watcher.connection_threshold",   "connection_threshold",   int)
                _apply("watcher.min_consecutive_polls",  "min_consecutive_polls",  int)
                _apply("watcher.discovery_interval_polls", "discovery_interval_polls", int)
                _apply("watcher.discovery_enabled",      "discovery_enabled",      bool)

                # CPU — also recompute hysteresis
                if "watcher.cpu_threshold" in settings:
                    new_cpu = float(settings["watcher.cpu_threshold"])
                    if self.cpu_threshold != new_cpu:
                        changed.append(f"cpu_threshold: {self.cpu_threshold} → {new_cpu}")
                        self.cpu_threshold = new_cpu
                        self.cpu_clear_threshold = new_cpu * 0.80

                # Memory — also recompute hysteresis
                if "watcher.memory_threshold" in settings:
                    new_mem = float(settings["watcher.memory_threshold"])
                    if self.memory_threshold != new_mem:
                        changed.append(f"memory_threshold: {self.memory_threshold} → {new_mem}")
                        self.memory_threshold = new_mem
                        self.memory_clear_threshold = new_mem * 0.80

                # Disk
                if "watcher.disk_threshold" in settings:
                    new_disk = float(settings["watcher.disk_threshold"])
                    if self.disk_threshold != new_disk:
                        changed.append(f"disk_threshold: {self.disk_threshold} → {new_disk}")
                        self.disk_threshold = new_disk

                self._last_api_config_fetch = time.time()

                if changed:
                    logger.info(f"🔄 [API CONFIG] Settings updated from DB: {', '.join(changed)}")
                else:
                    logger.debug("[API CONFIG] Settings unchanged")

                # ── Also refresh external checks from DB ──────────────────────
                checks_url = (
                    f"{self.api_base_url}/api/monitoring/watchers"
                    f"/{self.watcher_name}/checks"
                )
                try:
                    checks_resp = await client.get(checks_url)
                    if checks_resp.status_code == 200:
                        checks_data = checks_resp.json()
                        enabled_checks = [c for c in checks_data if c.get("enabled", True)]
                        # Always replace — even an empty list overrides hardcoded defaults.
                        # A watcher with no DB checks should run no external checks.
                        new_checks = [
                            ExternalCheckConfig(
                                check_type=c["check_type"],
                                target=c["target"],
                                name=c.get("name", ""),
                                port=c.get("port") or 0,
                                expected_status=c.get("expected_status", 200),
                                timeout_ms=c.get("timeout_ms", 5000),
                                latency_threshold_ms=c.get("latency_threshold_ms", 0),
                                tls_expiry_warning_days=c.get("tls_expiry_warning_days", 30),
                                container_name=c.get("container_name", ""),
                                service_name=c.get("service_name", ""),
                            )
                            for c in enabled_checks
                        ]
                        if new_checks != self.external_checks:
                            self.external_checks = new_checks
                            logger.info(
                                f"🌐 [EXTERNAL] Loaded {len(new_checks)} check(s) from DB "
                                f"(replaced hardcoded defaults)"
                                if not new_checks else
                                f"🌐 [EXTERNAL] Loaded {len(new_checks)} check(s) from DB"
                            )
                except Exception as exc_checks:
                    logger.debug(f"[API CONFIG] Could not load external checks: {exc_checks}")

                # ── Also refresh event-type default severities from the taxonomy ──
                try:
                    et_resp = await client.get(
                        f"{self.api_base_url}/api/event-types",
                        params={"enabled_only": "true"},
                    )
                    if et_resp.status_code == 200:
                        new_severity_map: Dict[str, str] = {}
                        for et in et_resp.json():
                            sev = et.get("default_severity")
                            if not sev:
                                continue
                            new_severity_map[et["code"]] = sev
                            for alias in et.get("aliases") or []:
                                new_severity_map[alias] = sev
                        if new_severity_map != self.event_type_severity:
                            self.event_type_severity = new_severity_map
                            logger.info(
                                f"🎚️ [SEVERITY] Loaded {len(new_severity_map)} event-type "
                                f"default severity override(s) from taxonomy"
                            )
                except Exception as exc_severity:
                    logger.debug(f"[API CONFIG] Could not load event-type severities: {exc_severity}")

                return bool(changed)

        except Exception as exc:
            logger.debug(f"[API CONFIG] Could not reach settings API: {exc}")
            return False

    async def load_log_monitors_from_api(self) -> None:
        """
        Fetch persisted log monitor configs from the platform DB and push them
        into the running LogMonitor service.

        Called once at startup (after the watcher is approved) so that monitors
        created or edited while the watcher was down are not silently dropped.
        The live-push path (kill-API /log-monitors/reload) handles runtime updates.
        """
        url = f"{self.api_base_url}/api/monitoring/watchers/{self.watcher_name}/log-monitors"
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=self._api_headers) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug(
                        f"[LOG-MONITOR] Could not load monitors from API: HTTP {resp.status_code}"
                    )
                    return
                monitors = resp.json()
                if not isinstance(monitors, list):
                    return
                self.log_monitor.reload_configs(monitors)
                logger.info(
                    f"📋 [LOG-MONITOR] Loaded {len(monitors)} monitor(s) from platform DB on startup"
                )
        except Exception as exc:
            logger.debug(f"[LOG-MONITOR] Could not fetch log monitors from API: {exc}")

    def _load_identity(self) -> None:
        """Load persisted watcher_id from .state/watcher_identity.json (if present)."""
        try:
            if self._identity_file.exists():
                data = json.loads(self._identity_file.read_text())
                self._watcher_id = data.get("watcher_id")
                if self._watcher_id:
                    logger.info(
                        f"[IDENTITY] Loaded watcher_id={self._watcher_id} "
                        f"for '{self.watcher_name}'"
                    )
        except Exception as exc:
            logger.warning(f"[IDENTITY] Could not load identity file: {exc}")

    def _save_identity(self, watcher_id: str) -> None:
        """Persist the platform-assigned watcher_id to disk."""
        try:
            self._identity_file.write_text(
                json.dumps({
                    "watcher_id": watcher_id,
                    "watcher_name": self.watcher_name,
                    "saved_at": datetime.utcnow().isoformat(),
                }, indent=2)
            )
            logger.info(f"[IDENTITY] Persisted watcher_id={watcher_id}")
        except Exception as exc:
            logger.warning(f"[IDENTITY] Could not save identity file: {exc}")

    async def register_with_api(self) -> None:
        """
        Register this watcher instance with the platform API.
        Called once on startup and periodically as a heartbeat.

        Reads the 'registration_status' from the response and gates event
        submission accordingly:
          pending  → log and wait; do NOT submit monitoring events
          approved → normal operation
          rejected → log error and exit
        """
        import socket as _socket
        url = f"{self.api_base_url}/api/monitoring/watchers/register"
        try:
            hostname = _socket.gethostname()
        except Exception:
            hostname = "unknown"
        payload = {
            "watcher_name": self.watcher_name,
            "display_name": self.watcher_name.replace("_", " ").title(),
            "host": hostname,
            "poll_interval": self.poll_interval,
            "sentinel_container": self.sentinel_container,
            "nginx_url": self.nginx_url,
            "kill_api_url": self.kill_api_url,
            "environment": self.environment,
            "adapter_mode": self.adapter.adapter_name,
            "watcher_version": WATCHER_VERSION,
            "metrics_history": self._metrics_buffer,
        }
        # For K8s adapters, include the namespace so the backend can substitute
        # {namespace} correctly in command templates (e.g. kubectl exec {pod} -n {namespace}).
        if self.adapter.adapter_name == "kubernetes":
            payload["targets"] = {
                "k8s_namespace": getattr(self.adapter, "namespace", "agentic-platform")
            }
        # Include stable UUID on heartbeats so the platform can look us up by id,
        # not by name — prevents a name collision from stealing our registration.
        if self._watcher_id:
            payload["watcher_id"] = self._watcher_id

        try:
            async with httpx.AsyncClient(timeout=5.0, headers=self._api_headers) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("registration_status", "approved")
                    prev = self._registration_status
                    self._registration_status = status

                    # Persist watcher_id on first successful registration
                    returned_id = data.get("watcher_id")
                    if returned_id and not self._watcher_id:
                        self._watcher_id = returned_id
                        self._save_identity(returned_id)

                    if status == "approved":
                        if prev != "approved":
                            logger.info(
                                f"✅ [REGISTER] Watcher '{self.watcher_name}' "
                                f"(id={self._watcher_id}) approved — monitoring events enabled"
                            )
                        else:
                            logger.debug(
                                f"📋 [REGISTER] Watcher '{self.watcher_name}' heartbeat OK (approved)"
                            )
                    elif status == "pending":
                        logger.info(
                            f"⏳ [REGISTER] Watcher '{self.watcher_name}' "
                            f"(id={self._watcher_id}) is PENDING operator approval — "
                            f"monitoring active but events suppressed. "
                            f"Approve in Admin → Monitoring Setup."
                        )
                    elif status == "disabled":
                        logger.info(
                            f"🔇 [REGISTER] Watcher '{self.watcher_name}' "
                            f"(id={self._watcher_id}) is DISABLED by operator — "
                            f"monitoring active, events suppressed until re-enabled."
                        )
                    elif status == "rejected":
                        logger.error(
                            f"🚫 [REGISTER] Watcher '{self.watcher_name}' "
                            f"(id={self._watcher_id}) has been REJECTED by an operator. Exiting."
                        )
                        import sys
                        sys.exit(1)
                else:
                    logger.warning(f"[REGISTER] Registration returned {resp.status_code}: {resp.text}")
                # Reset consecutive-failure counter on any response (even non-200)
                self._register_consecutive_failures = 0
        except Exception as exc:
            # Count failures so the poll loop can retry sooner during backend restarts
            self._register_consecutive_failures = getattr(self, "_register_consecutive_failures", 0) + 1
            logger.warning(f"[REGISTER] Could not register with platform (attempt {self._register_consecutive_failures}): {exc}")

    async def reload_config_from_api_if_stale(self) -> None:
        """Call load_config_from_api if the cache interval has elapsed."""
        if time.time() - self._last_api_config_fetch >= self._api_config_interval:
            await self.load_config_from_api()

    def write_status(
        self,
        state: str,
        event_type: str = "",
        process: str = "",
        syscall_count: int = 0
    ):
        """Write watcher status to file for dashboard visibility.

        Also persists active_conditions so they survive a watcher restart.
        """
        status = {
            "sentinel_container": self.sentinel_container,
            "state": state,
            "active_incident_id": self.active_incident_id or "",
            "last_anomaly_process": process or self.last_anomaly_process or "",
            "last_syscall_count": syscall_count,
            "last_event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            # Persist active_conditions so they survive watcher restarts
            "active_conditions": self.active_conditions,
            # Persist workflow IDs so reconciliation works after restarts
            "active_workflow_ids": self._active_workflow_ids,
        }
        self.status_file.write_text(json.dumps(status, indent=2))
        logger.info(f"📡 [STATUS] {state} | Incident: {self.active_incident_id} | Process: {process}")

    def restore_active_conditions(self) -> None:
        """
        Restore active_conditions from status file on startup.

        Prevents re-firing incidents that were already being tracked before
        a watcher container restart.
        """
        try:
            if self.status_file.exists():
                data = json.loads(self.status_file.read_text())
                saved = data.get("active_conditions", {})
                saved_wf = data.get("active_workflow_ids", {})
                if saved:
                    self.active_conditions = saved
                    self._active_workflow_ids = saved_wf
                    logger.info(
                        f"♻️  [RESTORE] Restored {len(saved)} active_conditions from status file: "
                        f"{list(saved.keys())}"
                    )
        except Exception as e:
            logger.warning(f"[RESTORE] Could not restore active_conditions: {e}")

    def get_all_containers(self) -> List[str]:
        """
        Return workload names for cluster-wide monitoring.
        Docker: docker ps (excludes sentinel/watcher).
        K8s:    adapter.list_targets() (pod names from the K8s API).
        """
        if self.adapter.adapter_name == "kubernetes":
            try:
                targets = self.adapter.list_targets()
                return targets if targets else []
            except Exception as exc:
                logger.warning(f"⚠️  [CLUSTER] K8s list_targets failed: {exc}")
                return []

        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10
            )
            containers = [c.strip() for c in result.stdout.splitlines() if c.strip()]
            excluded = {'sentinel_senses', 'watcher_brain'}
            monitored = [c for c in containers if c not in excluded]
            return monitored if monitored else []
        except Exception as e:
            logger.warning(f"⚠️  [CLUSTER] Failed to get container list: {e}")
            return [self.sentinel_container]

    def get_kernel_telemetry(self, container: str = None) -> Optional[Dict[str, int]]:
        """
        Get syscall telemetry from the sentinel.

        Docker mode: docker exec into the sentinel container and run bpftrace for 5 s.
        K8s mode:    GET /metrics from the sentinel HTTP service (sentinel_http.py keeps
                     bpftrace running continuously and serves the latest 5-second snapshot).
        """
        if self.adapter.adapter_name == "kubernetes":
            sentinel_url = os.getenv(
                "SENTINEL_METRICS_URL",
                "http://sentinel-metrics.agentic-platform.svc.cluster.local:9090/metrics",
            )
            try:
                import requests as _req
                resp = _req.get(sentinel_url, timeout=6)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.debug(f"⚠️  [TELEMETRY] Sentinel HTTP unreachable: {exc}")
                return None

        # Docker path — run bpftrace on demand for a fresh 5-second window
        target = container or self.sentinel_container
        cmd = [
            "docker", "exec", target, "bpftrace", "-f", "json", "-e",
            "tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } interval:s:5 { exit(); }"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            for line in result.stdout.splitlines():
                if '"@":' in line:
                    data = json.loads(line)
                    return data.get("data", {}).get("@", {})
            return {}
        except Exception as e:
            logger.debug(f"⚠️  [TELEMETRY] Failed to get kernel telemetry from {target}: {e}")
            return None

    # ── Sensitivity helpers ───────────────────────────────────────────────────

    def _resource_in_cooldown(self, resource_name: str) -> bool:
        """Check if a specific resource is still within its post-incident cooldown."""
        until = self.per_resource_cooldown.get(resource_name)
        if not until:
            return False
        now = datetime.utcnow()
        if now < until:
            remaining = int((until - now).total_seconds())
            logger.info(f"⏳ [COOLDOWN] {resource_name}: {remaining}s remaining")
            return True
        del self.per_resource_cooldown[resource_name]
        return False

    def _set_resource_cooldown(self, resource_name: str) -> None:
        """Start a per-resource cooldown after an incident is opened."""
        self.per_resource_cooldown[resource_name] = datetime.utcnow() + timedelta(seconds=self.cooldown_seconds)
        logger.info(f"❄️  [COOLDOWN] {resource_name}: {self.cooldown_seconds}s cooldown started")

    def _condition_in_cooldown(self, resource_name: str, anomaly_type: str) -> bool:
        """Per-condition cooldown — independent of other anomaly types on the same resource."""
        key = f"{resource_name}:{anomaly_type}"
        until = self.per_resource_cooldown.get(key)
        if not until:
            return False
        now = datetime.utcnow()
        if now < until:
            remaining = int((until - now).total_seconds())
            logger.info(f"⏳ [CONDITION COOLDOWN] {resource_name}/{anomaly_type}: {remaining}s remaining")
            return True
        del self.per_resource_cooldown[key]
        return False

    def _set_condition_cooldown(self, resource_name: str, anomaly_type: str) -> None:
        """Start a per-condition cooldown so other anomaly types on this resource can still fire."""
        key = f"{resource_name}:{anomaly_type}"
        self.per_resource_cooldown[key] = datetime.utcnow() + timedelta(seconds=self.cooldown_seconds)
        logger.info(f"❄️  [CONDITION COOLDOWN] {resource_name}/{anomaly_type}: {self.cooldown_seconds}s started")

    # _is_correlated_symptom() removed — cross-anomaly-type deduplication is now
    # handled by the backend (resource_name-only dedup query in monitoring_events.py).
    # The watcher sends all detected anomaly events; the backend links them to an
    # existing open incident for the same container rather than opening duplicates.

    def _increment_anomaly_count(self, resource_name: str, anomaly_type: str) -> int:
        """Increment and return the consecutive-poll counter for a resource+type pair."""
        key = f"{resource_name}:{anomaly_type}"
        self.consecutive_anomaly_counts[key] = self.consecutive_anomaly_counts.get(key, 0) + 1
        count = self.consecutive_anomaly_counts[key]
        logger.info(f"📈 [SUSTAINED] {resource_name} {anomaly_type}: {count}/{self.min_consecutive_polls} consecutive polls")
        return count

    def _reset_anomaly_count(self, resource_name: str, anomaly_type: str) -> None:
        """Reset the consecutive-poll counter (condition has cleared)."""
        key = f"{resource_name}:{anomaly_type}"
        if key in self.consecutive_anomaly_counts:
            logger.debug(f"🔄 [RESET] {resource_name} {anomaly_type} counter cleared")
            del self.consecutive_anomaly_counts[key]

    def _reset_stale_counters(
        self,
        current_anomaly_keys: set,
        container_stats: Optional[Dict],
    ) -> None:
        """
        Reset counters for conditions that are no longer detected this poll.

        For CPU/memory we apply hysteresis: the counter is only reset when the
        metric drops below the clear threshold (80 % of the alert threshold).
        This prevents rapid oscillation at the boundary from resetting the
        counter on every dip just below the alert level.

        For all other anomaly types (health checks, disk, etc.) the counter
        resets immediately when the condition is not detected.
        """
        for key in list(self.consecutive_anomaly_counts.keys()):
            if key in current_anomaly_keys:
                continue  # Still anomalous — nothing to reset

            resource_name, anomaly_type = key.split(":", 1)

            # CPU / memory hysteresis: only reset when well below threshold
            if anomaly_type in ("cpu_spike", "high_cpu") and container_stats:
                metrics = container_stats.get(resource_name)
                if metrics and metrics.cpu_percent > self.cpu_clear_threshold:
                    logger.debug(
                        f"🔄 [HYSTERESIS] {resource_name} CPU {metrics.cpu_percent:.1f}% "
                        f"above clear threshold {self.cpu_clear_threshold:.1f}% — keeping counter"
                    )
                    continue

            if anomaly_type == "memory_surge" and container_stats:
                metrics = container_stats.get(resource_name)
                if metrics and metrics.memory_percent > self.memory_clear_threshold:
                    logger.debug(
                        f"🔄 [HYSTERESIS] {resource_name} memory {metrics.memory_percent:.1f}% "
                        f"above clear threshold {self.memory_clear_threshold:.1f}% — keeping counter"
                    )
                    continue

            self._reset_anomaly_count(resource_name, anomaly_type)

    # ─────────────────────────────────────────────────────────────────────────

    # System/infrastructure processes that are expected to generate high syscall
    # counts and should never be treated as anomalies.
    SYSCALL_EXCLUDE = frozenset({
        # Container runtime (exact names)
        "containerd-shim", "containerd", "dockerd", "runc",
        # Docker CLI and tooling — the docker CLI is NOT dockerd but still generates
        # high syscall counts from the watcher's own polling (docker exec/ps/stats calls)
        # and from container lifecycle events.  These are infrastructure noise, not anomalies.
        "docker", "docker-compose", "docker-buildx", "docker-init",
        # Docker network proxy / relay (Docker user-space proxy for port forwarding)
        "Relay", "docker-proxy",
        # Init / system supervisors  — killing these would crash the host
        "init", "initd", "systemd", "systemd-journal", "systemd-udevd",
        "rpcbind", "dbus-daemon", "sshd", "cron", "crond", "tini",
        # Our own infrastructure processes
        "java",          # neo4j JVM
        "python3",       # backend / worker
        "python",
        "celery",
        "uvicorn",
        "node",          # vite / frontend
        "nginx",
        "bpftrace",      # sentinel itself
        "sh", "bash", "zsh",
        # Neo4j internal threads (high I/O syscalls are normal for the page cache / tx manager)
        "MuninnPageCache", "neo4j",
        # Redis / Postgres internal
        "redis-server", "postgres",
        # Kubernetes infrastructure — high baseline syscall rate is normal
        "kubelet", "kube-apiserver", "kube-controller", "kube-scheduler",
        "kube-proxy", "etcd", "coredns",
    })

    # Prefix-based exclusions for processes with dynamic suffixes like runc:[2:INIT]
    SYSCALL_EXCLUDE_PREFIXES: Tuple[str, ...] = (
        "runc:",            # container init variants: runc:[1:CHILD], runc:[2:INIT]
        "containerd-shim-", # containerd-shim-runc-v2 etc.
        "Relay(",           # Docker Relay(N) port-forwarding workers
        "docker-entrypoi",  # docker-entrypoint.sh (15-char comm truncation)
        "kube-",            # any kube-* K8s infrastructure process
    )

    def detect_anomaly(self) -> Tuple[bool, Optional[str], int, Optional[str]]:
        """
        Detect high syscall intensity anomalies using the sentinel_senses eBPF container.

        sentinel_senses is the ONLY container with bpftrace and privileged kernel access.
        It traces raw_syscalls:sys_enter which fires for every syscall on the HOST — meaning
        it sees ALL processes across ALL containers, not just its own.

        After identifying the top offending process we then locate which container it
        actually lives in (via pgrep) so the incident is attributed to the right resource.

        Phase 1 Optimization: Sample syscalls every N polls to reduce CPU overhead.

        Returns:
            (is_anomaly, process_name, syscall_count, container_name)
        """
        # ── Sentinel guard — skip syscall monitoring when Sentinel is not configured ──
        if not self.sentinel_container:
            return False, None, 0, None

        # Phase 1: Skip syscall collection on light polls (sample every N polls)
        if self.poll_count % self.syscall_sample_interval != 0:
            logger.debug(f"[SYSCALL SAMPLE] Skipping collection on poll #{self.poll_count} (sampling every {self.syscall_sample_interval})")
            return False, None, 0, None

        # Run bpftrace once in sentinel_senses — it sees the whole host
        telemetry = self.get_kernel_telemetry()   # defaults to self.sentinel_container
        if not telemetry:
            logger.debug("⚠️  [SYSCALL] No telemetry from sentinel_senses (bpftrace unavailable?)")
            return False, None, 0, None

        # Filter out known system/infra processes (exact match + prefix match)
        filtered = {
            p: c for p, c in telemetry.items()
            if p not in self.SYSCALL_EXCLUDE
            and not any(p.startswith(pfx) for pfx in self.SYSCALL_EXCLUDE_PREFIXES)
        }
        if not filtered:
            logger.debug("✓ [SYSCALL] Only system processes in telemetry window")
            return False, None, 0, None

        top_proc = max(filtered, key=filtered.get)
        count = filtered[top_proc]
        logger.info(f"📍 [SYSCALL] Top user process: {top_proc} ({count} syscalls/5s), threshold={self.anomaly_threshold}")

        if count > self.anomaly_threshold:
            # Identify which container the process belongs to.
            # Bursting processes (e.g. dd with sleep intervals) may have already exited
            # by the time bpftrace returns. Retry pgrep every 300ms for up to 3s to
            # catch the process during its next burst, then fall back to the cache,
            # then sentinel_container as last resort.
            import time as _time
            container = None
            for _attempt in range(10):
                container = self._find_process_container(top_proc)
                if container:
                    self._process_container_cache[top_proc] = container
                    break
                _time.sleep(0.3)
            if not container:
                container = self._process_container_cache.get(top_proc) or self.sentinel_container
                if top_proc in self._process_container_cache:
                    logger.info(f"[PROCESS HUNT] '{top_proc}' not found live — using cached container '{container}'")
            logger.warning(
                f"🚨 [SYSCALL ANOMALY] '{top_proc}' in '{container}': "
                f"{count} syscalls/5s (threshold: {self.anomaly_threshold})"
            )
            return True, top_proc, count, container

        logger.debug(f"✓ [SYSCALL] No anomaly — max={count} (threshold={self.anomaly_threshold})")
        return False, None, 0, None

    def _find_process_container(self, process_name: str) -> Optional[str]:
        """
        Find which running container a named process belongs to.

        Runs `pgrep -c <name>` inside every monitored container (exact name match)
        and returns the container with the HIGHEST process count.  This prevents
        a false attribution when the same process name exists in multiple containers
        (e.g. neo4j appearing before redis in `docker ps` order).

        Falls back to a single-hit search if pgrep -c is not available (older images).
        """
        containers = self.get_all_containers()
        best_container: Optional[str] = None
        best_count: int = 0
        fallback_container: Optional[str] = None  # first container with returncode 0

        use_adapter = self.adapter.adapter_name != "docker"

        for container in containers:
            # Sentinel uses hostPID=true and sees all node processes — skip it
            # so it never gets falsely attributed as the source container.
            if container.startswith("sentinel"):
                continue
            try:
                if use_adapter:
                    # K8s / SSH / vCenter — route through the execution adapter so
                    # the right transport (kubectl exec, ssh, etc.) is used instead
                    # of Docker CLI, which is not available / irrelevant in these modes.
                    r1 = self.adapter.exec(container, f"pgrep -c -x {process_name}", timeout=5)
                    if r1.success and r1.stdout.strip().isdigit():
                        count = int(r1.stdout.strip())
                    else:
                        r2 = self.adapter.exec(container, f"pgrep -x {process_name}", timeout=5)
                        if not r2.success:
                            continue
                        pids = [p for p in r2.stdout.strip().splitlines() if p.strip()]
                        count = len(pids) if pids else 1
                else:
                    # Docker mode — use docker exec directly (fastest path)
                    # First try procps-style pgrep -c (count mode, not supported by busybox/Alpine)
                    result = subprocess.run(
                        ["docker", "exec", container, "pgrep", "-c", "-x", process_name],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        try:
                            count = int(result.stdout.strip())
                        except ValueError:
                            count = 1
                    else:
                        # Either "not found" or busybox/Alpine (no -c flag).
                        result2 = subprocess.run(
                            ["docker", "exec", container, "pgrep", "-x", process_name],
                            capture_output=True, text=True, timeout=5
                        )
                        if result2.returncode != 0:
                            continue
                        pids = [p for p in result2.stdout.strip().splitlines() if p.strip()]
                        count = len(pids) if pids else 1

                logger.debug(
                    f"[PROCESS HUNT] '{process_name}' in '{container}': {count} PIDs"
                )
                if fallback_container is None:
                    fallback_container = container
                if count > best_count:
                    best_count = count
                    best_container = container
            except Exception:
                continue

        if best_container:
            logger.info(
                f"[PROCESS HUNT] '{process_name}' -> '{best_container}' "
                f"({best_count} PIDs, highest across {len(containers)} containers)"
            )
            return best_container

        logger.debug(f"[PROCESS HUNT] '{process_name}' not found in any container")
        return None

    def create_incident_alert(self, process: str, syscall_count: int, container: str = None) -> Dict[str, Any]:
        """
        Create alert payload for incident submission to agentic platform.

        Args:
            process: Process name detected in anomaly
            syscall_count: Number of syscalls in 5-second window
            container: Container where anomaly was detected (defaults to sentinel_container)

        Returns:
            Alert payload dict
        """
        resource = container or self.sentinel_container
        return {
            "severity": "critical",
            "type": "high_syscall_intensity",
            "resource_name": resource,
            # Dedicated field so runbook/mechanic can target the right process
            "anomaly_process": process,
            "title": f"High syscall intensity on {resource} (process: {process})",
            "description": (
                f"Kernel anomaly detected in '{resource}': process '{process}' generated "
                f"{syscall_count} syscalls in 5-second window (threshold: {self.anomaly_threshold}). "
                f"This indicates potential syscall bombing, resource exhaustion, "
                f"or compromised process behavior."
            ),
        }

    def generate_incident_id(self) -> str:
        """Generate unique incident ID."""
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        return f"INC-WATCHER-{timestamp}"

    async def submit_monitoring_event_to_platform(
        self,
        event_type: str,
        resource_name: str,
        raw_criticality: str,
        alert_payload: Dict[str, Any],
        signal_value: Optional[float] = None,
        signal_threshold: Optional[float] = None,
        anomaly_process: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Submit monitoring event to agentic platform via POST /api/monitoring-events.

        The backend will:
        1. Receive the MonitoringEvent
        2. Run EventQualificationService to score it
        3. If score >= threshold: open incident workflow automatically
        4. If score < threshold: dismiss event (no incident)

        Args:
            event_type: Event type (high_syscall_intensity, cpu_spike, etc.)
            resource_name: Resource name (e.g., sentinel_senses)
            raw_criticality: Raw signal criticality (info, warning, critical)
            alert_payload: Full alert details for audit
            signal_value: Numeric signal value (optional)
            signal_threshold: Threshold that was exceeded (optional)
            anomaly_process: Process involved (optional)

        Returns:
            (success, event_id, workflow_id) — workflow_id is None when the event
            did not qualify as an incident (scored below threshold).
        """
        # Gate: do not submit events until the operator has approved this watcher
        if self._registration_status in ("pending", "disabled"):
            label = "pending approval" if self._registration_status == "pending" else "disabled"
            logger.info(
                f"⏳ [GATE] Event '{event_type}' on '{resource_name}' suppressed — "
                f"watcher '{self.watcher_name}' is {label}"
            )
            return False, None, None
        if self._registration_status == "rejected":
            logger.error(f"🚫 [GATE] Watcher '{self.watcher_name}' is rejected; not submitting events")
            return False, None, None

        try:
            async with httpx.AsyncClient(timeout=30.0, headers=self._api_headers) as client:
                response = await client.post(
                    f"{self.api_base_url}/api/monitoring-events",
                    json={
                        "source": self.watcher_name,
                        "event_type": event_type,
                        "resource_name": resource_name,
                        "raw_criticality": raw_criticality,
                        "signal_value": signal_value,
                        "signal_threshold": signal_threshold,
                        "anomaly_process": anomaly_process,
                        "raw_payload": alert_payload,
                    }
                )

                if response.status_code == 201:
                    data = response.json()
                    event_id = data.get("event_id")
                    # API response uses 'qualified_as_incident', not 'qualified'
                    qualified = data.get("qualified_as_incident", False)
                    incident_workflow_id = data.get("incident_workflow_id")

                    if qualified and incident_workflow_id:
                        status_msg = f"linked to incident (workflow: {incident_workflow_id})"
                    elif qualified:
                        status_msg = "qualified (no workflow assigned)"
                    else:
                        status_msg = "scored below threshold"
                    logger.info(f"✓ [MONITORING EVENT] Event: {event_id}, {status_msg}")
                    return True, event_id, incident_workflow_id
                else:
                    logger.error(
                        f"❌ [EVENT SUBMISSION FAILED] Status: {response.status_code}, "
                        f"Response: {response.text}"
                    )
                    return False, None, None
        except Exception as e:
            logger.error(f"❌ [MONITORING EVENT ERROR] {e}")
            return False, None, None

    async def submit_condition_cleared(self, resource_name: str, original_event_type: str) -> None:
        """
        Submit a condition_cleared event to the platform.

        Called when the watcher confirms a previously-alerting condition has
        returned to normal.  The backend will close any open incidents for
        this resource + event_type combination with resolution_source=watcher_all_clear,
        regardless of whether automated remediation succeeded or failed.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self._api_headers) as client:
                response = await client.post(
                    f"{self.api_base_url}/api/monitoring-events",
                    json={
                        "source": self.watcher_name,
                        "event_type": "condition_cleared",
                        "resource_name": resource_name,
                        "raw_criticality": "info",
                        "signal_value": None,
                        "signal_threshold": None,
                        "anomaly_process": None,
                        "raw_payload": {
                            "original_event_type": original_event_type,
                            "cleared_at": datetime.utcnow().isoformat(),
                            "description": (
                                f"Condition '{original_event_type}' on '{resource_name}' "
                                f"has returned to normal"
                            ),
                        },
                    }
                )
                if response.status_code == 201:
                    logger.info(
                        f"✅ [ALL CLEAR] Sent condition_cleared for {resource_name} "
                        f"({original_event_type})"
                    )
                else:
                    logger.warning(
                        f"⚠️ [ALL CLEAR] Backend returned {response.status_code} "
                        f"for condition_cleared on {resource_name}"
                    )
        except Exception as e:
            logger.error(f"❌ [ALL CLEAR ERROR] Failed to submit condition_cleared: {e}")

    def is_in_cooldown(self) -> bool:
        """Check if currently in cooldown period."""
        if not self.cooldown_until:
            return False
        now = datetime.utcnow()
        if now < self.cooldown_until:
            remaining = int((self.cooldown_until - now).total_seconds())
            logger.info(f"⏳ [COOLDOWN] {remaining}s remaining")
            return True
        self.cooldown_until = None
        return False

    def set_cooldown(self):
        """Start cooldown period to prevent alert fatigue."""
        self.cooldown_until = datetime.utcnow() + timedelta(seconds=self.cooldown_seconds)
        logger.info(f"❄️  [COOLDOWN SET] {self.cooldown_seconds}s cooldown started")

    def get_docker_container_stats(self) -> Optional[Dict[str, ContainerMetrics]]:
        """
        Get Docker container metrics (CPU, memory, network, I/O).

        Returns:
            Dict mapping container_name -> ContainerMetrics, or None on error
        """
        return self.docker_stats.get_all_container_stats()

    def write_container_stats(self, stats: Dict[str, ContainerMetrics]):
        """Write container stats to file for dashboard visibility."""
        stats_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "containers": {}
        }

        for name, metrics in stats.items():
            stats_data["containers"][name] = {
                "cpu_percent": metrics.cpu_percent,
                "memory_used_mb": metrics.memory_used_mb,
                "memory_limit_mb": metrics.memory_limit_mb,
                "memory_percent": metrics.memory_percent,
                "network_in_mb": metrics.network_in_mb,
                "network_out_mb": metrics.network_out_mb,
                "io_read_mb": metrics.io_read_mb,
                "io_write_mb": metrics.io_write_mb,
                "pids": metrics.pids,
            }

        self.stats_file.write_text(json.dumps(stats_data, indent=2))

    def _record_metrics_snapshot(
        self,
        container_stats: Optional[Dict],
        anomaly_count: int,
        disk_map: Optional[Dict[str, float]] = None,
    ) -> None:
        """Append a summary snapshot to the rolling metrics buffer (max 20 points).

        Args:
            container_stats: Live ContainerMetrics dict (cpu/mem from docker stats).
            anomaly_count:   Total anomalies detected this poll cycle.
            disk_map:        Dict[container_name, usage_percent] from detect_disk_anomalies.
                             Passed directly so the widget never shows 0 due to a
                             container-name mismatch between docker-stats and df output.
        """
        if container_stats:
            cpu_vals = [m.cpu_percent for m in container_stats.values() if m.cpu_percent > 0]
            mem_vals = [m.memory_percent for m in container_stats.values() if m.memory_percent > 0]
            # Prefer the authoritative disk_map (from df) over the enriched attr to
            # avoid any container-name key mismatch producing a silent 0.
            if disk_map:
                disk_vals = [v for v in disk_map.values() if v > 0]
            else:
                disk_vals = [
                    getattr(m, "disk_percent", 0)
                    for m in container_stats.values()
                    if getattr(m, "disk_percent", 0) > 0
                ]
        else:
            cpu_vals = mem_vals = disk_vals = []

        point = {
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "cpu": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0,
            "mem": round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else 0,
            "disk": round(sum(disk_vals) / len(disk_vals), 1) if disk_vals else 0,
            "alerts": anomaly_count,
        }
        self._metrics_buffer.append(point)
        if len(self._metrics_buffer) > self._METRICS_BUFFER_MAX:
            self._metrics_buffer = self._metrics_buffer[-self._METRICS_BUFFER_MAX:]

    def _run_discovery_via_api(self, container_stats) -> Dict[str, int]:
        """
        Collect container properties via Docker and POST them to the backend API.
        The backend owns all Neo4j writes — the watcher never connects to Neo4j directly.
        """
        from agentic_os.services.discovery_service import DISCOVERY_EXCLUDE
        import requests as _requests

        containers = self.discovery.get_running_containers()
        batch = []
        for name in containers:
            if name in DISCOVERY_EXCLUDE:
                continue
            inspect = self.discovery.inspect_container(name)
            if not inspect:
                continue
            stats = (container_stats or {}).get(name)
            props = self.discovery.extract_properties(inspect, stats)
            batch.append({"container_name": name, "props": props})

        if not batch:
            return {"updated": 0, "new_cis": 0, "errors": 0, "total": 0}

        resp = _requests.post(
            f"{self.api_base_url}/api/cmdb/discovery",
            json={"source": self.watcher_name, "watcher_id": self._watcher_id or None, "containers": batch},
            headers=self._api_headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # Maps K8s 'app' labels to canonical seeded CMDB names so that K8s discovery
    # enriches existing CI nodes rather than creating parallel duplicates.
    _K8S_TO_CMDB_NAME: dict = {
        "backend":       "agentic_os_backend",
        "celery-worker": "agentic_os_celery_worker",
        "flower":        "agentic_os_flower",
        "frontend":      "agentic-frontend",
        "neo4j":         "agentic_os_neo4j",
        "postgres":      "agentic_os_postgres",
        "redis":         "agentic_os_redis",
        "sentinel":      "sentinel_senses",
        "watcher":       "watcher_brain",
    }

    def _run_k8s_discovery_via_api(self, container_stats: dict) -> dict:
        """
        K8s-mode CMDB discovery: reads pod specs from the Kubernetes API and
        posts to /api/cmdb/discovery.  Groups pods by their 'app' label so
        multiple replicas of the same Deployment converge to one CMDB node.
        K8s app labels are mapped to canonical seeded CI names via _K8S_TO_CMDB_NAME.
        """
        import requests as _requests
        from datetime import datetime, timezone
        from agentic_os.services.adapters.k8s_adapter import _parse_cpu_nano, _parse_mem_ki

        core = getattr(self.adapter, '_core_v1', None)
        ns   = getattr(self.adapter, 'namespace', 'default')
        if core is None:
            return {"updated": 0, "new_cis": 0, "errors": 0, "total": 0}

        pod_names = self.adapter.list_targets()
        if not pod_names:
            return {"updated": 0, "new_cis": 0, "errors": 0, "total": 0}

        _env_keys = {"ENVIRONMENT", "ENV", "DEPLOY_ENV", "APP_ENV", "NODE_ENV"}
        seen_apps: set = set()
        batch: list = []

        for pod_name in pod_names:
            try:
                pod = core.read_namespaced_pod(pod_name, ns)
            except Exception as exc:
                logger.debug(f"[K8S DISCOVERY] pod read failed {pod_name}: {exc}")
                continue

            # Derive app name from label, fallback to stripping hash suffix
            app_name = (pod.metadata.labels or {}).get('app') or pod_name.rsplit('-', 2)[0]
            # Remap to canonical seeded CMDB name where one exists
            app_name = self._K8S_TO_CMDB_NAME.get(app_name, app_name)
            if app_name in seen_apps:
                continue  # already processed a pod for this Deployment
            seen_apps.add(app_name)

            containers = pod.spec.containers or []
            first_c    = containers[0] if containers else None

            # Sum resource limits across all containers in the pod
            total_cpu_n  = 0
            total_mem_ki = 0
            for c in containers:
                lim = (c.resources.limits or {}) if c.resources else {}
                if lim.get('cpu'):
                    total_cpu_n  += _parse_cpu_nano(lim['cpu'])
                if lim.get('memory'):
                    total_mem_ki += _parse_mem_ki(lim['memory'])

            cpu_limit_cores = round(total_cpu_n / 1_000_000_000, 2) if total_cpu_n else None
            memory_limit_mb = round(total_mem_ki / 1024, 1)           if total_mem_ki else None

            # Exposed ports from first container
            ports = []
            if first_c and first_c.ports:
                for p in first_c.ports:
                    ports.append(f"{p.container_port}/{(p.protocol or 'TCP').upper()}")

            # Readiness from pod conditions
            health_status = None
            if pod.status and pod.status.conditions:
                for cond in pod.status.conditions:
                    if cond.type == 'Ready':
                        health_status = 'healthy' if cond.status == 'True' else 'unhealthy'
                        break

            # Environment from first container's env vars
            detected_environment = None
            if first_c and first_c.env:
                for ev in first_c.env:
                    if ev.name in _env_keys and ev.value:
                        detected_environment = ev.value.lower()
                        break

            # Live metrics from adapter poll (keyed by pod name)
            tm = (container_stats or {}).get(pod_name)

            batch.append({
                "container_name": app_name,
                "props": {
                    "docker_image":         first_c.image if first_c else None,
                    "platform":             "linux",
                    "cpu_limit_cores":      cpu_limit_cores,
                    "memory_limit_mb":      memory_limit_mb,
                    "ip_address":           pod.status.pod_ip if pod.status else None,
                    "exposed_ports":        ', '.join(ports) or None,
                    "container_status":     (pod.status.phase or 'unknown').lower() if pod.status else 'unknown',
                    "health_status":        health_status,
                    "started_at":           pod.status.start_time.isoformat() if (pod.status and pod.status.start_time) else None,
                    "detected_environment": detected_environment,
                    "last_discovered_at":   datetime.now(timezone.utc).isoformat(),
                    "current_cpu_percent":  round(tm.cpu_percent,    1) if tm else None,
                    "current_memory_mb":    round(tm.memory_used_mb, 1) if tm else None,
                    "current_memory_pct":   round(tm.memory_percent, 1) if tm else None,
                    "current_pids":         None,
                },
            })

        if not batch:
            return {"updated": 0, "new_cis": 0, "errors": 0, "total": 0}

        resp = _requests.post(
            f"{self.api_base_url}/api/cmdb/discovery",
            json={"source": self.watcher_name, "watcher_id": self._watcher_id or None, "containers": batch},
            headers=self._api_headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def detect_container_anomalies(self, stats: Dict[str, ContainerMetrics]) -> List[Tuple[str, str, str]]:
        """
        Detect container-level anomalies (CPU spike, memory surge).

        Args:
            stats: Container metrics dictionary

        Returns:
            List of (container_name, anomaly_type, description)
        """
        anomalies = []

        for name, metrics in stats.items():
            # CPU spike detection
            if metrics.cpu_percent > self.cpu_threshold:
                anomalies.append((
                    name,
                    "cpu_spike",
                    f"CPU usage {metrics.cpu_percent:.1f}% exceeds threshold {self.cpu_threshold}%"
                ))
                logger.warning(f"🔴 [CPU SPIKE] {name}: {metrics.cpu_percent:.1f}%")

            # Memory surge detection
            if metrics.memory_percent > self.memory_threshold:
                anomalies.append((
                    name,
                    "memory_surge",
                    f"Memory {metrics.memory_percent:.1f}% ({metrics.memory_used_mb:.0f}MB/{metrics.memory_limit_mb:.0f}MB) exceeds threshold {self.memory_threshold}%"
                ))
                logger.warning(f"🔴 [MEMORY SURGE] {name}: {metrics.memory_percent:.1f}%")

        return anomalies

    def get_culprit_process(self, container_name: str) -> Optional[str]:
        """
        Identify the top CPU-consuming process in a container.

        Preference order:
        1. Already-known active process for this resource (set when a syscall or
           other incident fired first) — avoids redundant sampling and ensures
           consistent attribution across correlated events.
        2. Live ps sampling via docker_stats (sampling artifacts already filtered
           in DockerStatsService.get_top_processes).

        Args:
            container_name: Container name

        Returns:
            Process name (e.g., "yes", "python"), or None
        """
        # Note: _active_process_by_resource removed — culprit is now identified
        # fresh each time via get_culprit_process() without cross-event caching.
        try:
            # Fetch top-5; artifacts already stripped in get_top_processes().
            processes = self.docker_stats.get_top_processes(container_name, limit=5)
            if processes:
                return processes[0]['name']
        except Exception as e:
            logger.error(f"❌ [GET CULPRIT] {e}")
        return None

    def create_container_anomaly_alert(self, container_name: str, anomaly_type: str, description: str) -> Dict[str, Any]:
        """
        Create alert payload for container anomaly with culprit process identification.

        Args:
            container_name: Name of container with anomaly
            anomaly_type: Type of anomaly (cpu_spike, memory_surge, etc.)
            description: Detailed description

        Returns:
            Alert payload dict
        """
        severity = "critical" if anomaly_type == "memory_surge" else "high"

        culprit = self.get_culprit_process(container_name)
        culprit_detail = f" (culprit: {culprit})" if culprit else ""

        type_display = {
            "cpu_spike": "CPU spike",
            "memory_surge": "Memory surge",
        }.get(anomaly_type, anomaly_type.replace("_", " ").title())

        return {
            "severity": severity,
            "type": anomaly_type,
            "resource_name": container_name,
            "title": f"{type_display} on {container_name}{culprit_detail}",
            "description": f"Container anomaly detected: {description}{culprit_detail}",
            "culprit_process": culprit,
        }

    # ==================== DISK MONITORING ====================

    def detect_disk_anomalies(
        self, container_names: List[str]
    ) -> Tuple[List[Tuple[str, str, str]], Dict[str, float]]:
        """
        Detect disk space anomalies across containers.

        Runs a single ``docker exec df`` pass per container and returns both
        the anomaly list (for incident creation) and a disk-usage map (so the
        poll loop can populate ``ContainerMetrics.disk_percent`` for the
        rolling metrics buffer without a second round of exec calls).

        Args:
            container_names: List of container names to check

        Returns:
            Tuple of:
              - anomalies: List of (container_name, anomaly_type, description)
              - disk_map:  Dict[container_name, usage_percent]  (all containers
                           for which df succeeded, regardless of threshold)
        """
        anomalies: List[Tuple[str, str, str]] = []
        disk_map: Dict[str, float] = {}

        for container in container_names:
            try:
                disk_metrics = self.advanced_monitor.get_container_disk_usage(container)
                if disk_metrics:
                    disk_map[container] = disk_metrics.usage_percent
                    if self.advanced_monitor.detect_disk_anomaly(disk_metrics, self.disk_threshold):
                        anomalies.append((
                            container,
                            "disk_full",
                            f"Disk usage {disk_metrics.usage_percent:.1f}% "
                            f"({disk_metrics.used_gb:.1f}GB/{disk_metrics.total_gb:.1f}GB) "
                            f"exceeds threshold {self.disk_threshold}%"
                        ))
                        logger.warning(f"💾 [DISK FULL] {container}: {disk_metrics.usage_percent:.1f}%")
            except Exception as e:
                logger.debug(f"[DISK CHECK] Skipped {container}: {e}")

        return anomalies, disk_map

    def create_disk_anomaly_alert(self, container_name: str, description: str) -> Dict[str, Any]:
        """Create alert for disk space anomaly."""
        return {
            "severity": "critical",
            "type": "disk_full",
            "resource_name": container_name,
            "title": f"Disk full on {container_name}",
            "description": f"Disk space anomaly detected: {description}",
        }

    # ==================== HEALTH CHECK MONITORING ====================

    def detect_health_check_anomalies(self, container_names: List[str]) -> List[Tuple[str, str, str]]:
        """
        Run health checks on critical containers.

        Args:
            container_names: List of container names to check

        Returns:
            List of (container_name, anomaly_type, description)
        """
        anomalies = []

        # Health check configuration per container
        health_checks = {
            "agentic_os_backend": [
                ("http", 8000, "/api/health"),
            ],
            "agentic_os_postgres": [
                ("tcp", 5432, ""),
            ],
            "agentic_os_redis": [
                ("tcp", 6379, ""),
            ],
        }

        for container, checks in health_checks.items():
            if container not in container_names:
                continue

            for check_type, port, path in checks:
                try:
                    if check_type == "http":
                        result = self.advanced_monitor.health_check_http(container, port, path)
                    elif check_type == "tcp":
                        result = self.advanced_monitor.health_check_tcp(container, port)
                    else:
                        continue

                    if result.status == "unhealthy":
                        anomalies.append((
                            container,
                            "health_check_failed",
                            f"Health check failed: {result.check_type} {result.endpoint} is {result.status}"
                        ))
                        logger.warning(f"🏥 [HEALTH CHECK] {container}: {result.check_type} {result.endpoint} = {result.status}")
                except Exception as e:
                    logger.debug(f"[HEALTH CHECK] Skipped {container}: {e}")

        return anomalies

    def create_health_check_alert(self, container_name: str, description: str) -> Dict[str, Any]:
        """Create alert for health check failure."""
        return {
            "severity": "critical",
            "type": "health_check_failed",
            "resource_name": container_name,
            "title": f"Health check failed on {container_name}",
            "description": f"Service health check failed: {description}",
        }

    # ==================== NETWORK MONITORING ====================

    def detect_network_anomalies(self, container_names: List[str]) -> List[Tuple[str, str, str]]:
        """
        Detect network connection anomalies.

        Args:
            container_names: List of container names to check

        Returns:
            List of (container_name, anomaly_type, description)
        """
        anomalies = []

        for container in container_names:
            try:
                connections = self.advanced_monitor.get_container_connections(container)
                if connections:
                    alert_msg = self.advanced_monitor.detect_connection_spike(connections, self.connection_threshold)
                    if alert_msg:
                        anomalies.append((
                            container,
                            "connection_spike",
                            alert_msg
                        ))
                        logger.warning(f"🔗 [CONNECTION SPIKE] {container}: {alert_msg}")
            except Exception as e:
                logger.debug(f"[NETWORK CHECK] Skipped {container}: {e}")

        return anomalies

    def create_network_anomaly_alert(self, container_name: str, description: str) -> Dict[str, Any]:
        """Create alert for network anomaly."""
        return {
            "severity": "high",
            "type": "connection_spike",
            "resource_name": container_name,
            "title": f"Connection spike on {container_name}",
            "description": f"Network anomaly detected: {description}",
        }

    # ==================== LOG MONITORING ====================

    def detect_log_anomalies(self, container_names: List[str]) -> List[Tuple[str, str, str]]:
        """
        Detect error logs in containers.

        Args:
            container_names: List of container names to check

        Returns:
            List of (container_name, anomaly_type, description)
        """
        anomalies = []

        for container in container_names:
            try:
                has_errors, error_lines = self.advanced_monitor.detect_log_errors(container)
                if has_errors and error_lines:
                    # Summary of first few errors
                    error_summary = " | ".join(error_lines[:2])
                    if len(error_lines) > 2:
                        error_summary += f" (+{len(error_lines)-2} more)"

                    anomalies.append((
                        container,
                        "log_error",
                        f"Found {len(error_lines)} error(s) in logs: {error_summary}"
                    ))
                    logger.warning(f"📋 [LOG ERROR] {container}: {len(error_lines)} errors detected")
            except Exception as e:
                logger.debug(f"[LOG CHECK] Skipped {container}: {e}")

        return anomalies

    def create_log_anomaly_alert(self, container_name: str, description: str) -> Dict[str, Any]:
        """Create alert for log errors (built-in docker log scanning)."""
        return {
            "severity": "high",
            "type": "log_error",
            "resource_name": container_name,
            "title": f"Error logs detected on {container_name}",
            "description": f"Application logs contain errors: {description}",
        }

    def create_log_monitor_event_alert(
        self, monitor_name: str, event_type: str, metadata: dict
    ) -> Dict[str, Any]:
        """Create alert for a custom log monitor event with full line capture."""
        severity = metadata.get("severity", "warning")
        matched_line = metadata.get("matched_line", "")
        match_count = metadata.get("match_count", 1)
        all_lines = metadata.get("all_matched_lines") or [matched_line]
        log_file = metadata.get("log_file", "")
        source = metadata.get("source", "file")

        event_display = event_type.replace("_", " ").title()
        source_label = f"container '{log_file}'" if source == "docker" else f"file '{log_file}'"
        count_str = f"{match_count} matching line{'s' if match_count != 1 else ''}"

        lines_block = "\n".join(all_lines[:10])

        return {
            "severity": severity,
            "type": event_type,
            "resource_name": monitor_name,
            "title": f"{event_display} on {monitor_name}",
            "description": (
                f"Log monitor detected {count_str} from {source_label}:\n\n{lines_block}"
            ),
        }

    def detect_adapter_log_anomalies(self, targets: List[str]) -> List[Tuple[str, str, str]]:
        """
        Detect error logs on SSH/K8s/SSM targets via the execution adapter.
        Uses journalctl (systemd) with syslog/messages fallback for non-systemd hosts.
        Returns list of (target, anomaly_type, description) tuples.
        """
        LOG_KEYWORDS = ("error", "exception", "failed", "traceback", "fatal")
        log_cmd = (
            "journalctl -n 100 --no-pager -p err..emerg 2>/dev/null "
            "|| grep -iE 'error|exception|failed|fatal' /var/log/syslog 2>/dev/null | tail -50 "
            "|| grep -iE 'error|exception|failed|fatal' /var/log/messages 2>/dev/null | tail -50"
        )
        anomalies = []
        for target in targets:
            try:
                result = self.adapter.exec(target, log_cmd, timeout=10)
                if not result.success or not result.stdout.strip():
                    continue
                error_lines = [
                    line for line in result.stdout.splitlines()
                    if any(kw in line.lower() for kw in LOG_KEYWORDS)
                ]
                if error_lines:
                    summary = " | ".join(error_lines[:2])
                    if len(error_lines) > 2:
                        summary += f" (+{len(error_lines) - 2} more)"
                    anomalies.append((
                        target, "log_error",
                        f"Found {len(error_lines)} error(s) in logs: {summary}"
                    ))
                    logger.warning(f"📋 [LOG ERROR] {target}: {len(error_lines)} errors via adapter")
            except Exception as e:
                logger.debug(f"[LOG CHECK] Skipped {target}: {e}")
        return anomalies

    # ==================== EXTERNAL CHECKS ====================

    def _discover_port_processes(self, container_name: str) -> Dict[int, Dict[str, Any]]:
        """
        Return port→process mapping for a container's listening TCP ports.
        Tries three methods in order: ss → netstat → /proc/net/tcp inode walk.
        Returns: { port_int: {"process": name, "pid": int} }
        """
        import re as _re

        def _parse_ss(output: str) -> Dict[int, Dict[str, Any]]:
            m_map: Dict[int, Dict[str, Any]] = {}
            for line in output.splitlines():
                if "LISTEN" not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                addr = parts[4]
                port_str = addr.rsplit(":", 1)[-1]
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                proc_name, pid = "", 0
                m = _re.search(r'\("([^"]+)",pid=(\d+)', line)
                if m:
                    proc_name, pid = m.group(1), int(m.group(2))
                if port:
                    m_map[port] = {"process": proc_name, "pid": pid}
            return m_map

        def _parse_netstat(output: str) -> Dict[int, Dict[str, Any]]:
            m_map: Dict[int, Dict[str, Any]] = {}
            for line in output.splitlines():
                if "LISTEN" not in line:
                    continue
                parts = line.split()
                if len(parts) < 7:
                    continue
                addr = parts[3]
                port_str = addr.rsplit(":", 1)[-1]
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                pid_prog = parts[-1]
                proc_name, pid = "", 0
                if "/" in pid_prog:
                    pid_str, proc_name = pid_prog.split("/", 1)
                    try:
                        pid = int(pid_str)
                    except ValueError:
                        pass
                if port:
                    m_map[port] = {"process": proc_name, "pid": pid}
            return m_map

        # ── Method 1: ss (iproute2) ───────────────────────────────────────────
        try:
            r = subprocess.run(
                ["docker", "exec", container_name, "ss", "-tlnp"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                result = _parse_ss(r.stdout)
                if result:
                    return result
        except Exception:
            pass

        # ── Method 2: netstat (net-tools) ────────────────────────────────────
        try:
            r = subprocess.run(
                ["docker", "exec", container_name, "netstat", "-tlnp"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                result = _parse_netstat(r.stdout)
                if result:
                    return result
        except Exception:
            pass

        # ── Method 3: /proc/net/tcp inode walk (universal fallback) ──────────
        # Works on any Linux container — no extra tools required.
        try:
            proc_sh = (
                "awk 'NR>1 && $4==\"0A\"{split($2,a,\":\"); print a[2],$10}' "
                "/proc/net/tcp /proc/net/tcp6 2>/dev/null "
                "| while read hexport inode; do "
                "  port=$((0x$hexport)); "
                "  for fd_path in /proc/[0-9]*/fd/*; do "
                "    pid=$(echo $fd_path | cut -d/ -f3); "
                "    link=$(readlink $fd_path 2>/dev/null); "
                "    if [ \"$link\" = \"socket:[$inode]\" ]; then "
                "      comm=$(cat /proc/$pid/comm 2>/dev/null); "
                "      echo \"$port $comm $pid\"; break; "
                "    fi; "
                "  done; "
                "done"
            )
            r = subprocess.run(
                ["docker", "exec", container_name, "sh", "-c", proc_sh],
                capture_output=True, text=True, timeout=10
            )
            port_map: Dict[int, Dict[str, Any]] = {}
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        port = int(parts[0])
                        port_map[port] = {"process": parts[1], "pid": int(parts[2])}
                    except (ValueError, IndexError):
                        pass
            return port_map
        except Exception as exc:
            logger.debug(f"[PORT-DISCOVERY] {container_name}: all methods failed — {exc}")
            return {}

    def _refresh_port_process_cache(self) -> None:
        """
        Update _port_process_cache for all containers referenced in external checks.
        Called every poll cycle — runs ss -tlnp inside each relevant container.
        """
        containers: set = set()
        for cfg in self.external_checks:
            if getattr(cfg, "container_name", ""):
                containers.add(cfg.container_name)
        for container in containers:
            port_map = self._discover_port_processes(container)
            if port_map:
                self._port_process_cache[container] = port_map
                logger.debug(f"[PORT-CACHE] {container}: ports {sorted(port_map.keys())}")

    def detect_external_anomalies(self) -> List[Tuple[str, str, str]]:
        """
        Run all configured external checks and return anomalies.
        Enriches _external_check_metadata with process info for alert payloads.
        Returns: List of (resource_name, anomaly_type, description)
        """
        import re as _re
        anomalies: List[Tuple[str, str, str]] = []

        event_type_map = {
            "ping":  "ping_failed",
            "http":  "external_http_failed",
            "https": "external_http_failed",
            "tcp":   "external_tcp_failed",
            "dns":   "dns_failed",
            "tls":   "tls_expiry",
        }

        for cfg in self.external_checks:
            container_name = getattr(cfg, "container_name", "") or ""
            service_name   = getattr(cfg, "service_name",   "") or ""

            # Auto-derive container_name from URL hostname when not explicitly set.
            # e.g. http://flower:5555 → hostname "flower" → try "flower" then
            # Docker Compose prefixed name (project_flower) via docker ps lookup.
            if not container_name and cfg.target:
                _m = _re.search(r'(?:https?://)?([^/:]+)', cfg.target)
                if _m:
                    _hostname = _m.group(1)
                    # Skip bare IPs and "localhost"
                    if not _re.match(r'^\d+\.\d+\.\d+\.\d+$', _hostname) and _hostname != "localhost":
                        # Try exact hostname first, then docker ps for a container whose
                        # name ends with the hostname (covers Docker Compose project prefix)
                        try:
                            _ps = subprocess.run(
                                ["docker", "ps", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=5,
                            )
                            _names = _ps.stdout.strip().splitlines()
                            # Prefer exact match, then suffix match
                            _exact = [n for n in _names if n == _hostname]
                            _suffix = [n for n in _names if n.endswith(f"_{_hostname}") or n.endswith(f"-{_hostname}")]
                            container_name = (_exact or _suffix or [_hostname])[0]
                            logger.debug(f"[EXTERNAL] Auto-derived container_name='{container_name}' from URL '{cfg.target}'")
                        except Exception:
                            container_name = _hostname  # best-effort fallback

            resource_name  = container_name or cfg.name or cfg.target

            # Extract port from target URL
            port = getattr(cfg, "port", 0) or 0
            if not port and cfg.target:
                m = _re.search(r':(\d+)(?:/|$)', cfg.target)
                if m:
                    port = int(m.group(1))
                elif cfg.check_type.lower() == "http":
                    port = 80
                elif cfg.check_type.lower() == "https":
                    port = 443

            try:
                result = self.advanced_monitor.run_external_check(cfg)

                if result.status in ("unhealthy", "degraded"):
                    anomaly_type = event_type_map.get(cfg.check_type.lower(), "external_http_failed")

                    rtt = f" ({result.response_time_ms:.0f}ms)" if result.response_time_ms else ""
                    if result.tls_days_remaining is not None:
                        detail = f"TLS cert expires in {result.tls_days_remaining}d"
                    elif result.status_code:
                        detail = f"HTTP {result.status_code}"
                    elif result.error:
                        detail = result.error
                    else:
                        detail = result.status

                    description = f"[{cfg.check_type.upper()}] {cfg.name or cfg.target}{rtt}: {detail}"

                    # ── Derive criticality from the *nature* of the failure ──────
                    # The monitor is best-placed to determine severity: a DNS
                    # resolution failure means the container is completely gone
                    # (→ critical); a timeout or HTTP error means it is degraded
                    # but still reachable (→ warning).
                    raw_criticality = self._derive_external_criticality(cfg.check_type, result)

                    # Enrich with process info from discovery cache
                    process_info: Dict[str, Any] = {}
                    failure_reason = "unknown"

                    if container_name and port and cfg.check_type.lower() in ("http", "https", "tcp"):
                        # Real-time PID refresh at moment of failure
                        fresh_map = self._discover_port_processes(container_name)
                        cached_map = self._port_process_cache.get(container_name, {})

                        if port in fresh_map and fresh_map[port].get("process"):
                            process_info  = fresh_map[port]
                            failure_reason = "hung"
                            logger.info(
                                f"[PORT-PROCESS] {container_name}:{port} → "
                                f"'{process_info['process']}' (pid={process_info['pid']}) is HUNG"
                            )
                        elif port in cached_map and cached_map[port].get("process"):
                            process_info  = cached_map[port]
                            failure_reason = "crashed"
                            logger.info(
                                f"[PORT-PROCESS] {container_name}:{port} → "
                                f"'{process_info['process']}' (pid={process_info['pid']}) CRASHED"
                            )
                        else:
                            failure_reason = "crashed"
                            logger.info(f"[PORT-PROCESS] {container_name}:{port} → no process found")

                    self._external_check_metadata[resource_name] = {
                        "container_name":  container_name,
                        "service_name":    service_name,
                        "port":            port,
                        "check_url":       cfg.target if cfg.check_type.lower() in ("http", "https") else "",
                        "process_info":    process_info,
                        "failure_reason":  failure_reason,
                        "raw_criticality": raw_criticality,  # per-failure severity
                    }

                    anomalies.append((resource_name, anomaly_type, description))
                    logger.warning(
                        f"🌐 [EXTERNAL] {anomaly_type} — {description} "
                        f"[{failure_reason}] criticality={raw_criticality}"
                    )

                else:
                    rtt = f"{result.response_time_ms:.0f}ms"
                    logger.info(
                        f"✅ [EXTERNAL] {cfg.check_type.upper()} {cfg.name or cfg.target}: healthy ({rtt})"
                    )
                    self._external_check_metadata.pop(resource_name, None)

            except Exception as exc:
                logger.warning(f"⚠️  [EXTERNAL CHECK] {resource_name}: unexpected error — {exc}")

        return anomalies

    @staticmethod
    def _derive_external_criticality(check_type: str, result: "ExternalCheckResult") -> str:
        """Determine raw_criticality from the *nature* of an external check failure.

        The monitor is the right place to make this decision because it knows
        what kind of error occurred:

        - DNS resolution failure  → the container/host is completely gone → critical
        - Connection refused      → service process is down (host is up)  → critical
        - Timeout                 → service is heavily loaded or hung      → warning
        - HTTP status error       → service responds but returns errors    → warning
        - TLS expiry              → cert issue, not an outage              → warning
        - Ping failed             → host unreachable                       → critical
        - Unknown                 → default to warning (conservative)
        """
        error = (result.error or "").lower()
        ctype = check_type.lower()

        # TLS expiry is informational — cert hasn't expired yet, just approaching
        if ctype == "tls" or result.tls_days_remaining is not None:
            return "warning"

        # DNS failure — container hostname cannot be resolved → completely gone
        if any(x in error for x in (
            "errno -5", "no address associated", "name or service not known",
            "nodename nor servname", "dns lookup failed", "getaddrinfo failed",
        )):
            return "critical"

        # Connection refused — process is down but host is reachable
        if any(x in error for x in ("connection refused", "errno 111", "connect call failed")):
            return "critical"

        # Ping failure always means host unreachable
        if ctype == "ping":
            return "critical"

        # Timeout — service degraded or overloaded
        if any(x in error for x in ("timed out", "timeout", "read timeout", "connect timeout")):
            return "warning"

        # HTTP status error (4xx/5xx) — service is up but responding with errors
        if result.status_code and result.status_code >= 400:
            return "warning"

        # Default: something failed but we don't know what → conservative
        return "warning"

    def create_external_anomaly_alert(self, resource_name: str, anomaly_type: str,
                                      description: str) -> Dict[str, Any]:
        """Create enriched alert payload for external check failures."""
        severity_map = {
            "ping_failed":          "critical",
            "external_http_failed": "critical",
            "external_tcp_failed":  "critical",
            "dns_failed":           "critical",
            "tls_expiry":           "warning",
        }
        title_map = {
            "ping_failed":          f"Host unreachable: {resource_name}",
            "external_http_failed": f"HTTP endpoint down: {resource_name}",
            "external_tcp_failed":  f"TCP port closed: {resource_name}",
            "dns_failed":           f"DNS resolution failed: {resource_name}",
            "tls_expiry":           f"TLS certificate expiring: {resource_name}",
        }
        meta  = self._external_check_metadata.get(resource_name, {})
        proc  = meta.get("process_info", {})
        return {
            "severity":       severity_map.get(anomaly_type, "high"),
            "type":           anomaly_type,
            "resource_name":  resource_name,
            "title":          title_map.get(anomaly_type, f"External check failed: {resource_name}"),
            "description":    description,
            "container":      meta.get("container_name", resource_name),
            "port":           meta.get("port", 0),
            "check_url":      meta.get("check_url", ""),
            "service_name":   meta.get("service_name", ""),
            "failure_reason": meta.get("failure_reason", "unknown"),
            "process_name":   proc.get("process", ""),
            "process_pid":    proc.get("pid", 0),
        }

    def execute_remediation(self, process: str) -> bool:
        """
        Execute remediation by terminating offending process.

        Args:
            process: Process name to terminate

        Returns:
            Success status
        """
        logger.info(f"🔧 [REMEDIATION] Terminating process '{process}'")
        try:
            if self.adapter.adapter_name == "kubernetes":
                import requests as _req
                kill_url = os.getenv(
                    "SENTINEL_KILL_URL",
                    "http://sentinel-metrics.agentic-platform.svc.cluster.local:9090/kill",
                )
                resp = _req.post(f"{kill_url}?process={process}", timeout=10)
                if resp.status_code == 200:
                    logger.info(f"✓ [REMEDIATION SUCCESS] Process '{process}' terminated via sentinel HTTP")
                    return True
                else:
                    logger.error(f"❌ [REMEDIATION FAILED] sentinel /kill returned {resp.status_code}: {resp.text}")
                    return False

            cmd = ["docker", "exec", self.sentinel_container, "pkill", "-9", process]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info(f"✓ [REMEDIATION SUCCESS] Process '{process}' terminated")
                return True
            else:
                logger.error(f"❌ [REMEDIATION FAILED] {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"❌ [REMEDIATION ERROR] {e}")
            return False

    async def _reconcile_active_conditions_with_db(self) -> None:
        """
        Cross-check active_conditions against the platform DB every few polls.

        For each resource we think has an open incident, query the workflow API.
        If the workflow is in a terminal state (resolved/failed/closed/rejected)
        or has been deleted (404), remove the resource from active_conditions so
        the watcher can fire a new incident next time the anomaly is detected.

        This handles two cases that the live-telemetry all-clear loop misses:
          1. Operator manually resolves an incident in the UI
          2. Admin deletes all incidents via the admin panel
        """
        if not self.active_conditions:
            return

        # Includes awaiting_manual: operator has taken ownership, watcher should stand down.
        terminal_states = {
            "resolved", "failed", "closed", "rejected",
            "cancelled", "awaiting_manual",
        }

        for condition_key in list(self.active_conditions.keys()):
            # condition_key format: "container_name:anomaly_type"
            container_name = condition_key.split(":")[0]
            workflow_id = self._active_workflow_ids.get(container_name)
            if not workflow_id:
                # No workflow ID recorded. This happens when:
                #   - Event scored below threshold (no incident created, wf_id=None)
                #   - Watcher restarted and state file was stale
                # Query platform for ANY open (non-terminal) incident for this resource.
                # If none found → clear active_conditions so the watcher can re-fire.
                # If found → store the workflow_id so future reconcile cycles can track it.
                logger.info(
                    f"🔍 [RECONCILE] No workflow_id for '{condition_key}' "
                    f"— querying platform for open incidents"
                )
                try:
                    async with httpx.AsyncClient(timeout=5.0, headers=self._api_headers) as _client:
                        _resp = await _client.get(
                            f"{self.api_base_url}/api/workflows",
                            params={"workflow_type": "incident", "limit": 20},
                        )
                    if _resp.status_code == 200:
                        _data = _resp.json()
                        _workflows = _data.get("workflows", _data if isinstance(_data, list) else [])
                        # Find any non-terminal incident for this resource
                        _open = next(
                            (w for w in _workflows
                             if w.get("context", {}).get("alert_payload", {}).get("resource_name") == container_name
                             and w.get("lifecycle_state") not in terminal_states),
                            None,
                        )
                        if _open:
                            # Found an open incident — store its ID so next cycle can track it
                            _wfid = str(_open.get("workflow_id", ""))
                            if _wfid:
                                logger.info(
                                    f"🔗 [RECONCILE] Linked '{condition_key}' → "
                                    f"existing incident {_wfid[:8]} ({_open.get('lifecycle_state')})"
                                )
                                self._active_workflow_ids[container_name] = _wfid
                        else:
                            # No open incident found — safe to clear the stale active_condition
                            logger.info(
                                f"🧹 [RECONCILE] No open incident for '{condition_key}' "
                                f"— clearing stale active_conditions entry"
                            )
                            self.active_conditions.pop(condition_key, None)
                            self._active_workflow_ids.pop(container_name, None)
                except Exception as _e:
                    logger.debug(f"[RECONCILE] Fallback lookup failed for '{container_name}': {_e}")
                continue

            try:
                async with httpx.AsyncClient(timeout=5.0, headers=self._api_headers) as client:
                    resp = await client.get(
                        f"{self.api_base_url}/api/workflows/{workflow_id}"
                    )

                if resp.status_code == 404:
                    # Workflow was deleted
                    logger.info(
                        f"🗑️  [RECONCILE] Workflow {workflow_id} for '{container_name}' "
                        f"no longer exists (deleted) — clearing active_conditions"
                    )
                    self.active_conditions.pop(condition_key, None)
                    self._active_workflow_ids.pop(container_name, None)

                elif resp.status_code == 200:
                    lifecycle = resp.json().get("lifecycle_state", "")
                    if lifecycle in terminal_states:
                        logger.info(
                            f"✅ [RECONCILE] Workflow {workflow_id} for '{container_name}' "
                            f"is '{lifecycle}' — clearing active_conditions"
                        )
                        self.active_conditions.pop(condition_key, None)
                        self._active_workflow_ids.pop(container_name, None)

            except Exception as e:
                logger.debug(f"[RECONCILE] Could not check DB state for '{container_name}': {e}")

    async def _run_adapter_metrics_poll(self) -> tuple:
        """
        Non-Docker metrics collection pass — used by SSH, K8s, vCenter, and SSM adapters.

        Calls adapter.get_metrics(target) for each target returned by
        adapter.list_targets() and applies the configured CPU / memory / disk
        thresholds using the same sustained-anomaly gate as Docker mode.

        Returns (anomalies, metrics_by_target) where:
          anomalies       — list of (resource_name, event_type, criticality) tuples
          metrics_by_target — dict[target_name, TargetMetrics] for dashboard history
        """
        loop = asyncio.get_event_loop()
        anomalies = []
        metrics_by_target: dict = {}

        try:
            targets = await loop.run_in_executor(None, self.adapter.list_targets)
        except Exception as exc:
            logger.warning(f"[ADAPTER POLL] list_targets failed: {exc}")
            return [], {}

        logger.debug(f"[ADAPTER POLL] Checking {len(targets)} target(s) via {self.adapter.adapter_name}")

        for target in targets:
            try:
                metrics = await loop.run_in_executor(None, lambda t=target: self.adapter.get_metrics(t))
                metrics_by_target[target] = metrics

                # ── CPU ───────────────────────────────────────────────────────
                if metrics.cpu_percent > 0:
                    cpu_key = f"{target}:high_cpu"
                    if metrics.cpu_percent >= self.cpu_threshold:
                        self.consecutive_anomaly_counts[cpu_key] = (
                            self.consecutive_anomaly_counts.get(cpu_key, 0) + 1
                        )
                        polls = self.consecutive_anomaly_counts[cpu_key]
                        logger.info(
                            f"📈 [ADAPTER] {target} CPU {metrics.cpu_percent:.1f}% "
                            f"(>{self.cpu_threshold}%) — {polls}/{self.min_consecutive_polls} polls"
                        )
                        if polls >= self.min_consecutive_polls:
                            crit = "critical" if metrics.cpu_percent >= self.cpu_threshold * 1.1 else "warning"
                            anomalies.append((target, "high_cpu", crit))
                    else:
                        # Hysteresis: only clear when well below threshold
                        if metrics.cpu_percent < self.cpu_threshold * 0.80:
                            self.consecutive_anomaly_counts.pop(cpu_key, None)

                # ── Memory ────────────────────────────────────────────────────
                if metrics.memory_percent > 0:
                    mem_key = f"{target}:high_memory"
                    if metrics.memory_percent >= self.memory_threshold:
                        self.consecutive_anomaly_counts[mem_key] = (
                            self.consecutive_anomaly_counts.get(mem_key, 0) + 1
                        )
                        polls = self.consecutive_anomaly_counts[mem_key]
                        logger.info(
                            f"📈 [ADAPTER] {target} Memory {metrics.memory_percent:.1f}% "
                            f"(>{self.memory_threshold}%) — {polls}/{self.min_consecutive_polls} polls"
                        )
                        if polls >= self.min_consecutive_polls:
                            anomalies.append((target, "high_memory", "critical"))
                    else:
                        if metrics.memory_percent < self.memory_threshold * 0.80:
                            self.consecutive_anomaly_counts.pop(mem_key, None)

                # ── Disk ──────────────────────────────────────────────────────
                if metrics.disk_percent > 0:
                    disk_key = f"{target}:disk_full"
                    if metrics.disk_percent >= self.disk_threshold:
                        self.consecutive_anomaly_counts[disk_key] = (
                            self.consecutive_anomaly_counts.get(disk_key, 0) + 1
                        )
                        polls = self.consecutive_anomaly_counts[disk_key]
                        if polls >= self.min_consecutive_polls:
                            anomalies.append((target, "disk_full", "critical"))
                    else:
                        self.consecutive_anomaly_counts.pop(disk_key, None)

                if metrics.cpu_percent > 0 or metrics.memory_percent > 0:
                    logger.debug(
                        f"✓ [ADAPTER] {target}: CPU={metrics.cpu_percent:.1f}% "
                        f"MEM={metrics.memory_percent:.1f}% DISK={metrics.disk_percent:.1f}%"
                    )

            except Exception as exc:
                logger.warning(f"[ADAPTER POLL] get_metrics({target}) failed: {exc}")

        return anomalies, metrics_by_target

    async def run(self):
        """
        Main event loop: poll for syscall and container anomalies, create incidents.
        Monitors: Syscalls (Sentinel), Docker stats, disk, health, network, logs.
        """
        # Restore active_conditions from previous run so we don't re-fire on restart
        self.restore_active_conditions()

        logger.info(f"🔄 [LOOP START] Polling every {self.poll_interval}s")
        logger.info(f"📊 [MONITORING] Syscalls (>{self.anomaly_threshold}), CPU (>{self.cpu_threshold}%), Memory (>{self.memory_threshold}%)")
        logger.info(f"💾 [MONITORING] Disk (>{self.disk_threshold}%), Connections (>{self.connection_threshold})\n")

        try:
            while True:
                try:
                    self.poll_count += 1

                    # Reload config: DB settings API (primary) + file fallback
                    await self.reload_config_from_api_if_stale()
                    self.reload_config_if_changed()

                    # ── Adapter-aware monitoring ──────────────────────────────────
                    # Docker mode: full container introspection via Docker socket.
                    # SSH / K8s / vCenter / SSM mode: metrics via adapter.get_metrics().
                    is_syscall_anomaly, process, count, anomaly_container = self.detect_anomaly()

                    if self.adapter.adapter_name == "docker":
                        # ── Docker path (existing behaviour) ─────────────────────────
                        container_stats = self.get_docker_container_stats()
                        container_names = list(container_stats.keys()) if container_stats else []

                        if container_stats is None:
                            logger.warning("⚠️  [DOCKER STATS] Failed to retrieve stats")
                        else:
                            logger.debug(f"✓ [DOCKER STATS] {len(container_stats)} containers")

                        # Discovery agent — collect via Docker, push to backend API
                        if (
                            self.discovery and self.discovery_enabled
                            and self.poll_count % self.discovery_interval_polls == 0
                        ):
                            try:
                                summary = self._run_discovery_via_api(container_stats)
                                logger.info(
                                    f"🔍 [DISCOVERY] Poll #{self.poll_count}: "
                                    f"{summary['updated']} updated, {summary['new_cis']} new"
                                )
                            except Exception as disc_err:
                                logger.warning(f"⚠️  [DISCOVERY] {disc_err}")

                        container_anomalies = (
                            self.detect_container_anomalies(container_stats)
                            if container_stats else []
                        )
                        disk_map: Dict[str, float] = {}   # populated below; passed to snapshot
                        if container_names:
                            disk_anomalies, disk_map = self.detect_disk_anomalies(container_names)
                            # Enrich container_stats with filesystem usage (best-effort).
                            # The authoritative value is forwarded directly via disk_map so
                            # the widget never shows 0 even if name keys don't align.
                            if container_stats:
                                for _name, _pct in disk_map.items():
                                    if _name in container_stats:
                                        container_stats[_name].disk_percent = _pct
                        else:
                            disk_anomalies = []
                        health_anomalies  = self.detect_health_check_anomalies(container_names) if container_names else []
                        network_anomalies = self.detect_network_anomalies(container_names) if container_names else []
                        log_anomalies     = self.detect_log_anomalies(container_names) if container_names else []

                        if container_stats:
                            self.write_container_stats(container_stats)

                    else:
                        # ── Non-Docker adapter path (SSH / K8s / vCenter / SSM) ───────
                        container_stats   = None
                        container_names   = []
                        container_anomalies = []
                        disk_map          = {}   # no df data on non-docker adapters
                        disk_anomalies    = []
                        health_anomalies  = []
                        network_anomalies = []

                        adapter_anomalies, container_stats = await self._run_adapter_metrics_poll()
                        container_anomalies = adapter_anomalies   # reuse existing pipeline
                        # container_stats is now Dict[pod_name, TargetMetrics]; the same
                        # fields (cpu_percent, memory_percent, disk_percent) are read by
                        # _record_metrics_snapshot so it populates the dashboard history.

                        # K8s CMDB discovery — same interval gate as the Docker path
                        if (
                            self.discovery and self.discovery_enabled
                            and self.adapter.adapter_name == "kubernetes"
                            and self.poll_count % self.discovery_interval_polls == 0
                        ):
                            try:
                                summary = self._run_k8s_discovery_via_api(container_stats)
                                logger.info(
                                    f"🔍 [DISCOVERY] Poll #{self.poll_count}: "
                                    f"{summary.get('updated', 0)} updated, "
                                    f"{summary.get('new_cis', 0)} new"
                                )
                            except Exception as disc_err:
                                logger.warning(f"⚠️  [DISCOVERY] K8s: {disc_err}")

                        loop = asyncio.get_event_loop()
                        try:
                            adapter_targets = await loop.run_in_executor(None, self.adapter.list_targets)
                        except Exception:
                            adapter_targets = []
                        log_anomalies = await loop.run_in_executor(
                            None, lambda: self.detect_adapter_log_anomalies(adapter_targets)
                        ) if adapter_targets else []

                    # External checks work in every mode
                    self._refresh_port_process_cache()
                    external_anomalies = self.detect_external_anomalies() if self.external_checks else []

                    # ── Log File Monitoring (works in all adapter modes) ──────────────
                    # Tails configured log files for regex patterns, emits custom events.
                    log_file_anomalies = []
                    _held_log_conditions: set = set()  # populated below when log monitor is enabled
                    if self.log_monitor.is_enabled():
                        loop = asyncio.get_event_loop()
                        # Pass the adapter for vcenter-source monitors
                        _vcenter_adap = (
                            self.adapter
                            if getattr(self.adapter, "adapter_name", None) == "vcenter"
                            else None
                        )
                        try:
                            log_matches = await loop.run_in_executor(
                                None, lambda: self.log_monitor.poll(_vcenter_adap)
                            )
                            for match in log_matches:
                                count_detail = (
                                    f" ({match.match_count} lines)" if match.match_count > 1 else ""
                                )
                                logger.info(
                                    f"📋 [LOG-MATCH] {match.monitor_name}: "
                                    f"{match.matched_line[:100]}{count_detail}"
                                )
                                cfg = self.log_monitor.configs[match.monitor_name]
                                # Use the actual container/VM/file as the resource so the
                                # incident shows "agentic_os_backend" or "prod-vm-01", not
                                # the monitor display name.
                                if cfg.source == "docker" and cfg.container:
                                    _lm_resource = cfg.container
                                elif cfg.source == "vcenter" and cfg.vm_name:
                                    _lm_resource = cfg.vm_name
                                else:
                                    _lm_resource = match.monitor_name
                                log_file_anomalies.append((
                                    _lm_resource,
                                    match.event_type,
                                    {
                                        "matched_line": match.matched_line,
                                        "match_count": match.match_count,
                                        "all_matched_lines": match.all_matched_lines,
                                        "source": cfg.source,
                                        "log_file": (
                                            cfg.container if cfg.source == "docker"
                                            else cfg.file
                                        ),
                                        "pattern": cfg.pattern,
                                        "severity": cfg.severity,
                                        "monitor_name": match.monitor_name,
                                    }
                                ))
                        except Exception as log_err:
                            logger.error(f"[LOG-MONITOR] Error polling: {log_err}")

                        # ── Quiet-poll tracking for log monitors ──────────────────
                        # condition_key format: "resource:event_type" where resource is
                        # the container name (docker) or monitor name (file).
                        # Build a resource→config map so the lookup works with either.
                        _cfg_by_resource: dict = {}
                        for _mname, _mcfg in self.log_monitor.configs.items():
                            if _mcfg.source == "docker" and _mcfg.container:
                                _rname = _mcfg.container
                            elif _mcfg.source == "vcenter" and _mcfg.vm_name:
                                _rname = _mcfg.vm_name
                            else:
                                _rname = _mname
                            _cfg_by_resource[_rname] = _mcfg

                        _matched_log_keys = set()
                        for _m in log_matches:
                            _m_cfg = self.log_monitor.configs.get(_m.monitor_name)
                            if _m_cfg and _m_cfg.source == "docker" and _m_cfg.container:
                                _m_res = _m_cfg.container
                            elif _m_cfg and _m_cfg.source == "vcenter" and _m_cfg.vm_name:
                                _m_res = _m_cfg.vm_name
                            else:
                                _m_res = _m.monitor_name
                            _matched_log_keys.add(f"{_m_res}:{_m.event_type}")

                        for _ck in list(self.active_conditions):
                            if ":" not in _ck:
                                continue
                            _res, _at = _ck.split(":", 1)
                            _cfg = _cfg_by_resource.get(_res)
                            if _cfg is None:
                                continue  # not a log monitor condition
                            if _ck in _matched_log_keys:
                                # Fresh match this poll — reset quiet counter
                                self._log_quiet_polls.pop(_ck, None)
                            else:
                                # Quiet poll — increment counter
                                self._log_quiet_polls[_ck] = (
                                    self._log_quiet_polls.get(_ck, 0) + 1
                                )
                                _quiet = self._log_quiet_polls[_ck]
                                _thresh = _cfg.clear_after_polls
                                if _quiet < _thresh:
                                    logger.info(
                                        f"[LOG-MONITOR] {_res}: quiet poll "
                                        f"{_quiet}/{_thresh} — holding all-clear"
                                    )
                                    _held_log_conditions.add(_ck)
                                else:
                                    logger.info(
                                        f"[LOG-MONITOR] {_res}: {_quiet} quiet polls "
                                        f">= {_thresh} — releasing all-clear"
                                    )
                                    self._log_quiet_polls.pop(_ck, None)

                    all_anomalies = (
                        container_anomalies + disk_anomalies + health_anomalies
                        + network_anomalies + log_anomalies + external_anomalies
                        + log_file_anomalies
                    )

                    self._record_metrics_snapshot(container_stats, len(all_anomalies), disk_map=disk_map)

                    # ── Build current anomaly key set ─────────────────────────────────
                    # Used for (a) all-clear logic and (b) stale counter resets.
                    currently_anomalous: set = set()
                    current_anomaly_keys: set = set()
                    if is_syscall_anomaly and anomaly_container:
                        currently_anomalous.add(anomaly_container)
                        current_anomaly_keys.add(f"{anomaly_container}:high_syscall_intensity")
                    for _cname, _atype, _ in all_anomalies:
                        currently_anomalous.add(_cname)
                        current_anomaly_keys.add(f"{_cname}:{_atype}")

                    # Inject held log conditions to suppress premature all-clear.
                    # These are conditions in their quiet-poll window (not yet matched,
                    # but not yet past clear_after_polls threshold either).  We add
                    # them directly here — bypassing log_file_anomalies — so they
                    # never reach the incident-fire path.
                    for _mk in _held_log_conditions:
                        _hres, _ = _mk.split(":", 1)
                        currently_anomalous.add(_hres)
                        current_anomaly_keys.add(_mk)

                    # ── Per-resource all-clear ────────────────────────────────────────
                    # Any resource that WAS tracked (active_conditions) but is NOT in
                    # currently_anomalous has returned to normal — send all-clear.
                    conditions_cleared: list = []
                    for condition_key, original_event_type in list(self.active_conditions.items()):
                        # condition_key is either "resource" (syscall, legacy) or
                        # "resource:anomaly_type" (container anomalies, per-condition tracking).
                        if ":" in condition_key:
                            resource_name = condition_key.split(":", 1)[0]
                            still_active = condition_key in current_anomaly_keys
                        else:
                            resource_name = condition_key
                            still_active = resource_name in currently_anomalous

                        if not still_active:
                            logger.info(
                                f"✓ [CLEARED] Condition '{original_event_type}' resolved for "
                                f"'{resource_name}' — sending all-clear"
                            )
                            await self.submit_condition_cleared(resource_name, original_event_type)
                            del self.active_conditions[condition_key]
                            self._active_workflow_ids.pop(resource_name, None)
                            # Clear both cooldown styles so the condition can re-fire
                            # once it genuinely returns (after min_consecutive_polls again).
                            self.per_resource_cooldown.pop(resource_name, None)
                            self.per_resource_cooldown.pop(condition_key, None)
                            conditions_cleared.append(resource_name)

                    # Clear the known-process registry for any resource that now has
                    # no active conditions at all (all its conditions just cleared).
                    # This allows a future incident on the same resource to re-establish
                    # the culprit from a fresh sample rather than stale data.
                    if conditions_cleared:
                        for cleared_resource in set(conditions_cleared):
                            still_has_condition = any(
                                k == cleared_resource or k.startswith(f"{cleared_resource}:")
                                for k in self.active_conditions
                            )
                            if not still_has_condition:
                                logger.debug(f"[ALL-CLEAR] No remaining conditions for '{cleared_resource}'")

                    # Persist the cleared state to JSON immediately.
                    # write_status() is only called inside the if/elif/else branches
                    # below, which means when other resources are "ongoing" (skipped
                    # by the active_conditions guard) the file is never updated and a
                    # watcher restart would restore the stale cleared entry — creating
                    # a deadlock where the resource is stuck in active_conditions yet
                    # never triggers an all-clear (it IS anomalous) or a new event
                    # (it IS in active_conditions).
                    if conditions_cleared:
                        logger.info(
                            f"💾 [PERSIST] Flushing cleared conditions to status file: "
                            f"{conditions_cleared}"
                        )
                        self.write_status("condition_cleared")

                    # ── Heartbeat + DB reconciliation ────────────────────────────────
                    # Normal cadence: every 3 polls (≈90s at default 30s interval).
                    # If backend was unreachable (e.g. restarting), retry every poll
                    # until we get a successful response — then drop back to normal cadence.
                    _failures = getattr(self, "_register_consecutive_failures", 0)
                    _should_register = (self.poll_count % 3 == 0) or (_failures > 0)
                    if _should_register:
                        # Re-register acts as a heartbeat — updates last_seen in the DB
                        await self.register_with_api()
                        await self._reconcile_active_conditions_with_db()

                    # ── Reset stale consecutive counters (with hysteresis) ────────────
                    self._reset_stale_counters(current_anomaly_keys, container_stats)
                    # ──────────────────────────────────────────────────────────────────

                    # ── Handle syscall anomalies (highest priority) ───────────────────
                    if is_syscall_anomaly:
                        consecutive_count = self._increment_anomaly_count(anomaly_container, "high_syscall_intensity")
                        self.last_anomaly_process = process

                        if consecutive_count < self.min_consecutive_polls:
                            # Not sustained long enough — observe quietly
                            logger.info(
                                f"👀 [WATCHING] {anomaly_container}: syscall spike "
                                f"{count} ({consecutive_count}/{self.min_consecutive_polls} consecutive polls)"
                            )
                            self.write_status("watching", "high_syscall_intensity", process, count)

                        elif anomaly_container in self._active_workflow_ids:
                            # An open incident already exists for this container
                            # (from a previous anomaly). Don't open a duplicate.
                            logger.info(
                                f"📊 [ONGOING] Incident already open for '{anomaly_container}' "
                                f"— suppressing duplicate syscall event"
                            )
                            self.write_status("incident_ongoing", "high_syscall_intensity", process, count)

                        elif self._resource_in_cooldown(anomaly_container):
                            self.write_status("cooldown", "high_syscall_intensity", process, count)

                        else:
                            # Sustained + no active incident + not in cooldown → fire
                            logger.warning(
                                f"\n🚨 [SUSTAINED SYSCALL] '{process}' in '{anomaly_container}': "
                                f"{count} syscalls over {consecutive_count} polls"
                            )
                            self.active_incident_id = self.generate_incident_id()
                            self.anomaly_start_time = datetime.utcnow()
                            alert = self.create_incident_alert(process, count, anomaly_container)

                            success, event_id, wf_id = await self.submit_monitoring_event_to_platform(
                                event_type="high_syscall_intensity",
                                resource_name=anomaly_container,
                                raw_criticality=self.event_type_severity.get("high_syscall_intensity", "critical"),
                                alert_payload=alert,
                                signal_value=float(count),
                                signal_threshold=float(self.anomaly_threshold),
                                anomaly_process=process,
                            )

                            if success:
                                self._set_resource_cooldown(anomaly_container)
                                self._reset_anomaly_count(anomaly_container, "high_syscall_intensity")
                                if wf_id:
                                    # Incident created or deduped — track it
                                    self.active_conditions[anomaly_container] = "high_syscall_intensity"
                                    self._active_workflow_ids[anomaly_container] = wf_id
                                    self.write_status("event_submitted", "high_syscall_intensity", process, count)
                                else:
                                    # Event submitted but scored below threshold — no incident
                                    # opened. Do NOT set active_conditions: the platform has no
                                    # open incident to guard, so we should not block future re-fires
                                    # after the cooldown.  Cooldown is already set above to prevent
                                    # immediate spam.
                                    logger.info(
                                        f"⚠️  [DISMISSED] {anomaly_container}/high_syscall_intensity: "
                                        f"event submitted but scored below threshold — "
                                        f"cooldown active, will retry after {self.cooldown_seconds}s"
                                    )
                                    self.write_status("event_submitted", "high_syscall_intensity", process, count)
                            else:
                                self.write_status("event_submission_failed", "high_syscall_intensity", process, count)

                    # ── Handle container / infra anomalies ───────────────────────────
                    # Processed independently of syscall anomalies — each condition type
                    # has its own cooldown and active-condition slot so a syscall incident
                    # does not suppress a concurrent cpu_spike or disk_full event.
                    if all_anomalies:
                        # Map anomaly types to platform event types and criticality levels
                        event_type_map = {
                            "cpu_spike":              "high_cpu",
                            "memory_surge":           "high_memory",
                            "disk_full":              "disk_full",
                            # health_check_failed intentionally NOT collapsed into
                            # "service_unresponsive" — it has its own taxonomy entry
                            # (application.availability.health_check_failing) distinct
                            # from external-reachability failures, so it can carry its
                            # own default severity instead of sharing one with them.
                            "connection_spike":       "high_latency",
                            "log_error":              "high_error_rate",
                            # External checks
                            "ping_failed":            "service_unresponsive",
                            "external_http_failed":   "service_unresponsive",
                            "external_tcp_failed":    "service_unresponsive",
                            "dns_failed":             "service_unresponsive",
                            "tls_expiry":             "certificate_expiry",
                        }
                        # Static criticality fallback — valid values: info/warning/critical.
                        # External check anomalies override this with per-failure
                        # criticality stored in _external_check_metadata (derived from
                        # the actual error: DNS failure → critical, timeout → warning, etc.)
                        criticality_map = {
                            "memory_surge":           "critical",
                            "disk_full":              "critical",
                            "cpu_spike":              "warning",
                            "health_check_failed":    "warning",
                            "connection_spike":       "warning",
                            "log_error":              "info",
                            # External checks — fallback (overridden by metadata below)
                            "ping_failed":            "critical",
                            "external_http_failed":   "critical",
                            "external_tcp_failed":    "critical",
                            "dns_failed":             "critical",
                            "tls_expiry":             "warning",
                        }

                        # Log-based events are one-time occurrences — the docker/file
                        # --since cursor advances past them after each poll, so they
                        # can never satisfy a multi-poll consecutive gate organically.
                        # Pre-set their counters to threshold so they fire on first match.
                        for _r, _a, _ in log_file_anomalies:
                            self.consecutive_anomaly_counts[f"{_r}:{_a}"] = self.min_consecutive_polls

                        for container_name, anomaly_type, description in all_anomalies:
                            consecutive_count = self._increment_anomaly_count(container_name, anomaly_type)
                            condition_key = f"{container_name}:{anomaly_type}"

                            if consecutive_count < self.min_consecutive_polls:
                                logger.info(
                                    f"👀 [WATCHING] {container_name} {anomaly_type}: "
                                    f"{consecutive_count}/{self.min_consecutive_polls} consecutive polls"
                                )
                                continue

                            # Per-condition active check — a cpu_spike incident does not
                            # block a separate disk_full or memory_surge from the same host.
                            if condition_key in self.active_conditions:
                                logger.debug(f"📊 [ONGOING] {container_name}/{anomaly_type}: condition already active, skipping")
                                continue

                            # Per-container incident check — if this container already has
                            # an open incident (from any anomaly type), don't fire a new one.
                            # The backend will link additional events to the existing incident.
                            if container_name in self._active_workflow_ids:
                                logger.info(
                                    f"📊 [ONGOING] Incident already open for '{container_name}' "
                                    f"— suppressing duplicate {anomaly_type} event"
                                )
                                continue

                            # Per-condition cooldown — syscall cooldown does not suppress cpu_spike.
                            if self._condition_in_cooldown(container_name, anomaly_type):
                                continue

                            # Sustained + no active incident + not in cooldown → fire
                            incident_id = self.generate_incident_id()

                            if anomaly_type in ("cpu_spike", "memory_surge"):
                                culprit = self.get_culprit_process(container_name)
                                culprit_detail = f" → {culprit}" if culprit else ""
                                # Correlation logic removed — backend dedup handles
                                # multiple anomaly types on the same container.
                                logger.warning(f"\n🚨 [SUSTAINED {anomaly_type.upper()}] {container_name}{culprit_detail} ({consecutive_count} polls)")
                                alert = self.create_container_anomaly_alert(container_name, anomaly_type, description)
                            elif anomaly_type == "disk_full":
                                culprit = None
                                logger.warning(f"\n🚨 [SUSTAINED DISK FULL] {container_name} ({consecutive_count} polls)")
                                alert = self.create_disk_anomaly_alert(container_name, description)
                            elif anomaly_type == "health_check_failed":
                                culprit = None
                                logger.warning(f"\n🚨 [SUSTAINED HEALTH CHECK FAIL] {container_name} ({consecutive_count} polls)")
                                alert = self.create_health_check_alert(container_name, description)
                            elif anomaly_type == "connection_spike":
                                culprit = None
                                logger.warning(f"\n🚨 [SUSTAINED CONNECTION SPIKE] {container_name} ({consecutive_count} polls)")
                                alert = self.create_network_anomaly_alert(container_name, description)
                            elif anomaly_type == "log_error":
                                culprit = None
                                logger.warning(f"\n🚨 [SUSTAINED LOG ERRORS] {container_name} ({consecutive_count} polls)")
                                alert = self.create_log_anomaly_alert(container_name, description)
                            else:
                                culprit = None
                                _external_types = {"ping_failed", "external_http_failed",
                                                   "external_tcp_failed", "dns_failed", "tls_expiry"}
                                if anomaly_type in _external_types:
                                    alert = self.create_external_anomaly_alert(container_name, anomaly_type, description)
                                elif isinstance(description, dict) and "matched_line" in description:
                                    # Custom log monitor event — use dedicated builder
                                    alert = self.create_log_monitor_event_alert(container_name, anomaly_type, description)
                                else:
                                    alert = self.create_container_anomaly_alert(container_name, anomaly_type, description)

                            platform_event_type = event_type_map.get(anomaly_type, anomaly_type)

                            # Priority order: (1) per-failure criticality for external
                            # checks, stored in metadata at detection time (DNS failure →
                            # critical, timeout → warning, etc.) — most specific, always
                            # wins when present; (2) the operator-configurable default from
                            # the Event Type Taxonomy, keyed by the submitted platform event
                            # type; (3) the hardcoded map below, unchanged, as a last resort
                            # for anything the taxonomy hasn't been configured for.
                            _ext_types = {"ping_failed", "external_http_failed",
                                          "external_tcp_failed", "dns_failed", "tls_expiry"}
                            if anomaly_type in _ext_types:
                                _meta = self._external_check_metadata.get(container_name, {})
                                raw_crit = (
                                    _meta.get("raw_criticality")
                                    or self.event_type_severity.get(platform_event_type)
                                    or criticality_map.get(anomaly_type, "critical")
                                )
                            elif isinstance(description, dict) and "severity" in description:
                                # Custom log monitor — use the operator-configured severity directly
                                raw_crit = description["severity"]
                            else:
                                raw_crit = (
                                    self.event_type_severity.get(platform_event_type)
                                    or criticality_map.get(anomaly_type, "warning")
                                )

                            success, event_id, wf_id = await self.submit_monitoring_event_to_platform(
                                event_type=platform_event_type,
                                resource_name=container_name,
                                raw_criticality=raw_crit,
                                alert_payload=alert,
                                # culprit_process for eBPF/container alerts;
                                # process_name for external HTTP/TCP alerts
                                anomaly_process=(
                                    alert.get("culprit_process")
                                    or alert.get("process_name")
                                ),
                            )

                            if success:
                                self.active_incident_id = incident_id
                                self._set_condition_cooldown(container_name, anomaly_type)
                                self._reset_anomaly_count(container_name, anomaly_type)
                                if wf_id:
                                    # Event qualified → real incident workflow created.
                                    # Track as active so we send all-clear on recovery
                                    # and reconcile can clear it when the incident closes.
                                    self.active_conditions[condition_key] = platform_event_type
                                    self._active_workflow_ids[container_name] = wf_id
                                    # Reset quiet counter so a fresh incident doesn't
                                    # immediately all-clear on the first quiet poll.
                                    self._log_quiet_polls.pop(condition_key, None)
                                else:
                                    # Event submitted but dismissed (below threshold).
                                    # Cooldown prevents immediate retry; do NOT add to
                                    # active_conditions so we can re-try after cooldown
                                    # (important when CMDB data or weights are updated).
                                    logger.info(
                                        f"ℹ️  [DISMISSED] {container_name}/{anomaly_type}: "
                                        f"event submitted but scored below threshold — "
                                        f"cooldown active, will retry after {self.cooldown_seconds}s"
                                    )
                                self.write_status("event_submitted", anomaly_type, container_name, 0)
                            else:
                                self.write_status("event_submission_failed", anomaly_type, container_name, 0)

                    # All systems healthy
                    else:
                        if self.active_incident_id:
                            self.active_incident_id = None
                            self.last_anomaly_process = None
                            self.anomaly_start_time = None

                        # Status: healthy with metrics
                        status_detail = ""
                        if container_stats:
                            containers_list = ', '.join(list(container_stats.keys())[:5])
                            status_detail = f" ({len(container_stats)} containers: {containers_list})"

                        self.write_status("healthy", "normal", "", 0)
                        logger.info(f"✓ [HEALTHY]{status_detail}")

                    # ── Synthetic transaction monitors ────────────────────────────────
                    await self._check_synthetic_monitors()

                    await asyncio.sleep(self.poll_interval)

                except Exception as e:
                    logger.error(f"❌ [POLL ERROR] {e}", exc_info=True)
                    self.write_status("error", str(e))
                    await asyncio.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("\n\n🛑 [STOP] Watcher stopped by user")


    # ── Synthetic transaction monitoring ──────────────────────────────────────

    @staticmethod
    def _extract_synthetic_failure_detail(output: str) -> Tuple[Optional[str], str]:
        """
        Pull a human-readable (page_name, reason) out of a synthetic monitor's
        script output, so incidents get a specific title like "Login failed"
        instead of a generic "Uptime Probe Failed" on every monitor regardless
        of which step actually broke.

        Matches the structured "Start Page N: <name>" / "End Page N - FAILED --
        <reason>" lines emitted by generateScriptDeterministically (see
        SyntheticsPage.tsx). Falls back to the older flat "RESULT : FAIL --
        <reason>" format for scripts saved before that rewrite, and finally to
        the last non-empty output line for anything else (timeouts, tracebacks).
        """
        page_names: Dict[str, str] = {}
        for line in output.splitlines():
            m = re.match(r"\s*Start Page (\d+):\s*(.+)", line)
            if m:
                page_names[m.group(1)] = m.group(2).strip()

        for line in output.splitlines():
            m = re.match(r"\s*End Page (\d+) - FAILED(?:\s*--\s*(.*))?", line)
            if m:
                page_num, reason = m.group(1), (m.group(2) or "").strip()
                return page_names.get(page_num), reason

        for line in output.splitlines():
            m = re.match(r"\s*RESULT\s*:\s*FAIL\s*--\s*(.*)", line.strip())
            if m:
                return None, m.group(1).strip()

        lines = [l.strip() for l in output.splitlines() if l.strip()]
        return None, (lines[-1] if lines else "no output")

    async def _check_synthetic_monitors(self) -> None:
        """
        Fetch enabled synthetic monitors from the backend, run any that are due,
        post results back, and emit a monitoring event on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.api_base_url}/api/synthetics",
                    headers=self._api_headers,
                )
                if resp.status_code != 200:
                    return
                monitors = resp.json()
        except Exception as exc:
            logger.debug(f"[SYNTHETIC] Could not fetch monitors: {exc}")
            return

        now = datetime.utcnow()
        loop = asyncio.get_event_loop()

        for mon in monitors:
            if not mon.get("enabled") or not mon.get("script"):
                continue

            monitor_id = mon["id"]
            schedule_mins = mon.get("schedule_mins", 60)
            last_run_at_str = mon.get("last_run_at")

            if last_run_at_str:
                try:
                    last_run_at = datetime.fromisoformat(last_run_at_str)
                    elapsed_mins = (now - last_run_at).total_seconds() / 60
                    if elapsed_mins < schedule_mins:
                        continue
                except ValueError:
                    pass

            logger.info(f"🔬 [SYNTHETIC] Running monitor '{mon['name']}'")

            # Run script in a thread (subprocess, blocking)
            def _run(m=mon):
                import sys, tempfile, os as _os, subprocess as _sub
                creds = m.get("credentials", {}) or {}
                env = dict(_os.environ)
                env.update(creds)
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(m["script"])
                    tmp_path = tmp.name
                try:
                    result = _sub.run(
                        [sys.executable, tmp_path],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    output = (result.stdout or "") + (result.stderr or "")
                    status = "pass" if result.returncode == 0 else "fail"
                    return status, output
                except _sub.TimeoutExpired:
                    return "error", "Script timed out after 120 seconds."
                except Exception as exc:
                    return "error", f"{type(exc).__name__}: {exc}"
                finally:
                    try:
                        _os.unlink(tmp_path)
                    except OSError:
                        pass

            try:
                status, output = await loop.run_in_executor(None, _run)
            except Exception as exc:
                status, output = "error", str(exc)

            logger.info(f"🔬 [SYNTHETIC] '{mon['name']}' → {status}")
            for line in output.strip().splitlines():
                logger.info(f"🔬 [SYNTHETIC]     {line}")

            # Post result back to backend
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{self.api_base_url}/api/synthetics/{monitor_id}/result",
                        json={"status": status, "output": output[-4000:]},
                        headers=self._api_headers,
                    )
            except Exception as exc:
                logger.warning(f"[SYNTHETIC] Could not post result: {exc}")

            mon_name = mon["name"]

            if status != "pass":
                # Increment independent consecutive-fail counter for this monitor
                fail_count = self.synthetic_fail_counts.get(mon_name, 0) + 1
                self.synthetic_fail_counts[mon_name] = fail_count
                logger.info(
                    f"🔬 [SYNTHETIC] '{mon_name}' fail streak: "
                    f"{fail_count}/{self.synthetic_min_consecutive_fails}"
                )

                # Fire alert exactly once when the threshold is first reached.
                # A failed transaction (bad status, failed assertion, rejected
                # login) is just as urgent as a hard script error/timeout — both
                # mean the monitored journey is broken for real users right now.
                if fail_count == self.synthetic_min_consecutive_fails:
                    criticality = self.event_type_severity.get("synthetic.transaction.failed", "critical")
                    page_name, reason = self._extract_synthetic_failure_detail(output)
                    title = (
                        f"{mon_name} - Transaction {page_name} failed"
                        if page_name else f"{mon_name} - Transaction failed"
                    )
                    await self.submit_monitoring_event_to_platform(
                        event_type="synthetic.transaction.failed",
                        resource_name=mon_name,
                        raw_criticality=criticality,
                        alert_payload={
                            "monitor_id": monitor_id,
                            "status": status,
                            "output": output[:2000],
                            "schedule_mins": mon.get("schedule_mins", 60),
                            "title": title,
                            "description": reason,
                        },
                    )
                    self.synthetic_alert_active.add(mon_name)
            else:
                # Recovery: send all-clear if an alert was previously fired
                if mon_name in self.synthetic_alert_active:
                    await self.submit_condition_cleared(mon_name, "synthetic.transaction.failed")
                    self.synthetic_alert_active.discard(mon_name)
                self.synthetic_fail_counts[mon_name] = 0


def get_watcher_service() -> WatcherService:
    """Get or create the Watcher service instance."""
    return WatcherService(
        sentinel_container=os.getenv("SENTINEL_CONTAINER", "sentinel_senses"),
        api_base_url=os.getenv("WATCHER_API_URL", "http://backend:8000"),
        poll_interval=int(os.getenv("WATCHER_POLL_INTERVAL", "10")),
        anomaly_threshold=int(os.getenv("WATCHER_ANOMALY_THRESHOLD", "20000")),
        cpu_threshold=float(os.getenv("WATCHER_CPU_THRESHOLD", "80.0")),
        memory_threshold=float(os.getenv("WATCHER_MEMORY_THRESHOLD", "90.0")),
        disk_threshold=float(os.getenv("WATCHER_DISK_THRESHOLD", "90.0")),
        connection_threshold=int(os.getenv("WATCHER_CONNECTION_THRESHOLD", "1000")),
        cooldown_seconds=int(os.getenv("WATCHER_COOLDOWN_SECONDS", "60")),
        min_consecutive_polls=int(os.getenv("WATCHER_MIN_CONSECUTIVE_POLLS", "3")),
        synthetic_min_consecutive_fails=int(os.getenv("WATCHER_SYNTHETIC_MIN_CONSECUTIVE_FAILS", "1")),
    )
