# Platform-Aware Runbook Selection

## Overview

Axiometica AIR introduces intelligent, deployment-platform-aware runbook selection (available from v1.0.0+). The system understands whether your services run in Docker containers, Kubernetes clusters, bare-metal Linux, or Windows servers — and automatically selects the right remediation strategy for each.

## Quick Example

**Scenario**: High syscall intensity detected on `agentic_os_neo4j` (a Neo4j database)

**Before v1.0.0**:
- System would select generic runbook: "High Syscall Intensity — Process Termination"
- Uses standard process kill, may not work in containerized environment

**v1.0.0+**:
- System detects: resource_type='graph-database', cmdb_platform='linux'
- Infers: "This is a Dockerized service" → deployment_platform='docker'
- Selects: "Kill Anomaly Process on Docker" (platform-specific runbook)
- Uses: `docker kill <container>` instead of bare OS `kill`

## How Platform Detection Works

### 1. Resource Type → Deployment Platform Inference

The system maps CMDB resource types to deployment platforms:

| Resource Type | Deployment Platform | Logic |
|---|---|---|
| `graph-database` | docker | Containerized database service |
| `database` | docker | Containerized database |
| `microservice` | docker | Cloud-native microservice |
| `worker` | docker | Background task processor |
| `cache` | docker | In-memory cache (Redis, Memcached) |
| `web-application` | docker | Web service |
| `frontend` | docker | Frontend service |
| `api` | docker | API service |
| `pod` | kubernetes | Kubernetes pod (explicit) |
| `kubernetes` | kubernetes | K8s resource |
| `vm` | linux | Virtual machine |
| `host` | linux | Bare-metal server |
| `server` | linux | Generic server |
| `service` | any | Generic service (ambiguous) |

### 2. Smart Inference with CMDB Context

When available, the system combines resource_type + CMDB platform information:

```
Resource:  graph-database
CMDB Platform: linux
         ↓
Inference: "Linux OS indicates the *container OS*, not deployment model"
         ↓
Deployment Platform: docker
```

**Why?** In cloud-native environments, database containers often run Linux inside. The OS isn't the deciding factor for remediation — the container orchestration platform is.

### 3. Explicit Platform Declarations

You can explicitly mark resources with their deployment platform in the CMDB:

```json
{
  "name": "my-k8s-database",
  "type": "database",
  "platform": "kubernetes"  // Explicit declaration takes precedence
}
```

Explicit declarations always take precedence over inference.

## Platform-Specific Runbooks

Each anomaly type can have platform-specific runbook variants:

### High Syscall Intensity

| Platform | Runbook | Tool | Command |
|---|---|---|---|
| docker | Kill Anomaly Process on Docker | process_kill | `docker kill <container_id>` |
| kubernetes | Kill Pod for High Syscalls | kubectl_scale | `kubectl delete pod <pod>` |
| linux | High Syscall Intensity — Process Termination | process_kill | `kill -9 <pid>` |
| any | High Syscall Intensity — Process Termination | process_kill | Generic process kill |

### High CPU Usage

| Platform | Runbook | Tool | Action |
|---|---|---|---|
| docker | Scale Docker Service | docker_service | Scale container replicas |
| kubernetes | Scale Kubernetes Deployment | kubectl_scale | Scale pod replicas |
| linux | Kill Process on Linux | process_kill | Kill high-CPU process |
| any | Generic CPU Remediation | process_kill | Generic remediation |

## Using Platform-Aware Runbooks

### Creating Platform-Specific Runbooks

When creating a runbook, specify the target platform:

```yaml
name: "Kill Anomaly Process on Docker"
event_type: "high_syscall_intensity"
service: null  # Works for any service
platform: "docker"  # THIS IS THE KEY
confidence: 0.87
enabled: true

steps:
  diagnostic:
    - type: "diagnostic"
      name: "Profile Process"
      tool: "trace_syscalls"
      args:
        container_id: "{container_id}"
  
  remediation:
    - type: "remediation"
      name: "Kill Container"
      tool: "process_kill"
      args:
        method: "docker_kill"
        target_id: "{container_id}"
```

### Platform-Aware Tool Selection

Tools can declare which platforms they support:

```python
{
    "name": "trace_syscalls",
    "description": "Trace system calls of a process",
    "platforms": ["docker", "linux"],  # Works on both
    "commandVariants": {
        "docker": "docker exec {container} strace -c -e trace=syscall {process}",
        "linux": "strace -c -e trace=syscall -p {pid}",
    }
}
```

When a runbook executes on a docker platform, the system substitutes `commandVariants['docker']`.

## Runbook Selection Algorithm

The MechanicAgent uses a 4-pass cascade:

### Pass 1: Most Specific
```sql
WHERE event_type = 'high_syscall_intensity'
  AND service IS NULL
  AND platform = 'docker'  -- EXACT match on platform
ORDER BY success_rate DESC, confidence DESC
```

