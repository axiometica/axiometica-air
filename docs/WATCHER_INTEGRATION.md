# Watcher Brain-and-Senses Integration

## Overview

The Watcher system implements an AI-driven, kernel-level anomaly detection and autonomous remediation architecture using a **Brain-and-Senses** model:

- **Sentinel (Senses)**: eBPF-based kernel monitor that traces syscalls and detects raw resource anomalies
- **Watcher (Brain)**: Python agent that consumes telemetry, applies intelligent analysis, and orchestrates incident response

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Host Kernel                                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ syscalls: sys_enter (5-second window)                │  │
│  │ Process: [nginx, python, redis, ...] with counts     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ eBPF tracepoint
                            │ (JSON stream)
                            ▼
┌──────────────────────────────────────────┐
│ Sentinel (Senses)                        │
│ Container: sentinel_senses               │
│ Image: quay.io/iovisor/bpftrace:latest   │
│ • Privileged mode                        │
│ • Access to /sys, /dev, /lib/modules     │
│ • bpftrace syscall monitoring            │
│ • Outputs: JSON telemetry stream         │
└──────────────────────────────────────────┘
                            │
                            │ docker exec bpftrace
                            │ (subprocess read)
                            ▼
┌──────────────────────────────────────────┐
│ Watcher Brain (AI Agent)                 │
│ Container: watcher_brain                 │
│ • Polls Sentinel every N seconds         │
│ • Detects anomalies (>1000 syscalls/5s, configurable)   │
│ • Analyzes context (process name, etc)   │
│ • Creates incidents via API              │
│ • Executes remediation (pkill)           │
│ • Manages cooldowns & feedback           │
└──────────────────────────────────────────┘
                            │
                            │ HTTP POST
                            │ /workflows/incident
                            ▼
┌──────────────────────────────────────────────────────┐
│ Axiometica AIR Platform                                   │
│ • Receives incident from Watcher                    │
│ • Orchestrates 6-agent incident workflow            │
│ • Routing: Sentinel → Librarian → Risk Assessor     │
│ • Mechanic → Policy Broker → Tool Registry          │
│ • Verifier                                          │
│ • Stores reasoning in PostgreSQL                    │
└──────────────────────────────────────────────────────┘
```

## Key Components

### 1. Sentinel Container (Senses)

**Image**: `quay.io/iovisor/bpftrace:latest`

**Purpose**: Kernel-level monitoring of syscalls without application instrumentation

**How it works**:
```bash
bpftrace -f json -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }'
```

- Hooks into `raw_syscalls:sys_enter` tracepoint
- Counts syscalls per process (by `comm` field)
- Outputs JSON in 5-second windows
- Runs in privileged mode with kernel debug files

**Requirements**:
- Privileged container
- Volume mounts: `/sys`, `/dev`, `/lib/modules`
- Kernel must have tracepoint support (Linux 4.7+)

### 2. Watcher Service (Brain)

**Location**: `backend/src/agentic_os/services/watcher_service.py`

**Class**: `WatcherService`

**Key Methods**:
- `get_kernel_telemetry()`: Read syscall data from Sentinel
- `detect_anomaly()`: Statistical analysis (>threshold = anomaly)
- `submit_incident_to_platform()`: HTTP POST to `/workflows/incident`
- `execute_remediation()`: `docker exec <container> pkill -9 <process>`
- `is_in_cooldown()`: Prevent alert fatigue
- `run()`: Main async event loop

### 3. Watcher Entrypoint

**Location**: `backend/watcher_main.py`

**Purpose**: Standalone async entrypoint for Watcher container

**Execution**:
```
docker run watcher_brain python watcher_main.py
```

### 4. Watcher Dockerfile

**Location**: `backend/Dockerfile.watcher`

**Key features**:
- Python 3.11 slim base
- Docker CLI installed (for `docker exec` remediation)
- Mounts docker socket (`/var/run/docker.sock`)
- Sets PYTHONPATH to `/app/src`

## Configuration

### Environment Variables

Set in `docker-compose.yml` `watcher` service:

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTINEL_CONTAINER` | `sentinel_senses` | Name of Sentinel container |
| `WATCHER_API_URL` | `http://backend:8000` | Axiometica AIR API base URL |
| `WATCHER_POLL_INTERVAL` | `10` | Polling interval in seconds |
| `WATCHER_ANOMALY_THRESHOLD` | `20000` | Syscall count threshold |
| `WATCHER_COOLDOWN_SECONDS` | `60` | Cooldown period between incidents |

### Example Configuration

```yaml
watcher:
  environment:
    SENTINEL_CONTAINER: sentinel_senses
    WATCHER_API_URL: http://backend:8000
    WATCHER_POLL_INTERVAL: "10"
    WATCHER_ANOMALY_THRESHOLD: "20000"  # >20k = anomaly
    WATCHER_COOLDOWN_SECONDS: "60"
```

