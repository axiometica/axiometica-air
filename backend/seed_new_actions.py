"""Seed the 23 new Host/SSH and Kubernetes approved actions into the live DB."""
import requests

DEFAULT_PROCESS_RULES = [
    {"priority": 1,  "allow": False, "pattern": "^(dockerd|containerd|containerd-shim.*)$", "description": "Container runtime"},
    {"priority": 2,  "allow": False, "pattern": "^(postgres|pg_.*)$",                        "description": "PostgreSQL"},
    {"priority": 3,  "allow": False, "pattern": "^(redis-server|redis-.*)$",                  "description": "Redis"},
    {"priority": 4,  "allow": False, "pattern": "^(java)$",                                   "description": "JVM"},
    {"priority": 5,  "allow": False, "pattern": "^(python3?|uvicorn|celery|gunicorn)$",       "description": "Platform backend"},
    {"priority": 6,  "allow": False, "pattern": "^(node|vite|npm|yarn)$",                     "description": "Frontend dev-server"},
    {"priority": 7,  "allow": False, "pattern": "^(sshd|systemd.*|init.*)$",                  "description": "System/init"},
    {"priority": 20, "allow": True,  "pattern": "^yes$",                                       "description": "CPU-bomb test"},
    {"priority": 21, "allow": True,  "pattern": "^stress(-ng)?$",                              "description": "stress/stress-ng"},
    {"priority": 28, "allow": True,  "pattern": "^(cat|tail|head|grep|awk|sed)$",             "description": "Unix utilities"},
    {"priority": 30, "allow": True,  "pattern": "^(ping|nc|curl|wget)$",                       "description": "Network tools"},
]

