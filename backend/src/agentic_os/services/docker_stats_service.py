"""
Docker Stats Service - Container Health Monitoring
Collects Docker container metrics (CPU, memory, network, I/O)
alongside syscall telemetry for comprehensive anomaly detection.
"""

import subprocess
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Processes that appear at the top of `ps --sort=-pcpu` due to the act of
# sampling itself.  They are transient measurement artifacts, not real culprits.
# We skip these and return the first *real* process below them.
_SAMPLING_ARTIFACTS: frozenset = frozenset({
    "ps", "sh", "bash", "dash", "ash",         # shells / ps itself
    "grep", "awk", "sed", "sort", "head",       # pipe helpers
    "tail", "cut", "tr", "wc", "xargs",
    "pgrep", "pkill", "kill",                   # signal tools
    "top", "htop", "atop",                      # interactive monitors
})


@dataclass
class ContainerMetrics:
    """Container performance metrics"""
    container_id: str
    container_name: str
    cpu_percent: float          # 0-100%
    memory_used_mb: float       # MB
    memory_limit_mb: float      # MB
    memory_percent: float       # 0-100%
    network_in_mb: float        # MB
    network_out_mb: float       # MB
    io_read_mb: float           # MB
    io_write_mb: float          # MB
    pids: int                   # Number of processes
    state: str                  # running, paused, exited
    timestamp: datetime
    # Filesystem usage — populated from docker exec df after stats collection.
    # Kept separate from docker stats because `docker stats` only reports
    # block I/O (reads/writes), not actual filesystem fill %.
    disk_percent: float = 0.0   # 0-100%; 0.0 = not yet collected


