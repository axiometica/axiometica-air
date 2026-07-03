# Axiometica AIR — System Architecture

**Last updated:** 2026-06-07 (v1.1.2)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Storm Agent — Correlated Event Meta-Orchestration](#2-storm-agent--correlated-event-meta-orchestration)
3. [Incident Pipeline — 7-Agent Sequence](#3-incident-pipeline--7-agent-sequence)
4. [Watcher All-Clear Mechanism](#4-watcher-all-clear-mechanism)
5. [Decoupled State Fields](#5-decoupled-state-fields)
6. [Step Abort Policy](#6-step-abort-policy)
7. [Typed Context Schema](#7-typed-context-schema)
8. [Event-Driven Pattern](#8-event-driven-pattern)
9. [Database Design](#9-database-design)
10. [Frontend Data Pipeline](#10-frontend-data-pipeline)
11. [Technology Stack Rationale](#11-technology-stack-rationale)
12. [Scalability and Performance](#12-scalability-and-performance)
13. [Security Architecture](#13-security-architecture)
14. [Deployment Architecture](#14-deployment-architecture)

---

## 1. System Overview

Axiometica AIR is an enterprise ITSM automation platform for autonomous incident and change management. It runs as nine Docker Compose containers:

| Container | Role |
|---|---|
| `backend` | FastAPI application server — REST API, WebSocket server, workflow orchestration |
| `celery_worker` | Celery workers — background execution of incident and change pipeline tasks |
| `postgres` | PostgreSQL — primary event-sourcing and state database |
| `redis` | Redis — Celery message broker and result backend |
| `neo4j` | Neo4j graph database — CMDB service topology and dependency relationships |
| `flower` | Flower — Celery task queue monitor and management UI (port 5555) |
| `frontend` | React 18 SPA — served by Vite in dev, nginx in production |
| `sentinel_senses` | eBPF sensor — runs bpftrace on the host kernel to observe all containers |
| `watcher_brain` | Python watcher orchestration — anomaly detection, condition tracking, incident raising |

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Frontend (React 18)                          │
│  Dashboard | Incidents | Storms | Approvals | CMDB | Settings        │
│                     WebSocket (real-time updates)                    │
└──────────────────────────────────────────────────────────────────────┘
                                 ↕ REST + WS
┌──────────────────────────────────────────────────────────────────────┐
│                    FastAPI Application Server                         │
│  Incident API | Approval API | Storm API | Policy API | Admin API    │
│  WebSocket Manager | Monitoring Events Handler | Storm Detection      │
└──────────────────────────────────────────────────────────────────────┘
            ↙                ↓                  ↘
  ┌──────────────┐  ┌──────────────┐   ┌──────────────┐
  │  PostgreSQL  │  │ Redis/Celery │   │    Neo4j     │
  │ (event store)│  │ (job queue)  │   │ (CMDB graph) │
  └──────────────┘  └──────────────┘   └──────────────┘
         ↕                  ↓
    LISTEN/NOTIFY    Celery Workers
    (event bus)             │
                   ┌────────┴──────────┐
                   ▼                   ▼
        StormAgent (meta)         7-Agent Pipeline
        ┌───────────────────┐  ┌──────────────────────┐
        │ Storm detection   │  │ SentinelAgent        │
        │ Phase 2 expansion │  │ LibrarianAgent       │
        │ LLM hypothesis    │  │ RiskAssessor         │
        │ Neo4j topology    │  │ MechanicAgent        │
        │ storm_hold        │  │ PolicyBrokerAgent    │
        │ awaiting_manual   │  │ ToolRegistryAgent    │
        └───────────────────┘  │ VerifierAgent        │
                               └──────────────────────┘
                                       ↕
┌──────────────────────────────────────────────────────┐
│               Watcher Subsystem                                        │
│  sentinel_senses (eBPF / bpftrace, host kernel)                       │
│  watcher_brain (Python, anomaly detection)                             │
│  Datadog / Dynatrace / Prometheus / PagerDuty / Zabbix / Grafana /    │
│  Generic webhook connectors (inbound push)                             │
│  Splunk (outbound pull)                                                │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Storm Agent — Correlated Event Meta-Orchestration

The Storm Agent is a meta-orchestrator that runs above the 7-agent pipeline. It detects when multiple incidents share a common root cause and coordinates their triage as a single unit rather than letting each trigger independent (and potentially conflicting) remediations.

### Why It Exists

When a core network switch fails, it may cause 10+ downstream services to raise health-check alerts within seconds of each other. Without the Storm Agent:
- Each incident enters the 7-agent pipeline independently
- Each pipeline selects a runbook (e.g., "restart service")
- 10 simultaneous service restarts may make the outage worse
- The actual root cause (the switch) is never addressed

With the Storm Agent:
- All 10 incidents are detected as a correlated burst
- They are placed in `storm_hold` — individual pipelines suppressed
- A single CAB approval is raised for the storm as a whole
- The operator sees the LLM hypothesis and Neo4j root cause candidates
- They can resolve all 10 with a single action after fixing the switch

### Key Design Decisions

**Lightweight detection, heavy analysis split**

Detection (`StormDetectionService.detect()`) runs synchronously as a FastAPI background task after every qualified event. It executes one SQL query and returns immediately — no blocking. If a storm is detected, it dispatches a Celery task for the heavy analysis (Neo4j + LLM).

**Merge-into-existing-storm pattern**

When multiple events qualify concurrently (e.g., 6 events arrive in 2 seconds), multiple Celery tasks may start simultaneously. Each task checks for an existing storm parent created in the last N minutes before creating a new one. If found, it adopts any uncovered incidents into the existing storm rather than creating a duplicate. This is a pragmatic "last writer merges" approach rather than full atomic locking.

**Source-agnostic**

Storm detection runs on the `workflow_states` table, not on the raw event stream. By the time detection runs, watcher events and all inbound webhook events (Datadog, Grafana, Generic, etc.) have been normalised into incidents by `EventQualificationService`. The storm can span all sources transparently.

**Storm lifecycle states**

| State | Who sets it | Meaning |
|-------|------------|---------|
| `storm_hold` | Storm Celery task | Child incident — individual pipeline suppressed; waits for storm coordination |
| `awaiting_manual` | Storm Celery task | Storm parent — operator owns the investigation; no standard pipeline approval gate |
| `open` | Release action | Child released from storm — individual pipeline resumed |
| `resolved` | Resolve action | Child or parent closed |

### v1.0.0 Enhancements

**Phase 2 — CMDB dependency expansion**

After initial storm formation, a second Celery task traverses the Neo4j service graph to find downstream services that depend on the already-affected resources. If those downstream services also have active incidents within the storm window, they are automatically adopted into the existing storm. This ensures the storm parent represents the true blast radius of the root-cause failure — not just the first-wave alerts.

**Pipeline hold buffer**

A configurable `storm.pipeline_hold_seconds` setting (default: 0, disabled) delays the start of the 7-agent pipeline after incident creation. This gives storm detection time to cluster correlated events before individual pipelines kick off, preventing redundant per-incident remediations that would be overridden by the storm anyway.

**Pre- and post-pipeline storm guards**

- **Pre-pipeline guard**: If storm detection has already adopted an incident before its pipeline starts, the pipeline is skipped and the incident remains in `storm_hold`.
- **Post-pipeline guard**: If a pipeline partially executes before storm adoption, the lifecycle state is corrected to `storm_hold` and a warning note is written to the storm parent documenting which remediations were applied before suppression.
- **Approval cancellation**: Pending individual pipeline approvals are automatically cancelled when their incident is merged into a storm.

**External connector storm eligibility**

Three independent controls determine whether externally sourced incidents (Splunk, Datadog, ServiceNow sync) participate in storm detection:

| Control | Scope | Default |
|---|---|---|
| `source_alert_time` COALESCE | Per-event | Automatic — uses original alert timestamp, not batch sync time |
| `allow_storm_detection` connector flag | Per-connector | `true` — opt-out per connector in Connector Hub |
| `storm.exclude_external_events` setting | Global | `false` — opt-out all external sources via Platform Settings |

All controls are permissive by default so existing deployments are unaffected.

---

## 3. Incident Pipeline — 7-Agent Sequence

Each agent in the pipeline reads the shared `IncidentWorkflowContext` dataclass written by previous agents and appends its own output fields before passing it forward. The full context is persisted to the database at each stage.

```
monitoring signal (from watcher, connector, or API)
      ↓
POST /api/monitoring-events
  - Condition-state dedup: if (resource, event_type) already open → return existing event_id (no new row)
  - EventQualificationService: score = criticality × event_type_multiplier × environment_multiplier
  - If score < threshold → dismiss (condition stays open, next identical signal is also dropped)
  - If score ≥ threshold → incident-level dedup, then open incident workflow
      ↓
SentinelAgent
  - Classifies incident: anomaly_type, severity, resource_id, service, environment
  - Creates IncidentWorkflowContext
  - Assigns incident number (INC0001, INC0002, ...)
      ↓
LibrarianAgent
  - Queries historical incidents for this service/anomaly combination
  - Retrieves runbook metadata from the runbook library
  - Attaches enrichment_data, historical_incidents to context
      ↓
RiskAssessor
  - Calculates 0–100 composite risk score from severity, resource criticality,
    dependency impact, business impact, historical recurrence
  - Attaches risk_score, risk_factors, risk_breakdown to context
      ↓
MechanicAgent
  - 5-tier runbook selection waterfall (see below)
  - Attaches selected_runbook, selection_tier, confidence_score to context
      ↓
PolicyBrokerAgent
  - Matches incident attributes against policy ruleset
  - Determines approval_required, blast_radius_limit, allowed_actions
  - If approval required → lifecycle_state = pending_approval, pipeline pauses
  - If approved (or no approval required) → pipeline continues
      ↓
ToolRegistryAgent
  - Dispatches runbook steps sequentially
  - Respects on_failure: abort|continue per step
  - Attaches execution_log, step_results, remediation_outcome to context
      ↓
VerifierAgent
  - Executes verification steps from runbook
  - Sets final remediation_outcome (succeeded|failed)
  - Updates lifecycle_state if verification confirms resolution
```

### MechanicAgent 5-Tier Waterfall

```
Tier 1: Runbook library
  exact match: anomaly_type + service + environment
  confidence: 90–100%
  ↓ (if no match)
Tier 2: Playbook library
  broader match: anomaly_type + service
  confidence: 70–85%
  ↓ (if no match)
Tier 3: Historical outcomes
  find the runbook that resolved similar past incidents
  confidence: 60–80% (weighted by recency and success rate)
  ↓ (if no match or insufficient history)
Tier 4: LLM synthesis
  generate runbook steps from incident context (OpenAI or Anthropic)
  confidence: 50–75%
  ↓ (if LLM unavailable or fails)
Tier 5: Fallback runbook
  generic safe-mode diagnostics-only runbook
  confidence: 30%
```

Policy thresholds on confidence score control auto-execution vs. approval requirements. Operators configure these thresholds in the admin settings.

---

## 3. Watcher All-Clear Mechanism

### Overview

The watcher all-clear mechanism provides authoritative confirmation from the monitoring layer that a condition has genuinely cleared — independent of whether the runbook reported success.

### active_conditions Tracking

`watcher_brain` maintains an `active_conditions` dict:

```python
active_conditions: dict[str, dict[str, ConditionData]]
# Structure: {resource_id: {condition_type: ConditionData}}
```

Each entry represents a currently active condition on a specific container. When watcher detects a new anomaly, it adds to this dict and raises an incident. When the anomaly resolves, it removes the entry and emits a `condition_cleared` event.

### Per-Resource Clear Logic

Clearing is **per-resource and per-condition-type**. This is intentional and different from an older "all systems healthy" design.

```
container-A has two active conditions:
  active_conditions["container-A"]["high_cpu"]     → INC0042 open
  active_conditions["container-A"]["disk_full"]    → INC0041 open

CPU normalizes on container-A:
  active_conditions["container-A"]["high_cpu"] removed
  → condition_cleared event: resource_id=container-A, anomaly_type=high_cpu
  → backend closes INC0042 (watcher_all_clear)
  → INC0041 remains open (disk_full still active)

Disk resolves later:
  active_conditions["container-A"]["disk_full"] removed
  → condition_cleared event: resource_id=container-A, anomaly_type=disk_full
  → backend closes INC0041 (watcher_all_clear)
```

### condition_cleared Event Flow

```
watcher_brain detects condition normalized
  ↓
POST /api/monitoring/events
  body: { event_type: "condition_cleared", resource_id, anomaly_type, timestamp }
  ↓
monitoring_events.py handler
  ↓
Query workflow_states WHERE resource_id = ? AND anomaly_type = ?
  AND lifecycle_state IN ('open', 'in_progress', 'failed', 'aborted')
  ↓
For each matching incident:
  lifecycle_state = 'resolved'
  resolution_source = 'watcher_all_clear'
  all_clear_received_at = now()
  ↓
WebSocket broadcast → frontend updates incident row in real-time
```

### Known Gap

`active_conditions` is currently stored only in memory. A `watcher_brain` restart loses the dict, and any conditions active at restart time will never emit `condition_cleared`. This means incidents tied to those conditions remain permanently open. See WISHLIST.md item 1 for the fix (persist to `.state/watcher_conditions.json`).

---

## 4. Decoupled State Fields

Three independent fields track incident resolution, not a single status field.

### Why Three Fields

A single `status` field cannot distinguish between:
- "Runbook succeeded AND watcher confirmed clear" (both indicators aligned)
- "Runbook aborted BUT watcher cleared it anyway" (partial execution, self-heal)
- "Runbook succeeded BUT watcher has not cleared yet" (runbook optimistic, condition may persist)
- "Manual operator resolved it, bypassing automation entirely"

The three-field model captures each dimension independently:

| Field | What it tracks | Who writes it |
|---|---|---|
| `lifecycle_state` | Overall incident position in lifecycle | Pipeline agents + watcher handler |
| `remediation_outcome` | How automation performed | ToolRegistryAgent + VerifierAgent |
| `resolution_source` | What actually cleared the incident | VerifierAgent (automated_remediation), watcher handler (watcher_all_clear), or API (manual) |

Additionally, `all_clear_received_at` records the timestamp when the watcher confirmed the condition cleared, independent of when the runbook completed.

### State Transition Reference

```
lifecycle_state transitions:
  open → in_progress → resolved        (happy path, auto-resolved)
  open → in_progress → aborted         (runbook abort policy triggered)
  open → in_progress → failed          (verification failed)
  open → pending_approval → in_progress (approval granted)
  open → pending_approval → failed     (approval rejected)
  any open state → resolved            (watcher all-clear)

remediation_outcome values:
  pending → succeeded                  (all steps passed + verification passed)
  pending → failed                     (runbook completed, verification failed)
  pending → aborted                    (step failed with on_failure: abort)
  pending → skipped                    (no runbook selected)

resolution_source values:
  null → automated_remediation         (set by VerifierAgent on success)
  null → watcher_all_clear             (set by watcher handler)
  null → manual                        (set by operator via API)
```

---

## 5. Step Abort Policy

### Default Behavior

Every runbook step has an `on_failure` policy. The default is `abort`. This means:

```yaml
steps:
  - name: collect_logs
    type: diagnostic
    on_failure: continue    # explicit continue: safe for diagnostics

  - name: restart_container
    type: remediation
    # on_failure not specified → defaults to abort
    # If restart_container fails, the entire runbook halts here.
    # No further steps execute.

  - name: scale_up
    type: remediation
    # This step will NOT execute if restart_container failed.
```

### Why This Matters

Without an abort default, a runbook failure mid-execution can leave the system in a worse state than before. Example: Step 1 drains traffic from `container-A`. Step 2 fails to restart `container-A`. Without abort, Step 3 attempts to delete old instances — but Step 2's failure means `container-A` is now neither drained nor running. The abort default prevents this by halting execution as soon as the environment deviates from expectation.

### Cascade Prevention

The abort policy is the primary mechanism for cascade prevention. It means a failed remediation action never causes additional destructive actions to execute. The `remediation_outcome` is set to `aborted` and the incident remains open for human review or watcher-driven auto-resolution.

Operators can explicitly opt into `on_failure: continue` for steps where partial failure is acceptable (most diagnostic steps fall into this category).

---

## 6. Typed Context Schema

### IncidentWorkflowContext

Defined in `backend/src/agentic_os/core/context_schema.py`. A Python dataclass that is created at pipeline entry and mutated by each agent in sequence.

The dataclass pattern (rather than a plain dict) provides:
- **Compile-time type checking** via mypy
- **Explicit field contracts** — each agent's input and output fields are declared in the class definition
- **Serialization** to JSON for database persistence (using dataclasses-json or Pydantic)
- **Early failure** — accessing a required field that wasn't written by a prior agent raises `AttributeError` at the calling agent's init, not silently at runtime

### Context Flow Through Agents

```python
# Each agent receives context, reads prior fields, adds its own
class SentinelAgent:
    def run(self, raw_event: dict) -> IncidentWorkflowContext:
        ctx = IncidentWorkflowContext()
        ctx.incident_id = generate_incident_id()   # INC0001, etc.
        ctx.anomaly_type = classify(raw_event)
        ctx.severity = assess_severity(raw_event)
        ctx.resource_id = raw_event["resource_id"]
        ctx.service = raw_event["service"]
        ctx.environment = raw_event["environment"]
        return ctx

class LibrarianAgent:
    def run(self, ctx: IncidentWorkflowContext) -> IncidentWorkflowContext:
        # reads: ctx.anomaly_type, ctx.service, ctx.resource_id
        ctx.historical_incidents = query_history(ctx.service, ctx.anomaly_type)
        ctx.relevant_runbooks = fetch_runbook_metadata(ctx.anomaly_type)
        return ctx

class RiskAssessor:
    def run(self, ctx: IncidentWorkflowContext) -> IncidentWorkflowContext:
        # reads: ctx.severity, ctx.service, ctx.historical_incidents
        ctx.risk_score, ctx.risk_factors = calculate_risk(ctx)
        return ctx

# ... and so on through MechanicAgent, PolicyBrokerAgent,
#     ToolRegistryAgent, VerifierAgent
```

The full context is persisted to the `workflow_states.context` JSONB column after each agent completes, providing a time-series record of context state at each pipeline stage.

---

## 7. Event-Driven Pattern

### PostgreSQL LISTEN/NOTIFY as Event Bus

The platform uses PostgreSQL's built-in `LISTEN/NOTIFY` as a lightweight event bus between the Celery workers (which run the incident pipeline) and the FastAPI server (which manages WebSocket connections to the frontend).

```
Celery worker completes agent step
  ↓
NOTIFY workflow_events, '{"workflow_id": "...", "event_type": "agent_completed", ...}'
  ↓
FastAPI LISTEN subscriber receives notification
  ↓
WebSocket broadcast to all connected clients subscribed to this workflow_id
  ↓
Frontend React state update → UI re-renders
```

This eliminates the need for a separate message bus (RabbitMQ, Kafka) for the real-time update path while keeping the architecture simple.

### Celery Task Flow

Incidents are processed asynchronously:

```
POST /api/monitoring/events (or direct incident submission)
  ↓
FastAPI handler: create workflow record, save to PostgreSQL
  ↓
celery_task.delay(workflow_id)   ← non-blocking, returns immediately
  ↓
API response: 200 OK { incident_id: "INC0042" }

Background (Celery worker):
  load workflow from database
  run SentinelAgent → update DB → NOTIFY
  run LibrarianAgent → update DB → NOTIFY
  run RiskAssessor → update DB → NOTIFY
  run MechanicAgent → update DB → NOTIFY
  run PolicyBrokerAgent → update DB → NOTIFY
    [if approval required: pause, wait for resume signal]
  run ToolRegistryAgent → update DB → NOTIFY
  run VerifierAgent → update DB → NOTIFY
  final state written → NOTIFY → WebSocket pushes "completed"
```

---

## 8. Database Design

### workflow_states Table

Primary state store for all incidents and changes. Each row represents one incident (or change).

```sql
CREATE TABLE workflow_states (
    -- Identity
    workflow_id         UUID PRIMARY KEY,
    incident_number     VARCHAR(10),      -- INC0001, INC0002, ...
    workflow_type       VARCHAR(50),      -- 'incident' | 'change'

    -- Classification
    anomaly_type        VARCHAR(100),     -- high_cpu, disk_full, service_down, etc.
    severity            VARCHAR(20),      -- critical | high | medium | low
    resource_id         VARCHAR(255),     -- container or host identifier
    service             VARCHAR(255),
    environment         VARCHAR(50),

    -- Decoupled state fields
    lifecycle_state     VARCHAR(50),      -- open | in_progress | pending_approval |
                                         --   resolved | failed | aborted
    remediation_outcome VARCHAR(50),      -- pending | succeeded | failed | aborted | skipped
    resolution_source   VARCHAR(50),      -- automated_remediation | watcher_all_clear | manual | NULL

    -- Risk
    risk_score          FLOAT,            -- 0–100
    risk_factors        JSONB,            -- breakdown by factor

    -- Timing
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP,
    all_clear_received_at TIMESTAMP,      -- when watcher confirmed condition cleared

    -- Content
    context             JSONB,            -- full IncidentWorkflowContext (updated per agent)
    reasoning_trace     TEXT[],           -- agent execution breadcrumb log
    summary             TEXT,             -- LLM or platform context summary
    runbook_name        VARCHAR(255),
    runbook_tier        INT,              -- 1–5 (which MechanicAgent tier was used)
    execution_log       JSONB             -- step-by-step runbook execution detail
);

-- Indexes for common query patterns
CREATE INDEX idx_workflow_lifecycle_state ON workflow_states(lifecycle_state);
CREATE INDEX idx_workflow_resource_anomaly ON workflow_states(resource_id, anomaly_type);
CREATE INDEX idx_workflow_incident_number ON workflow_states(incident_number);
CREATE INDEX idx_workflow_created_at ON workflow_states(created_at DESC);
```

### events Table (Audit Log)

Immutable event log for the audit trail and event replay.

```sql
CREATE TABLE events (
    event_id    UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflow_states(workflow_id),
    event_type  VARCHAR(100),   -- incident_created | agent_completed |
                                --   approval_requested | condition_cleared | etc.
    data        JSONB,
    timestamp   TIMESTAMP,
    source      VARCHAR(50)     -- which component emitted the event
);

CREATE INDEX idx_events_workflow_timestamp ON events(workflow_id, timestamp);
```

### approvals Table

```sql
CREATE TABLE approvals (
    approval_id     UUID PRIMARY KEY,
    workflow_id     UUID REFERENCES workflow_states(workflow_id),
    status          VARCHAR(20),    -- pending | approved | rejected
    requested_at    TIMESTAMP,
    decided_at      TIMESTAMP,
    decided_by      VARCHAR(255),
    decision_notes  TEXT
);

CREATE INDEX idx_approvals_status ON approvals(status, requested_at);
```

---

## 9. Frontend Data Pipeline

Data flows from the API through a transformation layer before reaching React components.

```
FastAPI REST response
  ↓
WorkflowResponse TypeScript interface
  (mirrors the backend Pydantic response model)
  ↓
transformWorkflow() — frontend/src/utils/workflowTransformer.ts
  - Maps API field names to frontend display model
  - Computes derived display fields (e.g., status badge label and variant)
  - Formats timestamps for display
  - Normalizes null/undefined fields to safe defaults
  ↓
WorkflowDisplayModel TypeScript interface
  (the shape consumed by components)
  ↓
IncidentListTable — frontend/src/components/IncidentListTable.tsx
  - Renders the sortable/filterable incident list
  - Passes selected row → WorkflowDetailsPhase6

WorkflowDetailsPhase6 — frontend/src/components/WorkflowDetailsPhase6.tsx
  - 5-tab detail view (Overview, Timeline, Remediation, Approval, Risk)
  - Subscribes to WebSocket for real-time updates on the open incident

StormsDashboard — frontend/src/components/StormsDashboard.tsx
  - Subscribes to the global WebSocket event stream (incident_created, incident_updated)
  - Refreshes both the storm list and the selected detail panel instantly on any incident change
  - Also runs a 30-second background poll for the detail panel as a resilience fallback
  - A stable useRef tracks the selected storm ID so the interval does not reset on selection change
```

### WebSocket Update Flow

```
Backend NOTIFY → FastAPI WS handler
  → ws.send_json({ type: "state_change", data: { workflow_id, new_state, ... } })
      ↓
Frontend useGlobalEvents hook (shared single WebSocket per browser tab)
  → dispatches to all registered listeners (IncidentList, StormsDashboard, etc.)
  → re-runs transformWorkflow() on updated data
  → React state update → component re-render
```

Real-time updates require no polling. The frontend always reflects the current state within one WebSocket message latency.

---

## 10. Technology Stack Rationale

### Python 3.11 + FastAPI (Backend)

FastAPI provides native async/await for WebSocket and database I/O, built-in Pydantic validation, and auto-generated OpenAPI docs. Python 3.11 gives the strongest typing support in the Python ecosystem and is stable in enterprise environments.

### PostgreSQL (Primary Database)

ACID guarantees are required for approval workflows (approve and resume must be atomic). LISTEN/NOTIFY enables the event bus without a separate broker. JSONB gives flexible event payload storage with indexable queries. The event sourcing model maps naturally to append-only SQL tables.

### Celery + Redis (Background Jobs)

Celery provides task tracking, retries with exponential backoff, rate limiting, and multiple queues (workflows, approvals, summaries can be routed to separate worker pools). Redis is used as the Celery broker for sub-millisecond message latency. Redis persistence is not critical here — Celery tasks are recoverable from the PostgreSQL state if a worker crashes.

### React 18 + TypeScript + Tailwind CSS (Frontend)

React 18 concurrent rendering ensures smooth UI updates during high-frequency WebSocket events. TypeScript enforces the API contract between the transformer layer and components. Tailwind enables the enterprise dark theme with a small, purged CSS bundle.

### eBPF + bpftrace (Monitoring)

Running bpftrace on the host kernel gives visibility into all containers with 1–2% CPU overhead and zero per-container instrumentation. This is the key advantage over sidecar patterns — no deployment changes needed as containers are added or removed.

---

## 11. Scalability and Performance

### Current Baseline (Single Docker Compose Host)

| Metric | Observed |
|---|---|
| API response time (99th percentile) | < 200ms |
| Incident pipeline duration | 5–30s (depends on runbook step count) |
| WebSocket state update latency | < 100ms |
| PostgreSQL query time (indexed) | < 50ms |
| Celery task throughput | 100+ tasks/second |

### Horizontal Scaling

**Backend (FastAPI):** Stateless — multiple instances behind a load balancer. No session affinity required. All state is in PostgreSQL.

**Celery workers:** Independent pool. Scale horizontally by adding worker containers. Workers consume from the shared Redis queue. Separate queues route workflow execution, approval handling, and scheduled tasks to dedicated worker pools.

**Event bus subscribers:** Multiple FastAPI instances can each subscribe to PostgreSQL LISTEN. WebSocket connections require sticky-session routing (same client always reaches the same FastAPI instance) to avoid missed broadcasts.

### Production Scaling Path

- **Phase 1 (current):** Single host Docker Compose. ~300–500 incidents/day capacity.
- **Phase 2:** Managed PostgreSQL (AWS RDS with failover) + managed Redis (ElastiCache) + multiple backend instances behind ALB. ~1,000–1,500 incidents/day.
- **Phase 3:** Kubernetes (EKS/GKE) with HPA on workers and backend. Unlimited scale with cluster capacity.

---

## 12. Security Architecture

### Current Posture

The platform includes role-based API authentication. The following additional hardening is planned before production deployment:

| Gap | Risk | Planned Fix |
|---|---|---|
| Hardcoded credentials in environment variables | Credentials exposed in compose config | Vault integration |
| No rate limiting | Endpoints vulnerable to abuse | FastAPI middleware or API gateway |
| No HTTPS enforcement | Traffic in plaintext on dev | TLS termination at ingress/load balancer |
| CORS open | Accepts requests from any origin | Restrict to known origins in production |

### Existing Controls

**API key authentication:** All API endpoints require a valid `X-API-Key` header. Keys are SHA-256 hashed before storage. Four roles — Admin, ITOM Admin, Operator, Viewer — each carry scoped permissions enforced at the handler layer.

**Input validation:** Pydantic models validate all API request bodies. Type constraints, max lengths, and enum restrictions are enforced before any handler logic executes.

**SQL injection prevention:** Exclusive use of SQLAlchemy ORM (parameterized queries). No raw SQL in application code.

**Approval-based authorization:** All mutating remediation actions require a matching policy that either auto-approves the action or routes it through CAB approval. No action executes without a governance decision.

**Audit trail:** Every workflow state change and agent execution is persisted to the events table as an immutable record, including actor identity (system vs. approver ID), action taken, policy that authorized it, and outcome.

---

## 13. Deployment Architecture

### Docker Compose Services

```yaml
services:
  postgres:         # PostgreSQL 15, primary database
  redis:            # Redis 7, Celery broker + result backend
  neo4j:            # Neo4j 5, CMDB service graph (port 7474 / 7687)
  backend:          # FastAPI + Uvicorn, port 8000
  frontend:         # React/Vite dev server (3000) or nginx (80 in prod)
  celery_worker:    # Celery worker pool, incident pipeline execution
  flower:           # Celery task queue monitor, port 5555
  sentinel_senses:  # bpftrace eBPF sensor (host network + privileged)
  watcher_brain:    # Python watcher orchestration, posts to backend API
```

`sentinel_senses` requires `privileged: true` and `network_mode: host` to access the host kernel for eBPF programs. All other services run with standard container isolation.

### Health Checks

```
GET /api/health  → 200 OK  { "status": "healthy" }    (liveness)
GET /api/ready   → 200 OK / 503                       (readiness: DB + Redis connected)
```

### Environment Variables

Key configuration is passed via environment variables or a `.env` file:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `OPENAI_API_KEY` | Optional — enables LLM summaries via OpenAI |
| `ANTHROPIC_API_KEY` | Optional — enables LLM summaries via Anthropic |
| `BACKEND_URL` | Used by watcher_brain to post events (should be Docker service name, not localhost) |

### Kubernetes Readiness

Helm chart deployment is planned (WISHLIST item 6). The architecture is already Kubernetes-compatible:
- Stateless API servers → Deployments with HPA
- PostgreSQL → StatefulSet or managed RDS
- Redis → StatefulSet or managed ElastiCache
- Celery workers → Deployments with HPA on queue depth metric
- `sentinel_senses` → DaemonSet (one per node, privileged)
