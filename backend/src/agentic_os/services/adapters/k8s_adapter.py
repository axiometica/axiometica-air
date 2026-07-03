"""
Kubernetes Adapter — kubectl / Python kubernetes client.

Runs commands inside Pods, kills processes, restarts Deployments, and
collects metrics via the Kubernetes metrics-server API.

When the watcher runs AS a Kubernetes Pod (KUBERNETES_SERVICE_HOST is set),
in-cluster config is loaded automatically.  When running outside the cluster
(local dev or CI), set WATCHER_K8S_KUBECONFIG or KUBECONFIG.

Environment variables:
  WATCHER_K8S_NAMESPACE        Namespace to watch  (default: default)
  WATCHER_K8S_LABEL_SELECTOR   Pod label filter    (default: watch all pods)
  WATCHER_K8S_KUBECONFIG       Path to kubeconfig  (default: in-cluster)
  WATCHER_K8S_CONTAINER        Default container name inside a pod (optional)
  WATCHER_K8S_METRICS_SERVER   true/false — use metrics-server API (default true)
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from .base import ExecutionAdapter, ExecResult, TargetMetrics

logger = logging.getLogger(__name__)

_NS  = os.environ.get("WATCHER_K8S_NAMESPACE",       "default")
_SEL = os.environ.get("WATCHER_K8S_LABEL_SELECTOR",  "")
_CFG = os.environ.get("WATCHER_K8S_KUBECONFIG",      "")
_USE_METRICS_SERVER = os.environ.get("WATCHER_K8S_METRICS_SERVER", "true").lower() == "true"


class KubernetesAdapter(ExecutionAdapter):
    """
    ExecutionAdapter backed by the Kubernetes API / kubectl.

    Target names are Pod names within the configured namespace.
    Deployment/StatefulSet restarts are also supported via restart_target().
    """

    def __init__(
        self,
        namespace: str = _NS,
        label_selector: str = _SEL,
        kubeconfig: str = _CFG,
        use_metrics_server: bool = _USE_METRICS_SERVER,
    ):
        self.namespace          = namespace
        self.label_selector     = label_selector
        self.kubeconfig         = kubeconfig
        self.use_metrics_server = use_metrics_server
        self._api_client        = None  # lazy-initialised
        self._core_v1           = None
        self._apps_v1           = None
        self._custom_objects    = None
        self._init_client()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_client(self) -> None:
        try:
            from kubernetes import client, config as k8s_config
            if self.kubeconfig:
                k8s_config.load_kube_config(config_file=self.kubeconfig)
            elif os.environ.get("KUBERNETES_SERVICE_HOST"):
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config()   # default ~/.kube/config
            self._core_v1        = client.CoreV1Api()
            self._apps_v1        = client.AppsV1Api()
            self._custom_objects = client.CustomObjectsApi()
            logger.info(f"[K8s] Client initialised — namespace={self.namespace} selector='{self.label_selector}'")
        except ImportError:
            raise RuntimeError(
                "kubernetes package not installed. "
                "Add 'kubernetes>=29.0.0' to requirements.txt"
            )
        except Exception as exc:
            raise RuntimeError(f"Cannot initialise Kubernetes client: {exc}") from exc

    @property
    def adapter_name(self) -> str:
        return "kubernetes"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _kubectl(self, args: List[str], timeout: int = 30) -> ExecResult:
        """Run a kubectl command, returning an ExecResult."""
        base = ["kubectl", "--namespace", self.namespace]
        if self.kubeconfig:
            base += ["--kubeconfig", self.kubeconfig]
        cmd = base + args
        cmd_str = " ".join(cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return ExecResult(
                success=result.returncode == 0,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                returncode=result.returncode,
                command=cmd_str,
            )
        except subprocess.TimeoutExpired:
            return ExecResult.error(f"kubectl timed out after {timeout}s", cmd_str)
        except FileNotFoundError:
            return ExecResult.error("kubectl not found on PATH", cmd_str)
        except Exception as exc:
            return ExecResult.error(str(exc), cmd_str)

    def _get_pod_container(self, pod_name: str, preferred: Optional[str] = None) -> str:
        """Return the first container name in a pod (or preferred if given)."""
        if preferred:
            return preferred
        try:
            pod = self._core_v1.read_namespaced_pod(pod_name, self.namespace)
            return pod.spec.containers[0].name
        except Exception:
            return ""

    # ── Core execution ────────────────────────────────────────────────────────

    def exec(self, target: str, command: str,
             timeout: int = 30, mode: str = "target") -> ExecResult:
        """
        Execute a shell command inside a Pod container.

        mode="target"  → kubectl exec -n <ns> <pod> -- sh -c "<command>"
        mode="host"    → runs directly on the watcher host (kubectl on PATH)
        """
        if mode == "host":
            cmd = ["sh", "-c", command]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                return ExecResult(r.returncode == 0, r.stdout.strip(), r.stderr.strip(),
                                  r.returncode, command)
            except Exception as exc:
                return ExecResult.error(str(exc), command)

        container = self._get_pod_container(target)
        args = ["exec", target]
        if container:
            args += ["-c", container]
        args += ["--", "sh", "-c", command]
        return self._kubectl(args, timeout=timeout)

    def kill_process(self, target: str, process_name: str,
                     signal: str = "SIGKILL") -> ExecResult:
        """pkill inside the pod."""
        sig_flag = signal.replace("SIG", "")
        return self.exec(target, f"pkill -{sig_flag} {process_name}", timeout=10)

    def check_process(self, target: str, process_name: str) -> dict:
        """pgrep inside the pod (zombie-aware)."""
        result = self.exec(
            target,
            f"ps ax -o s= -o comm= 2>/dev/null "
            f"| awk -v p='{process_name}' '$1!=\"Z\" && $2==p' | grep -q .",
            timeout=8,
        )
        return {"running": result.returncode == 0, "process_name": process_name}

    def restart_target(self, target: str, force: bool = False) -> ExecResult:
        """
        Restart a workload.

        target can be:
          - "pod/<name>"         → delete the pod (K8s recreates it)
          - "deployment/<name>"  → kubectl rollout restart deployment/<name>
          - "<pod-name>"         → delete the pod (best effort)
        """
        if "/" in target:
            kind, name = target.split("/", 1)
        else:
            # Try to find the owning deployment; fall back to pod delete
            kind, name = "pod", target

        if kind.lower() in ("deployment", "deploy", "sts", "statefulset", "daemonset", "ds"):
            return self._kubectl(["rollout", "restart", f"{kind}/{name}"], timeout=30)
        else:
            # Delete pod — K8s will recreate via the controller
            result = self._kubectl(["delete", "pod", name, "--grace-period=30"], timeout=45)
            if result.success:
                result.stdout = f"Pod '{name}' deleted — controller will recreate it"
            return result

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_targets(self) -> List[str]:
        """Return running Pod names in the namespace."""
        try:
            kwargs: dict = {"namespace": self.namespace, "field_selector": "status.phase=Running"}
            if self.label_selector:
                kwargs["label_selector"] = self.label_selector
            pods = self._core_v1.list_namespaced_pod(**kwargs)
            return [p.metadata.name for p in pods.items if p.status.phase == "Running"]
        except Exception as exc:
            logger.warning(f"[K8s] list_targets failed: {exc}")
            return []

    def list_deployments(self) -> List[str]:
        """Return all Deployment names in the namespace."""
        try:
            deps = self._apps_v1.list_namespaced_deployment(self.namespace)
            return [d.metadata.name for d in deps.items]
        except Exception as exc:
            logger.warning(f"[K8s] list_deployments failed: {exc}")
            return []

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self, target: str) -> TargetMetrics:
        """
        Collect Pod CPU and memory from the metrics-server API.
        Falls back to kubectl exec top if the metrics API is unavailable.
        """
        m = TargetMetrics(target=target)

        if self.use_metrics_server:
            try:
                # metrics-server exposes: /apis/metrics.k8s.io/v1beta1/namespaces/<ns>/pods/<pod>
                raw = self._custom_objects.get_namespaced_custom_object(
                    group="metrics.k8s.io", version="v1beta1",
                    namespace=self.namespace,
                    plural="pods", name=target,
                )
                containers = raw.get("containers", [])
                total_cpu_n  = 0  # nanocores
                total_mem_ki = 0  # kibibytes
                for c in containers:
                    usage = c.get("usage", {})
                    cpu_str = usage.get("cpu", "0n")
                    mem_str = usage.get("memory", "0Ki")
                    total_cpu_n  += _parse_cpu_nano(cpu_str)
                    total_mem_ki += _parse_mem_ki(mem_str)

                # CPU: convert nanocores to % assuming 1 core = 1e9 nanocores
                # Request-relative % requires knowing the pod's requested CPU.
                # Approximate: show as millicores / 1000 * 100 (% of 1 core).
                m.cpu_percent     = round(total_cpu_n / 1e7, 2)    # % of 1 core
                m.memory_used_mb  = round(total_mem_ki / 1024, 1)
                m.extra = {"source": "metrics-server",
                           "cpu_nanocores": total_cpu_n,
                           "memory_ki": total_mem_ki}
                return m
            except Exception as exc:
                logger.debug(f"[K8s] metrics-server unavailable for {target}: {exc}")

        # Fallback: kubectl exec top
        result = self.exec(target,
            "top -bn1 2>/dev/null | awk '/Cpu/{print $2}'; "
            "free -m 2>/dev/null | awk 'NR==2{printf \"%.1f %.0f %.0f\",$3/$2*100,$3,$2}'",
            timeout=12)
        if result.success:
            lines = result.stdout.splitlines()
            try: m.cpu_percent = float(lines[0].replace("%", ""))
            except Exception: pass
            try:
                parts = lines[1].split()
                m.memory_percent  = float(parts[0])
                m.memory_used_mb  = float(parts[1])
                m.memory_total_mb = float(parts[2])
            except Exception: pass
        return m

    # ── Health ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            self._core_v1.list_namespaced_pod(self.namespace, limit=1)
            return True
        except Exception:
            return False


# ── Unit parsing helpers ──────────────────────────────────────────────────────

def _parse_cpu_nano(s: str) -> int:
    """Convert '125m' (millicores) or '1000000000n' (nanocores) to nanocores."""
    s = s.strip()
    if s.endswith("n"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 1_000_000
    try:
        return int(float(s) * 1_000_000_000)
    except ValueError:
        return 0


def _parse_mem_ki(s: str) -> int:
    """Convert '128Ki', '1Gi', '512Mi' to kibibytes."""
    s = s.strip().upper()
    for suffix, factor in [("GI", 1024*1024), ("MI", 1024), ("KI", 1), ("G", 976563), ("M", 977), ("K", 1)]:
        if s.endswith(suffix):
            try:
                return int(float(s[:-len(suffix)]) * factor)
            except ValueError:
                return 0
    try:
        return int(s) // 1024
    except ValueError:
        return 0
