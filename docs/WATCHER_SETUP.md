# Watcher Service — Setup & Configuration Guide

**Last updated:** 2026-06-07  
**Platform version:** v1.1.2

---

## Overview

The Watcher subsystem consists of two containers that work together to detect infrastructure anomalies and feed them into the incident management pipeline:

| Container | Role |
|-----------|------|
| `sentinel_senses` | Runs `bpftrace` on the **host kernel** via eBPF. Observes all containers simultaneously without per-container agents. Emits syscall event counts as JSON. |
| `watcher_brain` | Python orchestration layer. Reads signals from `sentinel_senses`, polls Docker stats for CPU/memory/network metrics, applies configurable thresholds, raises incidents via the backend API, and auto-discovers containers into the Neo4j CMDB. |

Both containers start automatically with `docker compose up -d`. No separate installation or process management is required.

---

## Architecture

```
Host kernel
   │  (eBPF / bpftrace)
   ▼
sentinel_senses ──JSON stream──► watcher_brain
                                      │
                         ┌────────────┴─────────────┐
                         │  Docker stats polling     │
                         │  (CPU, memory, network)   │
                         └────────────┬─────────────┘
                                      │  HTTP POST
                                      ▼
                              agentic_os_backend
                              POST /api/monitoring-events
                                      │
                              ┌───────┴──────────┐
                              │ incident pipeline │
                              │ storm detection   │
                              │ CMDB discovery    │
                              └───────────────────┘
```

### eBPF availability

`sentinel_senses` uses `bpftrace` with the Linux kernel's tracepoint infrastructure. This requires:
- A Linux host with kernel 5.4+
- The container runs with `privileged: true` and `pid: host`

On **Windows (Docker Desktop with WSL2)** and **macOS (Docker Desktop)**, the eBPF layer starts but cannot attach to tracepoints outside its container. The watcher falls back to Docker stats monitoring only — CPU, memory, network, and disk metrics are still collected and anomalies are still detected.

---

## Default Configuration

Watcher thresholds are set via environment variables in `docker-compose.yml`. The `.env` file can override any of these.

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHER_POLL_INTERVAL` | `10` | Seconds between monitoring polls |
| `WATCHER_CPU_THRESHOLD` | `80.0` | CPU % that triggers a `high_cpu` anomaly |
| `WATCHER_MEMORY_THRESHOLD` | `90.0` | Memory % that triggers a `high_memory` anomaly |
| `WATCHER_DISK_THRESHOLD` | `90.0` | Disk usage % that triggers a `disk_full` anomaly |
| `WATCHER_CONNECTION_THRESHOLD` | `1000` | TCP connection count that triggers a `connection_spike` anomaly |
| `WATCHER_ANOMALY_THRESHOLD` | `1000` | Syscalls/5s that triggers a `high_syscall_intensity` anomaly |
| `WATCHER_MIN_CONSECUTIVE_POLLS` | `3` | Number of consecutive anomalous polls before opening an incident (noise filter) |
| `WATCHER_COOLDOWN_SECONDS` | `60` | Seconds after an incident before the same resource can trigger another |
| `WATCHER_DISCOVERY_ENABLED` | `true` | Auto-discover Docker containers into Neo4j CMDB |
| `WATCHER_DISCOVERY_INTERVAL_POLLS` | `15` | Run CMDB discovery every N polls (≈ every 2.5 min at default interval) |

### Changing a threshold

Edit `.env` or `docker-compose.yml`, then restart the watcher:

```bash
# Example: lower CPU threshold to 70%
# In .env:
WATCHER_CPU_THRESHOLD=70.0

