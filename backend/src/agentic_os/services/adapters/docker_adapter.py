"""
Docker Adapter — uses the Docker socket for container management.

This is the default adapter when the watcher runs inside a Docker
environment.  It wraps the exact same subprocess + docker CLI calls
that watcher_main.py used to call directly, so existing behaviour
is preserved unchanged.
"""

from __future__ import annotations

import logging
import subprocess
from typing import List

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)


class DockerAdapter(ExecutionAdapter):
    """Execution adapter backed by the Docker socket."""

    @property
    def adapter_name(self) -> str:
        return "docker"

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 12, mode: str = "target") -> ExecResult:
        if mode == "host":
            cmd = ["sh", "-c", command]
            cmd_str = command
        else:
            cmd = ["docker", "exec", target, "sh", "-c", command]
            cmd_str = f"docker exec {target} sh -c '{command}'"
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return ExecResult(
                success=result.returncode == 0,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                returncode=result.returncode,
                command=cmd_str,
            )
        except subprocess.TimeoutExpired:
            return ExecResult.error(f"Timed out after {timeout}s", cmd_str)
        except Exception as exc:
            return ExecResult.error(str(exc), cmd_str)

    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        sig_flag = signal.replace("SIG", "")
        cmd = ["docker", "exec", target, "pkill", f"-{sig_flag}", process_name]
        cmd_str = " ".join(cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            # pkill exit 1 means no process found — goal is "not running" → success
            success = result.returncode in (0, 1)
            return ExecResult(
                success=success,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                returncode=result.returncode,
                command=cmd_str,
            )
        except Exception as exc:
            return ExecResult.error(str(exc), cmd_str)

    def check_process(self, target: str, process_name: str) -> dict:
        result = self.exec(
            target,
            f"ps ax -o s= -o comm= 2>/dev/null "
            f"| awk -v p='{process_name}' '$1!=\"Z\" && $2==p' | grep -q .",
            timeout=6,
        )
        return {"running": result.returncode == 0, "process_name": process_name}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        flags = ["-t", "0"] if force else []
        cmd = ["docker", "restart"] + flags + [target]
        cmd_str = " ".join(cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            return ExecResult(
                success=result.returncode == 0,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                returncode=result.returncode,
                command=cmd_str,
            )
        except Exception as exc:
            return ExecResult.error(str(exc), cmd_str)

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            return [l.strip() for l in result.stdout.splitlines() if l.strip()]
        except Exception:
            return []

    # ── Metrics (Docker-native — faster than the base-class SSH fallback) ──────

    def get_metrics(self, target: str) -> TargetMetrics:
        """Use docker stats for CPU/memory; docker exec df for disk."""
        m = TargetMetrics(target=target)
        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format",
                 "{{.CPUPerc}}\t{{.MemUsage}}", target],
                capture_output=True, text=True, timeout=8,
            )
            if result.returncode == 0 and result.stdout.strip():
                cpu_str, mem_str = result.stdout.strip().split("\t")
                m.cpu_percent = float(cpu_str.replace("%", ""))
                # "512MiB / 2GiB"
                parts = mem_str.split("/")
                m.memory_used_mb  = _parse_mem(parts[0].strip())
                m.memory_total_mb = _parse_mem(parts[1].strip())
                if m.memory_total_mb:
                    m.memory_percent = round(m.memory_used_mb / m.memory_total_mb * 100, 1)
        except Exception:
            pass
        # Disk via exec
        try:
            disk = self.exec(target,
                "df / 2>/dev/null | awk 'NR==2{gsub(/%/,\"\",$5); print $5, $3/1048576, $2/1048576}'",
                timeout=6)
            if disk.success:
                p = disk.stdout.strip().split()
                m.disk_percent  = float(p[0])
                m.disk_used_gb  = float(p[1])
                m.disk_total_gb = float(p[2])
        except Exception:
            pass
        return m

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, text=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            return False


def _parse_mem(s: str) -> float:
    """Convert '512MiB', '2GiB', '1.5GB' etc. to MB."""
    s = s.strip().upper()
    for suffix, factor in [("GIB", 1024), ("GB", 1000), ("MIB", 1), ("MB", 1), ("KIB", 1/1024)]:
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)]) * factor
            except ValueError:
                return 0.0
    return 0.0
