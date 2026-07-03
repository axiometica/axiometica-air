# Axiometica AIR v2 — Complete Technical Reference

> **Version:** v1.1.2 · **Last updated:** 2026-06-07  
> A single document covering every major subsystem: monitoring, event ingestion, the 7-agent incident pipeline, workflow state, the all-clear mechanism, Celery task execution, event sourcing, WebSocket updates, the database schema, and the frontend data path.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Infrastructure — Docker Services](#2-infrastructure--docker-services)
3. [Monitoring Subsystem](#3-monitoring-subsystem)
   - 3.1 [sentinel_senses — eBPF Kernel Monitor](#31-sentinel_senses--ebpf-kernel-monitor)
   - 3.2 [watcher_brain — Anomaly Detection Agent](#32-watcher_brain--anomaly-detection-agent)
   - 3.3 [Detection Strategies](#33-detection-strategies)
   - 3.4 [Per-Resource All-Clear Mechanism](#34-per-resource-all-clear-mechanism)
4. [Event Ingestion & Qualification](#4-event-ingestion--qualification)
   - 4.1 [POST /api/monitoring-events](#41-post-apimonitoring-events)
   - 4.2 [EventQualificationService](#42-eventqualificationservice)
   - 4.3 [Qualification Scoring Factors](#43-qualification-scoring-factors)
5. [The 7-Agent Incident Pipeline](#5-the-7-agent-incident-pipeline)
   - 5.1 [Typed Context Schema](#51-typed-context-schema)
   - 5.2 [SentinelAgent](#52-sentinelagent)
   - 5.3 [LibrarianAgent](#53-librarianagent)
   - 5.4 [RiskAssessorAgent](#54-riskassessoragent)
   - 5.5 [MechanicAgent](#55-mechanicagent)
   - 5.6 [PolicyBrokerAgent](#56-policybrokéragent)
   - 5.7 [ToolRegistryAgent](#57-toolregistryagent)
   - 5.8 [VerifierAgent](#58-verifieragent)
6. [Workflow Engine](#6-workflow-engine)
   - 6.1 [Step Types](#61-step-types)
   - 6.2 [Routing & Branching](#62-routing--branching)
   - 6.3 [Timeout Enforcement](#63-timeout-enforcement)
   - 6.4 [Human Approval Steps](#64-human-approval-steps)
7. [Incident State Model](#7-incident-state-model)
   - 7.1 [Decoupled State Fields](#71-decoupled-state-fields)
   - 7.2 [Lifecycle State Machine](#72-lifecycle-state-machine)
   - 7.3 [Incident Enumeration (INC0001)](#73-incident-enumeration-inc0001)
8. [Runbooks & Remediation](#8-runbooks--remediation)
9. [Celery Async Task Execution](#9-celery-async-task-execution)
10. [Event Sourcing & PostgreSQL Event Bus](#10-event-sourcing--postgresql-event-bus)
11. [WebSocket Real-Time Updates](#11-websocket-real-time-updates)
12. [REST API Reference](#12-rest-api-reference)
13. [Database Schema](#13-database-schema)
14. [CMDB — Neo4j Integration](#14-cmdb--neo4j-integration)
15. [Frontend Architecture](#15-frontend-architecture)
16. [Configuration & Environment Variables](#16-configuration--environment-variables)

---

## 1. System Overview

Axiometica AIR v2 is an enterprise ITSM automation platform. It watches infrastructure in real-time and autonomously manages incidents from raw anomaly detection through remediation and resolution, with human-in-the-loop governance for high-risk actions.

### End-to-end flow in one diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  MONITORING LAYER                                                            │
│                                                                              │
│  sentinel_senses ──► HOST KERNEL (bpftrace tracepoint:raw_syscalls)         │
│  (eBPF container)     Sees ALL processes on ALL containers via host ns       │
│         │                                                                    │
│         ▼                                                                    │
│  watcher_brain ──► poll every 10s ──► detect_anomaly()                      │
│                ├── Syscalls > threshold?    →  high_syscall_intensity        │
│                ├── CPU  > 80%?             →  cpu_spike                      │
│                ├── Memory > 90%?           →  memory_surge                  │
│                ├── Disk > 90%?             →  disk_full                      │
│                ├── HTTP health check fail? →  health_check_failed            │
│                └── Log errors?            →  log_error                      │
│                         │                                                    │
│                         ▼ POST /api/monitoring-events                        │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  QUALIFICATION LAYER                                                         │
│                                                                              │
│  EventQualificationService                                                   │
│    score = f(event_type, CMDB criticality, user_count, failover, SPOF, SLA) │
│    if score ≥ threshold (50):  → open incident workflow                      │
│    else:                       → dismiss (no incident)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                          │ qualified
                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  CELERY TASK QUEUE (Redis broker)                                            │
│                                                                              │
│  execute_incident_workflow.delay(workflow_id)                                │
│         │                                                                    │
│         ▼ Celery worker picks up task                                        │
│  WorkflowEngine.execute(definition, state)                                   │
│                                                                              │
│  Step 1  ──► SentinelAgent    (classify, severity, sentinel context)         │
│  Step 2  ──► LibrarianAgent   (CMDB enrichment, environment, dependencies)  │
│  Step 3  ──► RiskAssessorAgent (9-factor risk score 0–100, priority P1–P5)  │
│  Step 4  ──► MechanicAgent    (5-tier runbook selection, resolved main_args) │
│  Step 5  ──► PolicyBrokerAgent (governance, approval_required decision)      │
│  Step 6  ──► ToolRegistryAgent (execute remediation steps, approved actions) │
│  Step 7  ──► VerifierAgent    (verify outcome, set resolution_source)        │
│                                                                              │
│  At each step: state persisted to PostgreSQL + event published via bus       │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  RESOLUTION LAYER                                                            │
│                                                                              │
│  watcher_brain ──► per-resource poll ──► condition_cleared event            │
│  (when anomaly disappears for that specific container)                       │
│         │                                                                    │
│         ▼ POST /api/monitoring-events (event_type=condition_cleared)         │
│  Backend closes matching open incidents                                      │
│  resolution_source = "watcher_all_clear"                                     │
│                                                                              │
│  OR: VerifierAgent confirms remediation worked                               │
│  resolution_source = "automated_remediation"                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React 18 + TypeScript + Vite)                                     │
│                                                                              │
│  WebSocket /ws/workflows/{id} ──► real-time incident updates                │
│  Incident table (INC0001…) · 5-tab detail view · Approval queue             │
│  Policy editor · Admin panel · Settings · Dark mode                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Infrastructure — Docker Services

All services are defined in `docker-compose.yml` at the repo root.

| Container | Image | Role | Exposed Port |
|---|---|---|---|
| `agentic_os_backend` | `./backend` | FastAPI REST API + WebSocket server | 8000 |
| `agentic_os_celery_worker` | `./backend` | Celery async task executor | — |
| `agentic_os_postgres` | `postgres:15` | Primary database (workflows, events, approvals) | 5432 |
| `agentic_os_redis` | `redis:7` | Celery broker + result backend | 6379 |
| `agentic_os_neo4j` | `neo4j:5` | CMDB graph database | 7474 (HTTP), 7687 (Bolt) |
| `agentic_os_flower` | `./backend` | Celery task monitor UI | 5555 |
| `sentinel_senses` | `./backend` (privileged) | eBPF bpftrace host-kernel monitoring | — |
| `watcher_brain` | `./backend` | Anomaly detection + incident submission | — |

### Key volume mounts

- `backend/src/` is volume-mounted into both the API and Celery containers — Python changes are live without rebuilding.
- `watcher_brain` mounts `backend/src/` and also `.state/` for config hot-reload and status files.
- `sentinel_senses` runs with `--privileged` and `--pid=host` to access the host kernel namespace for eBPF tracing.

### Startup sequence (`main.py` lifespan)

On backend startup, the following happen in order:

1. `init_db()` — creates all SQLAlchemy tables if they do not exist
2. `ApprovedActionRepository.seed_defaults()` — populates approved actions catalog (idempotent)
3. `seed_risk_weights()` — seeds default risk weight configuration (idempotent)
4. `seed_runbooks()` — seeds default runbooks from SQL files (idempotent)
5. `PostgresEventBus.connect()` — establishes async PostgreSQL LISTEN/NOTIFY connection
6. `seed_neo4j_database()` — seeds CMDB with default CI records (idempotent)
7. `WorkflowEngine` + `register_all_agents()` — registers all 22 agent handlers

---

## 3. Monitoring Subsystem

The monitoring subsystem consists of two containers working as a brain-and-senses pair.

### 3.1 sentinel_senses — eBPF Kernel Monitor

`sentinel_senses` is a Docker container with:
- `--privileged` and `--pid=host` flags for host kernel access
- `bpftrace` installed
- The container itself does **nothing** autonomously. It is an eBPF execution host.

`watcher_brain` issues `docker exec sentinel_senses bpftrace ...` commands to run kernel probes. Because `sentinel_senses` runs in the host PID namespace, bpftrace sees **all processes on all containers** from a single vantage point.

The bpftrace program run every poll cycle:

```
tracepoint:raw_syscalls:sys_enter {
    @[comm] = count();
}
interval:s:5 { exit(); }
```

This counts syscalls per process name across the entire host over a 5-second window and outputs the result as JSON.

### 3.2 watcher_brain — Anomaly Detection Agent

`watcher_brain` is a Python service running an `asyncio` event loop (`WatcherService.run()`). It polls every 10 seconds by default (configurable via `watcher_config.json` or environment variables).

**State maintained in memory:**

| Attribute | Type | Purpose |
|---|---|---|
| `active_incident_id` | `Optional[str]` | ID of the most recent incident submitted |
| `active_conditions` | `Dict[str, str]` | Maps `resource_name → platform_event_type` for all currently anomalous resources |
| `cooldown_until` | `Optional[datetime]` | If set, skip new anomaly submissions until this time |
| `last_anomaly_process` | `Optional[str]` | Last detected syscall anomaly process name |

**Main loop logic (simplified):**

```python
while True:
    reload_config_if_changed()                    # hot-wire threshold changes
    container_stats = get_docker_container_stats() # docker stats via API
    is_syscall, process, count, container = detect_anomaly()  # bpftrace

    all_anomalies = (
        detect_container_anomalies(container_stats)   # CPU, memory
        + detect_disk_anomalies(container_names)      # df
        + detect_health_check_anomalies(container_names)  # HTTP/TCP probes
        + detect_network_anomalies(container_names)   # netstat connection count
        + detect_log_anomalies(container_names)       # docker logs grep
    )

    # --- Per-resource all-clear (runs FIRST, every cycle) ---
    currently_anomalous = {container} | {c for c, _, _ in all_anomalies}
    for resource, event_type in list(active_conditions.items()):
        if resource not in currently_anomalous:
            submit_condition_cleared(resource, event_type)
            del active_conditions[resource]

    # --- Submit new anomalies ---
    if is_syscall:
        if not in_cooldown() and process != last_anomaly_process:
            submit_monitoring_event(high_syscall_intensity, container)
            active_conditions[container] = "high_syscall_intensity"
            set_cooldown()
    elif all_anomalies:
        for container, anomaly_type, desc in all_anomalies:
            submit_monitoring_event(anomaly_type, container)
            active_conditions[container] = platform_event_type

    await asyncio.sleep(poll_interval)
```

**Configuration hot-reload:** `watcher_config.json` is checked every poll cycle. If its mtime changed, all thresholds are reloaded without a container restart.

### 3.3 Detection Strategies

#### Syscall Intensity (highest priority)

Uses bpftrace on `sentinel_senses`. After measuring syscalls per process:

1. Known system processes are excluded via `SYSCALL_EXCLUDE` (exact match) and `SYSCALL_EXCLUDE_PREFIXES` (prefix match). Examples: `containerd-shim`, `java` (neo4j JVM), `python3` (backend), `redis-server`, `postgres`, `bpftrace` (sentinel itself).
2. The top remaining process is compared against `anomaly_threshold` (default: 20,000 syscalls/5s in Docker; configurable).
3. If exceeded, `_find_process_container()` runs `pgrep -x <process>` in each container to identify which container the process lives in.
4. An alert is submitted with `anomaly_process` set to the process name (critical for later `process_kill` targeting).

**Excluded process lists** prevent false positives from infrastructure-level processes:

```python
SYSCALL_EXCLUDE = frozenset({
    "containerd-shim", "containerd", "dockerd", "runc",
    "Relay", "docker-proxy",
    "init", "systemd", "sshd", "cron", "tini",
    "java", "python3", "python", "celery", "uvicorn",
    "node", "nginx", "bpftrace",
    "sh", "bash", "zsh",
    "MuninnPageCache", "neo4j",
    "redis-server", "postgres",
})
SYSCALL_EXCLUDE_PREFIXES = ("runc:", "containerd-shim-", "Relay(")
```

#### Container Stats (CPU / Memory)

Uses `docker stats --no-stream --format json` via `DockerStatsService`. Monitored per container:
- CPU > `cpu_threshold` (default 80%) → `cpu_spike`
- Memory > `memory_threshold` (default 90%) → `memory_surge`

For CPU/memory anomalies, `get_culprit_process()` runs `docker exec <container> ps aux` to identify the top-consuming process for the alert payload.

#### Disk Usage

Uses `docker exec <container> df -h /` parsed per container. Disk > `disk_threshold` (default 90%) → `disk_full`.

#### Health Checks

Configured per service:
- `agentic_os_backend` → HTTP `GET /health` on port 8000
- `agentic_os_postgres` → TCP connect on port 5432
- `agentic_os_redis` → TCP connect on port 6379

Failure → `health_check_failed`.

**Known issue:** Health checks use the container service name `agentic_os_backend` which resolves within the Docker network. When the backend is starting up, these checks can fail transiently and generate spurious incidents. The recommended fix is to use `http://backend:8000/api/health` (Docker Compose service name) for the HTTP check and add startup delay.

#### Network Connections

Uses `docker exec <container> netstat -tn` or `ss -tn`. Connection count > `connection_threshold` (default 1000) → `connection_spike`.

#### Log Errors

Uses `docker logs <container> --since 60s` and grepping for `ERROR`, `CRITICAL`, `FATAL`. Any matches → `log_error`.

### 3.4 Per-Resource All-Clear Mechanism

**Design:** When a resource's anomaly condition resolves, an explicit `condition_cleared` event is sent to the backend. The backend uses this to close any open incidents for that resource, regardless of what other resources are doing.

**Why this is separate from the anomaly detection block:**

Early versions used an `else` branch on the main `if/elif/else` — meaning all-clears only fired when ALL anomalies were clear. This was broken because persistent backend health check failures kept `all_anomalies` non-empty, permanently blocking all-clear events.

**Current implementation:** The all-clear check runs at the **top of every poll cycle**, before any anomaly handling:

```python
# Build set of resources with active anomalies THIS cycle
currently_anomalous: set = set()
if is_syscall_anomaly and anomaly_container:
    currently_anomalous.add(anomaly_container)
for container_name, _, _ in all_anomalies:
    currently_anomalous.add(container_name)

# Any tracked resource NOT in current anomalies has cleared
for resource_name, original_event_type in list(self.active_conditions.items()):
    if resource_name not in currently_anomalous:
        await self.submit_condition_cleared(resource_name, original_event_type)
        del self.active_conditions[resource_name]
```

**Backend handling of `condition_cleared`:** The `monitoring_events` route detects `event_type == "condition_cleared"` and calls `_handle_condition_cleared()`, which:
1. Finds all open incidents for the given `resource_name`
2. Sets `lifecycle_state = resolved`, `resolution_source = watcher_all_clear`, `all_clear_received_at = now()`
3. Leaves `remediation_outcome` unchanged (preserves whether automation succeeded or failed)

**Known gap:** `active_conditions` is in-memory. A `watcher_brain` restart loses the dict. If a condition was active before the restart, its `condition_cleared` event will never fire automatically. Workaround: manually POST a `condition_cleared` event. Planned fix: persist `active_conditions` to `.state/watcher_conditions.json` on every mutation.

---

## 4. Event Ingestion & Qualification

### 4.1 POST /api/monitoring-events

**Route:** `backend/src/agentic_os/api/routes/monitoring_events.py`

This endpoint receives all monitoring signals from `watcher_brain` and handles two distinct paths:

**Path A — `event_type == "condition_cleared"`:**
- Does not run qualification
- Calls `_handle_condition_cleared(resource_name)`
- Closes matching open incidents
- Returns `{"status": "condition_cleared", "incidents_closed": N}`

**Path B — any other event type:**

1. **Condition-state dedup check** — looks up `event_condition_state` for `(resource_name, event_type)`.  
   If the condition is already `open`, the original event is returned immediately (same `event_id`, no new row, no qualification run). This is the primary deduplication gate — it works regardless of event source (watcher, Prometheus webhook, Zabbix, manual POST).
2. Calls `EventQualificationService.qualify_event()`
3. Creates a `MonitoringEventModel` record in the database
4. Marks the condition `open` in `event_condition_state` with `qualified=False` initially
5. If `qualified == True` and score ≥ threshold:
   - Runs incident-level dedup against `workflow_states` (suppresses if an active incident already exists for this resource)
   - Creates a `WorkflowStateModel` with `workflow_type = incident`
   - Assigns an incident number (`INC0001`, etc.) via `EnumerationService`
   - Upgrades condition state to `qualified=True` (24 h TTL applies)
   - Queues `execute_incident_workflow.delay(workflow_id)` on Celery
   - Returns `{"qualified_as_incident": True, "incident_workflow_id": workflow_id}`
6. If `qualified == False`:
   - Updates the `MonitoringEventModel` with `status = dismissed`
   - Condition state remains `qualified=False` (15-minute TTL applies — see below)
   - Returns `{"qualified_as_incident": False, "qualification_reason": "..."}`

The condition stays `open` (and subsequent duplicates are absorbed) until one of:
- A `condition_cleared` signal is received for the resource (Path A)
- The linked incident is resolved or closed — by an operator (manual close) or by automation (Celery pipeline)
- **Dismissed TTL expires (15 min)** — conditions with `qualified=False` auto-close after 15 minutes so that CMDB or scoring-config changes take effect on the next watcher cycle, without requiring a manual fix
- **Qualified TTL expires (24 h)** — conditions with `qualified=True` auto-close after 24 hours as a safety net if the incident was never explicitly closed
- **CMDB environment/criticality update** — saving a CI in the CMDB Editor immediately closes any dismissed open conditions for that resource (see §4.1a)

### 4.2 EventQualificationService

**File:** `backend/src/agentic_os/services/event_qualification.py`

A lightweight pre-check that scores raw monitoring events against configurable weights stored in the `risk_weight_configs` database table. If the database config is unavailable, falls back to hardcoded defaults.

The service ensures that low-criticality events on low-importance resources do not generate incident workflows.

### 4.3 Qualification Scoring Factors

The qualification engine uses a **three-factor score** (not a points table). The full formula is:

```
base_score  = min(100, criticality_score × event_type_multiplier × 100)
final_score = base_score × environment_multiplier
qualified   = (final_score ≥ threshold)  AND  (final_score ≥ criticality_floor)
```

| Factor | Values | Notes |
|---|---|---|
| **Raw criticality** | `info`=0.3, `warning`=0.6, `critical`=1.0 | Always known from the alert |
| **Event-type multiplier** | 0.8–2.5 (configurable) | Amplifies or dampens the signal per alert category |
| **Environment multiplier** | `production`=1.0, `staging`=0.6, `qa`=0.4, `development`=0.3, `test`=0.2, `unknown`=0.75 | Pulled from the CI's `environment` attribute in CMDB |

**Qualification threshold:** 50 (configurable via Settings → Incident Qualification).

**Criticality floors** — even if `final_score ≥ threshold`, the event is dismissed if it falls below the floor for its severity level:

| Severity | Floor |
|---|---|
| `critical` | 30 |
| `warning` | 50 |
| `info` | 75 |

**Confidence:** `100%` when the resource CI is found in CMDB; `60%` when the CI is unknown. The Events page shows a "✓ High Confidence" badge when confidence ≥ 70%. Confidence does **not** affect the score — it is a signal quality indicator. Low confidence means environment and CI attributes were not available, so scoring used defaults.

**Unknown-CI policy** (`unknown_ci_behavior`, configurable):
- `qualify_normal` — score normally at 0.75× environment multiplier for unknown environment (default)
- `qualify_as_low` — cap base score at `unknown_ci_score_cap` (default 40) before applying environment multiplier
- `dismiss` — immediately dismiss any event whose resource is not in CMDB

**Event-type multipliers (default config):**

| Event type | Multiplier |
|---|---|
| `service_down` | 2.5× |
| `service_unresponsive` / `disk_full` / `database_error` | 2.0× |
| `health_check_failed` | 1.8× |
| `network_issue` | 1.7× |
| `high_memory` / `memory_surge` | 1.6× |
| `cpu_spike` / `high_cpu` | 1.5× |
| `connection_spike` / `high_latency` | 1.4× |
| `high_syscall_intensity` | 1.3× |
| `certificate_expiry` | 1.2× |
| `metrics_anomaly` | 1.0× |
| `high_error_rate` | 0.9× |
| `log_error` | 0.8× |

> CMDB-dependent factors (CI tier, business criticality, user count, SPOF status, SLA percent, failover) are evaluated in the **RiskAssessorAgent** (step 3 of the 7-agent pipeline) — after the incident is created. They affect the **risk score** displayed on incident cards, not the initial qualification decision.

---

## 5. The 7-Agent Incident Pipeline

The incident pipeline is a linear sequence of 7 agents. Each agent reads from the typed `IncidentWorkflowContext`, adds its own context layer, and passes the enriched state to the next agent. The workflow engine persists state to PostgreSQL after each step.

```
WorkflowState (in Celery task)
│
├── Step 1: SentinelAgent      → adds ctx.sentinel    (severity, anomaly type)
├── Step 2: LibrarianAgent     → adds ctx.cmdb        (CMDB data, environment)
├── Step 3: RiskAssessorAgent  → adds ctx.risk        (score 0-100, priority)
├── Step 4: MechanicAgent      → adds ctx.proposal    (runbook, steps, main_args)
├── Step 5: PolicyBrokerAgent  → adds ctx.governance  (approval decision)
│              │
│     [if approval_required]
│              └── human_approval step (pause, wait for WebSocket event)
│
├── Step 6: ToolRegistryAgent  → runs remediation steps, updates ctx.execution_results
└── Step 7: VerifierAgent      → adds ctx.verification (pass/fail per check)
```

### 5.1 Typed Context Schema

**File:** `backend/src/agentic_os/core/context_schema.py`

Each agent produces a typed dataclass that becomes a field on `IncidentWorkflowContext`. This eliminates fragile `dict.get()` lookups and enforces the contract between agents.

```python
@dataclass
class IncidentWorkflowContext:
    sentinel:          Optional[SentinelContext]     = None  # set by SentinelAgent
    cmdb:              Optional[CMDBContext]          = None  # set by LibrarianAgent
    risk:              Optional[RiskContext]          = None  # set by RiskAssessorAgent
    proposal:          Optional[Proposal]             = None  # set by MechanicAgent
    governance:        Optional[GovernanceContext]    = None  # set by PolicyBrokerAgent
    execution_results: List[Dict[str, Any]]          = field(default_factory=list)
    verification:      Optional[VerificationContext] = None  # set by VerifierAgent
    reasoning_trace:   List[str]                     = field(default_factory=list)
```

**Persistence:** `WorkflowState.set_context()` serializes the typed context to `context_schema` (JSON column in PostgreSQL) and also maintains the legacy untyped `context` dict for backward compatibility. The `PRESERVED_KEYS` set ensures that keys written directly to `state.context` (e.g., `decision_result`, `alert_payload`) are not erased when a later agent calls `set_context()`.

**Accessing context:**

```python
ctx = state.get_context()         # Returns IncidentWorkflowContext
# ...modify ctx...
state = self._set_typed_context(state, ctx)  # Persist both typed + untyped
```

### 5.2 SentinelAgent

**Agent name:** `sentinel`  
**Input:** `state.context["alert_payload"]` (written by `monitoring_events.py` before Celery task)  
**Output:** `ctx.sentinel` (SentinelContext), `state.severity`, `state.title`

Classifies the incident severity from the raw alert payload. Maps alert severity strings to the `Severity` enum (`critical → CRITICAL`, `high → HIGH`, etc.). Generates a human-readable title if the alert doesn't include one (`"High Syscall Intensity on agentic_os_neo4j (process: yes)"`). Sets `ctx.sentinel.confidence = 0.95` as the default detection confidence.

**Key output fields:**

```python
@dataclass
class SentinelContext:
    detected_anomaly: str       # "high_syscall_intensity"
    anomaly_type: str           # same as detected_anomaly
    alert_payload: AlertPayload # type, message, severity, anomaly_process
    timestamp: str              # ISO timestamp
    confidence: float           # 0.95 default
```

### 5.3 LibrarianAgent

**Agent name:** `librarian`  
**Input:** `ctx.sentinel`, `state.context["alert_payload"]["resource_name"]`  
**Output:** `ctx.cmdb` (CMDBContext), `state.context["cmdb_context"]` (backward compat)

Queries Neo4j CMDB for the affected resource. Retrieves:
- `get_resource_info(resource_name)` — CI record (type, status, owner, criticality, CI tier, user count, SLA, SPOF flag, failover availability, compliance scope)
- `get_dependencies(resource_name, depth=2)` — services this resource depends on
- `get_impacted_services(resource_name)` — services that depend on this resource (blast radius)
- `get_historical_incidents(resource_name, limit=3)` — recent incident history

**Critical fix:** `environment` is extracted from `resource_info["environment"]` and placed at the **top level** of `CMDBContext.environment`. Earlier versions required nested extraction (`cmdb_context.resource_info.environment`) which caused policy matching to fail.

```python
@dataclass
class CMDBContext:
    resource_name:    str
    resource_info:    ResourceInfo    # typed subset of CMDB fields
    environment:      str             # "prod" | "staging" | "dev" — at top level
    dependencies:     List[Dict]
    impacted_services: List[Dict]
    cmdb_context:     Optional[Dict]  # full raw dict (all scoring fields)
```

If the resource is not found in CMDB, the agent logs a warning and uses neutral defaults (environment="dev", type="unknown"). The pipeline continues — CMDB absence degrades quality but does not abort.

### 5.4 RiskAssessorAgent

**Agent name:** `risk_assessor`  
**Input:** `ctx.cmdb`  
**Output:** `ctx.risk` (RiskContext), `state.risk_score`, `state.severity` (reassessed), `state.context["risk_breakdown"]`, `state.context["incident_priority"]`

Performs a 9-factor weighted risk assessment. All weights are loaded from the `risk_weight_configs` database table (or hardcoded defaults if unavailable).

**The 9 factors:**

| # | Factor | Max Score | Source |
|---|---|---|---|
| 1 | Event severity (raw_criticality × compliance) | 20 pts | `state.severity` + compliance multiplier |
| 2 | CI tier | 15 pts | `ctx.cmdb.cmdb_context["ci_tier"]` |
| 3 | Business criticality | 20 pts | `ctx.cmdb.cmdb_context["business_criticality"]` |
| 4 | User impact | 15 pts | `ctx.cmdb.cmdb_context["user_count"]` |
| 5 | Blast radius (dependent services) | 15 pts | `len(ctx.cmdb.impacted_services)` |
| 6 | Failover availability | −5 pts | `ctx.cmdb.cmdb_context["failover_available"]` |
| 7 | SPOF status | 10 pts | `ctx.cmdb.cmdb_context["is_spof"]` |
| 8 | SLA impact | 10 pts | `ctx.cmdb.cmdb_context["sla_percent"]` |
| 9 | Historical incidents | 10 pts | `len(historical_incidents)` |

**Compliance multiplier** is applied to factor 1:
- `pci`, `hipaa`, `gdpr` → 1.5× (configurable)
- `soc2` → 1.2×
- `general` → 1.0×

**Priority matrix** maps `severity:business_criticality` to P1–P5 (e.g., `critical:tier_1 → P1`, `high:tier_2 → P2`).

**Severity reassessment:** The total score (0–100) is used to re-evaluate severity:
- ≥ 80 → CRITICAL
- ≥ 60 → HIGH
- ≥ 40 → MEDIUM
- ≥ 20 → LOW
- < 20 → INFO

The final `state.severity` may differ from the initial alert severity if CMDB data changes the picture.

### 5.5 MechanicAgent

**Agent name:** `mechanic`  
**Input:** `ctx.sentinel`, `ctx.cmdb`, `ctx.risk`  
**Output:** `ctx.proposal` (Proposal), `state.context["decision_result"]` = "approved" or "require_approval"

Selects the best remediation plan using a **5-tier priority system**:

| Tier | Source | Description |
|---|---|---|
| 1 | Database runbooks | Operator-authored runbooks matched by `event_type + service` (exact), then `event_type` only |
| 2 | CMDB playbooks | Service-specific playbooks from Neo4j |
| 3 | Historical incidents | Solutions from past incidents on the same resource |
| 4 | LLM generation | GPT/Claude generates a runbook if no other source available |
| 5 | Safe fallback | Default "observe and alert" steps |

**Runbook matching** (tier 1, primary path):

```python
rb = db.query(RunbookModel).filter(
    RunbookModel.event_type == event_type,
    RunbookModel.service == service,  # exact match first
    RunbookModel.enabled == True,
).first()

if not rb:
    rb = db.query(RunbookModel).filter(
        RunbookModel.event_type == event_type,
        RunbookModel.service == None,  # generic fallback
        RunbookModel.enabled == True,
    ).first()
```

**main_args resolution:** The proposal includes a `main_args` dict with all resolved tool arguments. Critically, `anomaly_process` from `ctx.sentinel.alert_payload.anomaly_process` is substituted into args that reference `{anomaly_process}`. This ensures ToolRegistryAgent sends the correct process name to `process_kill`.

```python
@dataclass
class Proposal:
    runbook_id:         str
    runbook_name:       str
    diagnostics_steps:  List[RunbookStep]
    remediation_steps:  List[RunbookStep]
    confidence:         float
    blast_radius:       int
    approval_required:  bool
    main_args:          Dict[str, Any]  # resolved: {"process_name": "yes", "signal": "SIGKILL"}
```

**Confidence scoring drives auto-execute vs. require-approval:**
- Confidence ≥ configured threshold → `decision_result = "approved"` (auto-execute)
- Confidence < threshold → `decision_result = "require_approval"`

### 5.6 PolicyBrokerAgent

**Agent name:** `broker`  
**Input:** `ctx.sentinel`, `ctx.cmdb`, `ctx.risk`, `ctx.proposal`  
**Output:** `ctx.governance` (GovernanceContext), `state.context["decision_result"]` (may override Mechanic's decision)

Evaluates all active `GovernancePolicyModel` records against the incident context. A governance policy triggers when ALL its conditions match:

```python
# Policy conditions (all are optional, must all match)
{
    "environment":    "prod",      # match ctx.cmdb.environment
    "service_name":   "database",  # match resource_name
    "min_risk_score": 75,          # match state.risk_score
    "min_severity":   "high"       # match state.severity
}
```

If any matching policy has `actions_requiring_approval` that includes the proposed action (or `"*"`), `approval_required = True`.

The broker also checks `PolicyModel` records (simpler incident-response policies that define allowed actions per anomaly type/environment combination).

```python
@dataclass
class GovernanceContext:
    matching_policies:    List[Dict]   # policies that fired
    approval_required:    bool
    approval_priority:    int          # 1–100, lower = higher priority queue position
    allowed_actions:      List[str]    # what automation can do
    blast_radius_limit:   Optional[int]
    requires_post_monitoring: bool
    decision_notes:       str
```

If `approval_required = True`, the broker sets `decision_result = "require_approval"`, which routes the workflow to the `human_approval` step. Execution is paused until an operator approves or rejects via the UI or API.

### 5.7 ToolRegistryAgent

**Agent name:** `execute`  
**Input:** `ctx.proposal`, `ctx.governance`, `state.context["decision_result"]`  
**Output:** `ctx.execution_results`, `state.remediation_outcome`

Executes the approved remediation steps in sequence. Each step references a `tool_name` from the `approved_actions` catalog.

**Step abort policy:** The default `on_failure: abort` means that if any step fails, remaining steps are **not executed**. This prevents cascading damage (e.g., if `check_process_health` fails, do not proceed to `restart_service`).

**Process-targeted actions** (e.g., `process_kill`) use `ctx.proposal.main_args["process_name"]` to identify the exact process. This was resolved by MechanicAgent from `ctx.sentinel.alert_payload.anomaly_process`.

**Approved action categories:**

| Category | Blast Radius | Examples |
|---|---|---|
| `diagnostic` | 1 (read-only) | `check_cpu`, `check_memory`, `get_process_list` |
| `remediation_safe` | 2 (moderate) | `restart_service`, `clear_cache` |
| `remediation_intrusive` | 3 (disruptive) | `process_kill`, `force_restart`, `drain_node` |

**Process rules:** Each `ApprovedActionModel` with `process_rules` has a JSON list of regex allow/deny rules. Before executing `process_kill`, the agent evaluates rules in priority order. If no rule matches, the action is denied (`default_deny`).

After all steps complete (or abort on failure), `state.remediation_outcome` is set:
- `"succeeded"` — all steps passed
- `"failed"` — a step failed
- `"aborted"` — a step failed and abort policy triggered

### 5.8 VerifierAgent

**Agent name:** `verify`  
**Input:** `ctx.execution_results`, `ctx.risk`, `ctx.proposal`  
**Output:** `ctx.verification` (VerificationContext), `state.lifecycle_state`

Runs verification checks to confirm whether the remediation was effective. Checks are defined in the runbook's `verification_steps` field.

```python
@dataclass
class VerificationResult:
    step_name:    str
    status:       str    # "passed" | "failed" | "warning"
    metric:       str    # what was measured
    actual_value: float
    threshold:    float
    message:      str

@dataclass
class VerificationContext:
    verification_results:  List[VerificationResult]
    overall_success:       bool
    remediation_effective: bool
    issues_resolved:       bool
```

If verification passes, the agent sets `state.lifecycle_state = resolved` and `state.remediation_outcome = succeeded`. If it fails, it sets `lifecycle_state = failed` (or leaves the incident open for watcher all-clear to resolve it).

---

## 6. Workflow Engine

**File:** `backend/src/agentic_os/core/workflow_engine.py`

The `WorkflowEngine` orchestrates workflow execution. It loads a `WorkflowDefinition` (step graph from YAML) and walks through steps, calling registered agent handlers.

### 6.1 Step Types

| Type | Description |
|---|---|
| `agent` | Calls a registered Python async function (agent handler) |
| `human_approval` | Pauses workflow, waits for `APPROVAL_GRANTED` or `APPROVAL_REJECTED` event |
| `decision` | Reads `state.context["decision_result"]` to choose next step |
| `external_call` | Placeholder for calls to external services (ServiceNow, Slack, etc.) |
| `parallel` | Placeholder for concurrent step execution |

### 6.2 Routing & Branching

Next step is determined by:

```python
def _get_next_step(step, state):
    decision = state.context.get("decision_result")
    if decision and decision in step.next_steps:
        return step.next_steps[decision]      # conditional route
    return step.next_steps.get("default")     # default route
```

This means agents control routing by writing `decision_result` to `state.context`. For example, MechanicAgent writes `"approved"` or `"require_approval"`, which routes to either the execution step or the approval step.

### 6.3 Timeout Enforcement

Every agent step is wrapped in `asyncio.wait_for()`:

```python
state = await asyncio.wait_for(
    handler(state),
    timeout=step.timeout_seconds or 300  # default 5 minutes
)
```

On `TimeoutError`, the error details are written to `state.context["last_error"]` and the exception is re-raised, causing the workflow to fail with `lifecycle_state = FAILED`.

### 6.4 Human Approval Steps

When a `human_approval` step is reached:

1. Workflow publishes `APPROVAL_REQUESTED` event on the event bus
2. Workflow pauses: `await self.bus.wait_for_event("approval.*", predicate=..., timeout=N)`
3. Operator sees the incident in the UI approval queue and clicks Approve/Reject
4. The approval route (`POST /api/approvals/{id}/approve`) publishes `APPROVAL_GRANTED` on the event bus
5. The waiting coroutine wakes, checks the event type, and continues or fails the workflow

**Race condition protection:** After the approval event arrives, the engine re-fetches state from the database to verify the workflow is still in `WAITING_APPROVAL`. If another process has already moved it, the late approval is discarded.

---

## 7. Incident State Model

### 7.1 Decoupled State Fields

Incidents track three independent dimensions to accurately represent what happened:

| Field | Values | Meaning |
|---|---|---|
| `lifecycle_state` | `open` → `resolved` / `failed` / `closed` | Overall incident status — is the ticket open or closed? |
| `remediation_outcome` | `pending` / `succeeded` / `failed` / `aborted` / `skipped` | How did the automated remediation steps perform? |
| `resolution_source` | `automated_remediation` / `watcher_all_clear` / `manual` | What ultimately cleared the condition? |
| `all_clear_received_at` | `datetime` / `null` | When did the watcher confirm the condition cleared? |

**Example interpretations:**

| lifecycle_state | remediation_outcome | resolution_source | Meaning |
|---|---|---|---|
| `resolved` | `succeeded` | `automated_remediation` | Platform killed the offending process, watcher confirmed normal |
| `resolved` | `aborted` | `watcher_all_clear` | Remediation step failed or timed out, but the condition cleared naturally |
| `resolved` | `failed` | `watcher_all_clear` | Remediation ran but failed; condition cleared on its own |
| `failed` | `failed` | `null` | Remediation failed, condition still active (requires manual action) |
| `waiting_approval` | `pending` | `null` | Waiting for human approval before executing |

### 7.2 Lifecycle State Machine

```
                  ┌─────────────┐
                  │    OPEN     │
                  └─────┬───────┘
                        │ (Celery task starts)
                        ▼
                  ┌─────────────┐
                  │ IN_PROGRESS │
                  └─────┬───────┘
                        │ (PolicyBroker: approval_required)
              ┌─────────┴──────────┐
              │ no approval needed │ approval required
              ▼                    ▼
          ┌──────┐         ┌──────────────────┐
          │EXEC  │         │ WAITING_APPROVAL  │
          └──┬───┘         └────────┬─────────┘
             │               approve│reject
             │             ┌────────┘
             ▼             ▼
         ┌──────────┐  ┌──────────┐
         │ RESOLVED │  │ REJECTED │
         └──────────┘  └──────────┘
             │
         ┌───┴─────────────────┐
         │ MONITORING (post)   │
         └─────────────────────┘

    Any step failure → FAILED
    Watcher all-clear → RESOLVED (from OPEN / IN_PROGRESS / WAITING_APPROVAL)
```

### 7.3 Incident Enumeration (INC0001)

Incident numbers are assigned using a PostgreSQL sequence (`incident_seq`), ensuring uniqueness even under concurrent writes.

```sql
CREATE SEQUENCE incident_seq START 1;
```

```python
# EnumerationService
result = db.execute(text("SELECT nextval('incident_seq')"))
incident_num = result.scalar()
incident_str = f"INC{incident_num:04d}"  # "INC0001", "INC9999", "INC10000"
```

Numbers are:
- **Auto-incremented** — never manually assigned
- **Atomic** — no race conditions possible with a DB sequence
- **Never recycled** — even after `DELETE`, the sequence continues
- **Not reset** by the admin "Delete All Incidents" endpoint (the sequence is independent of the data)

---

## 8. Runbooks & Remediation

**Table:** `runbooks`  
**File:** `backend/src/agentic_os/db/models.py` → `RunbookModel`

A runbook defines the complete remediation procedure for a specific incident type.

### Runbook structure

```json
{
  "name": "High Syscall Intensity - Process Termination",
  "event_type": "high_syscall_intensity",
  "service": null,
  "environment": null,
  "diagnostics": [
    {
      "order": 1,
      "type": "diagnostic",
      "name": "identify_process",
      "tool": "get_process_list",
      "args_json": {"container": "{resource_id}"}
    }
  ],
  "actions": [
    {
      "order": 1,
      "type": "remediation",
      "name": "kill_process",
      "tool": "process_kill",
      "args_json": {"process_name": "{anomaly_process}", "signal": "SIGKILL"}
    }
  ],
  "verification_steps": [
    {
      "order": 1,
      "type": "check",
      "name": "verify_syscall_normal",
      "tool": "check_syscall_rate",
      "args_json": {"threshold": 1000}
    }
  ],
  "confidence": 0.92,
  "blast_radius": 1,
  "enabled": true
}
```

### Template substitution

Args containing `{anomaly_process}` or `{resource_id}` are resolved by MechanicAgent when building `proposal.main_args`:

- `{anomaly_process}` → `ctx.sentinel.alert_payload.anomaly_process` (e.g., `"yes"`)
- `{resource_id}` → `ctx.cmdb.resource_name` (e.g., `"agentic_os_neo4j"`)

### Step abort policy

Steps default to `on_failure: abort`. When a step fails:
1. `remediation_outcome = "aborted"`
2. Remaining steps are skipped
3. Workflow transitions to `FAILED` unless the watcher sends an all-clear

This prevents cascading damage. For example, a failed `check_process_health` will not proceed to `restart_service`, which could cause an outage on an already-degraded service.

---

## 9. Celery Async Task Execution

**Broker:** Redis (`redis://redis:6379/0`)  
**Result backend:** Redis  
**Task file:** `backend/src/agentic_os/tasks/celery_app.py`  
**Monitor UI:** http://localhost:5555 (Flower)

When an incident is qualified, the workflow is queued:

```python
execute_incident_workflow.delay(str(workflow_id))
```

The Celery task fetches the `WorkflowStateModel` from the database, deserializes it into a `WorkflowState` object, loads the workflow definition (from `backend/workflows/incident_v1.yaml`), and calls `WorkflowEngine.execute()`.

**Why Celery:**
- The 7-agent pipeline can take 10–120+ seconds. Running it synchronously in the API request would time out.
- Celery workers are horizontally scalable — add more workers to process more concurrent incidents.
- Flower provides real-time task monitoring (success/failure counts, ETA, retry state).

**Worker concurrency:** Controlled by the `CELERY_CONCURRENCY` environment variable (default: number of CPU cores).

**Task retry:** Failed tasks are not automatically retried by default (to avoid double-remediation). Retries can be enabled per-task with explicit exponential backoff.

---

## 10. Event Sourcing & PostgreSQL Event Bus

### Event sourcing

Every meaningful state transition in a workflow appends an immutable `EventModel` record to the `events` table. Events are never updated or deleted.

```python
@dataclass
class EventEnvelope:
    workflow_id:    UUID
    workflow_type:  WorkflowType
    event_type:     EventType       # e.g., INCIDENT_RISK_ASSESSED
    source_agent:   str             # e.g., "risk_assessor"
    event_id:       UUID            # unique per event
    timestamp:      datetime
    correlation_id: UUID            # same for all events in a workflow
    causation_id:   Optional[UUID]  # ID of the event that caused this one
    payload:        dict            # step-specific data
```

This gives a complete audit trail: every agent action, every state change, every approval decision is recorded with a timestamp and correlation chain.

### PostgreSQL LISTEN/NOTIFY event bus

**File:** `backend/src/agentic_os/bus/postgres_bus.py`

The event bus uses PostgreSQL's built-in pub/sub mechanism:
- `NOTIFY agentic_os_events, '<json_payload>'` — publish an event
- `LISTEN agentic_os_events` — subscribe to events

This allows the FastAPI WebSocket handler to receive real-time workflow updates published by Celery workers without needing an additional message broker.

**Flow:**
1. Celery worker executes an agent step
2. WorkflowEngine calls `bus.publish(event)`
3. `PostgresEventBus` executes `NOTIFY agentic_os_events, '{"event_type": "...", ...}'`
4. FastAPI WebSocket handler has an active `LISTEN` connection
5. PostgreSQL delivers the notification to the handler
6. Handler broadcasts the update to all connected WebSocket clients for that workflow

---

## 11. WebSocket Real-Time Updates

**Endpoint:** `ws://localhost:8000/ws/workflows/{workflow_id}`  
**File:** `backend/src/agentic_os/api/ws.py`

The frontend connects to this WebSocket immediately after viewing an incident detail page. As the Celery worker progresses through agents, each step completion publishes an event, which the WebSocket handler broadcasts to the connected client.

**Message format:**

```json
{
  "type": "workflow_update",
  "lifecycle_state": "in_progress",
  "severity": "high",
  "risk_score": 74,
  "last_trace": "[RISK ASSESSOR AGENT] Total Risk Score: 74/100",
  "context": { ... }
}
```

**Frontend handling** (WorkflowDetailsPhase6.tsx):

```typescript
ws.subscribe((message: WorkflowUpdate) => {
  if (message.type === 'workflow_update') {
    setWorkflow(prev => ({
      ...prev,
      lifecycle_state: message.lifecycle_state || prev.lifecycle_state,
      risk_score: message.risk_score ?? prev.risk_score,
      reasoning_trace: message.last_trace
        ? [...prev.reasoning_trace, message.last_trace]
        : prev.reasoning_trace,
    }))
  }
})
```

The incident detail view shows a live "Execution Trace" that updates in real-time as each agent appends to `state.reasoning_trace`.

---

## 12. REST API Reference

Base URL: `http://localhost:8000/api`  
Interactive docs: `http://localhost:8000/api/docs`

### Workflow Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/workflows/incident` | Submit incident manually |
| `POST` | `/workflows/change` | Submit change request |
| `GET` | `/workflows` | List workflows with filtering, sorting, pagination |
| `GET` | `/workflows/{id}` | Get single workflow with full context |

**GET /workflows query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `workflow_type` | `incident` | Filter by type |
| `lifecycle_state` | (all) | Filter by state |
| `limit` | 20 | Page size |
| `offset` | 0 | Pagination offset |
| `sort_by` | `created_at` | Sort field |
| `sort_order` | `desc` | `asc` or `desc` |

### Monitoring Events

| Method | Path | Description |
|---|---|---|
| `POST` | `/monitoring-events` | Receive raw signal from watcher (qualifies automatically) |

**POST /monitoring-events body:**

```json
{
  "source": "watcher_brain",
  "event_type": "high_syscall_intensity",
  "resource_name": "agentic_os_neo4j",
  "raw_criticality": "critical",
  "signal_value": 25000,
  "signal_threshold": 20000,
  "anomaly_process": "yes",
  "raw_payload": { ... }
}
```

For `event_type = "condition_cleared"`, the body uses `raw_criticality: "info"` and `raw_payload.original_event_type`.

### Approvals

| Method | Path | Description |
|---|---|---|
| `GET` | `/approvals` | List pending approvals |
| `POST` | `/approvals/{id}/approve` | Approve with notes |
| `POST` | `/approvals/{id}/reject` | Reject with reason |

### Policies & Governance

| Method | Path | Description |
|---|---|---|
| `GET` / `POST` | `/policies` | List / create incident response policies |
| `PUT` / `DELETE` | `/policies/{id}` | Update / delete policy |
| `GET` / `POST` | `/governance-policies` | List / create governance policies |
| `PUT` / `DELETE` | `/governance-policies/{id}` | Update / delete governance policy |

### Runbooks

| Method | Path | Description |
|---|---|---|
| `GET` | `/runbooks` | List runbooks |
| `POST` | `/runbooks` | Create runbook |
| `PUT` / `DELETE` | `/runbooks/{id}` | Update / delete runbook |

### Metrics & Admin

| Method | Path | Description |
|---|---|---|
| `GET` | `/metrics/incidents` | Incident counts, MTTR, auto-resolution rate |
| `GET` | `/metrics/remediation` | Remediation success rate, tool usage |
| `GET` | `/admin/statistics` | System-wide counts |
| `GET` | `/admin/system-status` | Database / Redis connectivity |
| `POST` | `/admin/incidents/delete-all` | Delete all incidents (irreversible) |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Basic liveness check |
| `GET` | `/ready` | Comprehensive readiness (DB, Redis, Neo4j) |

---

## 13. Database Schema

**Database:** PostgreSQL 15  
**ORM:** SQLAlchemy with `declarative_base`  
**File:** `backend/src/agentic_os/db/models.py`

### workflow_states (core table)

| Column | Type | Description |
|---|---|---|
| `workflow_id` | UUID PK | Unique workflow identifier |
| `workflow_type` | ENUM | `incident` / `change` / `problem` / `request` |
| `incident_number` | INTEGER UNIQUE | Auto-incremented integer (1, 2, 3…) |
| `incident_number_str` | VARCHAR(20) UNIQUE | Formatted string ("INC0001") |
| `lifecycle_state` | ENUM | Current overall state |
| `context` | JSON | Untyped context dict (backward compat) |
| `context_schema` | JSON | Typed `IncidentWorkflowContext` as JSON |
| `severity` | ENUM | `critical / high / medium / low / info` |
| `title` | VARCHAR(500) | Human-readable incident title |
| `risk_score` | FLOAT | Weighted risk score 0–100 |
| `risk_level` | VARCHAR(50) | `critical / high / medium / low` |
| `summary` | TEXT | Executive narrative (3–4 sentences) |
| `technical_summary` | TEXT | Technical bullet-point digest |
| `governance_decision` | VARCHAR(50) | `approved / rejected / pending` |
| `remediation_outcome` | VARCHAR(50) | `succeeded / failed / aborted / skipped / pending` |
| `resolution_source` | VARCHAR(50) | `automated_remediation / watcher_all_clear / manual` |
| `all_clear_received_at` | DATETIME | When watcher confirmed condition cleared |
| `reasoning_trace` | JSON (list) | Agent reasoning log entries |
| `execution_log` | JSON (list) | Step execution entries |
| `correlation_id` | UUID | Links all events in a workflow |
| `created_at` / `updated_at` | DATETIME | Timestamps |

**Indexes:** `(workflow_type, lifecycle_state)`, `(correlation_id)`, `(created_at)`, `(incident_number)`, `(incident_number_str)`

### events (append-only audit log)

| Column | Type | Description |
|---|---|---|
| `event_id` | UUID PK | Unique event ID |
| `workflow_id` | UUID FK | Parent workflow |
| `event_type` | ENUM | e.g., `incident.risk_assessed` |
| `source_agent` | VARCHAR | Agent that published the event |
| `payload` | JSON | Step-specific data |
| `correlation_id` | UUID | Workflow correlation |
| `causation_id` | UUID | ID of triggering event |
| `created_at` | DATETIME | Event timestamp |

### approvals

| Column | Type | Description |
|---|---|---|
| `approval_id` | UUID PK | |
| `workflow_id` | UUID FK | |
| `governance_policy_id` | UUID FK | Policy that triggered this approval |
| `approval_type` | VARCHAR | `governance / cab` |
| `status` | VARCHAR | `pending / approved / rejected` |
| `proposed_action` | JSON | `{tool, target, args, blast_radius}` |
| `incident_summary` | JSON | `{anomaly_type, severity, risk_score}` |
| `decided_by` / `decision_notes` | VARCHAR | Who approved and why |
| `requested_at` / `decided_at` | DATETIME | |

### Other tables

| Table | Purpose |
|---|---|
| `monitoring_events` | Raw signals from watcher (with qualification results) |
| `event_condition_state` | Open/closed dedup state per `(resource_name, event_type)` — prevents duplicate rows for the same active condition |
| `agent_executions` | Per-agent execution records for debugging |
| `runbooks` | Operator-authored remediation procedures |
| `approved_actions` | Catalog of permitted automation tools |
| `policies` | Incident response policies (matching rules + actions) |
| `governance_policies` | Approval gate rules by environment/service/risk |
| `risk_weight_configs` | Configurable weights for risk scoring (key=`"default"`) |
| `llm_configs` | LLM provider settings (provider, model, API key) |

### event_condition_state

Primary key: `(resource_name, event_type)`. One row per active monitoring condition.

| Column | Type | Description |
|---|---|---|
| `resource_name` | VARCHAR(255) | CI or resource identifier |
| `event_type` | VARCHAR(100) | Alert category (`service_unresponsive`, `high_cpu`, etc.) |
| `status` | VARCHAR(20) | `open` — condition is active; `closed` — condition has resolved |
| `qualified` | BOOLEAN | `true` = an incident was created; `false` = event was dismissed |
| `last_event_id` | UUID | FK to the `monitoring_events` row that opened this condition |
| `opened_at` | TIMESTAMP | When the condition was last opened (reset on re-open after close) |
| `closed_at` | TIMESTAMP | When the condition closed (NULL while open) |
| `updated_at` | TIMESTAMP | Last modification time |

**Close triggers:**
- `condition_cleared` event received for the resource
- Incident resolved/closed by operator or automation pipeline
- **Dismissed TTL (15 min)** — `qualified=false` conditions auto-expire so CMDB/config changes take effect quickly
- **Qualified TTL (24 h)** — `qualified=true` conditions auto-expire as a safety net for incidents that were never explicitly closed
- **CMDB edit** — saving a CI's `environment`, `criticality`, or `service_class` in the CMDB Editor immediately closes any `qualified=false` (dismissed) conditions for that CI

---

## 14. CMDB — Neo4j Integration

**File:** `backend/src/agentic_os/services/cmdb.py`

Neo4j stores the Configuration Item (CI) graph. LibrarianAgent queries it at runtime to enrich incidents.

### Node types

| Label | Description |
|---|---|
| `Service` | Microservice / application |
| `Database` | Database instance |
| `Infrastructure` | Host, container, VM |
| `ExternalDependency` | External APIs, third-party services |

### Key node properties

```cypher
(s:Service {
  name: "payment-service",
  type: "microservice",
  status: "operational",
  owner: "payments-team",
  environment: "prod",
  business_criticality: "tier_1",
  ci_tier: 1,
  user_count: 50000,
  is_spof: true,
  sla_percent: 99.9,
  failover_available: false,
  compliance_scope: "pci"
})
```

### Relationships

```cypher
(service)-[:DEPENDS_ON]->(database)
(service)-[:HOSTED_ON]->(infrastructure)
(database)-[:DEPENDS_ON]->(storage)
```

### Key queries used by LibrarianAgent

```python
# Resource info
cmdb.get_resource_info(resource_name)
→ MATCH (n {name: $name}) RETURN n

# Dependencies (depth 2)
cmdb.get_dependencies(resource_name, depth=2)
→ MATCH (n {name: $name})-[:DEPENDS_ON*1..2]->(dep) RETURN dep

# Blast radius (who depends on us)
cmdb.get_impacted_services(resource_name)
→ MATCH (svc)-[:DEPENDS_ON]->(n {name: $name}) RETURN svc
```

### Seeding

Neo4j is seeded at startup by `seed_neo4j_database()` from `backend/scripts/neo4j_seed.cypher`. This creates default CI records for the Docker Compose containers (`agentic_os_backend`, `agentic_os_postgres`, `agentic_os_neo4j`, etc.) with realistic CMDB properties.

---

## 15. Frontend Architecture

**Stack:** React 18, TypeScript, Vite, Tailwind CSS  
**Dev server:** http://localhost:3000 (Vite HMR)

### Views

| View | Component | Description |
|---|---|---|
| Dashboard | `Dashboard.tsx` | Metrics header + incident table |
| Incident Detail | `WorkflowDetailsPhase6.tsx` | 5-tab detail view |
| Approval Queue | `ApprovalQueue.tsx` | Pending approvals, approve/reject |
| Policy Editor | `PolicyList.tsx` / `PolicyEditor.tsx` | Create/edit response policies |
| Runbook Editor | `RunbookList.tsx` / `RunbookEditor.tsx` | Create/edit runbooks |
| Admin Panel | `AdminPanel.tsx` | System stats, delete all incidents |
| Settings | `Settings.tsx` | LLM config, thresholds, UI preferences |

### Incident detail tabs

| Tab | Content |
|---|---|
| Overview | Summary, metadata, CI/Environment info |
| Timeline | Reasoning trace (real-time via WebSocket) |
| Remediation | Runbook steps, execution results |
| Approval | Approval history and decision |
| Risk | `RiskSummaryPage.tsx` — risk score gauge, 9-factor breakdown |

### Data types (TypeScript)

**File:** `frontend/src/types/index.ts`

```typescript
interface Workflow {
  id: string;
  incident_number_str?: string;        // "INC0001"
  workflow_type: 'incident' | 'change';
  lifecycle_state: string;
  severity?: string;
  risk_score?: number;                  // 0–100, display as Math.round()
  title?: string;
  summary?: string;
  remediation_outcome?: string;
  resolution_source?: string;
  all_clear_received_at?: string;
  context: Record<string, any>;         // untyped legacy
  context_schema?: IncidentWorkflowContext;  // typed (Phase 10)
  reasoning_trace: string[];
  created_at: string;
  updated_at: string;
}
```

**Important:** Risk scores are floats in the database (e.g., `75.585000000001`). All frontend display code must use `Math.round(risk_score)` to show clean integers.

### API service layer

**File:** `frontend/src/services/api.ts`

All HTTP calls go through Axios with a base URL from environment config. Key functions:

```typescript
getWorkflows(params)       // GET /api/workflows
getWorkflow(id)            // GET /api/workflows/{id}
createIncident(payload)    // POST /api/workflows/incident
approveWorkflow(id, notes) // POST /api/approvals/{id}/approve
rejectWorkflow(id, reason) // POST /api/approvals/{id}/reject
getPolicies()              // GET /api/policies
createPolicy(data)         // POST /api/policies
getMetrics()               // GET /api/metrics/incidents
deleteAllIncidents()       // POST /api/admin/incidents/delete-all
```

### WebSocket client

**File:** `frontend/src/services/websocket.ts`

```typescript
const ws = new WorkflowWebSocket(workflowId)
ws.connect().then(() => {
  ws.subscribe((message: WorkflowUpdate) => {
    // update local state with new lifecycle_state, risk_score, trace entries
  })
})
```

---

## 16. Configuration & Environment Variables

### Backend (set in `docker-compose.yml` environment section)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:agentic_os@postgres:5432/agentic_os` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection (Celery broker) |
| `NEO4J_URI` | `bolt://neo4j:7687` | Neo4j Bolt connection |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `agentic_os` | Neo4j password |
| `CELERY_BROKER_URL` | Same as `REDIS_URL` | Celery task queue broker |
| `CELERY_CONCURRENCY` | (CPU count) | Celery worker concurrency |

### Watcher (set in `docker-compose.yml` or `watcher_config.json`)

| Variable | Default | Description |
|---|---|---|
| `SENTINEL_CONTAINER` | `sentinel_senses` | Container with bpftrace |
| `WATCHER_API_URL` | `http://backend:8000` | Backend API endpoint |
| `WATCHER_POLL_INTERVAL` | `10` | Seconds between poll cycles |
| `WATCHER_ANOMALY_THRESHOLD` | `20000` | Syscalls/5s threshold |
| `WATCHER_CPU_THRESHOLD` | `80.0` | CPU % threshold |
| `WATCHER_MEMORY_THRESHOLD` | `90.0` | Memory % threshold |
| `WATCHER_DISK_THRESHOLD` | `90.0` | Disk % threshold |
| `WATCHER_CONNECTION_THRESHOLD` | `1000` | Network connections threshold |
| `WATCHER_COOLDOWN_SECONDS` | `60` | Cooldown between incident submissions |

### Hot-reload config

The file `backend/.state/watcher_config.json` is checked every poll cycle. If modified, thresholds are reloaded immediately without restarting the container:

```json
{
  "poll_interval": 10,
  "cooldown_seconds": 60,
  "syscall_threshold": 20000,
  "cpu_threshold": 80.0,
  "memory_threshold": 90.0,
  "disk_threshold": 90.0,
  "connection_threshold": 1000
}
```

### Risk weights

Risk scoring weights are stored in the `risk_weight_configs` table under `config_key = "default"`. They can be adjusted via `PUT /api/risk-config/default` without restarting any service.

### LLM configuration

LLM provider settings are stored in the `llm_configs` table and configurable via the Settings page or `PUT /api/llm-settings`. When no LLM is configured (or an LLM call fails), `PlatformContextService` generates structured summaries from typed context fields as a fallback — the platform never requires an LLM to function.

---

*Document covers Axiometica AIR v2.11 — May 2026*
