# Axiometica AIR — Feature Catalog

**Last updated:** 2026-07-15
**Platform status:** v1.6.0 — Production Ready

---

## Table of Contents

1. [Automated Incident Management](#1-automated-incident-management)
2. [Storm Agent — Correlated Event Detection](#2-storm-agent--correlated-event-detection)
3. [Decoupled Incident State](#3-decoupled-incident-state)
4. [Incident Enumeration and Tracking](#4-incident-enumeration-and-tracking)
5. [Policy-Based Governance](#5-policy-based-governance)
6. [Runbook Management](#6-runbook-management)
   - [6a. Event Type Taxonomy](#6a-event-type-taxonomy-v111)
7. [Real-Time Monitoring Integration](#7-real-time-monitoring-integration)
8. [Enterprise UI](#8-enterprise-ui)
9. [Typed Agent Context](#9-typed-agent-context)
10. [LLM Summaries](#10-llm-summaries)
11. [Change Management](#11-change-management)
12. [AI Tool Builder — Approved Actions Catalog](#12-ai-tool-builder--approved-actions-catalog)
13. [Feature Status Table](#13-feature-status-table)

---

## 1. Automated Incident Management

> See also: [Storm Agent — Correlated Event Detection](#2-storm-agent--correlated-event-detection) for multi-resource burst handling.

The core capability of Axiometica AIR is a fully automated incident lifecycle pipeline. A raw monitoring event enters at detection and exits as a verified, resolved incident — with no human intervention required unless policy mandates an approval gate.

The pipeline runs as a sequence of seven specialized agents, each extending the previous agent's typed context:

```
SentinelAgent → LibrarianAgent → RiskAssessor → MechanicAgent
    → PolicyBrokerAgent → ToolRegistryAgent → VerifierAgent
```

### Detection

The watcher subsystem uses eBPF-based monitoring via `sentinel_senses`, which runs `bpftrace` on the **host kernel** and observes all containers simultaneously without per-container agents. The `watcher_brain` Python orchestration layer interprets raw signals and raises incidents for the following anomaly types:

| Anomaly Type | Description |
|---|---|
| Syscall anomalies | Abnormal syscall frequency or pattern (potential exploit/security events) |
| CPU spikes | Sustained high CPU utilization on a container |
| Memory spikes | Memory consumption approaching or exceeding limits |
| Disk full | Filesystem usage threshold breach |
| Health check failures | Container health endpoint returning non-200 or not responding |
| Connection spikes | Abnormal inbound or outbound connection counts |
| Log errors | Elevated error rate in container log streams |

Each detected condition is tracked per-resource in `active_conditions` (a dict keyed by `resource_id`). Multiple concurrent conditions on the same resource are tracked independently — clearing one does not close incidents tied to other conditions on the same container.

### Classification and Enrichment

**SentinelAgent** receives the raw monitoring event and classifies the incident:
- Assigns anomaly type, severity, and initial risk indicators
- Tags affected service and environment
- Creates the `IncidentWorkflowContext` dataclass that flows through all subsequent agents

**LibrarianAgent** enriches the classified incident with historical context:
- Queries incident history for similar events on the same service
- Retrieves relevant runbook metadata
- Attaches prior remediation outcomes for the RiskAssessor to use in scoring

### Risk Scoring

**RiskAssessor** produces a 0–100 composite risk score using a multi-factor model:

| Factor | Description |
|---|---|
| Severity | Critical / High / Medium / Low base weight |
| Resource criticality | Production vs. staging vs. dev multiplier |
| Dependency impact | Number and criticality of downstream services affected |
| Business impact | Customer-facing vs. internal service tier |
| Historical recurrence | Repeated incidents on the same resource lower confidence in fast fix |

The risk score is stored in the context and consumed directly by PolicyBrokerAgent to determine whether human approval is required.

### Runbook Selection

**MechanicAgent** selects the best remediation runbook using a 5-tier confidence waterfall:

| Tier | Source | Confidence Range |
|---|---|---|
| 1 | Runbook library — exact match on anomaly type + service + environment | 90–100% |
| 2 | Playbook library — broader match on anomaly type + service | 70–85% |
| 3 | Historical outcomes — runbook that resolved similar past incidents | 60–80% |
| 4 | LLM synthesis — generated runbook from incident context (OpenAI or Anthropic) | 50–75% |
| 5 | Fallback runbook — generic safe-mode diagnostics-only runbook | 30% |

The agent selects the highest-confidence result available and attaches the selected runbook, the tier it came from, and the confidence score to the context.

### Policy Governance

**PolicyBrokerAgent** evaluates the proposed remediation against the organization's policy ruleset:

- Matches policies by `anomaly_type`, `service`, `environment`, and `severity`
- Determines whether approval is **required** before execution
- Enforces **blast radius limits** (maximum containers that can be restarted in one incident)
- Enforces **allowed actions** whitelist (e.g., `restart_container` is allowed; `delete_volume` is not)
- Incidents requiring approval pause and enter the **CAB approval queue** — execution does not proceed until a human approves or rejects

### Execution with Abort Policy

**ToolRegistryAgent** dispatches runbook steps in order. Each step declares an `on_failure` policy:

- `on_failure: abort` **(default)** — if the step fails, halt the entire runbook immediately. No further steps execute. This prevents cascading damage when the environment is in an unexpected state.
- `on_failure: continue` — log the failure and proceed to the next step. Appropriate for non-critical diagnostic steps where partial failure is acceptable.

The conservative default means no destructive action (restart, scale, flush) will execute after a prior step revealed something unexpected.

### Verification

A verification step that carries its own `tool` re-executes it to get a fresh, post-remediation measurement (mirroring the editor's Test Run behaviour) rather than trusting a stale pre-action value; a metric that can't be measured fails closed instead of being assumed to have passed.

**VerifierAgent** runs after the runbook completes, and resolves the incident on one signal only: did execution reach an explicit `incident_update` step? That step type sets the incident's resolution state (e.g. `resolved`) and is only reachable if every step before it — including verification — succeeded, since `on_failure: abort` (the default on every step) halts execution otherwise. No `incident_update` reached → `lifecycle_state = awaiting_manual`, regardless of how far execution got. This replaces an earlier heuristic that inferred resolution from `execution_result.success` (with a `process_kill`-specific recheck), which could mark an incident resolved without the underlying problem ever being confirmed fixed.

Verification details (including which `incident_update`, if any, fired) are attached to the incident's Remediation tab.

### All-Clear Auto-Resolution

After remediation completes (or even if a runbook was never selected), `watcher_brain` continues monitoring the affected resource. When the triggering condition clears on that specific resource, the watcher emits a `condition_cleared` event to the backend via the `POST /api/monitoring/events` endpoint. The backend handler:

1. Locates all open incidents for that `resource_id` and `anomaly_type`
2. Closes them with `lifecycle_state = resolved`
3. Sets `resolution_source = watcher_all_clear`
4. Records the `all_clear_received_at` timestamp

This provides authoritative confirmation from the monitoring layer — not just the runbook — that the condition is genuinely gone.

---

## 2. Storm Agent — Correlated Event Detection

The Storm Agent is a meta-orchestrator layer that sits above the standard 7-agent pipeline. Instead of treating every qualified event as an independent incident, it detects when a burst of incidents across multiple resources shares a common root cause — and coordinates their triage as a single unit.

### Detection

After every qualified event, a background task runs `StormDetectionService.detect()` — a single SQL query that counts open, uncorrelated incidents within a configurable look-back window.

A storm is triggered when all three conditions are met:

| Condition | Default | Setting key |
|-----------|---------|-------------|
| Incidents in window | ≥ 3 | `storm.min_incidents` |
| Distinct resources | ≥ 2 | `storm.min_resources` |
| Look-back window | 120 s | `storm.window_seconds` |

### Lifecycle

```
qualified events ──► StormDetectionService.detect()
                              │  (threshold met)
                              ▼
                    execute_storm_analysis_task (Celery)
                              │
                    ┌─────────┴─────────┐
                    │  Merge check:     │
                    │  storm parent     │
                    │  exists in last   │
                    │  N minutes?       │
                    └──────┬────────────┘
                  Yes ─────┘      └───── No
                   │                     │
              adopt remaining      create new storm parent
              children             lifecycle_state = awaiting_manual
                                   context.is_storm_parent = true
                                         │
                              move children → storm_hold
                              cancel individual CAB approvals
                              create single CAB approval on parent
```

### Root Cause Analysis

The `StormAgent` runs two complementary analyses:

**Neo4j topology traversal** (`storm.neo4j_topology_enabled`)
- Queries the CMDB dependency graph for each affected resource
- Identifies CIs that appear as upstream dependencies of two or more affected resources
- Ranks these shared ancestors as root cause candidates with confidence scores

**LLM hypothesis** (`storm.llm_hypothesis_enabled`)
- Sends affected resources, event types, and root cause candidates to the configured LLM provider
- Returns a natural-language hypothesis explaining the likely failure mode
- Falls back to a deterministic rule-based hypothesis when LLM is unavailable

**Pattern classification** — four deterministic storm patterns:

| Pattern | Trigger event types |
|---------|-------------------|
| `network_partition` | service_unresponsive, health_check_failed, network_anomaly, high_latency |
| `resource_exhaustion` | high_cpu, high_memory, disk_full, high_syscall_intensity |
| `service_cascade` | Mixed pattern with shared upstream CI |
| `mixed_signal_storm` | All other correlated bursts |

### Storm Actions

Operators resolve storms via the **⚡ Event Storms** UI page or the REST API:

| Action | Endpoint | Effect |
|--------|----------|--------|
| Release | `POST /api/storms/{id}/release` | Dismiss storm (false positive); returns children to open state and re-queues them through individual pipelines |
| Resolve | `POST /api/storms/{id}/resolve` | Root cause fixed; bulk-closes all children and the parent |

### Multi-Source Support

Storm detection is source-agnostic. Events arriving via the watcher brain HTTP API and via the Splunk webhook are both normalised by `EventQualificationService` before detection runs — storms can span both sources simultaneously.

### Runtime Settings

All Storm Agent parameters are configurable via `GET/PUT /api/settings/storm` without a service restart:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `storm.enabled` | bool | true | Enable/disable the Storm Agent entirely |
| `storm.window_seconds` | int | 120 | Look-back window for detection |
| `storm.min_incidents` | int | 3 | Minimum incidents to trigger |
| `storm.min_resources` | int | 2 | Minimum distinct resources |
| `storm.merge_window_minutes` | int | 5 | Window for merging concurrent detections |
| `storm.require_cab_approval` | bool | true | Force CAB gate on storm parent |
| `storm.auto_hold_children` | bool | true | Place children in storm_hold |
| `storm.llm_hypothesis_enabled` | bool | true | Enable LLM root cause hypothesis |
| `storm.neo4j_topology_enabled` | bool | true | Enable Neo4j dependency traversal |
| `storm.pipeline_hold_seconds` | int | 0 | Seconds to delay the incident pipeline after creation (gives storm detection time to cluster events before individual pipelines run) |
| `storm.exclude_external_events` | bool | false | Exclude all external connector incidents from storm detection globally |

### Phase 2 — CMDB Dependency Expansion *(v1.0.0)*

After the initial storm parent is created, a delayed background task (`execute_storm_expansion_task`) traverses the Neo4j service graph to find downstream services affected by the same root cause.

For each affected resource, the graph query finds incidents on CIs that:
- Depend on one or more affected resources (`DEPENDS_ON` edges, configurable depth)
- Have active incidents within a configurable time window (`storm.merge_window_minutes`)
- Are not already part of a storm

Qualifying downstream incidents are adopted into the storm parent and moved to `storm_hold`. This ensures the storm represents the full blast radius, not just the first wave of directly impacted resources.

### False Storm Prevention *(v1.0.0)*

External monitoring connectors (Splunk, Datadog, ServiceNow) can perform batch imports where many alerts are ingested simultaneously. Since all events share `created_at = now()`, they cluster into the storm detection window together and can trigger false storms.

Three independent controls prevent this:

**1. `source_alert_time` (automatic)**
At ingest, `monitoring_event.detected_at` is embedded into `context.alert_payload.source_alert_time`. The storm detection query uses `COALESCE(source_alert_time, created_at)` so that old alerts evaluated against their original timestamp are excluded from the current window.

**2. Per-connector `allow_storm_detection` flag**
Each connector can be individually excluded from storm detection via the Connector Hub settings. The flag is embedded as `storm_eligible = false` in the incident context at ingest time and filtered in the detection SQL.

**3. Global `storm.exclude_external_events` setting**
When enabled, all incidents with a `source_connector` field are excluded from storm detection. Use this as a kill-switch when all external connectors perform batch syncs.

---

## 3. Decoupled Incident State

Incident state is tracked across **three independent fields**, not a single status field. This model allows precise reporting on what happened, how automation performed, and what actually cleared the incident — independently.

### `lifecycle_state` — Overall Incident Status

Tracks the incident's position in the lifecycle:

| Value | Meaning |
|---|---|
| `open` | Active, unresolved incident |
| `in_progress` | Agent pipeline or remediation executing |
| `waiting_approval` | Waiting for CAB / operator approval |
| `approved` | Approval granted; execution authorised |
| `executing` | Runbook steps actively executing |
| `awaiting_manual` | Storm parent — human coordination required before any remediation |
| `storm_hold` | Child incident held pending storm parent resolution |
| `monitoring` | Post-remediation monitoring window active |
| `resolved` | Incident cleared (by automation, watcher all-clear, or manual action) |
| `closed` | Incident confirmed closed |
| `failed` | Remediation failed; incident unresolved |
| `rejected` | Approval denied — no action taken |
| `rolled_back` | Remediation was rolled back |

**Active filter:** `lifecycle_state=active` on the API (or "Active (All Open)" in the UI) returns all non-terminal states: `open`, `in_progress`, `waiting_approval`, `approved`, `executing`, `awaiting_manual`, `storm_hold`.

### `remediation_outcome` — How Automation Performed

Tracks automation performance, independent of whether the incident is resolved:

| Value | Meaning |
|---|---|
| `pending` | Remediation has not yet started |
| `succeeded` | Runbook completed all steps successfully |
| `failed` | Runbook completed but verification checks failed |
| `aborted` | Runbook halted because a step failed with `on_failure: abort` |
| `skipped` | No applicable runbook was found or execution was bypassed |

### `resolution_source` — What Actually Cleared It

| Value | Meaning |
|---|---|
| `automated_remediation` | Runbook fixed it, confirmed by VerifierAgent |
| `watcher_all_clear` | Watcher confirmed the condition cleared on the resource |
| `manual` | A human operator resolved it outside of automation |
| `null` | Not yet resolved |

**Why this matters:** The three-field model answers questions that a single status field cannot. For example: "The runbook aborted, but the watcher cleared the incident 4 minutes later — was it a self-heal or did the partial runbook execution help?" Each field answers a distinct question.

---

## 4. Incident Enumeration and Tracking

### Auto-Numbering

Every incident receives a sequential human-readable identifier: `INC0001`, `INC0002`, `INC0003`, and so on. The counter is stored in the database and increments atomically on creation. Incident numbers are never recycled or renumbered.

### Incident List Table

The dashboard incident table supports:
- **Sorting** on any column (incident number, severity, lifecycle state, created timestamp, service)
- **Filtering** by lifecycle state, severity, service, and environment
- **Pagination** with configurable page size
- **Action buttons** that appear on row hover (view detail, approve, resolve, delete)
- **Status badges** with color coding and special sub-labels:
  - `⛔` prefix for incidents where `remediation_outcome = aborted`
  - `🔔` all-clear sub-label for incidents where `resolution_source = watcher_all_clear`

### Per-Incident Detail View

Each incident has a 5-tab detail view:

| Tab | Contents |
|---|---|
| **Overview** | Incident number, summary, affected resource, severity, lifecycle state, all-clear timestamp if applicable |
| **Timeline** | Chronological event log from detection through each agent execution through resolution |
| **Remediation** | Selected runbook name and tier, step-by-step execution log with per-step outcome and failure details |
| **Approval** | CAB approval status, approver identity, decision timestamp, approval notes |
| **Risk** | Risk score (0–100), contributing factor breakdown, policy match details |

---

## 5. Policy-Based Governance

### Policy Rules

Policies are defined with match criteria and enforcement rules. Match criteria can combine `anomaly_type` (event type, supporting multi-select against the Event Type Taxonomy and domain wildcards like `infrastructure.*`), `service`, `environment`, `min_severity`, and `min_risk_score` (a numeric floor — incidents scoring below it are skipped by the policy):

```yaml
match:
  anomaly_type: [high_cpu, infrastructure.disk.full]
  service: payment-service
  environment: production
  min_severity: critical
  min_risk_score: 60
enforce:
  requires_approval: true
  blast_radius_limit: 2
  allowed_actions:
    - restart_container
    - scale_up
```

Match criteria support wildcards — omitting a field matches any value. Multiple policies can coexist for different service/environment combinations.

### Policy Priority and Precedence

When more than one policy matches an incident, only the single best match is applied — **there is no merging of matched policies' rules.** Precedence is decided entirely by the policy's **Policy Priority** number (lower number = higher precedence; default `50`); the matching policy with the lowest priority number wins. A policy with more specific match criteria does **not** automatically take precedence over a more generic one — if two policies match and the more specific one should win, it must be given a lower priority number explicitly. Policy Priority is shown at the top of the Create/Edit Policy form for this reason.

### Approval Workflow

When a policy requires approval:

1. The incident transitions to `lifecycle_state = pending_approval`
2. The incident appears in the **CAB approval queue** on the dashboard, ordered by risk score (highest first)
3. Authorized operators can **approve** (execution resumes) or **reject** (incident marked as failed/skipped)
4. Approval decisions are logged with approver identity and timestamp in the Approval tab
5. Real-time WebSocket push notifies the frontend of approval status changes without requiring a page refresh

### Confidence Gate — Trust-Based Approval Bypass

A policy can optionally define a **Confidence Gate** that automatically bypasses manual approval once a runbook has earned enough trust through real execution history:

- `confidence_gate_threshold` — minimum runbook confidence (0–100%) required
- `confidence_gate_min_runs` — minimum successful executions required
- `confidence_gate_runbook_id` *(optional)* — pins the gate to one specific, named runbook chosen by the policy author. When unset, the gate evaluates whichever runbook the normal event_type/service/platform lookup cascade resolves for the incident at execution time; pinning removes that ambiguity when multiple runbooks could match the same event type/service and the operator wants the gate's trust check tied to one known-good runbook specifically.
- Both threshold and min-runs conditions must be met simultaneously for the gate to bypass approval; the Policy Editor's runbook picker shows each runbook's live confidence % and success rate to inform the choice.

### Blast Radius Limits

Policies can cap the number of containers affected by a single incident's remediation. If execution would exceed the configured limit, the runbook aborts safely before any out-of-scope containers are touched.

### Approved Actions

The list of actions a policy may authorize is sourced live from the **Approved Actions catalogue** (the same catalogue used by the Settings page), grouped by category (`remediation_safe`, `remediation_intrusive`, `notify`) with blast-radius badges. Read-only diagnostic tools are excluded since they are never gated and always run regardless of policy.

### Policy Editor

The dashboard includes a visual policy rule builder for creating and modifying governance policies. Fields include match criteria dropdowns (including searchable Event Type multi-select), Policy Priority, approval toggle with Confidence Gate sub-section, blast radius numeric input, and an Approved Actions multi-select grouped by category. Changes take effect immediately for newly created incidents.

---

## 6. Runbook Management

Runbooks define the automated remediation procedures selected and executed by the MechanicAgent. The platform ships with a seeded library covering common incident types across Docker, Linux, Kubernetes, and cloud environments. All runbooks are database-backed and editable from the UI without a restart.

### Runbook Library UI

The **Runbooks** page presents all configured runbooks as cards:

- **AI confidence %** — calibrated from real execution history; updated after every run
- **Execution stats bar** — colour-coded success/failure progress bar showing outcome distribution
- **Trend badge** — ↑ improving / ↓ declining / → stable derived from the most recent execution window
- **Search** — filter by name, service, event type, or tags

### Step Types

| Type | Purpose | Default `on_failure` |
|---|---|---|
| `diagnostic` | Read-only information gathering (logs, metrics, health probes) | `continue` |
| `action` | Mutating corrective step (restart, flush, scale, process kill) | `abort` |
| `verification` | Post-action metric and health checks confirming resolution | `abort` |
| `incident_update` | Declares the incident's resolution state (e.g. `resolved`); the sole signal VerifierAgent trusts to mark an incident resolved | n/a |
| `decision` | Branch execution on a condition expression | n/a |
| `notify` | Escalate / acknowledge / resolve / message via PagerDuty, Slack, email, or webhook — routed to a named Notification Team or the platform defaults *(v1.5.0)* | `continue` |

### Failure Policy

The default `on_failure: abort` is intentional and conservative — it stops execution if a step fails unexpectedly, preventing a destructive action from running against an already-broken environment. Authors explicitly set `on_failure: continue` for steps where partial failure is acceptable (e.g. a diagnostic that may time out).

### Form Editor

The in-app guided form editor covers the most common runbook patterns:
- Select the trigger **event type** from the searchable taxonomy combobox (see §Event Type Taxonomy below)
- Choose **platform** (`any`, `docker`, `linux`, `windows`, `kubernetes`) — the engine selects the correct command variant for the target host at runtime
- Add steps using the **Approved Actions catalogue** — tool selector auto-populates the command template and parameter fields; the command is editable before saving
- **Verification steps** support metric + operator + threshold conditions (e.g. `cpu_percent < 75`) that the executor evaluates post-remediation

### Visual Workflow Editor *(v1.1.1)*

The **Visual Workflow Editor** is a canvas-based React Flow application for building runbooks with branching and output-capture logic. It opens in a separate browser tab at `/editor/`.

Key capabilities:

| Capability | Description |
|---|---|
| **Drag-and-drop canvas** | Drag step types from the sidebar onto the canvas; connect nodes by drawing edges |
| **Decision nodes** | Branch execution: true-path and false-path edges, evaluated against a condition expression at runtime |
| **Output capture** | Extract values from a step's output via JSONPath (e.g. `disk_percent ← $.usage_percent`) and inject them as `{disk_percent}` variables into subsequent step commands |
| **Conditional execution** | Per-step `run_if` expression — the executor skips the step if the condition evaluates false |
| **Platform-aware commands** | Tool selector resolves the correct OS command variant for the runbook's platform setting |
| **Live test execution** | Connect a real incident and run the runbook step by step; each canvas node animates through `pending → running → success / failed` with live output captured |
| **JSON panel** | Toggle a side panel showing the live runbook JSON — updates in real time as you edit the canvas; copy-pasteable for version control |
| **BFS auto-layout** | Imported runbooks are laid out automatically using a breadth-first-search algorithm that correctly positions branching graphs |
| **Save / Load** | Saves directly to the backend API; loading an existing runbook by `?id=` reconstructs the full canvas including edge routing |

See [VISUAL_RUNBOOK_EDITOR.md](VISUAL_RUNBOOK_EDITOR.md) for the full reference.

### Event Type Taxonomy Integration

Every runbook has an `event_type` field that ties it to the canonical event taxonomy. The form and visual editors both use a **live searchable combobox** backed by `/api/event-types` — typing filters across code, label, and category simultaneously. The MechanicAgent selects runbooks whose `event_type` matches the normalised code of the incoming incident.

---

## 6a. Event Type Taxonomy *(v1.1.1)*

The event type taxonomy is the canonical, database-backed registry of incident classification codes used throughout the platform.

### Why a Taxonomy

Before v1.1.1, event types were a flat frozenset hardcoded in the normaliser. This made it impossible to add site-specific types without a code change. The taxonomy replaces the frozenset with a managed table (`event_type_taxonomy`) that:
- Ships with **210 pre-seeded canonical types** across 9 domains
- Is fully manageable from the Admin UI — no restart required
- Powers type selectors in the runbook and policy editors via a live API query
- Supports **alias chains** so legacy flat codes (e.g. `high_cpu`) transparently normalise to their hierarchical equivalents (`infrastructure.compute.cpu_high`)

### Taxonomy Domains

| Domain | Types | Colour |
|---|---|---|
| `infrastructure` | 27 | Amber |
| `container` | 32 | Blue |
| `application` | 26 | Green |
| `database` | 31 | Purple |
| `cloud` | 30 | Sky |
| `network` | 18 | Cyan |
| `security` | 34 | Red |
| `log` | 5 | Orange |
| `synthetic` | 6 | Pink |
| `custom` | 1 | Slate |

### Admin UI

Navigate to **Admin → Event Types** (requires `itom_admin` or `admin` role). Features:
- Grouped by domain with colour-coded badges
- Per-type enable/disable toggle — disabled types are hidden from selectors but do not break existing runbooks
- **Add Event Type** modal — code (dot-separated, lowercase), label, domain, description
- Search across code, label, and domain simultaneously
- Stats row showing total / enabled / category counts

### API

```
GET  /api/event-types              — list all (or ?enabled_only=true, ?category=security)
GET  /api/event-types/domains      — domain summary with counts
POST /api/event-types              — create custom type
PATCH /api/event-types/{code}      — update label, description, enabled flag
DELETE /api/event-types/{code}     — remove (safe — existing runbooks retain the code value)
```

---

## 7. Real-Time Monitoring Integration

### sentinel_senses (eBPF Layer)

`sentinel_senses` runs `bpftrace` programs on the **host kernel**, giving it visibility into all containers simultaneously without per-container instrumentation. This is a single sensor for the entire host — not a sidecar pattern — so no deployment changes are needed when new containers start.

Raw signals emitted include syscall event counts, CPU and memory readings, filesystem utilization, network connection counts, and health check results.

### watcher_brain (Orchestration Layer)

`watcher_brain` is the Python orchestration service that:
- Consumes raw signals from `sentinel_senses`
- Applies configurable threshold logic to classify conditions (e.g., CPU > 90% sustained for 30 seconds = spike)
- Maintains `active_conditions`: a dict keyed by `resource_id` tracking every active condition type per container
- Raises incidents to the backend when conditions breach thresholds
- Emits per-resource `condition_cleared` events to the backend when conditions normalize

### Accurate Disk Reporting *(v1.1.2)*

Disk utilisation is now read directly from `df -B1` inside each container rather than derived from container runtime stats. This eliminates zero-readings caused by container name mismatches between `docker stats` output and `docker exec df` output — a silent failure that caused the monitoring dashboard to display 0% disk for all containers.

### Watcher Metrics Dashboard *(v1.1.2)*

A real-time monitoring dashboard shows CPU, memory, and disk across all monitored containers over a 20-sample rolling window. Alert thresholds (CPU %, memory %, disk %, syscall rate) are adjustable live from the UI and pushed to the watcher within 30 seconds — no restart required.

### Per-Resource All-Clear

The all-clear mechanism is **per-resource and per-condition-type**. When CPU normalizes on `container-A`, only `container-A`'s CPU incidents are closed. A concurrent disk-full incident on the same container remains open until the disk condition independently clears. This granularity prevents premature closure of still-active incidents.

### Synthetic Transaction Monitoring *(v1.6.0)*

Beyond passive resource metrics and single-endpoint health probes, the watcher can replay a scripted, multi-page user journey — login, navigate, submit — against a real target and assert on both HTTP status and page content. This catches the class of failure a plain uptime check misses entirely: a page that returns 200 but renders broken or empty.

- **HAR-based capture:** record the journey once in Chrome DevTools (Network → Export HAR with content) and upload it. The platform parses pages, requests, and likely credential fields automatically — no scripting required to get started.
- **Deterministic script generation:** the replay script is compiled directly from the parsed HAR — no LLM call on the critical path. An LLM is only invoked on demand, via **Fix with AI**, to patch a script after a failed test run.
- **Credentials as environment variables:** values are Fernet-encrypted at rest and injected into the replay subprocess at run time — never embedded in the generated script text.
- **Per-page content assertions:** an optional regex checked against the full combined response body of every request on a page (case-insensitive) — the parsed page/assertion structure is persisted (`pages_json`) so it can be reviewed and edited later without re-uploading the HAR.
- **Watcher poll-gated scheduling, not Celery:** every enabled monitor is evaluated on each watcher poll cycle, but only actually executes once its own `schedule_mins` (default 15) has elapsed since `last_run_at` — decoupling a monitor's cadence from the watcher's much faster internal poll interval.
- **Structured per-run output:** each run logs a start/end line per page and a method/path/status/latency line per request, both in the watcher's own logs and via a **Log** button on the monitor row in the UI — no container shell access needed to see why a run failed.
- **Full incident integration:** consecutive failures (configurable via `WATCHER_SYNTHETIC_MIN_CONSECUTIVE_FAILS`) raise a `synthetic.transaction.failed` event through the same qualification and 7-agent pipeline as every other anomaly type, and auto-clear on the next passing run.

---

## 8. Enterprise UI

### Dashboard

- **Dark theme** throughout — designed for operations center display environments
- **Incident list table** as the primary view with sortable columns, a filter bar, pagination controls, and row-level action buttons appearing on hover
- **Real-time updates** via WebSocket — new incidents and status changes appear immediately without page refresh
- **MTTR Breakdown card** — mean time to resolution split into three honest buckets: Auto · No Approval, Auto · With Approval, and Manual, each showing weighted-average duration and incident count *(v1.1.3)*

### Incident Detail View

A 5-tab layout provides full context at each stage of an incident's lifecycle. Status badges use color coding and icon prefixes for quick visual triage at the list level:
- `⛔ aborted` — remediation was halted by the abort policy
- `🔔 all-clear` — watcher confirmed the condition cleared on this resource

### Policy Editor

Visual rule builder for governance policies. Supports match criteria dropdowns (event type multi-select, service, environment, severity, min risk score), Policy Priority, approval required toggle with Confidence Gate sub-section (including pinning the gate to a specific runbook *(v1.1.3)*), blast radius numeric limit, and an Approved Actions multi-select sourced live from the Approved Actions catalogue *(v1.1.3)*. Policies can be created, edited, or deleted from the UI.

### Storm Incident Detail View *(v1.1.2)*

Storm parent incidents have a redesigned detail view:
- **Overview tab** — storm timeline showing affected resources and incident sequence; shared upstream CI highlighted
- **AI Insights tab** — LLM-generated root cause hypothesis and confidence score displayed prominently

### Runbook Library *(v1.1.2)*

- **Searchable catalogue** — filter runbooks by name, service, or tags
- **Live confidence scoring** — each card shows AI-calibrated confidence %, updated after every execution
- **Execution stats bar** — colour-coded success/failure progress bar per runbook
- **Trend badges** — ↑ improving / ↓ declining / → stable based on recent execution history
- **Runbook editor** — edit step definitions and abort policies from the UI

### CMDB Force Graph *(v1.1.2)*

Interactive force-layout graph in the CMDB page. Nodes distribute naturally on load via physics simulation — no collapsing to a single point. `HOSTED_ON` and `PART_OF` edges traversable from any CI node.

### Admin Panel

- **Delete all incidents** with confirmation modal
- **System stats** showing incident counts by lifecycle state and agent execution metrics
- **Risk configuration** endpoints to adjust risk scoring weights per factor

### Settings Page

Platform-level configuration including LLM provider selection (OpenAI or Anthropic), API key management, watcher threshold adjustments, notification settings, and **About section** showing the running platform version.

---

## 9. Typed Agent Context

### IncidentWorkflowContext

All agent-to-agent data is passed via a single typed Python dataclass, `IncidentWorkflowContext`, defined in `backend/src/agentic_os/core/context_schema.py`. The context object is created by SentinelAgent and passed through each subsequent agent. Each agent reads the fields written by prior agents and adds its own output fields.

```
SentinelAgent      → adds: incident_id, anomaly_type, severity, resource_id, service, environment
LibrarianAgent     → adds: historical_incidents, relevant_runbooks, enrichment_data
RiskAssessor       → adds: risk_score, risk_factors, risk_breakdown
MechanicAgent      → adds: selected_runbook, selection_tier, confidence_score
PolicyBrokerAgent  → adds: policy_match, approval_required, blast_radius_limit, allowed_actions
ToolRegistryAgent  → adds: execution_log, step_results, remediation_outcome
VerifierAgent      → adds: verification_results, verification_passed, final_lifecycle_state
```

**Benefits of the typed schema:**
- **Type safety** — mypy validates field access at development time
- **Auditability** — the full context at each stage is persisted to the database
- **Explicit contracts** — agents declare their input and output fields; no implicit coupling
- **Early failure** — missing required fields raise at agent initialization, not mid-execution

---

## 10. LLM Summaries

### AI-Generated Summaries

When an LLM provider is configured (OpenAI or Anthropic), the platform generates:
- Plain-language incident summaries for the Overview tab
- Root cause analysis suggestions based on the enriched context
- Recommended next steps for incidents that could not be auto-resolved

### Platform Context Fallback

If no LLM provider is configured or the LLM call fails, the **platform context service** generates structured summaries directly from the typed context fields. These summaries are deterministic, always available, and provide actionable information without requiring any external API.

The UI degrades gracefully — operators see a well-structured, informative summary regardless of LLM availability. No feature is blocked by the absence of an LLM.

---

## 11. Change Management

A secondary change management workflow runs alongside the incident pipeline and shares the same infrastructure:

- Change request creation with automated risk assessment
- CAB approval workflows using the same approval queue as incident management
- Change execution with runbook-driven steps and rollback runbooks
- Post-change verification using the same VerifierAgent pattern
- Full audit trail in the same event log as incidents

Change management reuses policy governance, runbook execution, typed context, and WebSocket update infrastructure without duplication.

---

## 12. AI Tool Builder — Approved Actions Catalog

The AI Tool Builder generates complete, multi-environment Approved Action catalog entries from a plain-English description. It uses a **three-call LLM pipeline** to produce higher-quality output than a single prompt can achieve.

### Three-Call Pipeline

| Call | Purpose | Output |
|------|---------|--------|
| **Call 0 — Research** | Identifies the single best bare shell command for the task and produces 8–10 realistic sample output lines with real-looking values (IPs, PIDs, sizes, port numbers) | `{"command": "...", "sample_output": [...]}` |
| **Call 1 — Structure** | Drafts the full catalog entry (tool name, description, per-adapter command variants, parameters, output field names and types) informed by the researched command and sample | Full tool JSON without patterns |
| **Call 2 — Patterns** | Analyses the authoritative research sample to locate each output field by position or marker; Python builds the regex mechanically from a location strategy — the LLM never writes regex syntax directly | Completed `output_fields` with `kind` and `pattern` |

### Location Strategies (Call 2)

Rather than asking the LLM to write regex, Call 2 maps each field to a **location strategy** and Python constructs the pattern:

| Strategy | Use case | Example |
|----------|---------|---------|
| `single_value` | Entire line is the value | `wc -l` output: `42` |
| `column` | Whitespace-delimited token at position N | Column 3 of `ps aux` output |
| `after_literal` | Value follows a fixed label | `used_memory:1048576` |
| `end_split_before` | Value is before a delimiter in the last token | `1234` from `1234/sshd` |
| `end_split_after` | Value is after a delimiter in the last token | `sshd` from `1234/sshd` |
| `last_column` | Value is the final whitespace-delimited token | |
| `count` | Count lines matching a pattern; always returns an integer | Lines containing `LISTEN` |

**Column header matching** — when a command prints a header row (`ss`, `netstat`, `docker ps`), Call 2 identifies which header label semantically matches each output field and uses that column's position, preventing the common off-by-one error of using the Nth field name as column N.

**Header-row filtering** — lines with no digit characters are excluded from pattern validation (headers like `Netid State Recv-Q Send-Q` contain no digits), so patterns are validated only against real data rows.

### Tabular Output Handling

Commands that produce multi-row tables (`ss`, `netstat`, `ps`, `df`, `docker ps`) are handled through one of three strategies, chosen based on the description's intent:

- **Parameterise + filter** — add a `{{port}}` or `{{process_name}}` parameter and pipe through `grep` so the output is 0–1 matching lines. Best for "check if X is present" queries.
- **Aggregate** — pipe through `| wc -l` or `| grep -c <pattern>` to produce a single count. Best for "how many X" queries. Generates a `<noun>_count` field with `type: integer`.
- **Both** — when the description asks for a count and a specific check, both patterns are applied.

For descriptions without a clear filter/count intent, the AI still appends a `_count` summary field so runbooks can threshold on row volume.

### `kind: count` Extraction Mode

Output fields with `kind: count` count the number of lines in the command output matching a pattern at runtime, rather than capturing a value from a single line. This is set automatically when Call 2 detects a `*_count` field name against tabular output.

```json
{
  "field": "listen_count",
  "kind": "count",
  "pattern": "LISTEN",
  "type": "integer"
}
```

At execution time, `_extract_output_fields()` uses `re.findall(pattern, output, re.MULTILINE)` and returns the match count. An empty pattern counts all non-empty lines.

### Multi-Environment Command Generation

Call 1 generates adapter-specific command variants for every environment the platform supports:

| Adapter | Convention |
|---------|-----------|
| `docker` | `docker exec {{container_name}} <bare-command>` |
| `kubernetes` | `kubectl exec -n {{namespace}} {{pod_name}} -- <bare-command>` |
| `ssh` | Bare shell command, runs on remote host |
| `aws_ssm` | Bare shell command, delivered via SSM Run Command |
| `azure` | Bare shell command, delivered via Azure Run Command |
| `any` | Bare shell fallback for unrecognised adapters |

Adapter-scoped parameters (`container_name`, `namespace`, `pod_name`) are automatically marked `required: false` — they are injected from the watcher's registration context, not supplied by the operator.

### Command Conventions Enforced by Prompt

- **curl HTTP checks** — always uses `-s -o /dev/null -w "%{http_code} %{time_total}\n"` producing a single space-separated line. JSON format strings and non-existent curl constructs (e.g. `${if_eq:200}`) are explicitly prohibited.
- **Tabular commands** — always piped to produce a single value rather than a raw table.

### Modal UI

The AI Tool Builder opens as a **portal modal** (rendered via `createPortal` to avoid stacking context issues) with three steps:

1. **Describe** — plain-English description with optional adapter hints. Cmd/Ctrl+Enter to generate.
2. **Review** — read-only JSON preview of the generated definition; collapsible **Refine with Real Output** accordion pre-filled with the research sample. The Refine step merges: fields that already have patterns are kept; blank-pattern fields are updated; new fields are appended.
3. **Register** — tool is saved to the catalog as **disabled by default**, with an inline notice to test before enabling.

A three-stage spinner is shown during generation, listing the three pipeline calls in progress.

---

## 13. Feature Status Table

| Feature | Status |
|---|---|
| **Incident Pipeline** | |
| Automated incident detection (eBPF watcher) | Implemented |
| 7-agent incident pipeline (Sentinel through Verifier) | Implemented |
| Typed context schema (IncidentWorkflowContext) | Implemented |
| Risk scoring (0–100, multi-factor) | Implemented |
| MechanicAgent 5-tier runbook selection with confidence scoring | Implemented |
| Policy governance (approval, blast radius, allowed actions) | Implemented |
| CAB approval queue | Implemented |
| Step abort policy (on_failure: abort default) | Implemented |
| Decoupled state fields (lifecycle_state / remediation_outcome / resolution_source) | Implemented |
| Per-resource watcher all-clear mechanism | Implemented |
| Celery async task execution | Implemented |
| **Storm Detection** | |
| StormDetectionService (SQL scan on every qualified event) | Implemented |
| StormAgent — Neo4j topology traversal for root cause | Implemented |
| StormAgent — LLM hypothesis generation | Implemented |
| Storm pattern classification (4 pattern types) | Implemented |
| CMDB dependency expansion (Phase 2 adoption) | Implemented |
| False storm prevention (source_alert_time, per-connector flag, global kill-switch) | Implemented |
| Storm runtime settings (window, thresholds, toggles — no restart) | Implemented |
| **Incident Tracking** | |
| Incident auto-numbering (INC0001, INC0002, ...) | Implemented |
| Sortable, filterable, paginated incident table | Implemented |
| 5-tab per-incident detail view | Implemented |
| Real-time WebSocket updates | Implemented |
| **Monitoring (Watcher)** | |
| sentinel_senses eBPF host-kernel monitoring | Implemented |
| watcher_brain multi-type anomaly detection | Implemented |
| Per-resource condition cleared event flow | Implemented |
| CMDB auto-discovery (containers → Neo4j) | Implemented |
| Watcher metrics dashboard (CPU/memory/disk rolling graphs) | Implemented (v1.1.2) |
| Accurate disk reporting via df -B1 (fixes zero-disk bug) | Implemented (v1.1.2) |
| Live threshold controls — hot-reload without restart | Implemented (v1.1.2) |
| Synthetic Transaction Monitoring — HAR-based scripted journey replay | Implemented (v1.6.0) |
| Per-page content assertions with persisted page/request structure | Implemented (v1.6.0) |
| Deterministic script generation + on-demand AI script repair | Implemented (v1.6.0) |
| **Connector Hub** | |
| Open webhook event ingest (any monitoring tool) | Implemented |
| ServiceNow certified connector (bidirectional) | Implemented |
| Splunk certified connector | Implemented |
| Datadog certified connector | Implemented |
| Dynatrace certified connector | Implemented |
| Prometheus / Alertmanager certified connector | Implemented |
| PagerDuty certified connector (inbound webhook ingest) | Implemented |
| PagerDuty outbound escalation — Events API v2 trigger / acknowledge / resolve | Implemented (v1.5.0) |
| Zabbix certified connector | Implemented |
| Grafana Unified Alerting certified connector | Implemented |
| Generic webhook connector (any source, multi-source) | Implemented |
| Per-connector governance flags (auto-remediation, storm eligibility, webhook secret) | Implemented |
| **Notification Teams** *(v1.5.0)* | |
| Explicit team-based alert routing — PagerDuty routing key, Slack channel, email recipients, webhook (any combination, independent of CMDB/ServiceNow) | Implemented |
| Unified `notify` action — escalate / acknowledge / resolve / message, with legacy `alert_escalate` / `alert_update` / `send_alert` aliases | Implemented |
| Live team autocomplete in both the form and visual runbook editors | Implemented |
| **Authentication & Users** | |
| JWT authentication (login, token refresh) | Implemented |
| Role-based access control (admin, itom_admin, operator, viewer) | Implemented |
| User management (create, edit, deactivate) | Implemented |
| **Slack ChatOps** | |
| Outbound notifications (critical incidents, approvals, resolutions, storms) | Implemented |
| Inbound NL chat (query incidents, MTTR, runbooks) | Implemented |
| Interactive approve / reject from Slack | Implemented |
| Socket Mode (no public URL) and webhook mode | Implemented |
| Role enforcement (Slack email ↔ platform role) | Implemented |
| **Virtual Chat / AI Ops Assistant** | |
| AI Ops Assistant with SSE streaming responses | Implemented |
| Live platform context (incidents, approvals, MTTR) | Implemented |
| **Platform Intelligence** | |
| TuningAgent — multi-check analysis of resolved incidents (false-positive rate, domain/event multipliers, governance effectiveness, resource-level noise, runbook step health, failure root-cause taxonomy) | Implemented |
| Live config application (no restart required) | Implemented |
| Scheduled tuning runs (configurable schedule, force-refresh override) | Implemented (v1.1.3) |
| Closed-loop auto-apply — recommendations that prove out over ≥3 verified cycles apply automatically; a regression reverts the pattern to manual review within one cycle | Implemented (v1.1.3) |
| KPI dashboard with trend charts and tooltips (noise reduction, MTTR impact, approval load, etc.) | Implemented (v1.1.3) |
| Run History tab — persisted run-by-run record via `PlatformIntelRunModel` | Implemented (v1.1.3) |
| Async analysis execution via Celery (non-blocking, page-level spinner) | Implemented (v1.1.3) |
| **Summaries** | |
| LLM summaries (OpenAI / Anthropic) | Implemented |
| Platform context fallback summaries (no LLM required) | Implemented |
| **Runbooks** | |
| Database-backed runbook library with seeded catalogue | Implemented |
| Step types: diagnostic, action, verification, incident_update, decision, notify | Implemented |
| Per-step on_failure: abort \| continue policy | Implemented |
| Draft/publish workflow with version history (runbooks + policies) | Implemented (v1.4.0) |
| Form editor — event type combobox, platform selector, tool catalog, verification conditions | Implemented |
| Visual Workflow Editor — React Flow canvas with drag-and-drop | Implemented (v1.1.1) |
| Decision nodes — true/false branching evaluated at runtime | Implemented (v1.1.1) |
| Output capture — JSONPath extraction + variable interpolation between steps | Implemented (v1.1.1) |
| Conditional steps — run_if expressions | Implemented (v1.1.1) |
| Live test execution — real-time step animation with incident context | Implemented (v1.1.1) |
| BFS auto-layout for imported runbooks | Implemented (v1.1.1) |
| Runbook library cards — confidence %, execution stats bar, trend badges | Implemented (v1.1.2) |
| **Event Type Taxonomy** | |
| 210 canonical event types across 9 domains | Implemented (v1.1.1) |
| Hierarchical dot-separated codes (domain.subdomain.type) | Implemented (v1.1.1) |
| Alias support — legacy flat codes map to hierarchical equivalents | Implemented (v1.1.1) |
| Admin UI — grouped by domain, enable/disable per type, add custom | Implemented (v1.1.1) |
| Live combobox in runbook and policy editors | Implemented (v1.1.1) |
| REST CRUD API (/api/event-types) | Implemented (v1.1.1) |
| **UI** | |
| Enterprise dark theme dashboard | Implemented |
| Policy editor (visual rule builder) | Implemented |
| Admin panel (delete all, system stats) | Implemented |
| Settings page | Implemented |
| Status badges (⛔ aborted, 🔔 all-clear) | Implemented |
| Event Storms page | Implemented |
| Storm incident detail — Overview + AI Insights tabs | Implemented (v1.1.2) |
| Connector Hub UI | Implemented |
| Runbook Library — searchable catalogue, confidence scoring, stats bar, trend badges | Implemented (v1.1.2) |
| Runbook editor — in-UI step / abort policy editing | Implemented (v1.1.2) |
| CMDB force graph — physics-based node layout, HOSTED_ON/PART_OF traversal | Implemented (v1.1.2) |
| Active (All Open) incident filter — returns all non-terminal states correctly | Implemented (v1.1.2) |
| **Deployment** | |
| Docker Compose deployment (all services containerized) | Implemented |
| Change management workflow | Implemented |
| **AI Tool Builder** *(v1.6.0)* | |
| 3-call LLM pipeline — research command + sample, draft structure, generate patterns | Implemented (v1.6.0) |
| Location-strategy pattern generation — column, after_literal, end_split, count (no LLM regex) | Implemented (v1.6.0) |
| Column header matching — semantic header-to-column resolution for ss/netstat/docker ps | Implemented (v1.6.0) |
| Header-row filtering — digit-presence heuristic excludes header lines from pattern validation | Implemented (v1.6.0) |
| `kind: count` extraction mode — counts matching lines at runtime via re.findall | Implemented (v1.6.0) |
| Tabular output handling — parameterise+filter, aggregate (wc -l / grep -c), or both | Implemented (v1.6.0) |
| Multi-environment command generation — docker, kubernetes, ssh, aws_ssm, azure, any | Implemented (v1.6.0) |
| Portal modal UI — spinner, step pills, read-only JSON preview, collapsible Refine accordion | Implemented (v1.6.0) |
| Research sample pre-fill — Refine textarea pre-populated from Call 0 output | Implemented (v1.6.0) |
| Merge logic — Refine keeps existing patterns, fills blanks, appends new fields | Implemented (v1.6.0) |
| Tools registered as disabled by default with inline test-before-enable notice | Implemented (v1.6.0) |
| **Planned** | |
| Kubernetes / Helm deployment | Planned |
| Distributed tracing (OpenTelemetry + Jaeger) | Planned |
| Advanced analytics (MTTR trend lines over time, service health scorecards) | Planned |
| ML-based incident prediction | Planned |
| Multi-tenancy | Planned |
| Mobile companion app (React Native) | Planned |
| GraphQL API layer | Planned |
| Vault integration (secrets management) | Planned |