## Incident Flow

### 1. Anomaly Detection
```
Watcher polls Sentinel every 10 seconds:
  Syscall count: {nginx: 5000, python: 25000, redis: 3000}
  
  Top process: python with 25000 syscalls
  Threshold: 20000
  
  ANOMALY DETECTED ✓
```

### 2. Alert Creation
```
Alert Payload:
{
  "severity": "critical",
  "type": "high_syscall_intensity",
  "resource_name": "sentinel_senses",
  "description": "Kernel anomaly detected: process 'python' generated 25000 syscalls in 5-second window..."
}
```

### 3. Incident Submission
```
POST /workflows/incident HTTP/1.1
Host: backend:8000

{
  "severity": "critical",
  "type": "high_syscall_intensity",
  "resource_name": "sentinel_senses",
  "description": "..."
}

Response:
{
  "workflow_id": "cf5c728b-fad9-4be6-a72d-6e673cb4a507",
  "lifecycle_state": "open",
  "reasoning_trace": [...]
}
```

### 4. Orchestration
The Axiometica AIR platform receives the incident and orchestrates:

1. **SentinelAgent**: Classifies severity (CRITICAL)
2. **LibrarianAgent**: Maps dependencies and context
3. **RiskAssessorAgent**: Calculates risk score
4. **MechanicAgent**: Selects remediation playbook
5. **PolicyBrokerAgent**: Determines if auto-execute or manual approval
6. **ToolRegistryAgent**: Executes remediation action
7. **VerifierAgent**: Post-remediation health check

### 5. Remediation Execution
```
If PolicyBrokerAgent approves:
  docker exec sentinel_senses pkill -9 python
  
  ✓ Process terminated
  
Send all-clear to platform:
  POST /workflows/incident with status=resolved
```

### 6. Cooldown
After incident creation/remediation, Watcher enters 60-second cooldown to prevent alert fatigue.

## Telemetry & Status Monitoring

### Status File

**Location**: `backend/.state/watcher_status.json`

**Content**:
```json
{
  "sentinel_container": "sentinel_senses",
  "state": "incident_triggered",
  "active_incident_id": "INC-WATCHER-20260508T195000Z",
  "last_anomaly_process": "python",
  "last_syscall_count": 25000,
  "last_event_type": "high_syscall_intensity",
  "cooldown_until": "2026-05-08T19:51:00Z",
  "timestamp": "2026-05-08T19:50:00Z"
}
```

### Log Messages

Watcher outputs structured logs:

```
🚀 [INIT] Watcher Brain initialized for Sentinel: sentinel_senses
🔄 [LOOP START] Polling every 10s
📡 [STATUS] healthy
🚨 [ANOMALY] Process 'python': 25000 syscalls
📞 [PLATFORM CALL] Creating incident INC-WATCHER-20260508T195000Z
✓ [INCIDENT CREATED] ID: INC-WATCHER-20260508T195000Z, Workflow: cf5c728b-...
❄️  [COOLDOWN SET] 60s cooldown started
✓ [CLEARED] Anomaly resolved for process 'python'
```

## Usage Guide

### 1. Start All Services

```bash
cd axiometica-air
docker-compose up -d
```

Containers:
- `sentinel_senses`: eBPF monitor (privileged)
- `watcher_brain`: Incident orchestrator
- `agentic_os_backend`: FastAPI server
- `agentic_os_frontend`: React UI
- `agentic_os_postgres`: PostgreSQL
- `agentic_os_redis`: Redis
- `agentic_os_celery_worker`: Background tasks

### 2. Verify Services

```bash
# Check all containers are running
docker-compose ps

# View Watcher logs
docker logs -f watcher_brain

# View Sentinel telemetry
docker exec sentinel_senses bpftrace -f json -e \
  'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } interval:s:5 { exit(); }'
```

### 3. Test Integration

```bash
# Run test script
bash scripts/test_watcher.sh

# Or manually test:
curl -X POST http://localhost:8000/workflows/incident \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "critical",
    "type": "high_syscall_intensity",
    "resource_name": "test-service",
    "description": "Test incident"
  }'
```

### 4. Monitor Incidents

```bash
# List incidents
curl http://localhost:8000/workflows?workflow_type=incident&limit=10

# Get specific workflow
curl http://localhost:8000/workflows/{workflow_id}

# View reasoning trace
curl http://localhost:8000/workflows/{workflow_id} | jq '.reasoning_trace'
```

## Tuning Parameters

### Anomaly Threshold

**Current**: 20,000 syscalls per 5-second window

**Adjust based on**:
- Workload: High-load services may need higher threshold
- False positives: Increase if too many alerts
- Detection sensitivity: Decrease for early warning

