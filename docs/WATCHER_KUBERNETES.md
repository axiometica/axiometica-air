# Watcher — Kubernetes Deployment Guide

**Applies to:** KinD, AKS, EKS, GKE, DOKS, and any standard Kubernetes cluster  
**Related files:** `k8s/base/01-rbac.yaml`, `k8s/base/11-watcher.yaml`, `k8s/base/12-sentinel.yaml`

---

## Architecture

The observability subsystem is split into two components with deliberately separate privilege boundaries:

```
┌─────────────────────────────────────────────────────────────┐
│  Node (host kernel)                                         │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  sentinel  (DaemonSet — one pod per node)            │   │
│  │  • hostPID: true  → sees all processes on the node  │   │
│  │  • CAP_BPF, CAP_SYS_ADMIN → runs bpftrace           │   │
│  │  • GET  /metrics  → 5-second syscall counts (JSON)  │   │
│  │  • GET  /health   → readiness probe                  │   │
│  │  • POST /kill     → pkill -9 <process> (host-wide)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                 │
│                    HTTP /metrics                            │
│                           ▼                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  watcher  (Deployment — one pod)                     │   │
│  │  • Unprivileged container                            │   │
│  │  • K8s ServiceAccount with ClusterRole               │   │
│  │  • Polls sentinel for syscall telemetry              │   │
│  │  • Polls metrics-server for CPU/memory               │   │
│  │  • Runs health/TCP/DNS synthetic checks              │   │
│  │  • Creates incidents via backend API                 │   │
│  │  • Executes runbook remediation via kubectl exec     │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Why two components?

`bpftrace` requires `CAP_BPF`, `CAP_SYS_ADMIN`, and `hostPID: true` — privileges too dangerous for the main orchestration pod. Sentinel is the minimal privileged sidecar that does one thing: count kernel syscalls and expose them over HTTP. The watcher is completely unprivileged at the kernel level and gets its reach through the Kubernetes API instead.

**Blast radius if compromised:**
- Sentinel — can see/kill host processes, cannot touch the K8s API
- Watcher — can exec into pods and patch deployments, cannot touch the kernel

---

## Sentinel Telemetry

### Currently collected

| Signal | Mechanism | Endpoint |
|--------|-----------|----------|
| Syscall counts per process (5s window) | `bpftrace tracepoint:raw_syscalls:sys_enter` | `GET /metrics` |

### Excluded from anomaly detection

The following processes are filtered out of syscall anomaly scoring because they generate high baseline noise that is expected behaviour:

```
python3, sh, bash, dash          # scripting runtimes
kubelet, kube-apiserver,         # K8s control plane
kube-controller, kube-scheduler,
kube-proxy, etcd, coredns
kube-*  (prefix match)           # any kube-* process
```

### What sentinel could additionally provide (same privileges, no changes needed)

| Telemetry | bpftrace hook |
|-----------|---------------|
| New outbound TCP connections per process | `tracepoint:syscalls:sys_enter_connect` |
| DNS queries per process | `tracepoint:syscalls:sys_enter_sendto` (port 53) |
| Files opened per process | `tracepoint:syscalls:sys_enter_openat` |
| New process spawns | `tracepoint:syscalls:sys_enter_execve` |
| Privilege escalation attempts | `tracepoint:syscalls:sys_enter_setuid` |
| Disk I/O per process | `tracepoint:block:block_rq_issue` |
| Per-container resource usage (kernel) | `/sys/fs/cgroup/kubepods/*/cpu.stat` |
| OOM kill events | `/dev/kmsg` |

Each of these would be a new endpoint on sentinel (e.g. `GET /execve-events`) polled by the watcher.

### Kill endpoint

`POST /kill?process=<name>` runs `pkill -9 <name>` at the host level. Because sentinel has `hostPID: true`, this kills the named process in any pod on the node — not just within sentinel's own container. Runbook remediation steps that use the kill action route through this endpoint.

---

## Watcher RBAC

Defined in `k8s/base/01-rbac.yaml`. The watcher runs under a dedicated `ServiceAccount` bound to a `ClusterRole`:

| Resource | Verbs | Purpose |
|----------|-------|---------|
| `pods`, `nodes`, `services`, `endpoints`, `namespaces` | get, list, watch | Service discovery, pod enumeration, health checks |
| `pods/exec` | create | Remediation — run commands inside pods; `_find_process_container` pgrep search |
| `pods/log` | get | Log-based anomaly detection |
| `pods` | delete | Pod restart remediation |
| `deployments`, `statefulsets`, `daemonsets`, `replicasets` | get, list, watch, update, patch | Scale/restart deployments as remediation |
| `metrics.k8s.io/pods`, `metrics.k8s.io/nodes` | get, list | CPU and memory metrics from metrics-server |

> **Note:** `pods/exec` (`create`) is the critical permission. Without it, the watcher cannot run remediation commands inside containers or search for processes by name across pods.

---

## Adapter Auto-Selection

When deployed to Kubernetes, the watcher detects its environment at startup and selects the `KubernetesAdapter` automatically. No configuration is needed.

| Detected environment | Adapter selected | Remediation transport |
|----------------------|------------------|-----------------------|
| Inside a K8s pod | `KubernetesAdapter` | `kubectl exec` via in-cluster API |
| Docker socket available | `DockerAdapter` | `docker exec` |
| `WATCHER_SSH_HOST` set | `SSHAdapter` | SSH into remote hosts |
| `WATCHER_SSM_INSTANCE_IDS` set (AWS) | `AWSSsmAdapter` | AWS Systems Manager Run Command |
| `AZURE_SUBSCRIPTION_ID` + `AZURE_RESOURCE_GROUP` set | `AzureAdapter` | Azure Run Command |
| `VCENTER_HOST` set | `vCenterAdapter` | VMware guest exec |
| None of the above | `DockerAdapter` | Fallback |

Override with: `WATCHER_ADAPTER=kubernetes|docker|ssh|aws_ssm|azure|vcenter`

---

## Remediation Capabilities by Environment

Remediation works independently of sentinel in every environment. Sentinel only adds syscall telemetry and the host-level kill shortcut.

| Action | K8s | Docker | SSH | SSM | Azure |
|--------|-----|--------|-----|-----|-------|
| Restart a pod/container | ✓ (delete pod) | ✓ (restart) | ✓ | ✓ | ✓ |
| Exec command in container | ✓ | ✓ | ✓ | ✓ | ✓ |
| Scale a deployment | ✓ (patch replicas) | — | — | — | — |
| Kill a host process | ✓ (via sentinel `/kill`) | ✓ (via sentinel) | ✓ (pkill over SSH) | ✓ | ✓ |
| Syscall anomaly detection | ✓ (sentinel DaemonSet) | ✓ (sentinel sidecar) | — | — | — |

---

## Deployment Targets

### Local — KinD (Windows)

```powershell
# Full deploy (build + load + apply)
.\k8s\scripts\deploy-kind.ps1

# Skip rebuild (images already loaded)
.\k8s\scripts\deploy-kind.ps1 -SkipBuild

# Stop cluster without destroying it
docker stop desktop-control-plane

# Resume cluster
docker start desktop-control-plane
kubectl config use-context kind-desktop
```

### Local — KinD (Linux / macOS)

```bash
./k8s/scripts/deploy-kind.sh
./k8s/scripts/deploy-kind.sh --skip-build
./k8s/scripts/deploy-kind.sh --skip-migrations
```

### Managed K8s — AKS / EKS / GKE

```bash
export ACR_NAME=myregistry          # Azure ACR (or set REGISTRY_PREFIX for non-Azure)
export RESOURCE_GROUP=my-rg
export CLUSTER_NAME=my-aks
export PLATFORM_HOST=itsm.example.com
bash k8s/scripts/deploy-aks.sh
```

See `docs/AKS_DEPLOYMENT_WALKTHROUGH.md` for a step-by-step guide.

### GCP VM — Docker Compose

```bash
bash scripts/deploy-gcp.sh [GITHUB_USERNAME] [GITHUB_TOKEN]
```

Uses `DockerAdapter` instead of `KubernetesAdapter`. Sentinel runs as a Docker container with `--privileged` and `--pid=host`.

---

## Key Configuration

Set via environment variables in the watcher pod (or `.env` for Docker Compose):

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHER_API_KEY` | — | Required. Auth key for backend API |
| `WATCHER_ANOMALY_THRESHOLD` | `20000` | Syscall count per 5s to trigger anomaly |
| `WATCHER_COOLDOWN_SECONDS` | `60` | Minimum seconds between incidents for the same resource |
| `WATCHER_MIN_CONSECUTIVE_POLLS` | `3` | Consecutive polls above threshold before incident fires |
| `WATCHER_CPU_THRESHOLD` | `80.0` | CPU % threshold |
| `WATCHER_MEMORY_THRESHOLD` | `90.0` | Memory % threshold |
| `WATCHER_DISK_THRESHOLD` | `90.0` | Disk % threshold |
| `WATCHER_ADAPTER` | auto | Force a specific adapter: `kubernetes`, `docker`, `ssh`, `aws_ssm`, `azure`, `vcenter` |
| `SENTINEL_URL` | `http://sentinel:9090` | Sentinel HTTP endpoint (K8s service name) |

---

## Single-Node Cluster Notes (KinD / Docker Desktop)

KinD runs the entire cluster inside a single Docker container. All pods share the same CPUs and kernel. This has practical consequences for load testing:

- **High-syscall workloads** (e.g. `dd`, `yes`) pin shared CPU and starve other pods — neo4j is particularly sensitive
- **Sentinel's `hostPID`** means it sees processes from all pods; the watcher excludes sentinel from process-container attribution for this reason
- For sustained load testing, reduce `WATCHER_MIN_CONSECUTIVE_POLLS` to `1` to fire on a single detection rather than needing multiple consecutive polls

For production testing use a multi-node managed cluster (AKS/EKS/GKE) where each node has dedicated CPU allocation.