NEW_ACTIONS = [
    # ══ HOST / SSH — DIAGNOSTICS ══════════════════════════════════════════════
    {
        "tool_name": "host_service_status",
        "name": "Host Service Status",
        "description": "Check systemd service state on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl status {service}",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True,  "description": "IP or hostname"},
            {"name": "service", "type": "string", "required": True,  "description": "systemd service name"},
        ],
    },
    {
        "tool_name": "host_logs",
        "name": "Host Journal Logs",
        "description": "Fetch systemd journal entries for a service via SSH.",
        "command": "ssh {host} journalctl -u {service} -n {lines} --no-pager",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string",  "required": True},
            {"name": "service", "type": "string",  "required": True},
            {"name": "lines",   "type": "integer", "required": False, "default": 100},
            {"name": "since",   "type": "string",  "required": False, "description": "e.g. 10m ago"},
        ],
    },
    {
        "tool_name": "host_top_processes",
        "name": "Host Top Processes",
        "description": "List highest CPU/memory processes on a remote host via SSH.",
        "command": "ssh {host} ps aux --sort=-{sort_by} | head -{limit}",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string",  "required": True},
            {"name": "limit",   "type": "integer", "required": False, "default": 10},
            {"name": "sort_by", "type": "string",  "required": False, "default": "cpu", "description": "cpu | rss"},
        ],
    },
    {
        "tool_name": "host_disk_usage",
        "name": "Host Disk Usage",
        "description": "Check disk space and largest directories on a remote host via SSH.",
        "command": "ssh {host} df -h && du -sh {path}/* 2>/dev/null | sort -rh | head -20",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host", "type": "string", "required": True},
            {"name": "path", "type": "string", "required": False, "default": "/var"},
        ],
    },
    {
        "tool_name": "host_process_info",
        "name": "Host Process Info",
        "description": "Get PID, status, and resource usage for a named process on a remote host via SSH.",
        "command": "ssh {host} ps -fp $(pgrep {process_name})",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "host_netstat",
        "name": "Host Network Connections",
        "description": "List active TCP/UDP connections and listening ports on a remote host via SSH.",
        "command": "ssh {host} ss -tunaop",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",  "type": "string", "required": True},
            {"name": "state", "type": "string", "required": False, "default": "all",
             "description": "all | established | listening | time-wait"},
        ],
    },
    # ══ HOST / SSH — REMEDIATION SAFE ════════════════════════════════════════
    {
        "tool_name": "host_service_restart",
        "name": "Host Service Restart",
        "description": "Restart a systemd service on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl restart {service}",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "host_service_stop",
        "name": "Host Service Stop",
        "description": "Stop a systemd service on a bare-metal or VM host via SSH.",
        "command": "ssh {host} systemctl stop {service}",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "host",    "type": "string", "required": True},
            {"name": "service", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "host_log_cleanup",
        "name": "Host Log Cleanup",
        "description": "Delete log files older than N days on a remote host via SSH.",
        "command": "ssh {host} find {path} -name '*.log' -mtime +{days_to_retain} -delete",
        "category": "remediation_safe", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "host",           "type": "string",  "required": True},
            {"name": "path",           "type": "string",  "required": False, "default": "/var/log"},
            {"name": "days_to_retain", "type": "integer", "required": False, "default": 7},
        ],
    },
    # ══ HOST / SSH — REMEDIATION INTRUSIVE ═══════════════════════════════════
    {
        "tool_name": "host_process_kill",
        "name": "Host Process Kill",
        "description": "Send a POSIX signal to a named process on a remote host via SSH.",
        "command": "ssh {host} kill -{signal} $(pgrep {process_name})",
        "category": "remediation_intrusive", "blast_radius": 3, "requires_approval": False,
        "parameters": [
            {"name": "host",         "type": "string", "required": True},
            {"name": "process_name", "type": "string", "required": True},
            {"name": "signal",       "type": "string", "required": False, "default": "SIGTERM",
             "description": "SIGTERM | SIGKILL | SIGHUP | SIGINT"},
        ],
        "process_rules": DEFAULT_PROCESS_RULES,
    },
    {
        "tool_name": "host_reboot",
        "name": "Host Reboot",
        "description": "Gracefully reboot a bare-metal or VM host via SSH. Requires manual approval.",
        "command": "ssh {host} systemctl reboot",
        "category": "remediation_intrusive", "blast_radius": 3, "requires_approval": True,
        "parameters": [
            {"name": "host",          "type": "string",  "required": True},
            {"name": "delay_seconds", "type": "integer", "required": False, "default": 0,
             "description": "0 = immediate reboot"},
        ],
    },
    # ══ KUBERNETES — DIAGNOSTICS ══════════════════════════════════════════════
    {
        "tool_name": "k8s_pod_logs",
        "name": "K8s Pod Logs",
        "description": "Fetch recent log lines from a Kubernetes pod.",
        "command": "kubectl logs {pod} -n {namespace} --tail={lines} --timestamps",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "pod",       "type": "string",  "required": True},
            {"name": "namespace", "type": "string",  "required": False, "default": "default"},
            {"name": "lines",     "type": "integer", "required": False, "default": 100},
            {"name": "container", "type": "string",  "required": False,
             "description": "Specific container in multi-container pod"},
        ],
    },
    {
        "tool_name": "k8s_pod_describe",
        "name": "K8s Describe Pod",
        "description": "Full kubectl describe output: events, conditions, resource limits, and restart counts.",
        "command": "kubectl describe pod {pod} -n {namespace}",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "pod",       "type": "string", "required": True},
            {"name": "namespace", "type": "string", "required": False, "default": "default"},
        ],
    },
    {
        "tool_name": "k8s_events",
        "name": "K8s Events",
        "description": "Get recent Kubernetes events sorted by timestamp for a namespace or label selector.",
        "command": "kubectl get events -n {namespace} --sort-by=.lastTimestamp",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "namespace",      "type": "string", "required": False, "default": "default"},
            {"name": "label_selector", "type": "string", "required": False,
             "description": "Optional label filter e.g. app=myapp"},
        ],
    },
    {
        "tool_name": "k8s_top_pods",
        "name": "K8s Top Pods",
        "description": "CPU and memory resource usage for all pods in a namespace.",
        "command": "kubectl top pods -n {namespace} --sort-by={sort_by}",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "namespace", "type": "string", "required": False, "default": "default"},
            {"name": "sort_by",   "type": "string", "required": False, "default": "cpu",
             "description": "cpu | memory"},
        ],
    },
    {
        "tool_name": "k8s_rollout_status",
        "name": "K8s Rollout Status",
        "description": "Check the progress and health of a Kubernetes deployment rollout.",
        "command": "kubectl rollout status deployment/{deployment} -n {namespace}",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string", "required": True},
            {"name": "namespace",  "type": "string", "required": False, "default": "default"},
        ],
    },
    {
        "tool_name": "k8s_pod_status",
        "name": "K8s Pod Status",
        "description": "List pods and their phase/status using a label selector.",
        "command": "kubectl get pods -n {namespace} -l {label_selector} -o wide",
        "category": "diagnostic", "blast_radius": 1, "requires_approval": False,
        "parameters": [
            {"name": "namespace",      "type": "string", "required": False, "default": "default"},
            {"name": "label_selector", "type": "string", "required": True,
             "description": "e.g. app=myapp"},
        ],
    },
    # ══ KUBERNETES — REMEDIATION SAFE ════════════════════════════════════════
    {
        "tool_name": "k8s_rollout_restart",
        "name": "K8s Rollout Restart",
        "description": "Trigger a rolling restart of a Kubernetes deployment (zero-downtime).",
        "command": "kubectl rollout restart deployment/{deployment} -n {namespace}",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string", "required": True},
            {"name": "namespace",  "type": "string", "required": False, "default": "default"},
        ],
    },
    {
        "tool_name": "k8s_scale",
        "name": "K8s Scale Deployment",
        "description": "Set replica count for a Kubernetes deployment.",
        "command": "kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}",
        "category": "remediation_safe", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "deployment", "type": "string",  "required": True},
            {"name": "namespace",  "type": "string",  "required": False, "default": "default"},
            {"name": "replicas",   "type": "integer", "required": True},
            {"name": "min_cap",    "type": "integer", "required": False, "default": 1,
             "description": "Safety floor"},
        ],
    },
    # ══ KUBERNETES — REMEDIATION INTRUSIVE ═══════════════════════════════════
    {
        "tool_name": "k8s_delete_pod",
        "name": "K8s Delete Pod",
        "description": "Delete a pod so the ReplicaSet immediately recreates it. grace_seconds=0 for stuck Terminating pods.",
        "command": "kubectl delete pod {pod} -n {namespace} --grace-period={grace_seconds}",
        "category": "remediation_intrusive", "blast_radius": 2, "requires_approval": False,
        "parameters": [
            {"name": "pod",           "type": "string",  "required": True},
            {"name": "namespace",     "type": "string",  "required": False, "default": "default"},
            {"name": "grace_seconds", "type": "integer", "required": False, "default": 30,
             "description": "0 = force delete"},
        ],
    },
    {
        "tool_name": "k8s_cordon_node",
        "name": "K8s Cordon Node",
        "description": "Mark a Kubernetes node as unschedulable — no new pods will be placed on it.",
        "command": "kubectl cordon {node}",
        "category": "remediation_intrusive", "blast_radius": 2, "requires_approval": True,
        "parameters": [
            {"name": "node", "type": "string", "required": True},
        ],
    },
    {
        "tool_name": "k8s_drain_node",
        "name": "K8s Drain Node",
        "description": "Cordon node and gracefully evict all pods. Requires manual approval.",
        "command": "kubectl drain {node} --ignore-daemonsets --delete-emptydir-data --grace-period={grace_sec}",
        "category": "remediation_intrusive", "blast_radius": 3, "requires_approval": True,
        "parameters": [
            {"name": "node",      "type": "string",  "required": True},
            {"name": "grace_sec", "type": "integer", "required": False, "default": 60},
            {"name": "ignore_ds", "type": "boolean", "required": False, "default": True,
             "description": "Ignore DaemonSet pods"},
        ],
    },
]

ok = skip = fail = 0
for action in NEW_ACTIONS:
    r = requests.post("http://localhost:8000/api/approved-actions", json=action)
    if r.status_code == 201:
        ok += 1
        print(f"  ✓  {action['tool_name']}")
    elif r.status_code == 409:
        skip += 1
        print(f"  -  {action['tool_name']} (already exists)")
    else:
        fail += 1
        print(f"  ✗  {action['tool_name']} → {r.status_code}: {r.text[:100]}")

print(f"\nDone: {ok} created, {skip} skipped, {fail} failed  (total catalog: {ok + skip + 40} actions)")
