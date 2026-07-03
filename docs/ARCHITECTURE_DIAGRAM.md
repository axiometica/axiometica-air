# Axiometica AIR - Architecture Diagrams

## System Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          INFRASTRUCTURE LAYER                            │
│  Servers • Containers • Applications • Databases • Network Devices       │
└──────────────────────┬───────────────────────────────────────────────────┘
                       │
      ┌────────────────┼────────────────┐
      │                │                │
      ▼                ▼                ▼
  ┌─────────┐     ┌─────────┐     ┌──────────┐
  │Sentinel │     │ Health  │     │  Log     │
  │ eBPF    │     │ Checks  │     │ Monitor  │
  │(syscall)│     │(HTTP)   │     │          │
  └────┬────┘     └────┬────┘     └────┬─────┘
       │               │               │
       └───────────────┴───────────────┘
                       │
                       ▼
            ┌──────────────────────┐
            │   Watcher Service    │
            │  (Anomaly Detection) │
            │   Python + Rules     │
            └──────────┬───────────┘
                       │
                 POST /api/monitoring-events
                       │
       ┌───────────────▼───────────────┐
       │  Event Qualification Service  │
       │  (threshold scoring)          │
       └───────────────┬───────────────┘
                       │
            ┌──────────▼──────────┐
            │Storm Detection Svc  │
            │(correlated events)  │
            └────┬─────────┬──────┘
                 │         │
          [storm burst]  [single]
            ┌─────┴──┐     │
            │        │     │
            ▼        ▼     ▼
       ┌────────────────────────────────┐
       │   7-AGENT AI PIPELINE          │
       │  (Celery task queue executor)  │
       │                                │
       │ 1. SentinelAgent (classify)    │
       │ 2. LibrarianAgent (CMDB)       │
       │ 3. RiskAssessor (score)        │
       │ 4. MechanicAgent (runbook)     │
       │ 5. PolicyBroker (govern)       │
       │    ├─ Apply rules              │
       │    └─ Mark for approval?       │
       │         │                      │
       │         ├─ YES → waiting_approval
       │         └─ NO → proceed        │
       │ 6. ToolRegistry (execute)      │
       │ 7. VerifierAgent (validate)    │
       └────────┬───────────────────────┘
                │
                ▼
       ┌──────────────────────┐
       │   Event Bus (PGLISTEN)
       │   (PostgreSQL NOTIFY) │
       └──────────┬───────────┘
                  │
                  ▼
       ┌──────────────────────┐
       │   WebSocket Handler  │
       │   (FastAPI)          │
       └──────────┬───────────┘
                  │
                  ▼
       ┌──────────────────────┐
       │   React UI (Browser) │
       │   Real-time updates  │
       └──────────────────────┘
```

---

## Data Flow: Incident Detection to Resolution

```
[1] DETECTION
    Sentinel (eBPF)  ─────► [syscall data]
    Watcher (Python) ─────► [anomaly score] ─┐
    Health checks ────────► [status] ──────────┼─► [qualified?]
    Log monitor ───────────► [patterns] ──────┘
                                                │
                                           threshold check
                                                │
                                         YES   │  NO
                                          ▼    └──→ (ignored)
[2] CORRELATION
    StormDetectionService ──────┐
    (≥3 incidents, ≥2 resources)│
                         correlated?
                          │        │
                      YES │        │ NO
                          ▼        ▼
                      STORM     SINGLE
                    (parent)    (incident)
                    awaiting_    │
                    manual       │
                        │        │
                        └────┬───┘
                             │
[3] AI PIPELINE EXECUTION (Celery)
    ├─ [SentinelAgent] Classify type/severity
    │   ↓ writes: ctx.sentinel
    ├─ [LibrarianAgent] CMDB enrichment
    │   ↓ writes: ctx.cmdb
    ├─ [RiskAssessor] Calculate risk score
    │   ↓ writes: ctx.risk (0-100)
    ├─ [MechanicAgent] Select runbook
    │   ↓ writes: ctx.proposal
    ├─ [PolicyBroker] Apply governance
    │   ↓ writes: ctx.governance
    │   └─► approval_required?
    │       ├─ YES → lifecycle_state = "waiting_approval"
    │       │        [CAB Approval Queue]
    │       │        operator reviews + approves/rejects
    │       │        (if rejected: lifecycle_state = "failed")
    │       │        (if approved: continue)
    │       └─ NO → proceed
    ├─ [ToolRegistry] Execute runbook
    │   ↓ writes: ctx.execution_results
    │   ├─ on_failure: abort
    │   └─ step timeout handling
    ├─ [VerifierAgent] Validate resolution
    │   ↓ writes: ctx.verification
    │   └─► resolution_source = "automated_remediation"
    │
