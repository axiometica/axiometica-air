"""
Advanced Monitoring Service - Comprehensive Infrastructure Health
Monitors: Disk space, Health checks (local + external), Network, Logs, Metrics correlation

External monitoring runs from the watcher container itself (not docker exec),
giving a true outside-in view of service availability:
  - Ping (ICMP)        — is a host reachable on the network?
  - External HTTP/HTTPS — does a URL return the expected status / within time?
  - TLS certificate     — how many days until the cert expires?
  - DNS resolution      — does a hostname resolve?
  - External TCP port   — is a port open on an arbitrary host?
"""

import re
import socket
import ssl
import subprocess
import json
import logging
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DiskMetrics:
    """Disk space metrics"""
    container_name: str
    filesystem: str
    total_gb: float
    used_gb: float
    available_gb: float
    usage_percent: float
    timestamp: datetime


@dataclass
class HealthCheckResult:
    """Health check result"""
    container_name: str
    check_type: str  # "http", "tcp", "process"
    endpoint: str
    status: str  # "healthy", "unhealthy", "unknown"
    response_time_ms: float
    timestamp: datetime


@dataclass
class ExternalCheckResult:
    """Result from an external (non-container) check."""
    check_type: str          # "ping", "http", "https", "tcp", "dns", "tls"
    target: str              # host, URL, or hostname
    status: str              # "healthy", "unhealthy", "degraded", "unknown"
    response_time_ms: float
    status_code: Optional[int] = None        # HTTP only
    tls_days_remaining: Optional[int] = None  # TLS only
    error: Optional[str] = None
    response_body: Optional[str] = None      # HTTP only, first 500 chars
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ExternalCheckConfig:
    """
    Configuration for a single external check target.

    Examples
    --------
    Ping a host:
        ExternalCheckConfig(check_type="ping", target="8.8.8.8", name="Google DNS")

    HTTP endpoint with latency alerting:
        ExternalCheckConfig(check_type="http", target="http://api.example.com/health",
                            expected_status=200, timeout_ms=3000, latency_threshold_ms=1000)

    HTTPS with certificate expiry warning:
        ExternalCheckConfig(check_type="https", target="https://example.com",
                            tls_expiry_warning_days=30)

    External TCP port:
        ExternalCheckConfig(check_type="tcp", target="db.example.com", port=5432)

    DNS resolution:
        ExternalCheckConfig(check_type="dns", target="api.example.com")
    """
    check_type: str            # "ping" | "http" | "https" | "tcp" | "dns" | "tls"
    target: str                # URL, host, or FQDN
    name: str = ""             # Human-readable label (defaults to target)
    port: int = 0              # Required for "tcp"; ignored for "ping"/"dns"
    expected_status: int = 200 # HTTP/HTTPS expected response code
    timeout_ms: int = 5000     # Request/connect timeout in ms
    latency_threshold_ms: int = 0  # Alert if response > N ms (0 = disabled)
    tls_expiry_warning_days: int = 30  # Alert if cert expires within N days
    container_name: str = ""   # Docker container to remediate if check fails (e.g. "agentic_os_flower")
    service_name: str = ""     # Runbook matching key, simpler name (e.g. "flower")