**Example**:
```yaml
WATCHER_ANOMALY_THRESHOLD: "30000"  # More conservative
```

### Polling Interval

**Current**: 10 seconds

**Trade-offs**:
- Lower: Faster anomaly detection, more CPU usage
- Higher: Less frequent telemetry read, delayed response

**Example**:
```yaml
WATCHER_POLL_INTERVAL: "5"  # Faster detection
```

### Cooldown Period

**Current**: 60 seconds

**Prevents**:
- Alert fatigue from persistent anomalies
- Rapid retries of remediation actions

**Example**:
```yaml
WATCHER_COOLDOWN_SECONDS: "120"  # Longer cooldown
```

## Troubleshooting

### Issue: Watcher container crashes

**Symptoms**:
```
docker logs watcher_brain
Error: Cannot connect to Sentinel
```

**Solution**:
1. Check Sentinel is running: `docker ps | grep sentinel_senses`
2. Verify container name matches: `SENTINEL_CONTAINER: sentinel_senses`
3. Check network connectivity: `docker network inspect agenticplatform_v2_agentic_os_network`

### Issue: No syscall data from Sentinel

**Symptoms**:
```
No telemetry collected
```

**Solution**:
1. Verify eBPF support: `docker exec sentinel_senses uname -r`
2. Check kernel has tracepoint support: `docker exec sentinel_senses cat /proc/tracepoints | grep raw_syscalls`
3. Run bpftrace manually: `docker exec -it sentinel_senses bpftrace -e 'BEGIN { print("OK"); }'`

### Issue: Remediation not executing

**Symptoms**:
```
Process still running after pkill attempt
```

**Solution**:
1. Check PolicyBrokerAgent approved the action
2. Verify Watcher has docker socket access: `docker exec watcher_brain docker ps`
3. Check permissions: `ls -la /var/run/docker.sock`

## Advanced Usage

### Custom Anomaly Detection

Extend `detect_anomaly()` for additional strategies:

```python
class WatcherService:
    def detect_anomaly(self) -> Tuple[bool, Optional[str], int]:
        # Current: syscall intensity
        
        # Could add:
        # - Syscall rate acceleration
        # - Process memory growth
        # - Network connection spikes
        # - File descriptor exhaustion
```

### Integration with CMDB

The Watcher can be extended to query the CMDB for context:

```python
from agentic_os.services.cmdb import get_cmdb

cmdb = get_cmdb()
resource_info = cmdb.get_resource_info("sentinel_senses")
dependencies = cmdb.get_dependencies("sentinel_senses")
```

### Custom Remediation Actions

Extend `execute_remediation()` for process-specific actions:

```python
def execute_remediation(self, process: str) -> bool:
    # Current: pkill -9
    
    # Could implement:
    if process == "memory_hog":
        return self.restart_container("memory_hog_svc")
    elif process == "cpu_spike":
        return self.reduce_concurrency("cpu_spike_svc")
    else:
        return self.terminate_process(process)
```

## Integration with Other Platforms

The Watcher system is designed to be platform-agnostic. To use with your original Axiometica AIR:

1. Ensure Neo4j CMDB is accessible
2. Update `WATCHER_API_URL` to point to your platform's API
3. Modify incident payload structure if needed
4. Test with `scripts/test_watcher.sh`

## Security Considerations

### Privileged Container

The Sentinel container runs in privileged mode with kernel debug access:
- **Risk**: Container escape could affect host kernel
- **Mitigation**: Use dedicated security policies, network isolation
- **Recommendation**: Run in isolated environment (VM/bare-metal)

### Docker Socket Mount

Watcher mounts `/var/run/docker.sock`:
- **Risk**: Full Docker API access
- **Mitigation**: Use separate user, apply socket permissions
- **Recommendation**: Run on isolated Docker daemon

### Remediation Actions

Watcher can execute `pkill` on host processes:
- **Risk**: Terminating critical processes could cause outage
- **Mitigation**: Policy Broker approval gates, cooldown periods
- **Recommendation**: Start with "recommend_only" decision, promote to auto-execute

## Performance

**Resource Usage**:
- Watcher: ~50-100MB RAM (Python)
- Sentinel: ~100-200MB RAM (bpftrace)
- Total overhead: <5% CPU at 10s polling interval

**Latency**:
- Anomaly detection: <1 second
- Incident creation: <2 seconds (HTTP POST)
- Remediation execution: <5 seconds (docker exec pkill)
- Total: ~8 seconds from anomaly to resolution

## References

- [eBPF](https://ebpf.io/) - Extended Berkeley Packet Filter
- [bpftrace](https://github.com/iovisor/bpftrace) - Dynamic tracing tool
- [Linux Kernel Tracepoints](https://www.kernel.org/doc/html/latest/trace/tracepoints.html)
- [Axiometica AIR Platform Architecture](../README.md)