[4] ALTERNATIVE: Watcher All-Clear
    Watcher detects condition returned to normal
    ├─ POST /api/monitoring-events (event_type="condition_cleared")
    ├─ lifecycle_state = "resolved"
    └─ resolution_source = "watcher_all_clear"

[5] BROADCAST
    Event bus (NOTIFY) ─────────► WebSocket ─────► React UI
    Incident appears in dashboard
    Incident detail auto-updates
    Notifications sent (Slack, etc.)
```

---

## Database Schema (Simplified)

```
workflow_states (incidents/changes)
├─ id (UUID primary key)
├─ workflow_type (enum: incident, change)
├─ incident_number_str (string: INC0042)
├─ lifecycle_state (enum)
├─ remediation_outcome (enum)
├─ resolution_source (enum)
├─ context (JSONB: full IncidentWorkflowContext)
├─ created_at, updated_at (timestamps)
└─ created_by, updated_by (user FK)

events (append-only audit log)
├─ id (UUID)
├─ workflow_id (FK → workflow_states)
├─ event_type (string: classification_complete, remediation_started, etc.)
├─ data (JSONB)
├─ created_at
└─ correlation_id (chains related events)

approvals (CAB queue)
├─ id (UUID)
├─ workflow_id (FK → workflow_states)
├─ requested_at, requested_by
├─ status (enum: pending, approved, rejected)
├─ approved_at, approved_by
├─ rejection_reason
└─ expires_at

runbooks
├─ id (UUID)
├─ name (string)
├─ description
├─ steps (JSONB array)
├─ on_failure (enum: abort, continue)
├─ execution_count, success_count
├─ confidence_score (0-100)
└─ updated_by, updated_at