Result: "Kill Anomaly Process on Docker" (if exists)

### Pass 2: Service-Specific, Platform-Agnostic
```sql
WHERE event_type = 'high_syscall_intensity'
  AND service IS NULL
  AND platform = 'any'
ORDER BY success_rate DESC, confidence DESC
```

Result: Generic runbook (fallback if Pass 1 found nothing)

### Pass 3: Generic for Platform
```sql
WHERE event_type = 'high_syscall_intensity'
  AND service IS NULL
  AND platform = 'kubernetes'  -- Different platform match
ORDER BY success_rate DESC, confidence DESC
```

Result: Platform-specific fallback from different platform

### Pass 4: Fully Generic
```sql
WHERE event_type = 'high_syscall_intensity'
  AND service IS NULL
  AND platform = 'any'
ORDER BY success_rate DESC, confidence DESC
```

Result: Universal fallback (always exists)

## Chat Integration

When you ask the chat about runbooks with an incident open in the UI, the system now:

1. **Extracts platform** from the incident's CMDBContext
2. **Filters runbooks** by that platform:
   - Platform-specific runbooks appear first
   - Generic ('any') runbooks appear second
3. **Suggests** the most relevant remediation strategy

Example:
```
User: "What runbooks apply to this incident?"
Chat: "Based on the incident's Docker platform, I recommend:
      1. Kill Anomaly Process on Docker (87% confidence)
      2. High Syscall Intensity — Process Termination (94% confidence)
```

## Migration from v4.x

### No Action Required

Existing runbooks without platform tags will default to `platform='any'` and work as before.

Existing incidents will have their platform re-derived on the next workflow execution using the new smart inference logic.

### Optional: Add Platform Tags to Existing Runbooks

To get the benefits of platform-aware selection immediately:

```bash
# View current runbooks
curl http://localhost:8000/api/runbooks

# Update a runbook with platform
curl -X PUT http://localhost:8000/api/runbooks/<id> \
  -H "Content-Type: application/json" \
  -d '{"platform": "docker"}'
```

### Optional: Seed New Platform-Specific Runbooks

```bash
# Run the seed script
python backend/src/agentic_os/db/platform_seed_data.py
```

This adds pre-built runbooks for Docker, Kubernetes, and Linux platforms.

## Best Practices

### 1. Tag Your Runbooks

Always include a `platform` field when creating runbooks:
- `docker` - Container-based services
- `kubernetes` - K8s-orchestrated services
- `linux` - Bare-metal or VM servers
- `windows` - Windows servers
- `any` - Universal (works everywhere)

### 2. Create Platform Variants

For common remediation types, create variants for different platforms:

```
High CPU → Kill Process
  ├─ docker:     Scale containers
  ├─ kubernetes: Scale pods
  ├─ linux:      Kill process
  └─ any:        Generic fallback
```

### 3. Use Confidence Scores

When you have multiple runbooks for the same event:
- Platform-specific: confidence=0.85-0.95
- Platform-agnostic: confidence=0.70-0.85
- Generic fallback: confidence=0.50-0.70

The system will select based on success_rate first, then confidence.

### 4. Document Assumptions

In the runbook description, note the platform assumptions:

```
"Kill Anomaly Process on Docker" — 
Works only in Docker environments. 
Uses 'docker kill' to terminate container. 
For Kubernetes, use K8s-variant runbook.
For bare Linux, use process_kill runbook.
```

## Troubleshooting

### Platform Not Being Detected

Check the incident's context to see what was derived:

```bash
curl http://localhost:8000/api/workflows/<incident_id> | jq '.context.cmdb.platform'
```

If it's wrong, check:
1. **CMDB data**: Is the resource_type correct?
2. **Neo4j graph**: Does the resource exist?
3. **Logs**: Check `[LIBRARIAN]` and `[MECHANIC]` logs

### Wrong Runbook Being Selected

The system uses a 4-pass cascade. To debug:

1. Check incident platform: `curl ... | jq '.context.cmdb.platform'`
2. Check available runbooks:
   ```bash
   curl "http://localhost:8000/api/runbooks?event_type=high_syscall_intensity"
   ```
3. Check logs for cascade results:
   ```
   [_lookup_runbook] No match in pass (svc=None, plat=docker)
   [_lookup_runbook] Matched 'Generic...' (svc=None, plat=any)
   ```

### Tool Command Not Working

Check the platform-variant mapping:

```bash
# Get tool definition
curl http://localhost:8000/api/tools/process_kill | jq '.commandVariants'

# Expected output:
{
  "docker": "docker kill {container_id}",
  "linux": "kill -9 {pid}",
  "kubernetes": "kubectl delete pod {pod_name}"
}
```

## See Also

- [Runbook Design Guide](./RUNBOOK_DESIGN.md)
- [Tool Development Guide](./TOOL_DEVELOPMENT.md)
- [Incident Workflow](./INCIDENT_WORKFLOW.md)
