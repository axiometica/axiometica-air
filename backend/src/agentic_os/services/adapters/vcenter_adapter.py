"""
VMware vCenter Adapter — Guest Operations API (agentless VM management).

Uses the VMware vSphere Automation SDK (pyvmomi) to execute commands
inside VMs through the vCenter control plane.  No SSH or direct network
access to individual VMs is required — the only network path needed is
watcher → vCenter HTTPS (port 443).

Prerequisites:
  - pyvmomi installed (pip install pyvmomi)
  - VMware Tools running inside each guest VM
  - vCenter service account with "Guest Operations" privilege

Environment variables consumed by AdapterFactory:
  VCENTER_HOST              vCenter FQDN or IP
  VCENTER_USER              service account (e.g. watcher@vsphere.local)
  VCENTER_PASSWORD          password
  VCENTER_GUEST_USER        in-guest OS username (e.g. root or Administrator)
  VCENTER_GUEST_PASSWORD    in-guest OS password
  VCENTER_VM_NAMES          comma-separated VM names to monitor (empty = all powered-on)
  VCENTER_DATACENTER        datacenter name filter (optional)
  VCENTER_IGNORE_SSL        true/false (default true — self-signed certs common)
  VCENTER_PORT              vCenter port (default 443)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Dict, List, Optional

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)


class vCenterAdapter(ExecutionAdapter):
    """
    Execute commands inside VMware guest VMs via vCenter Guest Operations.

    Execution flow for exec():
      1. Stage output to /tmp/watcher_<uuid>.out inside the VM
      2. Start the process via GuestProcessManager.StartProgramInGuest()
      3. Poll until the process exits (or timeout)
      4. Download output via GuestFileManager.InitiateFileTransferFromGuest()
      5. Clean up temp file
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        guest_user: str,
        guest_password: str,
        vm_names: Optional[List[str]] = None,
        datacenter: Optional[str] = None,
        port: int = 443,
        ignore_ssl: bool = True,
    ):
        self.host           = host
        self.user           = user
        self.password       = password
        self.guest_user     = guest_user
        self.guest_password = guest_password
        self.vm_names       = vm_names      # None → discover all powered-on VMs
        self.datacenter     = datacenter
        self.port           = port
        self.ignore_ssl     = ignore_ssl
        self._si            = None          # vSphere ServiceInstance
        self._vm_cache: Dict[str, object] = {}
        self._connect()

    # ── Class method for env-var construction ─────────────────────────────────

    @classmethod
    def from_env(cls) -> "vCenterAdapter":
        vm_names_raw = os.environ.get("VCENTER_VM_NAMES", "")
        return cls(
            host          = os.environ["VCENTER_HOST"],
            user          = os.environ["VCENTER_USER"],
            password      = os.environ["VCENTER_PASSWORD"],
            guest_user    = os.environ.get("VCENTER_GUEST_USER", "root"),
            guest_password= os.environ.get("VCENTER_GUEST_PASSWORD", ""),
            vm_names      = [v.strip() for v in vm_names_raw.split(",") if v.strip()] or None,
            datacenter    = os.environ.get("VCENTER_DATACENTER"),
            port          = int(os.environ.get("VCENTER_PORT", "443")),
            ignore_ssl    = os.environ.get("VCENTER_IGNORE_SSL", "true").lower() == "true",
        )

    @property
    def adapter_name(self) -> str:
        return "vcenter"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _connect(self) -> None:
        try:
            from pyVim.connect import SmartConnect
        except ImportError:
            raise RuntimeError(
                "pyvmomi not installed. Add it to requirements.txt: pyvmomi>=8.0"
            )
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if self.ignore_ssl:
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        try:
            self._si = SmartConnect(
                host=self.host, user=self.user, pwd=self.password,
                port=self.port, sslContext=ctx,
            )
            logger.info(f"[vCenter] Connected to {self.host} as {self.user}")
        except Exception as exc:
            raise RuntimeError(f"Cannot connect to vCenter {self.host}: {exc}") from exc

    def _content(self):
        return self._si.RetrieveContent()

    def _get_vm(self, vm_name: str):
        if vm_name in self._vm_cache:
            return self._vm_cache[vm_name]
        from pyVmomi import vim
        container = self._content().viewManager.CreateContainerView(
            self._content().rootFolder, [vim.VirtualMachine], True
        )
        for vm in container.view:
            if vm.name == vm_name:
                self._vm_cache[vm_name] = vm
                container.Destroy()
                return vm
        container.Destroy()
        raise ValueError(f"VM '{vm_name}' not found in vCenter")

    def _guest_creds(self):
        from pyVmomi import vim
        return vim.vm.guest.NamePasswordAuthentication(
            username=self.guest_user,
            password=self.guest_password,
        )

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 30, mode: str = "target") -> ExecResult:
        """
        Run a shell command inside the VM via GuestProcessManager.
        Output is captured via a temp file and GuestFileManager download.
        """
        try:
            import urllib.request
            from pyVmomi import vim

            vm      = self._get_vm(target)
            content = self._content()
            pm      = content.guestOperationsManager.processManager
            fm      = content.guestOperationsManager.fileManager
            creds   = self._guest_creds()

            out_path = f"/tmp/.watcher_{uuid.uuid4().hex[:8]}.out"
            # Wrap command to capture both stdout and exit code
            wrapped = f"{command} > {out_path} 2>&1; echo $? >> {out_path}"

            spec = vim.vm.guest.ProcessManager.ProgramSpec(
                programPath="/bin/sh",
                arguments=f"-c '{wrapped}'",
                workingDirectory="/tmp",
            )
            pid = pm.StartProgramInGuest(vm, creds, spec)

            # Poll until process exits
            deadline = time.time() + timeout
            exit_code: Optional[int] = None
            while time.time() < deadline:
                try:
                    procs = pm.ListProcessesInGuest(vm, creds, [pid])
                    if procs and procs[0].exitCode is not None:
                        exit_code = procs[0].exitCode
                        break
                except Exception:
                    pass
                time.sleep(0.75)

            # Download output file
            raw_output = ""
            try:
                transfer = fm.InitiateFileTransferFromGuest(vm, creds, out_path)
                raw_output = urllib.request.urlopen(transfer.url).read().decode(errors="replace")
            except Exception as dl_err:
                raw_output = f"[output download failed: {dl_err}]"
            finally:
                try:
                    fm.DeleteFileInGuest(vm, creds, out_path)
                except Exception:
                    pass

            lines = raw_output.strip().splitlines()
            # Last line is the exit code we appended
            if lines and lines[-1].strip().isdigit():
                rc      = int(lines[-1].strip())
                stdout  = "\n".join(lines[:-1])
            else:
                rc      = exit_code if exit_code is not None else -1
                stdout  = raw_output.strip()

            return ExecResult(
                success=rc == 0,
                stdout=stdout,
                stderr="",
                returncode=rc,
                command=f"[vcenter:{target}] {command}",
            )
        except Exception as exc:
            return ExecResult.error(str(exc), f"[vcenter:{target}] {command}")

    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        """
        Terminate processes by name via GuestProcessManager.TerminateProcessInGuest().
        Falls back to pkill via exec() for SIGTERM/SIGINT.
        """
        if signal not in ("SIGKILL", "KILL"):
            # For graceful signals, use pkill via exec
            sig_flag = signal.replace("SIG", "")
            return self.exec(target, f"pkill -{sig_flag} {process_name}", timeout=8)

        try:
            from pyVmomi import vim
            vm      = self._get_vm(target)
            content = self._content()
            pm      = content.guestOperationsManager.processManager
            creds   = self._guest_creds()

            procs   = pm.ListProcessesInGuest(vm, creds)
            killed  = 0
            for proc in procs:
                if proc.name == process_name and proc.exitCode is None:
                    try:
                        pm.TerminateProcessInGuest(vm, creds, proc.pid)
                        killed += 1
                    except Exception:
                        pass

            msg = (
                f"Terminated {killed} process(es) named '{process_name}' on VM '{target}'"
                if killed
                else f"No running process named '{process_name}' found on VM '{target}'"
            )
            return ExecResult(
                success=killed > 0,
                stdout=msg, stderr="",
                returncode=0 if killed > 0 else 1,
                command=f"TerminateProcessInGuest:{process_name}@{target}",
            )
        except Exception as exc:
            return ExecResult.error(str(exc), f"vcenter:kill:{process_name}@{target}")

    def check_process(self, target: str, process_name: str) -> dict:
        """List processes via GuestProcessManager and search by name."""
        try:
            from pyVmomi import vim
            vm      = self._get_vm(target)
            pm      = self._content().guestOperationsManager.processManager
            creds   = self._guest_creds()
            procs   = pm.ListProcessesInGuest(vm, creds)
            running = any(p.name == process_name and p.exitCode is None for p in procs)
            return {"running": running, "process_name": process_name}
        except Exception as exc:
            return {"running": False, "process_name": process_name, "error": str(exc)}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        """Reboot VM via vCenter power operations."""
        try:
            vm = self._get_vm(target)
            if force:
                task = vm.ResetVM_Task()
                msg  = f"VM '{target}' hard reset (ResetVM) initiated via vCenter"
            else:
                vm.RebootGuest()
                msg  = f"VM '{target}' graceful reboot (RebootGuest) initiated via vCenter"
            return ExecResult(True, msg, "", 0, f"vCenter:restart:{target}")
        except Exception as exc:
            return ExecResult.error(str(exc), f"vCenter:restart:{target}")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        if self.vm_names:
            return self.vm_names
        try:
            from pyVmomi import vim
            content   = self._content()
            container = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.VirtualMachine], True
            )
            powered_on = vim.VirtualMachine.PowerState.poweredOn
            names = [vm.name for vm in container.view if vm.runtime.powerState == powered_on]
            container.Destroy()
            return names
        except Exception as exc:
            logger.warning(f"[vCenter] list_targets failed: {exc}")
            return []

    # ── Metrics (native — vCenter quickStats, no exec needed) ─────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """
        Pull CPU and memory metrics from vCenter's in-memory quickStats.
        No guest exec required — instant and low-overhead.
        Disk requires a guest exec to df (quickStats has no per-disk usage).
        """
        m = TargetMetrics(target=target)
        try:
            vm       = self._get_vm(target)
            summary  = vm.summary
            qs       = summary.quickStats
            cfg      = summary.config

            cpu_mhz          = qs.overallCpuUsage or 0
            mem_used_mb       = qs.guestMemoryUsage or 0
            mem_total_mb      = cfg.memorySizeMB or 1
            num_cpu           = cfg.numCpu or 1
            # Estimate total MHz assuming ~2 GHz per vCPU (conservative)
            total_cpu_mhz     = num_cpu * 2000
            m.cpu_percent     = round(min(cpu_mhz / total_cpu_mhz * 100, 100.0), 1)
            m.memory_used_mb  = mem_used_mb
            m.memory_total_mb = mem_total_mb
            m.memory_percent  = round(mem_used_mb / mem_total_mb * 100, 1)
            m.extra           = {
                "cpu_mhz": cpu_mhz,
                "vm_power_state": str(vm.runtime.powerState),
                "vmware_tools": str(vm.guest.toolsStatus if vm.guest else "unknown"),
            }
        except Exception as exc:
            logger.debug(f"[vCenter] get_metrics({target}) failed: {exc}")

        # Disk from guest exec (optional, skip if tools not running)
        try:
            disk = self.exec(
                target,
                "df / 2>/dev/null | awk 'NR==2{gsub(/%/,\"\",$5); printf \"%.1f %.3f %.3f\",$5,$3/1048576,$2/1048576}'",
                timeout=15,
            )
            if disk.success:
                parts = disk.stdout.strip().split()
                m.disk_percent  = float(parts[0])
                m.disk_used_gb  = float(parts[1])
                m.disk_total_gb = float(parts[2])
        except Exception:
            pass

        return m

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            self._content()
            return True
        except Exception:
            try:
                self._connect()
                return True
            except Exception:
                return False