docker compose restart watcher
```

---

## Anomaly Types

The watcher detects and reports the following anomaly types. Each produces a `monitoring_event` that is normalised and enters the full incident pipeline.

| Anomaly Type | Detection Source | Description |
|---|---|---|
| `high_syscall_intensity` | eBPF (sentinel_senses) | Syscall rate exceeds `WATCHER_ANOMALY_THRESHOLD` over a 5-second window |
| `high_cpu` | Docker stats | Container CPU % exceeds `WATCHER_CPU_THRESHOLD` |
| `high_memory` | Docker stats | Container memory % exceeds `WATCHER_MEMORY_THRESHOLD` |
| `disk_full` | `df -B1` per container | Filesystem utilisation exceeds `WATCHER_DISK_THRESHOLD` — read directly from `df` inside each container (v1.1.2+), not from Docker stats |
| `connection_spike` | Docker stats | TCP connection count exceeds `WATCHER_CONNECTION_THRESHOLD` |
| `health_check_failed` | HTTP probe | Container health endpoint returns non-200 or times out |
| *(custom)* | Log monitor | Pattern matched in container docker logs or a log file — event type is operator-configured (e.g. `log_error_detected`) |
| `condition_cleared` | All sources | Previously anomalous condition has normalised (triggers auto-resolution) |

---

## Log Monitors

Log monitors watch container stdout/stderr (via `docker logs`) or a log file inside the watcher container for regex pattern matches. When enough matching lines are found in a single poll, a monitoring event is raised. When the pattern stops matching for a configurable number of consecutive polls, an all-clear is sent.

### Configuration

Log monitors are configured through the platform UI at **Settings → Log Monitors**, or via the API (`GET/POST/PATCH/DELETE /api/monitoring/watchers/{id}/log-monitors`). They are stored in the database and pushed to the watcher automatically — no restart required.

| Field | Default | Description |
|-------|---------|-------------|
| **Name** | — | Unique display name (used as the monitor identifier) |
| **Source** | `docker` | `docker` — tail a named container's stdout/stderr via `docker logs`; `file` — tail a log file path inside the watcher container |
| **Container** | — | Container name to watch (docker source only), e.g. `agentic_os_backend` |
| **Log File** | — | Absolute path inside the watcher container (file source only) |
| **Pattern** | — | Python regex matched against each log line (case-insensitive) |
| **Event Type** | `log_error_detected` | Monitoring event type emitted when the pattern fires |
| **Min Occurrences** | `1` | Minimum matching lines per poll interval before the event fires |
| **Severity** | `warning` | Raw criticality sent to the incident pipeline: `info`, `warning`, `high`, or `critical` |
| **Clear After Polls** | `3` | Consecutive quiet polls (no match) required before an all-clear is sent. `0` = immediate all-clear on first quiet poll |
| **Poll Interval** | `30` | Seconds between log polls for this monitor |

### How `clear_after_polls` works

When a log monitor condition is active and the pattern stops appearing, the watcher does **not** immediately send an all-clear. Instead it counts consecutive quiet polls. Only when `quiet_count >= clear_after_polls` does it emit `condition_cleared`. This prevents false recoveries from log bursts that momentarily stop between polls.

```
Poll 1: 2 ERROR lines matched → incident fired
Poll 2: 0 matches → quiet 1/3 — holding all-clear
Poll 3: 0 matches → quiet 2/3 — holding all-clear
Poll 4: 0 matches → quiet 3/3 — releasing all-clear → condition_cleared sent
```

Set `clear_after_polls: 0` for immediate all-clear on the first quiet poll (suitable for very high-frequency monitors or one-shot alerts).

### Testing a log monitor

Inject matching lines directly into a container's docker log stream:

```bash
# Inject 2 JSON-format ERROR lines into the backend container
docker exec agentic_os_backend sh -c '
  TS=$(date -u +"%Y-%m-%dT%H:%M:%S")
  printf "{\"levelname\": \"ERROR\", \"message\": \"test error 1\", \"asctime\": \"$TS\"}\n" >> /proc/1/fd/2
  printf "{\"levelname\": \"ERROR\", \"message\": \"test error 2\", \"asctime\": \"$TS\"}\n" >> /proc/1/fd/2
'

# Watch the watcher detect it (within one poll interval)
docker logs watcher_brain -f | grep -E "LOG-MONITOR|SUSTAINED|quiet poll|all-clear"
```

### Kubernetes limitation

The `docker` source mode runs `docker logs <container>` as a subprocess and requires the Docker socket (`/var/run/docker.sock`) to be mounted in the watcher pod. **This is not available on Kubernetes** (containerd/CRI-O clusters have no Docker daemon). See [WATCHER_KUBERNETES.md](./WATCHER_KUBERNETES.md#log-monitors-on-kubernetes) for alternatives.

---

## Multi-Condition Tracking

The watcher tracks each active condition **per resource, per anomaly type** in its `active_conditions` dict. This means:

- A container can have simultaneous `high_cpu` and `high_memory` conditions open
- Clearing one condition does not close incidents tied to other conditions on the same container
- Each `condition_cleared` event is targeted at a specific `resource_id` + `anomaly_type` pair

When a condition clears, the watcher posts `POST /api/monitoring-events` with `event_type: condition_cleared`. The backend locates all open incidents for that resource and anomaly type and resolves them with `resolution_source = watcher_all_clear`.

---

## CMDB Auto-Discovery

When `WATCHER_DISCOVERY_ENABLED=true`, the watcher periodically queries the Docker daemon for all running containers and creates or updates `ConfigurationItem` nodes in Neo4j. Each CI records:

- Container name and ID
- Image name and tag
- Current status (running, stopped, etc.)
- Exposed ports
- Docker network memberships

This gives the blast radius analysis in RiskAssessor a live, auto-maintained CMDB without manual data entry.

To trigger discovery immediately:

```bash
docker exec watcher_brain python -c "
from watcher_main import discovery_agent
import asyncio
asyncio.run(discovery_agent.discover())
"
```

---

## Logs

View watcher logs:

```bash
# Follow live output
docker logs watcher_brain -f

# Watcher detected an anomaly
docker logs watcher_brain | grep "anomaly\|incident\|THRESHOLD"

# eBPF / sentinel output
docker logs sentinel_senses -f

