"""
Watcher environment auto-detection.

Determines where the watcher process is running so it can select the
appropriate execution adapter and report its deployment context to the
platform.

Detection priority (most specific → least specific):
  1. Kubernetes   — KUBERNETES_SERVICE_HOST env var (always injected by K8s)
  2. Docker       — /.dockerenv file or cgroup markers
  3. AWS EC2      — IMDSv1/v2 endpoint responds at 169.254.169.254
  4. Azure VM     — IMDS endpoint responds with Metadata header
  5. GCP Compute  — metadata.google.internal responds
  6. VMware VM    — DMI product_name or sys_vendor contains "VMware"
  7. Hyper-V VM   — DMI sys_vendor contains "Microsoft Corporation"
  8. Bare metal   — none of the above matched

All cloud probes use a 500 ms timeout so startup is not materially delayed.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class WatcherEnvironment(str, Enum):
    KUBERNETES  = "kubernetes"
    DOCKER      = "docker"
    AWS_EC2     = "aws_ec2"
    AZURE_VM    = "azure_vm"
    GCP_COMPUTE = "gcp_compute"
    VMWARE_VM   = "vmware_vm"
    HYPERV_VM   = "hyperv_vm"
    BARE_METAL  = "bare_metal"
    UNKNOWN     = "unknown"


# Human-readable labels for the UI
ENV_LABELS: dict[WatcherEnvironment, str] = {
    WatcherEnvironment.KUBERNETES:  "Kubernetes Pod",
    WatcherEnvironment.DOCKER:      "Docker Container",
    WatcherEnvironment.AWS_EC2:     "AWS EC2",
    WatcherEnvironment.AZURE_VM:    "Azure VM",
    WatcherEnvironment.GCP_COMPUTE: "GCP Compute Engine",
    WatcherEnvironment.VMWARE_VM:   "VMware vSphere VM",
    WatcherEnvironment.HYPERV_VM:   "Hyper-V VM",
    WatcherEnvironment.BARE_METAL:  "Bare Metal / VM",
    WatcherEnvironment.UNKNOWN:     "Unknown",
}

# Icons / colours for the UI badge
ENV_STYLE: dict[WatcherEnvironment, dict] = {
    WatcherEnvironment.KUBERNETES:  {"color": "#3b82f6", "icon": "☸"},
    WatcherEnvironment.DOCKER:      {"color": "#0db7ed", "icon": "🐳"},
    WatcherEnvironment.AWS_EC2:     {"color": "#f59e0b", "icon": "☁"},
    WatcherEnvironment.AZURE_VM:    {"color": "#0078d4", "icon": "☁"},
    WatcherEnvironment.GCP_COMPUTE: {"color": "#4285f4", "icon": "☁"},
    WatcherEnvironment.VMWARE_VM:   {"color": "#607d8b", "icon": "⬡"},
    WatcherEnvironment.HYPERV_VM:   {"color": "#00bcf2", "icon": "⬡"},
    WatcherEnvironment.BARE_METAL:  {"color": "#9ca3af", "icon": "🖥"},
    WatcherEnvironment.UNKNOWN:     {"color": "#6b7280", "icon": "?"},
}


def _read_dmi(path: str) -> str:
    try:
        return Path(path).read_text().strip().lower()
    except Exception:
        return ""


def _probe_imds(url: str, headers: dict | None = None, timeout: float = 0.5) -> bool:
    """Non-blocking HTTP probe for cloud IMDS endpoints."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers=headers or {})
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def detect_environment() -> WatcherEnvironment:
    """
    Detect the runtime environment and return the matching WatcherEnvironment.
    Called once at watcher startup; result cached on WatcherService.
    """
    # ── 1. Kubernetes ─────────────────────────────────────────────────────────
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        logger.info("[ENV] Detected: Kubernetes Pod (KUBERNETES_SERVICE_HOST set)")
        return WatcherEnvironment.KUBERNETES

    # ── 2. Docker ─────────────────────────────────────────────────────────────
    if Path("/.dockerenv").exists():
        logger.info("[ENV] Detected: Docker Container (/.dockerenv present)")
        return WatcherEnvironment.DOCKER

    # Check cgroup as a fallback for Docker / K8s
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        if "kubepods" in cgroup:
            logger.info("[ENV] Detected: Kubernetes Pod (cgroup marker)")
            return WatcherEnvironment.KUBERNETES
        if "docker" in cgroup or "/container" in cgroup:
            logger.info("[ENV] Detected: Docker Container (cgroup marker)")
            return WatcherEnvironment.DOCKER
    except Exception:
        pass

    # ── 3. AWS EC2 ────────────────────────────────────────────────────────────
    # IMDSv1 probe (no token needed for detection)
    if _probe_imds("http://169.254.169.254/latest/meta-data/"):
        logger.info("[ENV] Detected: AWS EC2 (IMDS responded)")
        return WatcherEnvironment.AWS_EC2

    # ── 4. Azure VM ───────────────────────────────────────────────────────────
    if _probe_imds(
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        headers={"Metadata": "true"},
    ):
        logger.info("[ENV] Detected: Azure VM (IMDS responded)")
        return WatcherEnvironment.AZURE_VM

    # ── 5. GCP Compute Engine ─────────────────────────────────────────────────
    if _probe_imds(
        "http://metadata.google.internal/computeMetadata/v1/",
        headers={"Metadata-Flavor": "Google"},
    ):
        logger.info("[ENV] Detected: GCP Compute Engine (metadata server responded)")
        return WatcherEnvironment.GCP_COMPUTE

    # ── 6. Hypervisor via DMI ─────────────────────────────────────────────────
    product = _read_dmi("/sys/class/dmi/id/product_name")
    vendor  = _read_dmi("/sys/class/dmi/id/sys_vendor")

    if "vmware" in product or "vmware" in vendor:
        logger.info("[ENV] Detected: VMware vSphere VM (DMI)")
        return WatcherEnvironment.VMWARE_VM

    if "microsoft corporation" in vendor or "hyper-v" in product or "virtual machine" in product:
        logger.info("[ENV] Detected: Hyper-V VM (DMI)")
        return WatcherEnvironment.HYPERV_VM

    # ── 7. Bare metal / isolated VM ───────────────────────────────────────────
    logger.info("[ENV] Detected: Bare Metal / Isolated VM (no cloud/hypervisor markers)")
    return WatcherEnvironment.BARE_METAL
