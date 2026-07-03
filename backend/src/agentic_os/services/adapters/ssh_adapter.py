"""
SSH Adapter — remote command execution via paramiko.

Supports Linux/Unix VMs and bare-metal hosts reachable by SSH.
Authentication: SSH private key (preferred) or password.

Environment variables consumed by AdapterFactory:
  WATCHER_SSH_HOST         target hostname or IP (single host mode)
  WATCHER_SSH_PORT         SSH port (default 22)
  WATCHER_SSH_USER         SSH username (default: root)
  WATCHER_SSH_KEY_PATH     path to private key file
  WATCHER_SSH_PASSWORD     password (if no key; stored in memory only)
  WATCHER_SSH_HOSTS_JSON   JSON list of {name, host, port, user, key_path}
                           for multi-host mode
  WATCHER_SSH_KNOWN_HOSTS  path to known_hosts file (default: auto-accept)
"""

from __future__ import annotations

import json
import logging
import os
import socket as _socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)


@dataclass
class SSHTarget:
    name: str
    host: str
    port: int = 22
    user: str = "root"
    key_path: Optional[str] = None
    password: Optional[str] = None


def _targets_from_env() -> List[SSHTarget]:
    """Build target list from environment variables."""
    # Multi-host JSON config takes priority
    hosts_json = os.environ.get("WATCHER_SSH_HOSTS_JSON", "")
    if hosts_json:
        try:
            entries = json.loads(hosts_json)
            return [
                SSHTarget(
                    name=e.get("name", e["host"]),
                    host=e["host"],
                    port=int(e.get("port", 22)),
                    user=e.get("user", "root"),
                    key_path=e.get("key_path"),
                    password=e.get("password"),
                )
                for e in entries
            ]
        except Exception as exc:
            logger.warning(f"[SSH] Could not parse WATCHER_SSH_HOSTS_JSON: {exc}")

    # Single-host env vars
    host = os.environ.get("WATCHER_SSH_HOST", "")
    if host:
        return [
            SSHTarget(
                name=os.environ.get("WATCHER_SSH_NAME", host),
                host=host,
                port=int(os.environ.get("WATCHER_SSH_PORT", "22")),
                user=os.environ.get("WATCHER_SSH_USER", "root"),
                key_path=os.environ.get("WATCHER_SSH_KEY_PATH"),
                password=os.environ.get("WATCHER_SSH_PASSWORD"),
            )
        ]
    return []


class SSHAdapter(ExecutionAdapter):
    """
    Execute commands on remote Linux/Unix hosts via SSH.

    Opens a fresh connection per call (connection pool not needed at
    watcher polling rates, and avoids stale-connection errors after
    network interruptions).
    """

    def __init__(self, targets: Optional[List[SSHTarget]] = None):
        self._targets: Dict[str, SSHTarget] = {}
        for t in (targets or _targets_from_env()):
            self._targets[t.name] = t
        logger.info(
            f"[SSH] Adapter initialised with {len(self._targets)} target(s): "
            f"{', '.join(self._targets)}"
        )

    @property
    def adapter_name(self) -> str:
        return "ssh"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self, target_name: str):
        """Return a connected paramiko SSHClient."""
        try:
            import paramiko
        except ImportError:
            raise RuntimeError(
                "paramiko not installed. Add it to requirements.txt: paramiko>=3.4"
            )
        target = self._targets[target_name]
        client = paramiko.SSHClient()

        known_hosts = os.environ.get("WATCHER_SSH_KNOWN_HOSTS", "")
        if known_hosts:
            client.load_host_keys(known_hosts)
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = {
            "hostname": target.host,
            "port": target.port,
            "username": target.user,
            "timeout": 10,
            "banner_timeout": 10,
        }
        if target.key_path:
            kwargs["key_filename"] = target.key_path
        elif target.password:
            kwargs["password"] = target.password
            kwargs["look_for_keys"] = False
            kwargs["allow_agent"] = False

        client.connect(**kwargs)
        return client

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 12, mode: str = "target") -> ExecResult:
        if target not in self._targets:
            return ExecResult.error(f"SSH target '{target}' not configured", command)
        try:
            client = self._connect(target)
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdout_str = stdout.read().decode(errors="replace").strip()
            stderr_str = stderr.read().decode(errors="replace").strip()
            rc = stdout.channel.recv_exit_status()
            client.close()
            return ExecResult(
                success=rc == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                returncode=rc,
                command=f"[ssh:{target}] {command}",
            )
        except Exception as exc:
            return ExecResult.error(str(exc), f"[ssh:{target}] {command}")

    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        sig_flag = signal.replace("SIG", "")
        return self.exec(target, f"pkill -{sig_flag} {process_name}", timeout=8)

    def check_process(self, target: str, process_name: str) -> dict:
        result = self.exec(target, f"pgrep -x '{process_name}' > /dev/null 2>&1", timeout=6)
        return {"running": result.returncode == 0, "process_name": process_name}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        # Try systemctl first, fall back to reboot
        if force:
            return self.exec(target, "sudo reboot -f", timeout=10)
        result = self.exec(target, "sudo systemctl daemon-reload 2>/dev/null; sudo reboot", timeout=10)
        return result

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        return list(self._targets.keys())

    # ── Metrics (native — psutil-style via /proc) ──────────────────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """Collect metrics via a single compound SSH command (one round-trip)."""
        m = TargetMetrics(target=target)
        script = (
            "echo CPU: $(top -bn1 2>/dev/null | awk '/Cpu/{print $2}' | tr -d '%us,');"
            "echo MEM: $(free -m 2>/dev/null | awk 'NR==2{printf \"%.1f %.0f %.0f\",$3/$2*100,$3,$2}');"
            "echo DISK: $(df / 2>/dev/null | awk 'NR==2{gsub(/%/,\"\",$5); printf \"%.1f %.3f %.3f\",$5,$3/1048576,$2/1048576}');"
            "echo LOAD: $(awk '{print $1}' /proc/loadavg 2>/dev/null)"
        )
        result = self.exec(target, script, timeout=12)
        if not result.success:
            return m
        for line in result.stdout.splitlines():
            if line.startswith("CPU:"):
                try: m.cpu_percent = float(line[4:].strip())
                except Exception: pass
            elif line.startswith("MEM:"):
                try:
                    parts = line[4:].strip().split()
                    m.memory_percent  = float(parts[0])
                    m.memory_used_mb  = float(parts[1])
                    m.memory_total_mb = float(parts[2])
                except Exception: pass
            elif line.startswith("DISK:"):
                try:
                    parts = line[5:].strip().split()
                    m.disk_percent  = float(parts[0])
                    m.disk_used_gb  = float(parts[1])
                    m.disk_total_gb = float(parts[2])
                except Exception: pass
            elif line.startswith("LOAD:"):
                try: m.load_avg_1m = float(line[5:].strip())
                except Exception: pass
        return m

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        for name, target in self._targets.items():
            try:
                sock = _socket.create_connection((target.host, target.port), timeout=3)
                sock.close()
                return True
            except Exception:
                pass
        return False