# Show last 50 lines
docker logs watcher_brain --tail 50
```

Expected healthy output from watcher_brain:
```
[Watcher] Poll 42 — agentic_os_backend: cpu=12.3% mem=45.1%
[Watcher] Poll 42 — agentic_os_neo4j: cpu=8.1% mem=72.4%
[Watcher] Discovery: 8 containers → Neo4j CMDB updated
```

Expected anomaly detection output:
```
[Watcher] ANOMALY: agentic_os_neo4j cpu=91.2% (threshold=80.0%) — poll 3/3
[Watcher] Opening incident: resource=agentic_os_neo4j type=high_cpu
[Watcher] POST /api/monitoring-events → 201 Created (incident INC0047)
```

Expected all-clear output:
```
[Watcher] CLEARED: agentic_os_neo4j cpu=14.3% (below threshold)
[Watcher] Emitting condition_cleared for agentic_os_neo4j / high_cpu
```

---

## Generating a Test Incident

Trigger a `high_syscall_intensity` anomaly by running a busy loop in any container:

```bash
# Start the load (watcher detects within 10-20 seconds)
docker exec -d agentic_os_neo4j sh -c "yes > /dev/null"

# Watch watcher detect it
docker logs watcher_brain -f

# Watch the backend create the incident
docker logs agentic_os_backend -f

# Open http://localhost:3000 — an INC000X will appear on the dashboard

# Stop the load after 30-60 seconds (triggers all-clear → auto-resolution)
docker exec agentic_os_neo4j pkill yes
```

---

## Sending a Manual Monitoring Event

You can inject monitoring events directly into the backend API (useful for testing integrations or simulating cleared conditions):

```bash
# Simulate a high_cpu anomaly
curl -X POST http://localhost:8000/api/monitoring-events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(grep WATCHER_API_KEY .env | cut -d= -f2)" \
  -d '{
    "source": "watcher_brain",
    "event_type": "high_cpu",
    "resource_name": "my-service",
    "raw_criticality": "high",
    "raw_payload": {
      "cpu_percent": 91.2,
      "description": "CPU spike"
    }
  }'

# Simulate a condition cleared
curl -X POST http://localhost:8000/api/monitoring-events \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(grep WATCHER_API_KEY .env | cut -d= -f2)" \
  -d '{
    "source": "watcher_brain",
    "event_type": "condition_cleared",
    "resource_name": "my-service",
    "raw_criticality": "info",
    "raw_payload": {
      "original_event_type": "high_cpu",
      "description": "CPU normalised"
    }
  }'
```

---

## Troubleshooting

### Watcher not starting

```bash
docker logs watcher_brain --tail 50
```

Common causes:

**`WATCHER_API_KEY not set`** — run the installer (`install.sh` / `install.bat`) or add the key to `.env`.

**`Connection refused to http://backend:8000`** — the backend is not yet healthy. The watcher depends on the backend; if the backend is still starting, the watcher retries automatically. Wait for `docker compose ps` to show backend as `(healthy)`.

### No incidents being created despite high load

1. Check `WATCHER_MIN_CONSECUTIVE_POLLS` — the anomaly must persist for this many consecutive polls before an incident is opened. With the default of 3 and a 10-second poll interval, the condition must be sustained for ~30 seconds.

2. Check the cooldown — if an incident was recently created for the same resource, the cooldown period (`WATCHER_COOLDOWN_SECONDS`) suppresses new incidents.

3. Verify sentinel_senses is running:
   ```bash
   docker exec sentinel_senses bpftrace -e 'BEGIN { print("ok\n"); exit(); }'
   # Should print: ok
   ```

### eBPF errors in sentinel_senses logs

On non-Linux hosts (Windows/macOS), errors like `ERROR: failed to attach kprobe` are expected and non-fatal. Docker stats monitoring continues to function.

On Linux, if you see permission errors:
```
Error opening BPF kernel id: permission denied
```
Ensure the container is running with `privileged: true` and the host kernel is 5.4 or later.

### Duplicate incidents for the same resource

Reduce `WATCHER_COOLDOWN_SECONDS` or verify that the condition is genuinely clearing between incidents. If the all-clear is not being emitted, check the backend logs for errors handling `condition_cleared` events.

### Watcher consuming excessive CPU

Increase `WATCHER_POLL_INTERVAL` (e.g., from `10` to `20`). Edit `.env` and restart:
```bash
docker compose restart watcher
```

---

## Integration with External Monitoring Tools

The Watcher subsystem is one of two event ingest paths. The **Connector Hub** provides webhook-based ingest for external monitoring tools (Datadog, Dynatrace, Splunk, Prometheus, PagerDuty, Zabbix, ServiceNow). All events — whether from the watcher or from connectors — are normalised by `EventQualificationService` before entering the incident pipeline and storm detection.

See **[ADMIN_GUIDE.md § Connector Hub](./ADMIN_GUIDE.md)** for connector configuration.

---

## State Files

The watcher persists lightweight state to two JSON files mounted from the host:

| File | Contents |
|------|----------|
| `backend/.state/watcher_config.json` | Runtime configuration snapshot |
| `backend/.state/watcher_status.json` | Last poll timestamp, active condition counts |

These files survive container restarts. If you need to reset watcher state (e.g., to clear stuck conditions after maintenance):

```bash
docker compose stop watcher
echo '{}' > backend/.state/watcher_status.json
docker compose start watcher
```
