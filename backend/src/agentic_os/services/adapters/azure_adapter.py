"""
Azure VM Adapter — Run Command API (agentless).

Executes commands on Azure Virtual Machines via the Azure Run Command API.
No SSH or open inbound ports required — all traffic flows through the
Azure control plane (HTTPS 443 to management.azure.com).

This mirrors the AWS SSM adapter pattern: the Azure VM agent (pre-installed
on all Azure marketplace images) receives commands via the control plane and
returns output.

Prerequisites:
  - azure-mgmt-compute, azure-mgmt-monitor, azure-identity installed
  - Azure VM Agent running on each target VM (pre-installed on all
    Azure marketplace images — Windows and Linux)
  - Service principal with "Virtual Machine Contributor" role on the
    resource group (or subscription)

Environment variables:
  AZURE_SUBSCRIPTION_ID     Azure subscription ID (required)
  AZURE_RESOURCE_GROUP      Resource group containing the VMs (required)
  AZURE_TENANT_ID           Service principal tenant (required)
  AZURE_CLIENT_ID           Service principal application ID (required)
  AZURE_CLIENT_SECRET       Service principal secret (required)
  AZURE_VM_NAMES            Comma-separated VM names to monitor (empty = all)
  AZURE_OS_TYPE             linux (default) or windows
  AZURE_USE_MONITOR         true/false — use Azure Monitor for metrics (default true)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Dict, List, Optional

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)

_SUBSCRIPTION  = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "")
_OS_TYPE       = os.environ.get("AZURE_OS_TYPE", "linux").lower()
_USE_MONITOR   = os.environ.get("AZURE_USE_MONITOR", "true").lower() == "true"


class AzureAdapter(ExecutionAdapter):
    """
    Execute commands on Azure VMs via the Run Command API.

    Execution flow for exec():
      1. POST /runCommand to the Azure Compute API (async LRO)
      2. Poll the operation until complete (default timeout 60 s)
      3. Return stdout/stderr from the RunCommandResult

    Targets are Azure VM names within the configured resource group.
    """

    def __init__(
        self,
        subscription_id: str = _SUBSCRIPTION,
        resource_group: str = _RESOURCE_GROUP,
        vm_names: Optional[List[str]] = None,
        os_type: str = _OS_TYPE,
        use_monitor: bool = _USE_MONITOR,
    ):
        self.subscription_id = subscription_id
        self.resource_group  = resource_group
        self.vm_names        = vm_names or self._names_from_env()
        self.os_type         = os_type
        self.use_monitor     = use_monitor
        self._compute        = None
        self._monitor        = None
        self._credential     = None
        self._init_clients()

    @classmethod
    def from_env(cls) -> "AzureAdapter":
        return cls()

    def _names_from_env(self) -> List[str]:
        raw = os.environ.get("AZURE_VM_NAMES", "")
        return [n.strip() for n in raw.split(",") if n.strip()] if raw else []

    def _init_clients(self) -> None:
        try:
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient

            # Prefer explicit service principal creds; fall back to DefaultAzureCredential
            # (works with managed identities, env vars, CLI login, etc.)
            tenant = os.environ.get("AZURE_TENANT_ID")
            client = os.environ.get("AZURE_CLIENT_ID")
            secret = os.environ.get("AZURE_CLIENT_SECRET")

            if tenant and client and secret:
                self._credential = ClientSecretCredential(
                    tenant_id=tenant, client_id=client, client_secret=secret
                )
                logger.info("[AZURE] Authenticated with service principal")
            else:
                self._credential = DefaultAzureCredential()
                logger.info("[AZURE] Authenticated with DefaultAzureCredential")

            self._compute = ComputeManagementClient(self._credential, self.subscription_id)

            if self.use_monitor:
                try:
                    from azure.mgmt.monitor import MonitorManagementClient
                    self._monitor = MonitorManagementClient(self._credential, self.subscription_id)
                except ImportError:
                    logger.warning("[AZURE] azure-mgmt-monitor not installed — metrics will use fallback")

            logger.info(
                f"[AZURE] Adapter initialised — subscription={self.subscription_id[:8]}… "
                f"resource_group={self.resource_group} os={self.os_type}"
            )
        except ImportError:
            logger.error(
                "[AZURE] azure-mgmt-compute / azure-identity not installed. "
                "Run: pip install azure-mgmt-compute azure-mgmt-monitor azure-identity"
            )
            raise
        except Exception as exc:
            logger.error(f"[AZURE] Failed to initialise clients: {exc}")
            raise

    # ── Core identifier ───────────────────────────────────────────────────────

    @property
    def adapter_name(self) -> str:
        return "azure"

    # ── Target discovery ──────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        """Return names of running Azure VMs in the resource group."""
        try:
            if self.vm_names:
                return self.vm_names

            vms = self._compute.virtual_machines.list(self.resource_group)
            names = []
            for vm in vms:
                # Check power state
                try:
                    iv = self._compute.virtual_machines.instance_view(
                        self.resource_group, vm.name
                    )
                    statuses = {s.code for s in (iv.statuses or [])}
                    if "PowerState/running" in statuses:
                        names.append(vm.name)
                except Exception:
                    names.append(vm.name)  # include if status check fails
            logger.info(f"[AZURE] Discovered {len(names)} running VM(s): {names}")
            return names
        except Exception as exc:
            logger.warning(f"[AZURE] list_targets failed: {exc}")
            return []

    # ── Command execution ─────────────────────────────────────────────────────

    def exec(
        self,
        target: str,
        command: str,
        timeout: int = 60,
        mode: str = "target",
    ) -> ExecResult:
        """
        Run a shell command on an Azure VM via Run Command.

        mode="target"  — runs inside the VM (the normal case)
        mode="host"    — runs on the watcher container itself (for local ops)
        """
        if mode == "host":
            import subprocess
            try:
                result = subprocess.run(
                    ["sh", "-c", command],
                    capture_output=True, text=True, timeout=timeout
                )
                return ExecResult(
                    success=result.returncode == 0,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                    command=command,
                )
            except Exception as exc:
                return ExecResult.error(str(exc), command)

        # mode="target" — Azure Run Command
        cmd_type = "RunPowerShellScript" if self.os_type == "windows" else "RunShellScript"
        try:
            from azure.mgmt.compute.models import RunCommandInput
        except ImportError:
            return ExecResult.error("azure-mgmt-compute not installed", command)

        try:
            run_cmd = RunCommandInput(command_type=cmd_type, script=[command])
            poller = self._compute.virtual_machines.begin_run_command(
                self.resource_group, target, run_cmd
            )
            result = poller.result(timeout=timeout)

            stdout = ""
            stderr = ""
            if result and result.value:
                for msg in result.value:
                    if msg.code == "ComponentStatus/StdOut/succeeded":
                        stdout = msg.message or ""
                    elif msg.code == "ComponentStatus/StdErr/succeeded":
                        stderr = msg.message or ""

            success = result is not None
            logger.debug(
                f"[AZURE] exec '{target}' → {'OK' if success else 'FAIL'} "
                f"stdout={len(stdout)}b stderr={len(stderr)}b"
            )
            return ExecResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                returncode=0 if success else 1,
                command=command,
                raw_output=(stdout + "\n" + stderr).strip() or None,
            )
        except Exception as exc:
            logger.error(f"[AZURE] Run Command failed on '{target}': {exc}")
            return ExecResult.error(str(exc), command)

    # ── Process management ────────────────────────────────────────────────────

    def kill_process(
        self, target: str, process_name: str, signal: str = "SIGKILL"
    ) -> ExecResult:
        if self.os_type == "windows":
            command = f"Stop-Process -Name '{process_name}' -Force"
        else:
            sig_num = {"SIGKILL": "9", "SIGTERM": "15", "SIGHUP": "1"}.get(signal, "9")
            command = f"kill -{sig_num} $(pgrep {process_name})"
        return self.exec(target, command, mode="target")

    def check_process(self, target: str, process_name: str) -> dict:
        if self.os_type == "windows":
            command = f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue"
        else:
            command = f"pgrep {process_name} && echo RUNNING || echo STOPPED"
        result = self.exec(target, command, timeout=20, mode="target")
        running = result.success and (
            "RUNNING" in result.stdout or
            (self.os_type == "windows" and result.stdout.strip())
        )
        return {"running": running, "process_name": process_name}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        """Restart the Azure VM via the management API."""
        try:
            logger.info(f"[AZURE] Restarting VM '{target}' (force={force})")
            poller = self._compute.virtual_machines.begin_restart(
                self.resource_group, target
            )
            poller.result(timeout=300)
            return ExecResult(
                success=True, stdout=f"VM '{target}' restarted",
                stderr="", returncode=0,
                command=f"azure_restart({target})",
            )
        except Exception as exc:
            logger.error(f"[AZURE] restart_target failed: {exc}")
            return ExecResult.error(str(exc), f"azure_restart({target})")

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """
        Collect CPU / memory metrics.
        Tries Azure Monitor first (requires CloudWatch Agent equivalent — Azure Monitor Agent).
        Falls back to executing shell commands inside the VM.
        """
        m = TargetMetrics(target=target)

        if self.use_monitor and self._monitor:
            try:
                m = self._get_metrics_from_monitor(target, m)
                if m.cpu_percent > 0:
                    return m
            except Exception as exc:
                logger.debug(f"[AZURE] Monitor metrics fallback for '{target}': {exc}")

        # Fallback: exec inside VM
        return super().get_metrics(target)

    def _get_metrics_from_monitor(self, target: str, m: TargetMetrics) -> TargetMetrics:
        """Query Azure Monitor for Percentage CPU metric."""
        from datetime import datetime, timedelta, timezone

        end   = datetime.now(timezone.utc)
        start = end - timedelta(minutes=5)
        timespan = f"{start.isoformat()}/{end.isoformat()}"

        resource_id = (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{target}"
        )

        result = self._monitor.metrics.list(
            resource_id,
            timespan=timespan,
            interval="PT1M",
            metricnames="Percentage CPU",
            aggregation="Average",
        )
        for metric in result.value:
            for ts in metric.timeseries:
                for dp in reversed(ts.data or []):
                    if dp.average is not None:
                        m.cpu_percent = round(dp.average, 1)
                        break
        return m