Neo4j CMDB (separate graph database)
├─ Node: Container
│  ├─ properties: name, image, port, status
│  └─ relationships: HOSTED_ON, PART_OF
├─ Node: Service
│  └─ properties: name, owner, criticality
├─ Node: Host
│  └─ properties: name, ip, region
└─ Relationships traverse to show incident correlation
```

---

## 7-Agent Pipeline Deep Dive

```
┌──────────────────────────────────────────────────────────────┐
│                    INCIDENT ARRIVES                          │
│  (from watcher, webhook, manual submission)                  │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ [1] SentinelAgent              │
        │   classify(incident)           │
        │                                │
        │ Input: raw event               │
        │ ├─ type: high_syscall_intensity
        │ ├─ severity: high              │
        │ └─ resource: container_name    │
        │                                │
        │ Output: ctx.sentinel           │
        │ ├─ classification: syscall_bomb
        │ ├─ impact: high                │
        │ └─ confidence: 95%             │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ [2] LibrarianAgent             │
        │   enrich_from_cmdb(incident)   │
        │                                │
        │ Queries Neo4j:                 │
        │ ├─ MATCH (c:Container) WHERE  │
        │ │  c.name = resource_name      │
        │ ├─ MATCH (s:Service) ←PART_OF │
        │ │  (c:Container)               │
        │ └─ MATCH (h:Host) ←HOSTED_ON  │
        │   (c:Container)                │
        │                                │
        │ Output: ctx.cmdb               │
        │ ├─ container: {...}            │
        │ ├─ service: [name, owner]      │
        │ ├─ host: [name, ip]            │
        │ └─ impacted_services: 3        │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ [3] RiskAssessor               │
        │   score_impact(incident)       │
        │                                │
        │ 9-factor model:                │
        │ ├─ Criticality: 25 pts         │
        │ ├─ Blast radius: 20 pts        │
        │ ├─ Urgency: 15 pts             │
        │ ├─ Dependencies: 12 pts        │
        │ ├─ Time of day: 10 pts         │
        │ ├─ User impact: 10 pts         │
        │ ├─ Revenue impact: 5 pts       │
        │ ├─ SLA status: 2 pts           │
        │ └─ Repeatability: 1 pts        │
        │                                │
        │ Output: ctx.risk               │
        │ ├─ risk_score: 67/100          │
        │ ├─ priority: P2                │
        │ └─ factors: [breakdown]        │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ [4] MechanicAgent              │
        │   select_runbook(incident)     │
        │                                │
        │ Selection tiers (in order):    │
        │ ├─ T1: Database exact match    │
        │ ├─ T2: CMDB playbooks          │
        │ ├─ T3: Historical outcomes     │
        │ ├─ T4: LLM synthesis           │
        │ └─ T5: Safe fallback (manual)  │
        │                                │
        │ Output: ctx.proposal           │
        │ ├─ runbook_id: UUID            │
        │ ├─ runbook_name: string        │
        │ ├─ steps: [array]              │
        │ ├─ tier_selected: integer      │
        │ └─ confidence: percentage      │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ [5] PolicyBrokerAgent          │
        │   apply_governance(incident)   │
        │                                │
        │ Queries governance_policies:   │
        │ ├─ WHERE env = incident.env    │
        │ ├─ WHERE service_id = ...      │
        │ └─ WHERE risk_score >= ...     │
        │                                │
        │ Applies rules:                 │
        │ ├─ If (env==prod && risk>50)  │
        │ │  → approval_required = true   │
        │ ├─ If (service==payment)       │
        │ │  → escalation_required       │
        │ └─ If (!allow_auto_fix)        │
        │   → manual_only = true         │
        │                                │
        │ Output: ctx.governance         │
        │ ├─ approval_required: boolean  │
        │ ├─ escalation_required: boolean
        │ ├─ policies_matched: [ids]     │
        │ └─ gates: [approval objects]   │
        └────────────┬───────────────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
     approval=YES          approval=NO
          │                     │
          ▼                     ▼
    waiting_approval      ┌────────────────────────────────┐
    [CAB Queue]          │ [6] ToolRegistryAgent          │
    (human review)       │   execute_runbook(incident)    │
          │              │                                │
          │              │ For each step in runbook:      │
       approved/          │ ├─ Load action template        │
      rejected by        │ ├─ Substitute incident context │
      operator           │ ├─ Execute action              │
          │              │ ├─ Check step success          │
          │              │ └─ on_failure: [abort/continue]
          │              │                                │
          │              │ Output: ctx.execution_results  │
          │              │ ├─ steps_executed: [array]     │
          │              │ ├─ steps_failed: [array]       │
          └──────┬───────┤ ├─ total_duration: seconds     │
                 │       │ └─ remediation_attempted: bool │
                 │       └────────────┬───────────────────┘
                 │                    │
                 └────────┬───────────┘
                          │
                          ▼
        ┌────────────────────────────────┐
        │ [7] VerifierAgent              │
        │   verify_resolution(incident)  │
        │                                │
        │ Checks:                        │
        │ ├─ Is condition still present? │
        │ ├─ Did metrics improve?        │
        │ ├─ Are dependencies healthy?   │
        │ └─ Is service responding?      │
        │                                │
        │ Output: ctx.verification       │
        │ ├─ resolved: boolean           │
        │ ├─ verified_at: timestamp      │
        │ └─ resolution_source:          │
        │    "automated_remediation"     │
        │                                │
        │ Final lifecycle_state:         │
        │ ├─ "resolved" if successful    │
        │ ├─ "failed" if unsuccessful    │
        │ └─ "aborted" if user stopped   │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ Event Bus Notification          │
        │ (PostgreSQL NOTIFY)             │
        │ ├─ incident:resolved            │
        │ ├─ incident:failed              │
        │ └─ incident:aborted             │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │ WebSocket Broadcast             │
        │ ├─ React UI updates live        │
        │ ├─ Slack notification sent      │
        │ └─ Audit log entry created      │
        └────────────────────────────────┘
```

---

## Real-Time Event Bus

```
┌─────────────────────────────────────────────────────────┐
│ Celery Worker (executing incident pipeline)             │
│                                                         │
│ await workflow_engine.execute(incident)                 │
│                                                         │
│ for agent in [sentinel, librarian, ...]:               │
│   agent.run(context)                                   │
│   [agent writes to context]                            │
│   db.save_workflow_state(context)                      │
│   [emit event]                                         │
│                  │                                     │
└────────┬─────────▼─────────┬──────────────────────────┘
         │                   │
         │                   ▼
         │         ┌──────────────────────┐
         │         │ PostgreSQL Database  │
         │         │ workflow_states table│
         │         │ events table (append)│
         │         │                      │
         │         │ NOTIFY incident_...  │
         │         └──────────┬───────────┘
         │                    │
         │                    ▼
         │         ┌──────────────────────────────┐
         │         │ PostgreSQL Listener (app)    │
         │         │ LISTEN incident_updates      │
         │         │ on_notification(event) {     │
         │         │   emit websocket:broadcast() │
         │         │ }                            │
         │         └──────────┬───────────────────┘
         │                    │
         ▼                    ▼
    [Other workers]    WebSocket Handler
                            │
                            ▼
                    ┌───────────────────┐
                    │ Connected clients │
                    │ (browser windows) │
                    │                   │
                    │ React components  │
                    │ re-render         │
                    └───────────────────┘
```

---

**For detailed technical information, see [docs/ARCHITECTURE.md](./ARCHITECTURE.md)**
