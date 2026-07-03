"""
AWS SSM Adapter — Systems Manager Run Command + CloudWatch Metrics.

Executes commands on EC2 instances via AWS SSM Run Command without
requiring SSH or open inbound ports.  All traffic flows through the
AWS control plane (SSM endpoint).

Prerequisites:
  - boto3 installed
  - SSM Agent running on the EC2 instance (pre-installed on Amazon Linux 2/2023,
    Ubuntu 16.04+, Windows Server 2016+)
  - IAM instance profile with AmazonSSMManagedInstanceCore policy
  - Watcher IAM role/user with ssm:SendCommand + ssm:GetCommandInvocation

Environment variables:
  AWS_REGION               AWS region           (default: us-east-1)
  AWS_PROFILE              AWS CLI profile      (optional)
  WATCHER_SSM_INSTANCE_IDS Comma-separated EC2 instance IDs  (i-xxx,i-yyy)
                           OR use WATCHER_SSM_TAG_KEY/TAG_VALUE to auto-discover
  WATCHER_SSM_TAG_KEY      EC2 tag key for auto-discovery     (e.g. Environment)
  WATCHER_SSM_TAG_VALUE    EC2 tag value for auto-discovery   (e.g. production)
  WATCHER_SSM_OS           linux (default) or windows
  WATCHER_CLOUDWATCH       true/false — use CloudWatch for CPU/memory (default true)
  WATCHER_CW_NAMESPACE     CloudWatch namespace (default: CWAgent)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Dict, List, Optional

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)

_REGION     = os.environ.get("AWS_REGION", "us-east-1")
_OS         = os.environ.get("WATCHER_SSM_OS", "linux")
_USE_CW     = os.environ.get("WATCHER_CLOUDWATCH", "true").lower() == "true"
_CW_NS      = os.environ.get("WATCHER_CW_NAMESPACE", "CWAgent")


class AWSSsmAdapter(ExecutionAdapter):
    """
    Execute commands on EC2 instances via AWS SSM Run Command.

    Targets are EC2 instance IDs (i-0123456789abcdef0).
    Auto-discovery via EC2 tags is supported.
    CloudWatch Agent metrics are used for CPU/memory if available.
    """

    def __init__(
        self,
        instance_ids: Optional[List[str]] = None,
        region: str = _REGION,
        os_type: str = _OS,
        use_cloudwatch: bool = _USE_CW,
        cw_namespace: str = _CW_NS,
        tag_key: Optional[str] = None,
        tag_value: Optional[str] = None,
    ):
        self.region          = region
        self.os_type         = os_type
        self.use_cloudwatch  = use_cloudwatch
        self.cw_namespace    = cw_namespace
        self.tag_key         = tag_key or os.environ.get("WATCHER_SSM_TAG_KEY")
        self.tag_value       = tag_value or os.environ.get("WATCHER_SSM_TAG_VALUE")
        self._explicit_ids   = instance_ids or self._ids_from_env()
        self._ssm            = None
        self._ec2            = None
        self._cw             = None
        self._init_clients()

    @classmethod
    def from_env(cls) -> "AWSSsmAdapter":
        return cls()

    def _ids_from_env(self) -> List[str]:
        raw = os.environ.get("WATCHER_SSM_INSTANCE_IDS", "")
        return [i.strip() for i in raw.split(",") if i.strip()]

    def _init_clients(self) -> None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError(
                "boto3 not installed. Add 'boto3>=1.34' to requirements.txt"
            )
        session      = boto3.Session(region_name=self.region)
        self._ssm    = session.client("ssm")
        self._ec2    = session.client("ec2")
        self._cw     = session.client("cloudwatch") if self.use_cloudwatch else None
        logger.info(
            f"[AWS-SSM] Adapter initialised — region={self.region} "
            f"instances={self._explicit_ids or 'tag-discovered'}"
        )

    @property
    def adapter_name(self) -> str:
        return "aws_ssm"

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 30, mode: str = "target") -> ExecResult:
        """
        Run a shell command on an EC2 instance via SSM Run Command.
        Polls GetCommandInvocation until completion or timeout.
        """
        if mode == "host":
            import subprocess
            r = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=timeout)
            return ExecResult(r.returncode == 0, r.stdout.strip(), r.stderr.strip(), r.returncode, command)

        doc = "AWS-RunShellScript" if self.os_type == "linux" else "AWS-RunPowerShellScript"
        params = {"commands": [command], "executionTimeout": [str(timeout)]}
        try:
            resp = self._ssm.send_command(
                InstanceIds=[target],
                DocumentName=doc,
                Parameters=params,
                Comment=f"watcher-{uuid.uuid4().hex[:8]}",
                TimeoutSeconds=min(timeout + 10, 3600),
            )
            cmd_id = resp["Command"]["CommandId"]
            return self._poll_command(cmd_id, target, command, timeout)
        except Exception as exc:
            return ExecResult.error(str(exc), f"[ssm:{target}] {command}")

    def _poll_command(self, cmd_id: str, instance_id: str,
                      original_cmd: str, timeout: int) -> ExecResult:
        deadline  = time.time() + timeout + 15
        cmd_str   = f"[ssm:{instance_id}] {original_cmd}"
        while time.time() < deadline:
            try:
                inv = self._ssm.get_command_invocation(
                    CommandId=cmd_id, InstanceId=instance_id
                )
                status = inv["Status"]
                if status in ("Success", "Failed", "Cancelled", "TimedOut", "DeliveryTimedOut"):
                    rc = 0 if status == "Success" else 1
                    return ExecResult(
                        success=status == "Success",
                        stdout=inv.get("StandardOutputContent", "").strip(),
                        stderr=inv.get("StandardErrorContent", "").strip(),
                        returncode=rc,
                        command=cmd_str,
                    )
            except self._ssm.exceptions.InvocationDoesNotExist:
                pass
            time.sleep(2)
        return ExecResult.error(f"SSM command timed out after {timeout}s", cmd_str)

    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        sig_flag = signal.replace("SIG", "")
        if self.os_type == "linux":
            return self.exec(target, f"pkill -{sig_flag} {process_name}", timeout=10)
        else:
            # Windows
            return self.exec(target, f"Stop-Process -Name {process_name} -Force", timeout=10)

    def check_process(self, target: str, process_name: str) -> dict:
        if self.os_type == "linux":
            result = self.exec(target, f"pgrep -x '{process_name}' > /dev/null 2>&1", timeout=8)
        else:
            result = self.exec(target,
                f"if (Get-Process -Name {process_name} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
                timeout=8)
        return {"running": result.returncode == 0, "process_name": process_name}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        """Reboot the EC2 instance via SSM or EC2 API."""
        try:
            if force:
                self._ec2.reboot_instances(InstanceIds=[target])
            else:
                # Graceful: use shutdown -r
                self.exec(target, "sudo shutdown -r +1 'Watcher-initiated reboot'", timeout=10)
            return ExecResult(True, f"Instance '{target}' reboot initiated", "", 0,
                              f"ssm:restart:{target}")
        except Exception as exc:
            return ExecResult.error(str(exc), f"ssm:restart:{target}")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        if self._explicit_ids:
            return self._explicit_ids
        # Tag-based discovery
        if self.tag_key and self.tag_value:
            try:
                resp = self._ec2.describe_instances(
                    Filters=[
                        {"Name": f"tag:{self.tag_key}", "Values": [self.tag_value]},
                        {"Name": "instance-state-name",  "Values": ["running"]},
                    ]
                )
                ids = [
                    i["InstanceId"]
                    for r in resp["Reservations"]
                    for i in r["Instances"]
                ]
                return ids
            except Exception as exc:
                logger.warning(f"[AWS-SSM] Tag discovery failed: {exc}")
        # SSM-managed instances discovery
        try:
            resp = self._ssm.describe_instance_information(
                Filters=[{"Key": "PingStatus", "Values": ["Online"]}]
            )
            return [i["InstanceId"] for i in resp.get("InstanceInformationList", [])]
        except Exception as exc:
            logger.warning(f"[AWS-SSM] SSM instance discovery failed: {exc}")
            return []

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """
        Pull metrics from CloudWatch Agent (preferred) or SSM exec fallback.
        CloudWatch Agent publishes per-instance CPU/memory/disk to CWAgent namespace.
        """
        m = TargetMetrics(target=target)

        if self.use_cloudwatch and self._cw:
            try:
                import datetime
                now  = datetime.datetime.utcnow()
                ago5 = now - datetime.timedelta(minutes=5)
                dims = [{"Name": "InstanceId", "Value": target}]

                def _cw_stat(metric: str, stat: str = "Average") -> Optional[float]:
                    r = self._cw.get_metric_statistics(
                        Namespace=self.cw_namespace,
                        MetricName=metric,
                        Dimensions=dims,
                        StartTime=ago5,
                        EndTime=now,
                        Period=300,
                        Statistics=[stat],
                    )
                    points = r.get("Datapoints", [])
                    return points[-1][stat] if points else None

                cpu   = _cw_stat("cpu_usage_active")     # CWAgent metric
                mem   = _cw_stat("mem_used_percent")
                disk  = _cw_stat("disk_used_percent")

                if cpu  is not None: m.cpu_percent    = round(cpu, 1)
                if mem  is not None: m.memory_percent = round(mem, 1)
                if disk is not None: m.disk_percent   = round(disk, 1)
                m.extra = {"source": "cloudwatch"}
                return m
            except Exception as exc:
                logger.debug(f"[AWS-SSM] CloudWatch metrics unavailable for {target}: {exc}")

        # Fallback: SSM exec (uses base class compound shell script)
        return super().get_metrics(target)

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            resp = self._ssm.describe_instance_information(MaxResults=1)
            return True
        except Exception:
            return False
