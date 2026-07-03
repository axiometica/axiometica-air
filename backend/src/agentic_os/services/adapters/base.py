"""
Execution adapter base class.

Every adapter implements the same interface so WatcherService and the Kill-API
can call exec / kill / restart / list_targets without knowing the underlying
transport (Docker socket, SSH, vCenter Guest Ops, kubectl, …).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ExecResult:
    """Normalised result from any exec call."""
    success: bool
    stdout: str
    stderr: str
    returncode: int
    command: str
    raw_output: Optional[str] = None  # combined stdout+stderr for UI display

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "command": self.command,
            "raw_output": self.raw_output or "\n".join(
                filter(None, [self.stdout, self.stderr])
            ) or None,
        }

    @classmethod
    def error(cls, message: str, command: str = "") -> "ExecResult":
        return cls(
            success=False, stdout="", stderr=message,
            returncode=-1, command=command
        )


@dataclass
class TargetMetrics:
    """Normalised system metrics from any target."""
    target: str
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    load_avg_1m: float = 0.0
    extra: dict = field(default_factory=dict)


class ExecutionAdapter(ABC):
    """
    Abstract base for environment-specific command execution.

    Implementations:
      DockerAdapter   — Docker socket (default, current behaviour)
      SSHAdapter      — paramiko SSH to remote VMs / bare metal
      vCenterAdapter  — VMware vCenter Guest Operations API
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Short identifier shown in logs and registration record."""

    # ── Core execution ────────────────────────────────────────────────────────

    @abstractmethod
    def exec(self, target: str, command: str,
             timeout: int = 12, mode: str = "target") -> ExecResult:
        """
        Run a shell command on/in the target.

        mode="target"  run inside the target (container exec / SSH / Guest Ops)
        mode="host"    run on the watcher host itself (docker/kubectl on PATH)
        """

    @abstractmethod
    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        """Send signal to a named process."""

    @abstractmethod
    def check_process(self, target: str, process_name: str) -> dict:
        """Return {"running": bool, "process_name": str}."""

    @abstractmethod
    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        """Restart the target (container restart / VM reboot / service restart)."""

    # ── Discovery ─────────────────────────────────────────────────────────────

    @abstractmethod
    def list_targets(self) -> List[str]:
        """Return names of all reachable targets."""

    # ── Metrics (optional — override in subclasses for richer data) ───────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """Collect basic CPU/memory/disk metrics. Override for native efficiency."""
        cpu = self.exec(target,
            "top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2}' | tr -d '%us,'",
            timeout=8)
        mem = self.exec(target,
            "free -m 2>/dev/null | awk 'NR==2{printf \"%.1f %.0f %.0f\", $3/$2*100, $3, $2}'",
            timeout=8)
        disk = self.exec(target,
            "df / 2>/dev/null | awk 'NR==2{gsub(/%/,\"\",$5); print $5, $3/1048576, $2/1048576}'",
            timeout=8)

        m = TargetMetrics(target=target)
        try:
            m.cpu_percent = float(cpu.stdout.strip()) if cpu.success else 0.0
        except Exception:
            pass
        try:
            parts = mem.stdout.strip().split()
            m.memory_percent = float(parts[0])
            m.memory_used_mb  = float(parts[1])
            m.memory_total_mb = float(parts[2])
        except Exception:
            pass
        try:
            parts = disk.stdout.strip().split()
            m.disk_percent  = float(parts[0])
            m.disk_used_gb  = float(parts[1])
            m.disk_total_gb = float(parts[2])
        except Exception:
            pass
        return m

    # ── Port / process helpers ────────────────────────────────────────────────

    def detect_port_process(self, target: str, port: int) -> dict:
        """Find the process listening on a port (best-effort)."""
        result = self.exec(
            target,
            f"ss -tlnp 2>/dev/null | grep ':{port} ' | head -1 || "
            f"netstat -tlnp 2>/dev/null | grep ':{port} ' | head -1",
            timeout=8,
        )
        return {
            "found": result.success and bool(result.stdout.strip()),
            "output": result.stdout.strip(),
            "port": port,
        }

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Quick connectivity check. Override for efficiency."""
        try:
            return len(self.list_targets()) >= 0
        except Exception:
            return False
