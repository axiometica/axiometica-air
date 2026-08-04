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
  WATCHER_SSH_KNOWN_HOSTS       path to known_hosts file (required unless strict mode disabled)
  WATCHER_SSH_STRICT_HOST_KEYS  "true" (default) or "false" to skip host-key verification
"""

from __future__ import annotations

import json
import logging
import os
import socket as _socket
import time
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
    credential_name: Optional[str] = None


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


@dataclass
class _ApiCredential:
    """Lightweight credential object returned by the platform API resolve endpoint."""
    name: str
    username: str
    port: int
    private_key: str


class SSHAdapter(ExecutionAdapter):
    """
    Execute commands on remote Linux/Unix hosts via SSH.

    Opens a fresh connection per call (connection pool not needed at
    watcher polling rates, and avoids stale-connection errors after
    network interruptions).
    """

    _CREDENTIAL_CACHE_TTL = 300  # re-resolve every 5 minutes

    def __init__(self, targets: Optional[List[SSHTarget]] = None):
        self._targets: Dict[str, SSHTarget] = {}
        for t in (targets or _targets_from_env()):
            self._targets[t.name] = t
        self._credential_cache: Dict[str, tuple] = {}  # hostname → (credential, timestamp)
        logger.info(
            f"[SSH] Adapter initialised with {len(self._targets)} target(s): "
            f"{', '.join(self._targets)}"
        )

    @property
    def adapter_name(self) -> str:
        return "ssh"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_credential(self, hostname: str, credential_name: Optional[str] = None):
        """Look up an SSH credential with TTL cache to avoid per-exec API calls."""
        cache_key = credential_name or hostname
        cached = self._credential_cache.get(cache_key)
        if cached:
            cred, ts = cached
            if time.monotonic() - ts < self._CREDENTIAL_CACHE_TTL:
                return cred
        result = self._resolve_credential_uncached(hostname, credential_name)
        if result:
            self._credential_cache[cache_key] = (result, time.monotonic())
        return result

    def _resolve_credential_uncached(self, hostname: str, credential_name: Optional[str] = None):
        """Look up an SSH credential — tries direct DB first, then platform API."""
        # Path 1: direct DB query (works when co-located with the backend)
        try:
            from agentic_os.db.database import SessionLocal
            from agentic_os.api.routes.ssh_credentials import resolve_credential
            from agentic_os.db.models import SSHCredentialModel
            db = SessionLocal()
            try:
                cred = None
                if credential_name:
                    cred = db.query(SSHCredentialModel).filter_by(
                        name=credential_name, enabled=True,
                    ).first()
                if not cred:
                    cred = resolve_credential(hostname, db)
                if cred:
                    logger.info(f"[SSH] Resolved DB credential '{cred.name}' for host '{hostname}'")
                    return cred
            finally:
                db.close()
        except Exception as exc:
            logger.debug(f"[SSH] DB credential lookup unavailable: {exc}")

        # Path 2: resolve via platform API (remote watchers)
        api_url = os.environ.get("WATCHER_API_URL", "")
        api_key = os.environ.get("WATCHER_API_KEY", "")
        if api_url:
            try:
                import httpx
                headers = {"X-API-Key": api_key} if api_key else {}
                body: dict = {"hostname": hostname}
                if credential_name:
                    body["credential_name"] = credential_name
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(
                        f"{api_url}/api/settings/ssh-credentials/resolve",
                        json=body,
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("found"):
                            logger.info(f"[SSH] Resolved API credential '{data['name']}' for host '{hostname}'")
                            return _ApiCredential(
                                name=data["name"],
                                username=data["username"],
                                port=data["port"],
                                private_key=data["private_key"],
                            )
            except Exception as exc:
                logger.debug(f"[SSH] API credential lookup failed: {exc}")

        return None

    def _connect(self, target_name: str):
        """Return a connected paramiko SSHClient."""
        try:
            import paramiko
            import io
        except ImportError:
            raise RuntimeError(
                "paramiko not installed. Add it to requirements.txt: paramiko>=3.4"
            )
        target = self._targets[target_name]
        client = paramiko.SSHClient()

        strict = os.environ.get("WATCHER_SSH_STRICT_HOST_KEYS", "true").lower() != "false"
        known_hosts = os.environ.get("WATCHER_SSH_KNOWN_HOSTS", "")
        if known_hosts:
            client.load_host_keys(known_hosts)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif strict:
            raise RuntimeError(
                "WATCHER_SSH_KNOWN_HOSTS is not set. Provide a known_hosts file "
                "or set WATCHER_SSH_STRICT_HOST_KEYS=false to skip host-key verification."
            )
        else:
            logger.warning("[SSH] Host-key verification disabled (WATCHER_SSH_STRICT_HOST_KEYS=false)")
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
        else:
            # No credentials from env — try credential store (DB or API)
            db_cred = self._resolve_credential(target.host, target.credential_name)
            if db_cred:
                kwargs["username"] = db_cred.username
                kwargs["port"] = db_cred.port
                kwargs["pkey"] = paramiko.RSAKey.from_private_key(
                    io.StringIO(db_cred.private_key)
                )
            else:
                logger.warning(
                    f"[SSH] No credentials for '{target_name}' — "
                    f"attempting agent/default key"
                )

        client.connect(**kwargs)
        return client

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 12, mode: str = "target") -> ExecResult:
        if target not in self._targets:
            return ExecResult.error(f"SSH target '{target}' not configured", command)

        # Strip redundant ssh prefix — paramiko already handles the SSH transport.
        # Command variants like "ssh {target} sh -c '...'" are written for Docker
        # adapters that shell out; here we just need the inner command.
        import re as _re
        _ssh_prefix = _re.match(r'^ssh\s+\S+\s+', command)
        if _ssh_prefix:
            command = command[_ssh_prefix.end():]
            logger.debug(f"[SSH] Stripped ssh prefix, running: {command}")

        client = None
        try:
            client = self._connect(target)
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdout_str = stdout.read().decode(errors="replace").strip()
            stderr_str = stderr.read().decode(errors="replace").strip()
            rc = stdout.channel.recv_exit_status()
            return ExecResult(
                success=rc == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                returncode=rc,
                command=f"[ssh:{target}] {command}",
            )
        except Exception as exc:
            return ExecResult.error(str(exc), f"[ssh:{target}] {command}")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

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

    def refresh_targets(self, api_targets: List[dict]):
        """Replace target list with platform-managed targets from the API.

        api_targets: list of {name, host, port, credential_name} dicts
        from GET /api/monitoring/watchers/{id}/targets/active.
        Env-var targets not present in the API response are preserved as
        a bootstrap fallback.
        """
        env_targets = _targets_from_env()
        env_hosts = {(t.host, t.port) for t in env_targets}

        new_targets: Dict[str, SSHTarget] = {}
        for t in api_targets:
            name = t.get("name") or t["host"]
            new_targets[name] = SSHTarget(
                name=name,
                host=t["host"],
                port=t.get("port", 22),
                credential_name=t.get("credential_name"),
            )
        for t in env_targets:
            if (t.host, t.port) not in {(a["host"], a.get("port", 22)) for a in api_targets}:
                new_targets[t.name] = t

        added = set(new_targets) - set(self._targets)
        removed = set(self._targets) - set(new_targets)
        if added or removed:
            logger.info(
                f"[SSH] Target refresh: {len(new_targets)} total "
                f"(+{len(added)} added, -{len(removed)} removed)"
            )
        self._targets = new_targets

    # ── Metrics (native — psutil-style via /proc) ──────────────────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """Collect metrics via a single compound SSH command (one round-trip)."""
        m = TargetMetrics(target=target)
        script = (
            "echo CPU: $(ps -eo pcpu --no-headers 2>/dev/null | awk '{s+=$1}END{if(NR>0)printf \"%.1f\",s; else print \"0.0\"}');"
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