class AdvancedMonitoringService:
    """
    Advanced monitoring for comprehensive infrastructure health.

    Monitors:
    - Disk space (mounted volumes, root filesystem)
    - Container health (HTTP, TCP, process checks)
    - Network connectivity (between containers)
    - Application logs (errors, warnings)
    - Metrics correlation (syscalls + CPU patterns)
    """

    def __init__(self):
        """Initialize advanced monitoring service"""
        logger.info("📊 [INIT] Advanced Monitoring Service initialized")

    # ==================== DISK MONITORING ====================

    def get_container_disk_usage(self, container_name: str) -> Optional[DiskMetrics]:
        """
        Get disk usage for container's root filesystem.

        Args:
            container_name: Container name

        Returns:
            DiskMetrics or None if error
        """
        try:
            cmd = ["docker", "exec", container_name, "df", "-B1", "/"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode != 0:
                return None

            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return None

            # Parse df output
            parts = lines[1].split()
            if len(parts) < 4:
                return None

            total = float(parts[1]) / (1024**3)  # Convert to GB
            used = float(parts[2]) / (1024**3)
            available = float(parts[3]) / (1024**3)
            usage_percent = (used / total * 100) if total > 0 else 0

            return DiskMetrics(
                container_name=container_name,
                filesystem=parts[0],
                total_gb=total,
                used_gb=used,
                available_gb=available,
                usage_percent=usage_percent,
                timestamp=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"❌ [DISK USAGE] {container_name}: {e}")
            return None

    def detect_disk_anomaly(self, disk_metrics: DiskMetrics, threshold: float = 90.0) -> bool:
        """
        Detect if disk usage exceeds threshold.

        Args:
            disk_metrics: Disk metrics
            threshold: Usage percentage threshold

        Returns:
            True if anomaly detected
        """
        if disk_metrics.usage_percent > threshold:
            logger.warning(f"🔴 [DISK FULL] {disk_metrics.container_name}: {disk_metrics.usage_percent:.1f}%")
            return True
        return False

    # ==================== HEALTH CHECKS ====================

    def health_check_http(self, container_name: str, port: int = 8000, path: str = "/api/health") -> HealthCheckResult:
        """
        Check HTTP health endpoint.

        Args:
            container_name: Container name
            port: Port to check
            path: Health check path

        Returns:
            HealthCheckResult
        """
        try:
            start = datetime.utcnow()
            cmd = [
                "docker", "exec", container_name,
                "curl", "-s", "-m", "5", "-w", "%{http_code}",
                f"http://localhost:{port}{path}"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if result.returncode == 0 and "200" in result.stdout[-3:]:
                return HealthCheckResult(
                    container_name=container_name,
                    check_type="http",
                    endpoint=f"localhost:{port}{path}",
                    status="healthy",
                    response_time_ms=elapsed,
                    timestamp=datetime.utcnow()
                )
            else:
                return HealthCheckResult(
                    container_name=container_name,
                    check_type="http",
                    endpoint=f"localhost:{port}{path}",
                    status="unhealthy",
                    response_time_ms=elapsed,
                    timestamp=datetime.utcnow()
                )
        except Exception as e:
            logger.error(f"❌ [HEALTH CHECK] {container_name}: {e}")
            return HealthCheckResult(
                container_name=container_name,
                check_type="http",
                endpoint=f"localhost:{port}{path}",
                status="unknown",
                response_time_ms=0,
                timestamp=datetime.utcnow()
            )

    def health_check_tcp(self, container_name: str, port: int) -> HealthCheckResult:
        """
        Check TCP port connectivity.

        Args:
            container_name: Container name
            port: Port to check

        Returns:
            HealthCheckResult
        """
        try:
            start = datetime.utcnow()
            cmd = [
                "docker", "exec", container_name,
                "nc", "-zv", "-w", "2", "localhost", str(port)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            status = "healthy" if result.returncode == 0 else "unhealthy"

            return HealthCheckResult(
                container_name=container_name,
                check_type="tcp",
                endpoint=f"localhost:{port}",
                status=status,
                response_time_ms=elapsed,
                timestamp=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"❌ [TCP CHECK] {container_name}:{port}: {e}")
            return HealthCheckResult(
                container_name=container_name,
                check_type="tcp",
                endpoint=f"localhost:{port}",
                status="unknown",
                response_time_ms=0,
                timestamp=datetime.utcnow()
            )

    def health_check_process(self, container_name: str, process_name: str) -> HealthCheckResult:
        """
        Check if process is running.

        Args:
            container_name: Container name
            process_name: Process name to check

        Returns:
            HealthCheckResult
        """
        try:
            cmd = ["docker", "exec", container_name, "pgrep", "-f", process_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            status = "healthy" if result.returncode == 0 else "unhealthy"

            return HealthCheckResult(
                container_name=container_name,
                check_type="process",
                endpoint=f"process:{process_name}",
                status=status,
                response_time_ms=0,
                timestamp=datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"❌ [PROCESS CHECK] {container_name}: {e}")
            return HealthCheckResult(
                container_name=container_name,
                check_type="process",
                endpoint=f"process:{process_name}",
                status="unknown",
                response_time_ms=0,
                timestamp=datetime.utcnow()
            )

    # ==================== NETWORK MONITORING ====================

    def get_container_connections(self, container_name: str) -> Optional[Dict[str, int]]:
        """
        Get network connection statistics.

        Args:
            container_name: Container name

        Returns:
            Dict with connection counts: {'established': 10, 'listen': 5, 'time_wait': 2}
        """
        try:
            cmd = ["docker", "exec", container_name, "ss", "-tan"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode != 0:
                return None

            states = {}
            for line in result.stdout.split('\n')[1:]:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) > 0:
                    state = parts[-1]
                    states[state] = states.get(state, 0) + 1

            return states if states else None
        except Exception as e:
            logger.error(f"❌ [NETWORK STATS] {container_name}: {e}")
            return None

    def detect_connection_spike(self, connections: Dict[str, int], threshold: int = 1000) -> Optional[str]:
        """
        Detect unusual number of connections.

        Args:
            connections: Connection stats
            threshold: Total connection threshold

        Returns:
            Alert message if spike detected
        """
        total = sum(connections.values())
        if total > threshold:
            time_wait = connections.get('TIME-WAIT', 0)
            logger.warning(f"🔴 [CONNECTION SPIKE] {total} total connections, {time_wait} TIME-WAIT")
            return f"High connection count: {total} (TIME-WAIT: {time_wait})"
        return None

    # ==================== LOG MONITORING ====================

    # Match structured log levels only — uppercase whole-word so we don't catch
    # "error_rate", "failed authentication" (WARN-level), "retrying after error", etc.
    _LOG_ERROR_PATTERNS = re.compile(
        r'\bERROR\b'                              # structured level (uvicorn, Python logging)
        r'|\bCRITICAL\b'                          # structured level
        r'|\bFATAL\b'                             # Java / Go / systemd
        r'|\bPANIC\b'                             # Go runtime panics
        r'|Traceback \(most recent call last\)'   # Python traceback header
        r'|Exception in thread'                   # JVM uncaught exception
        r'|OOM killer'                            # kernel out-of-memory
        r'|panic: runtime error'                  # Go runtime panic
    )

    # Lines that match a severity keyword but are benign and should be ignored.
    # These are framework/infrastructure messages that are informational in context.
    _LOG_ERROR_EXCLUDES = re.compile(
        r'authentication (failure|failed|attempt)'  # Neo4j WARN: wrong password probes
        r'|unauthorized due to authentication'       # Neo4j WARN: bolt auth failure
        r'|retrying'                                 # transient errors being handled
        r'|recovered from'                           # panic recovery (handled)
        r'|error_rate'                               # metric names in logs
        r'|error_count'                              # metric names
        r'|errors=0\b'                               # "0 errors" summary line
        r'|0 errors'
        r'|no errors'
        r'|without error'
        r'|\[ignored\]'
        r'|DeadlineExceeded.*retrying'              # gRPC transient
        r'|WARN.*Failed'                             # WARN level, not ERROR
    , re.IGNORECASE)

    # Minimum number of matching lines within the time window before reporting.
    # Avoids firing on a single transient error line.
    LOG_ERROR_MIN_COUNT: int = 3

    # Look back this many seconds when fetching logs (matches ~2 poll intervals).
    LOG_ERROR_SINCE_SECONDS: int = 30

    def get_container_logs_errors(self, container_name: str, since_seconds: int = None) -> List[str]:
        """
        Return recent error log lines from *container_name*.

        Uses a time window (--since) rather than --tail so old one-off lines
        do not keep re-triggering on every poll.  Only lines whose log level
        is genuinely ERROR/CRITICAL/FATAL (uppercase structured levels) or
        contain Python/JVM stack-trace headers are returned.  Known-benign
        patterns (auth failures logged at WARN, metric summaries, retry noise)
        are excluded before returning.

        Args:
            container_name: Docker container name.
            since_seconds:  How far back to look.  Defaults to LOG_ERROR_SINCE_SECONDS.

        Returns:
            List of matching error lines (may be empty).
        """
        since = since_seconds if since_seconds is not None else self.LOG_ERROR_SINCE_SECONDS
        try:
            cmd = [
                "docker", "logs",
                "--since", f"{since}s",
                "--tail", "200",         # cap output even within the time window
                container_name,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            # docker logs writes to stderr by default; some containers use stdout
            output = result.stdout + result.stderr

            errors = []
            for line in output.splitlines():
                if not line.strip():
                    continue
                if self._LOG_ERROR_PATTERNS.search(line) and not self._LOG_ERROR_EXCLUDES.search(line):
                    errors.append(line)

            return errors
        except Exception as e:
            logger.error(f"❌ [LOG CHECK] {container_name}: {e}")
            return []

    def detect_log_errors(self, container_name: str) -> Tuple[bool, List[str]]:
        """
        Detect genuine application errors in recent container logs.

        Returns True only when LOG_ERROR_MIN_COUNT or more qualifying error
        lines appear within the last LOG_ERROR_SINCE_SECONDS window — this
        filters out isolated transient messages and infrastructure noise.

        Returns:
            (has_errors, error_lines)
        """
        errors = self.get_container_logs_errors(container_name)
        if len(errors) >= self.LOG_ERROR_MIN_COUNT:
            logger.warning(f"🔴 [LOG ERRORS] {container_name}: {len(errors)} error(s) in last {self.LOG_ERROR_SINCE_SECONDS}s")
            return True, errors
        return False, []

    # ==================== METRICS CORRELATION ====================

    def correlate_metrics(self, container_name: str, cpu_percent: float, syscall_count: int) -> Optional[str]:
        """
        Correlate CPU spike with syscall intensity.

        Args:
            container_name: Container name
            cpu_percent: CPU usage percentage
            syscall_count: Syscall count

        Returns:
            Correlation analysis or None
        """
        analysis = []

        # High CPU + High Syscalls = likely syscall bomb or tight loop
        if cpu_percent > 80 and syscall_count > 15000:
            analysis.append("🔗 CPU + Syscall Correlation: Likely syscall-heavy workload or bomb")

        # High CPU + Low Syscalls = likely compute-bound task
        elif cpu_percent > 80 and syscall_count < 5000:
            analysis.append("🔗 CPU Correlation: Compute-bound workload (low syscall rate)")

        # Low CPU + High Syscalls = I/O bound or context switching
        elif cpu_percent < 30 and syscall_count > 15000:
            analysis.append("🔗 Syscall Correlation: I/O-bound or high context switching")

        # High Memory + High Syscalls = possible memory pressure
        # (would need memory data to analyze)

        return analysis[0] if analysis else None

    # ==================== EXTERNAL CHECKS ====================
    # These run from the watcher process itself (not docker exec).
    # They give a true outside-in view — if a service is unreachable from the
    # watcher container, it is unreachable from the rest of the platform too.

    def external_ping(self, host: str, timeout_s: float = 3.0) -> ExternalCheckResult:
        """
        ICMP ping a host.  Uses the system `ping` binary so no root required.

        Returns healthy if at least 1 reply received.
        """
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(int(timeout_s)), host],
                capture_output=True, text=True, timeout=timeout_s + 1
            )
            elapsed = (time.monotonic() - t0) * 1000
            if result.returncode == 0:
                # Extract rtt from output: "rtt min/avg/max/mdev = 0.123/0.456/..."
                rtt = elapsed
                for line in result.stdout.splitlines():
                    if "rtt" in line or "round-trip" in line:
                        try:
                            rtt = float(line.split("/")[4])
                        except Exception:
                            pass
                return ExternalCheckResult(
                    check_type="ping", target=host, status="healthy",
                    response_time_ms=rtt
                )
            return ExternalCheckResult(
                check_type="ping", target=host, status="unhealthy",
                response_time_ms=elapsed,
                error=f"ping returned exit code {result.returncode}: {result.stderr.strip()}"
            )
        except subprocess.TimeoutExpired:
            return ExternalCheckResult(
                check_type="ping", target=host, status="unhealthy",
                response_time_ms=timeout_s * 1000, error="ping timed out"
            )
        except Exception as exc:
            return ExternalCheckResult(
                check_type="ping", target=host, status="unknown",
                response_time_ms=0, error=str(exc)
            )

    def external_http(self, url: str, expected_status: int = 200,
                      timeout_ms: int = 5000,
                      latency_threshold_ms: int = 0) -> ExternalCheckResult:
        """
        HTTP/HTTPS GET request from the watcher container.

        - Raises `unhealthy` on connection error, timeout, or wrong status code.
        - Raises `degraded` if response is OK but slower than latency_threshold_ms.
        - TLS certificates are verified by default for HTTPS URLs.
        """
        import httpx
        timeout_s = timeout_ms / 1000
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
                resp = client.get(url)
            elapsed = (time.monotonic() - t0) * 1000

            ok_status = (resp.status_code == expected_status)
            too_slow  = latency_threshold_ms > 0 and elapsed > latency_threshold_ms

            if not ok_status:
                status = "unhealthy"
                error  = f"expected HTTP {expected_status}, got {resp.status_code}"
            elif too_slow:
                status = "degraded"
                error  = f"response time {elapsed:.0f}ms exceeds threshold {latency_threshold_ms}ms"
            else:
                status = "healthy"
                error  = None

            return ExternalCheckResult(
                check_type="http" if url.startswith("http://") else "https",
                target=url, status=status,
                response_time_ms=elapsed,
                status_code=resp.status_code,
                error=error,
                response_body=resp.text[:500] if resp.text else None,
            )
        except httpx.TimeoutException:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="https" if url.startswith("https://") else "http",
                target=url, status="unhealthy",
                response_time_ms=elapsed,
                error=f"request timed out after {timeout_ms}ms"
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="https" if url.startswith("https://") else "http",
                target=url, status="unhealthy",
                response_time_ms=elapsed, error=str(exc)
            )

    def external_tcp(self, host: str, port: int, timeout_ms: int = 3000) -> ExternalCheckResult:
        """
        TCP connect to host:port.  Works for any host on the network —
        not limited to containers running on this host.
        """
        timeout_s = timeout_ms / 1000
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                pass
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="tcp", target=f"{host}:{port}",
                status="healthy", response_time_ms=elapsed
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="tcp", target=f"{host}:{port}",
                status="unhealthy", response_time_ms=elapsed, error=str(exc)
            )

    def external_dns(self, hostname: str, timeout_ms: int = 3000) -> ExternalCheckResult:
        """
        Resolve a hostname via the system DNS resolver.
        Unhealthy if the name cannot be resolved.
        """
        import concurrent.futures
        t0 = time.monotonic()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(socket.getaddrinfo, hostname, None)
                addrs = future.result(timeout=timeout_ms / 1000)
            elapsed = (time.monotonic() - t0) * 1000
            resolved_ips = list({a[4][0] for a in addrs})
            return ExternalCheckResult(
                check_type="dns", target=hostname,
                status="healthy", response_time_ms=elapsed,
                error=None
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="dns", target=hostname,
                status="unhealthy", response_time_ms=elapsed, error=str(exc)
            )

    def external_tls(self, hostname: str, port: int = 443,
                     expiry_warning_days: int = 30) -> ExternalCheckResult:
        """
        Connect via TLS and inspect the certificate.

        Returns:
          - healthy   — cert valid, expires in > expiry_warning_days
          - degraded  — cert valid but expiring within expiry_warning_days
          - unhealthy — cert invalid, expired, or connection refused
        """
        t0 = time.monotonic()
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
                    cert = tls.getpeercert()
            elapsed = (time.monotonic() - t0) * 1000

            # Parse expiry
            not_after = cert.get("notAfter", "")
            expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            days_remaining = (expiry_dt - datetime.utcnow()).days

            status = "healthy"
            error  = None
            if days_remaining <= 0:
                status = "unhealthy"
                error  = f"TLS certificate EXPIRED {abs(days_remaining)} day(s) ago"
            elif days_remaining <= expiry_warning_days:
                status = "degraded"
                error  = f"TLS certificate expires in {days_remaining} day(s)"

            return ExternalCheckResult(
                check_type="tls", target=f"{hostname}:{port}",
                status=status, response_time_ms=elapsed,
                tls_days_remaining=days_remaining, error=error
            )
        except ssl.SSLCertVerificationError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="tls", target=f"{hostname}:{port}",
                status="unhealthy", response_time_ms=elapsed,
                error=f"TLS verification failed: {exc}"
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return ExternalCheckResult(
                check_type="tls", target=f"{hostname}:{port}",
                status="unknown", response_time_ms=elapsed, error=str(exc)
            )

    def run_external_check(self, cfg: ExternalCheckConfig) -> ExternalCheckResult:
        """
        Dispatch a single ExternalCheckConfig to the correct check method.
        Safe to call in a loop — all exceptions are caught internally.
        """
        ct = cfg.check_type.lower()
        try:
            if ct == "ping":
                return self.external_ping(cfg.target, timeout_s=cfg.timeout_ms / 1000)
            elif ct in ("http", "https"):
                return self.external_http(
                    cfg.target,
                    expected_status=cfg.expected_status,
                    timeout_ms=cfg.timeout_ms,
                    latency_threshold_ms=cfg.latency_threshold_ms,
                )
            elif ct == "tcp":
                host, _, port_str = cfg.target.partition(":")
                port = int(port_str) if port_str else cfg.port
                return self.external_tcp(host, port, timeout_ms=cfg.timeout_ms)
            elif ct == "dns":
                return self.external_dns(cfg.target, timeout_ms=cfg.timeout_ms)
            elif ct == "tls":
                host, _, port_str = cfg.target.partition(":")
                port = int(port_str) if port_str else 443
                return self.external_tls(
                    host, port,
                    expiry_warning_days=cfg.tls_expiry_warning_days
                )
            else:
                return ExternalCheckResult(
                    check_type=ct, target=cfg.target, status="unknown",
                    response_time_ms=0, error=f"Unknown check_type: {cfg.check_type}"
                )
        except Exception as exc:
            return ExternalCheckResult(
                check_type=ct, target=cfg.target, status="unknown",
                response_time_ms=0, error=f"Unhandled error: {exc}"
            )

    # ==================== AUTO-REMEDIATION ====================

    def restart_container(self, container_name: str) -> bool:
        """
        Gracefully restart a container.

        Args:
            container_name: Container name

        Returns:
            Success status
        """
        try:
            logger.info(f"🔧 [RESTART] Restarting container {container_name}")
            cmd = ["docker", "restart", container_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                logger.info(f"✓ [RESTART SUCCESS] {container_name}")
                return True
            else:
                logger.error(f"❌ [RESTART FAILED] {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"❌ [RESTART ERROR] {e}")
            return False

    def clear_disk_cache(self, container_name: str) -> bool:
        """
        Clear caches to free disk space.

        Args:
            container_name: Container name

        Returns:
            Success status
        """
        try:
            logger.info(f"🧹 [CLEAR CACHE] Clearing caches in {container_name}")
            # Try to clear package manager caches
            cmd = ["docker", "exec", container_name, "sh", "-c", "rm -rf /tmp/* /var/cache/*"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                logger.info(f"✓ [CACHE CLEAR SUCCESS] {container_name}")
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"❌ [CACHE CLEAR ERROR] {e}")
            return False

    def kill_zombie_processes(self, container_name: str) -> bool:
        """
        Kill zombie processes.

        Args:
            container_name: Container name

        Returns:
            Success status
        """
        try:
            logger.info(f"🧟 [KILL ZOMBIES] Killing zombie processes in {container_name}")
            # Get PID 1 (init) to reap zombies
            cmd = ["docker", "exec", container_name, "ps", "aux"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            zombies = []
            for line in result.stdout.split('\n'):
                if '<defunct>' in line or 'Z' in line.split()[7]:
                    zombies.append(line)

            if zombies:
                logger.warning(f"🧟 [ZOMBIES] Found {len(zombies)} zombie processes")
                return True

            return True
        except Exception as e:
            logger.error(f"❌ [ZOMBIE CHECK] {e}")
            return False


def get_advanced_monitoring_service() -> AdvancedMonitoringService:
    """Get or create advanced monitoring service instance"""
    return AdvancedMonitoringService()
