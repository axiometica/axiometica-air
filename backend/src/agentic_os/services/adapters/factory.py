"""
Adapter factory — selects and constructs the right ExecutionAdapter.

Selection priority:
  1. WATCHER_ADAPTER env var (explicit override)
       "docker" | "ssh" | "vcenter" | "kubernetes" | "aws_ssm"
  2. Auto-select from detected environment:
       kubernetes           → KubernetesAdapter
       docker               → DockerAdapter
       vmware_vm            → vCenterAdapter (if VCENTER_HOST set) else SSHAdapter
       aws_ec2              → AWSSsmAdapter  (if WATCHER_SSM_INSTANCE_IDS or tag set)
                              else SSHAdapter (if WATCHER_SSH_HOST set)
                              else DockerAdapter
       azure_vm/gcp_compute → SSHAdapter (if WATCHER_SSH_HOST set) else DockerAdapter
       bare_metal/hyperv    → SSHAdapter (if WATCHER_SSH_HOST set) else DockerAdapter
  3. DockerAdapter as final fallback
"""

from __future__ import annotations

import logging
import os

from .base import ExecutionAdapter
from ..environment_detector import WatcherEnvironment

logger = logging.getLogger(__name__)


def create_adapter(environment: WatcherEnvironment) -> ExecutionAdapter:
    """
    Return the best ExecutionAdapter for the detected environment.
    All adapters are imported lazily so missing optional dependencies
    (paramiko, pyvmomi, kubernetes, boto3) don't crash Docker-only deployments.
    """
    explicit = os.environ.get("WATCHER_ADAPTER", "").lower().strip()
    if explicit:
        logger.info(f"[ADAPTER] Using explicit WATCHER_ADAPTER={explicit}")
        return _build(explicit)

    # ── Kubernetes: running inside a K8s pod ──────────────────────────────────
    if environment == WatcherEnvironment.KUBERNETES:
        logger.info("[ADAPTER] Kubernetes env → KubernetesAdapter")
        return _build("kubernetes")

    # ── Docker ────────────────────────────────────────────────────────────────
    if environment == WatcherEnvironment.DOCKER:
        return _build("docker")

    # ── VMware vSphere VM ─────────────────────────────────────────────────────
    if environment == WatcherEnvironment.VMWARE_VM:
        if os.environ.get("VCENTER_HOST"):
            logger.info("[ADAPTER] VMware env + VCENTER_HOST → vCenterAdapter")
            return _build("vcenter")
        if os.environ.get("WATCHER_SSH_HOST") or os.environ.get("WATCHER_SSH_HOSTS_JSON"):
            logger.info("[ADAPTER] VMware env + SSH config → SSHAdapter")
            return _build("ssh")
        logger.info("[ADAPTER] VMware env, no remote config → Docker (fallback)")
        return _build("docker")

    # ── AWS EC2 ───────────────────────────────────────────────────────────────
    if environment == WatcherEnvironment.AWS_EC2:
        has_ssm = bool(
            os.environ.get("WATCHER_SSM_INSTANCE_IDS")
            or os.environ.get("WATCHER_SSM_TAG_KEY")
        )
        if has_ssm:
            logger.info("[ADAPTER] AWS EC2 env + SSM config → AWSSsmAdapter")
            return _build("aws_ssm")
        if os.environ.get("WATCHER_SSH_HOST") or os.environ.get("WATCHER_SSH_HOSTS_JSON"):
            logger.info("[ADAPTER] AWS EC2 env + SSH config → SSHAdapter")
            return _build("ssh")
        logger.info("[ADAPTER] AWS EC2 env, no SSM/SSH config → Docker (fallback)")
        return _build("docker")

    # ── Azure VM ──────────────────────────────────────────────────────────────
    if environment == WatcherEnvironment.AZURE_VM:
        if os.environ.get("AZURE_SUBSCRIPTION_ID") and os.environ.get("AZURE_RESOURCE_GROUP"):
            logger.info("[ADAPTER] Azure env + subscription/resource-group → AzureAdapter")
            return _build("azure")
        if os.environ.get("WATCHER_SSH_HOST") or os.environ.get("WATCHER_SSH_HOSTS_JSON"):
            logger.info("[ADAPTER] Azure env + SSH config → SSHAdapter (no Run Command creds)")
            return _build("ssh")
        logger.info("[ADAPTER] Azure env, no credentials → Docker (fallback)")
        return _build("docker")

    # ── GCP / Hyper-V / Bare metal ────────────────────────────────────────────
    if environment in (
        WatcherEnvironment.GCP_COMPUTE,
        WatcherEnvironment.HYPERV_VM,
        WatcherEnvironment.BARE_METAL,
    ):
        if os.environ.get("WATCHER_SSH_HOST") or os.environ.get("WATCHER_SSH_HOSTS_JSON"):
            logger.info(f"[ADAPTER] {environment} env + SSH config → SSHAdapter")
            return _build("ssh")
        if os.environ.get("VCENTER_HOST"):
            return _build("vcenter")
        logger.info(f"[ADAPTER] {environment} env, no remote config → Docker (fallback)")
        return _build("docker")

    return _build("docker")


def _build(name: str) -> ExecutionAdapter:
    if name == "docker":
        from .docker_adapter import DockerAdapter
        adapter = DockerAdapter()
        logger.info("[ADAPTER] DockerAdapter initialised")
        return adapter

    if name == "ssh":
        from .ssh_adapter import SSHAdapter
        adapter = SSHAdapter()
        logger.info(f"[ADAPTER] SSHAdapter initialised — targets: {adapter.list_targets()}")
        return adapter

    if name in ("vcenter", "vmware"):
        from .vcenter_adapter import vCenterAdapter
        adapter = vCenterAdapter.from_env()
        logger.info(
            f"[ADAPTER] vCenterAdapter initialised — "
            f"vCenter: {adapter.host}, VMs: {adapter.list_targets()}"
        )
        return adapter

    if name in ("kubernetes", "k8s"):
        from .k8s_adapter import KubernetesAdapter
        adapter = KubernetesAdapter()
        logger.info(
            f"[ADAPTER] KubernetesAdapter initialised — "
            f"namespace={adapter.namespace}, pods: {len(adapter.list_targets())}"
        )
        return adapter

    if name in ("aws_ssm", "ssm"):
        from .aws_ssm_adapter import AWSSsmAdapter
        adapter = AWSSsmAdapter.from_env()
        logger.info(
            f"[ADAPTER] AWSSsmAdapter initialised — "
            f"region={adapter.region}, targets: {adapter.list_targets()}"
        )
        return adapter

    if name in ("azure", "azure_vm"):
        from .azure_adapter import AzureAdapter
        adapter = AzureAdapter.from_env()
        logger.info(
            f"[ADAPTER] AzureAdapter initialised — "
            f"subscription={adapter.subscription_id[:8]}… "
            f"resource_group={adapter.resource_group}, VMs: {adapter.list_targets()}"
        )
        return adapter

    logger.warning(f"[ADAPTER] Unknown adapter '{name}', falling back to Docker")
    from .docker_adapter import DockerAdapter
    return DockerAdapter()