class DockerStatsService:
    """
    Collects Docker container statistics for health monitoring.

    Monitors:
    - CPU usage and limits
    - Memory usage and limits
    - Network I/O
    - Block I/O (disk reads/writes)
    - Process count
    - Container state
    """

    def __init__(self):
        """Initialize Docker stats service"""
        logger.info("🐳 [INIT] Docker Stats Service initialized")

    def get_all_container_stats(self) -> Optional[Dict[str, ContainerMetrics]]:
        """
        Get stats for all running containers.

        Returns:
            Dict mapping container_name -> ContainerMetrics, or None on error
        """
        try:
            cmd = ["docker", "stats", "--no-stream", "--format", "json"]
            logger.debug(f"🔍 [DOCKER STATS] Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            logger.debug(f"🔍 [DOCKER STATS] Return code: {result.returncode}")

            if result.returncode != 0:
                logger.error(f"❌ [DOCKER STATS] Failed with code {result.returncode}")
                logger.error(f"❌ [DOCKER STATS] stderr: {result.stderr}")
                logger.error(f"❌ [DOCKER STATS] stdout: {result.stdout}")
                return None

            # Log raw output for debugging
            if result.stdout:
                logger.debug(f"🔍 [DOCKER STATS] Raw stdout length: {len(result.stdout)} bytes")
                logger.debug(f"🔍 [DOCKER STATS] First 500 chars: {result.stdout[:500]}")
            else:
                logger.warning(f"⚠️  [DOCKER STATS] Empty stdout from docker stats")

            if result.stderr:
                logger.debug(f"🔍 [DOCKER STATS] stderr output: {result.stderr}")

            # Parse JSONL output (one JSON object per line)
            stats_dict = {}
            lines = result.stdout.strip().split('\n') if result.stdout else []
            logger.debug(f"🔍 [DOCKER STATS] Total lines to parse: {len(lines)}")

            for idx, line in enumerate(lines):
                if not line.strip():
                    logger.debug(f"🔍 [DOCKER STATS] Line {idx}: empty (skipping)")
                    continue
                try:
                    logger.debug(f"🔍 [DOCKER STATS] Parsing line {idx}: {line[:100]}...")
                    data = json.loads(line)
                    # Try multiple possible field names for container name
                    name = data.get('Names', '') or data.get('Name', '') or data.get('Container', '')
                    name = name.strip()
                    if name:
                        logger.debug(f"✓ [DOCKER STATS] Parsed container: {name}")
                        metrics = self._parse_docker_stats(data)
                        stats_dict[name] = metrics
                    else:
                        logger.debug(f"⚠️  [DOCKER STATS] Line {idx}: No container name found (available keys: {list(data.keys())})")
                except json.JSONDecodeError as je:
                    logger.warning(f"⚠️  [DOCKER STATS] Line {idx}: JSON parse error: {je}")
                    logger.warning(f"⚠️  [DOCKER STATS] Line {idx}: Content: {line}")
                    continue

            logger.debug(f"🔍 [DOCKER STATS] Parsed {len(stats_dict)} containers")

            if not stats_dict:
                logger.warning(f"⚠️  [DOCKER STATS] No containers parsed from output")
                return None

            return stats_dict
        except subprocess.TimeoutExpired as te:
            logger.error(f"❌ [DOCKER STATS] Timeout after 15 seconds: {te}")
            return None
        except FileNotFoundError as fe:
            logger.error(f"❌ [DOCKER STATS] docker command not found: {fe}")
            return None
        except Exception as e:
            logger.error(f"❌ [DOCKER STATS ERROR] {type(e).__name__}: {e}")
            import traceback
            logger.error(f"❌ [DOCKER STATS] Traceback: {traceback.format_exc()}")
            return None

    def get_container_stats(self, container_name: str) -> Optional[ContainerMetrics]:
        """
        Get stats for a specific container.

        Args:
            container_name: Container name or ID

        Returns:
            ContainerMetrics or None if not found
        """
        all_stats = self.get_all_container_stats()
        if all_stats:
            return all_stats.get(container_name)
        return None

    def _parse_docker_stats(self, data: Dict[str, Any]) -> ContainerMetrics:
        """
        Parse docker stats JSON object into ContainerMetrics.

        Args:
            data: Raw docker stats object

        Returns:
            ContainerMetrics
        """
        # Parse CPU percentage (e.g., "25.50%")
        cpu_str = data.get('CPUPerc', '0%').strip('%')
        cpu_percent = float(cpu_str) if cpu_str else 0.0

        # Parse memory (e.g., "256MiB / 1GiB")
        mem_str = data.get('MemUsage', '0B / 0B')
        mem_used_mb = self._parse_memory(mem_str.split('/')[0].strip())
        mem_limit_mb = self._parse_memory(mem_str.split('/')[1].strip()) if '/' in mem_str else 1024

        # Avoid division by zero
        if mem_limit_mb == 0:
            mem_limit_mb = 1024
        memory_percent = (mem_used_mb / mem_limit_mb) * 100 if mem_limit_mb > 0 else 0

        # Parse network I/O (e.g., "1.2MiB / 500KiB")
        net_str = data.get('NetIO', '0B / 0B')
        net_in_mb = self._parse_memory(net_str.split('/')[0].strip())
        net_out_mb = self._parse_memory(net_str.split('/')[1].strip()) if '/' in net_str else 0

        # Parse block I/O (e.g., "10MiB / 5MiB")
        io_str = data.get('BlockIO', '0B / 0B')
        io_read_mb = self._parse_memory(io_str.split('/')[0].strip())
        io_write_mb = self._parse_memory(io_str.split('/')[1].strip()) if '/' in io_str else 0

        # Parse PIDs
        pids_str = data.get('PIDs', '0')
        pids = int(pids_str) if pids_str else 0

        return ContainerMetrics(
            container_id=data.get('ID', ''),
            container_name=data.get('Names', ''),
            cpu_percent=cpu_percent,
            memory_used_mb=mem_used_mb,
            memory_limit_mb=mem_limit_mb,
            memory_percent=memory_percent,
            network_in_mb=net_in_mb,
            network_out_mb=net_out_mb,
            io_read_mb=io_read_mb,
            io_write_mb=io_write_mb,
            pids=pids,
            state="running",  # From docker stats, only running containers shown
            timestamp=datetime.utcnow()
        )

    def _parse_memory(self, mem_str: str) -> float:
        """
        Parse memory string to MB.

        Examples:
            "256MiB" -> 256.0
            "1GiB" -> 1024.0
            "512KiB" -> 0.5
            "0B" -> 0.0

        Args:
            mem_str: Memory string with unit

        Returns:
            Memory in MB
        """
        mem_str = mem_str.strip()
        if not mem_str or mem_str == '0B':
            return 0.0

        # Remove spaces
        mem_str = mem_str.replace(' ', '')

        # Extract number and unit
        for i, char in enumerate(mem_str):
            if not char.isdigit() and char != '.':
                number = float(mem_str[:i])
                unit = mem_str[i:].lower()
                break
        else:
            return float(mem_str)

        # Convert to MB
        conversions = {
            'b': 1 / (1024 * 1024),
            'kb': 1 / 1024,
            'kib': 1 / 1024,
            'mb': 1,
            'mib': 1,
            'gb': 1024,
            'gib': 1024,
            'tb': 1024 * 1024,
            'tib': 1024 * 1024,
        }

        multiplier = conversions.get(unit, 1)
        return number * multiplier

    def detect_cpu_spike(self, stats: Dict[str, ContainerMetrics], threshold: float = 80.0) -> Optional[str]:
        """
        Detect container with CPU spike.

        Args:
            stats: Container stats dictionary
            threshold: CPU percentage threshold (default: 80%)

        Returns:
            Container name with high CPU, or None
        """
        for name, metrics in stats.items():
            if metrics.cpu_percent > threshold:
                logger.warning(f"🔴 [CPU SPIKE] {name}: {metrics.cpu_percent}%")
                return name
        return None

    def detect_memory_surge(self, stats: Dict[str, ContainerMetrics], threshold: float = 90.0) -> Optional[str]:
        """
        Detect container with memory surge.

        Args:
            stats: Container stats dictionary
            threshold: Memory percentage threshold (default: 90%)

        Returns:
            Container name with high memory, or None
        """
        for name, metrics in stats.items():
            if metrics.memory_percent > threshold:
                logger.warning(f"🔴 [MEMORY SURGE] {name}: {metrics.memory_percent}% ({metrics.memory_used_mb}MB/{metrics.memory_limit_mb}MB)")
                return name
        return None

    def detect_high_pids(self, stats: Dict[str, ContainerMetrics], threshold: int = 500) -> Optional[str]:
        """
        Detect container with too many processes.

        Args:
            stats: Container stats dictionary
            threshold: PID count threshold (default: 500)

        Returns:
            Container name with high PID count, or None
        """
        for name, metrics in stats.items():
            if metrics.pids > threshold:
                logger.warning(f"🔴 [HIGH PID COUNT] {name}: {metrics.pids} processes")
                return name
        return None

    def get_container_names(self) -> List[str]:
        """
        Get list of all running container names.

        Returns:
            List of container names
        """
        try:
            cmd = ["docker", "ps", "--format", "{{.Names}}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return [name.strip() for name in result.stdout.strip().split('\n') if name.strip()]
        except Exception as e:
            logger.error(f"❌ [GET CONTAINERS] {e}")
        return []

    def find_container_by_process(self, process_name: str) -> Optional[str]:
        """
        Find container running a specific process.

        Args:
            process_name: Process name (e.g., "python", "redis-server")

        Returns:
            Container name, or None if not found
        """
        containers = self.get_container_names()
        for container in containers:
            try:
                cmd = ["docker", "exec", "-i", container, "pgrep", "-f", process_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    return container
            except Exception:
                continue
        return None

    def get_top_processes(self, container_name: str, limit: int = 5) -> Optional[list]:
        """
        Get top CPU-consuming processes in a container, sorted by CPU descending,
        with sampling artifacts filtered out.

        We always fetch more rows than requested so that after filtering transient
        helper processes (ps, sh, grep, …) we still have enough real candidates.

        Primary method: `ps -eo pid,comm,pcpu --sort=-pcpu --no-headers` inside
        the container.  Fallback: `docker top` (unsorted; no CPU %).

        Returns:
            List of dicts, artifacts excluded, sorted by CPU:
            [{'pid': '123', 'name': 'yes', 'cpu_percent': 99.0}]
        """
        fetch_n = max(limit + len(_SAMPLING_ARTIFACTS), 20)

        # ── Primary: ps sorted by CPU ─────────────────────────────────────────
        try:
            cmd = [
                "docker", "exec", container_name,
                "ps", "-eo", "pid,comm,pcpu", "--sort=-pcpu", "--no-headers"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                all_procs = []
                for line in result.stdout.strip().splitlines()[:fetch_n]:
                    parts = line.split()
                    if len(parts) >= 2:
                        all_procs.append({
                            'pid': parts[0],
                            'name': parts[1],
                            'cpu_percent': float(parts[2]) if len(parts) > 2 else 0.0,
                            'cmd': parts[1],
                        })
                # Strip sampling artifacts; keep up to `limit` real processes.
                real_procs = [p for p in all_procs if p['name'] not in _SAMPLING_ARTIFACTS]
                if real_procs:
                    return real_procs[:limit]
                # All results were artifacts (shouldn't happen) — return raw list
                # so callers always have something rather than None.
                if all_procs:
                    return all_procs[:limit]
        except Exception:
            pass  # fall through to docker top

        # ── Fallback: docker top (no CPU sorting, but works everywhere) ───────
        try:
            cmd = ["docker", "top", container_name, "-o", "pid,comm,args"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return None
            all_procs = []
            for line in lines[1:fetch_n + 1]:
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    all_procs.append({
                        'pid': parts[0],
                        'name': parts[1],
                        'cpu_percent': 0.0,
                        'cmd': parts[2] if len(parts) > 2 else parts[1],
                    })
            real_procs = [p for p in all_procs if p['name'] not in _SAMPLING_ARTIFACTS]
            return (real_procs or all_procs)[:limit] or None
        except Exception as e:
            logger.error(f"❌ [GET TOP PROCESSES] {container_name}: {e}")
            return None

    def format_stats_summary(self, stats: Dict[str, ContainerMetrics]) -> str:
        """
        Format stats as human-readable summary.

        Args:
            stats: Container stats dictionary

        Returns:
            Formatted string
        """
        lines = ["🐳 CONTAINER STATS SUMMARY"]
        lines.append("=" * 80)

        for name, metrics in stats.items():
            lines.append(f"\n{name}:")
            lines.append(f"  CPU: {metrics.cpu_percent:6.2f}%")
            lines.append(f"  Memory: {metrics.memory_used_mb:8.1f}MB / {metrics.memory_limit_mb:8.1f}MB ({metrics.memory_percent:6.2f}%)")
            lines.append(f"  Network: ↓{metrics.network_in_mb:6.1f}MB ↑{metrics.network_out_mb:6.1f}MB")
            lines.append(f"  I/O: Read {metrics.io_read_mb:6.1f}MB, Write {metrics.io_write_mb:6.1f}MB")
            lines.append(f"  PIDs: {metrics.pids}")

        return '\n'.join(lines)


def get_docker_stats_service() -> DockerStatsService:
    """Get or create Docker stats service instance"""
    return DockerStatsService()
