# Storm Detection & Analysis

> Developer / operator reference for the Axiometica AIR correlated-event storm feature.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
   - 2.1 [Phase 1 — Time-Window Detection](#21-phase-1--time-window-detection)
   - 2.2 [Phase 2 — Dependency Expansion](#22-phase-2--dependency-expansion)
   - 2.3 [Storm Agent](#23-storm-agent)
   - 2.4 [Storm Parent Incident](#24-storm-parent-incident)
   - 2.5 [Storm Children](#25-storm-children)
3. [Race Condition Handling](#3-race-condition-handling)
4. [Approval Cancellation](#4-approval-cancellation)
5. [Merge Path](#5-merge-path)
6. [Event Type Correlation Groups](#6-event-type-correlation-groups)
7. [Confidence Scoring](#7-confidence-scoring)
8. [Lifecycle States](#8-lifecycle-states)
9. [Platform Settings](#9-platform-settings)
10. [API Endpoints](#10-api-endpoints)
11. [Typical Storm Scenario](#11-typical-storm-scenario)
12. [Known Limitations & Operational Notes](#12-known-limitations--operational-notes)

---

## 1. Overview

The storm feature is a **meta-orchestrator** that sits above the standard 7-agent incident pipeline. It detects **correlated event storms** — a burst of incidents across multiple resources within a short time window that suggests a shared root cause rather than independent failures.

**Example:** A network partition causes multiple service health checks to fail simultaneously. Without storm detection each incident would enter the individual pipeline, potentially triggering separate remediations that race with one another and make the situation worse. The storm feature groups these into a single parent incident that requires CAB-level approval before any coordinated action is taken.

Storm detection has two phases and a dedicated analysis agent:

- **Phase 1** (`storm_detection.py`) — fast SQL scan that triggers on a new incident being created.
- **Phase 2** (`celery_app.py`) — Neo4j CMDB query that adopts downstream service incidents into the storm.
- **Storm Agent** (`storm_agent.py`) — topology analysis, pattern classification, and root cause hypothesis.

---

## 2. Architecture

### 2.1 Phase 1 — Time-Window Detection

**Source:** `backend/src/agentic_os/services/storm_detection.py`

Phase 1 runs as a **FastAPI background task** immediately after every new incident is created. It is intentionally lightweight: a single SQL query followed by in-Python threshold checks.

#### Detection query

```sql
SELECT
    workflow_id::text                               AS wf_id,
    context -> 'alert_payload' ->> 'type'           AS event_type,
    context -> 'alert_payload' ->> 'resource_name'  AS resource_name,
    created_at
FROM workflow_states
WHERE workflow_type    = 'incident'
  AND lifecycle_state NOT IN ('resolved', 'closed', 'storm_hold')
  AND created_at      > :cutoff          -- window_seconds look-back
  AND storm_id        IS NULL            -- not already in a storm
  AND (context ->> 'is_storm_parent' IS NULL
       OR (context ->> 'is_storm_parent')::boolean IS DISTINCT FROM true)
  AND (:exclude_id IS NULL OR workflow_id::text != :exclude_id)
ORDER BY created_at DESC
```

#### Threshold checks (in order)

| Check | Default | Setting key |
|-------|---------|-------------|
| `n_incidents >= min_incidents` | 3 | `storm.min_incidents` |
| `n_resources >= min_resources` | 2 | `storm.min_resources` |
| Event types are correlated (see §6) | — | — |

All three must pass. If they do, Phase 1 returns a `StormCandidate` and the caller fires the `execute_storm_analysis_task` Celery task.

#### Exclusions

An incident is skipped from Phase 1 consideration if any of the following are true:

- `lifecycle_state` is `resolved`, `closed`, or `storm_hold`
- `storm_id IS NOT NULL` (already belongs to a storm)
- `context.is_storm_parent = true` (is itself a storm parent)

#### Settings are read live

`_load_storm_settings(db)` queries `platform_settings` on every `detect()` call. Environment variables (`STORM_WINDOW_SECONDS`, `STORM_MIN_INCIDENTS`, `STORM_MIN_RESOURCES`) are used as fallback when DB rows are absent — no service restart is required to change thresholds.

---

### 2.2 Phase 2 — Dependency Expansion

**Source:** `backend/src/agentic_os/tasks/celery_app.py` — `_phase2_dependency_expansion`

Phase 2 runs in two passes:

1. **Immediately** after the storm parent is created (as part of `execute_storm_analysis_task`).
2. **45 seconds later** via a second Celery task (`execute_storm_expansion_task`).

The delayed pass catches external connector events (e.g., Splunk alerts) that arrive after the initial storm creation.

#### What Phase 2 does

For each resource in the storm, Phase 2 queries the **Neo4j CMDB** for services that have a `DEPENDS_ON` relationship pointing to that resource:

```cypher
MATCH (svc)-[:DEPENDS_ON]->(resource {name: $resource_name})
RETURN svc.name AS service_name
```

Any **open** incident on those downstream services, created within `storm.merge_window_minutes` (default 5 min, delayed pass uses 15 min), is adopted into the storm:

- `storm_id` set to parent's `workflow_id`
- `lifecycle_state` set to `storm_hold`
- Pending individual approvals cancelled

This catches **application-tier cascades** — e.g., Splunk-reported `api-gateway`, `payment-service`, and `auth-service` alerts that have different event types from the data-tier incidents that triggered Phase 1, and would otherwise not match the Phase 1 correlation check.

---

### 2.3 Storm Agent

**Source:** `backend/src/agentic_os/agents/storm_agent.py`

The Storm Agent is called by `execute_storm_analysis_task`. It does not write to the database — that is the Celery task's responsibility.

#### Analysis steps

1. **Topology build** — for each affected resource, queries Neo4j (`cmdb.get_dependencies(resource, depth=2)`) to retrieve upstream dependency chains.
2. **Common upstream identification** — finds CIs that appear in the dependency chain of 2 or more affected resources. These become `root_cause_candidates` (capped at 5, sorted by `affected_count` descending).
3. **Pattern classification** — classifies the storm pattern from observed event types (see §6 for groups and labels).
4. **LLM hypothesis** — calls the configured LLM provider via `summary_service`. Falls back to deterministic rule-based text if the LLM is not configured or disabled.
5. **Confidence scoring** — produces a float 0.0–1.0 (see §7).

#### Output schema

```python
{
    "root_cause_candidates": [
        {
            "name": str,
            "type": str,
            "affected_count": int,
            "affected_resources": List[str],
            "criticality": str,
        }
    ],
    "topology_evidence": {
        "resource_name": [ {"name": ..., "type": ..., ...} ]
    },
    "llm_hypothesis": str,          # human-readable root cause hypothesis
    "affected_resources": List[str],
    "event_type_pattern": str,       # see §6
    "confidence": float,             # 0.0–1.0
    "incident_count": int,
    "neo4j_available": bool,
    "llm_used": bool,
}
```

This dict is stored as `context.storm_analysis` on the storm parent incident.

#### Lazy Neo4j initialisation

The agent initialises the `CMDBService` on first use. Connection parameters come from environment variables:

| Variable | Default |
|----------|---------|
| `NEO4J_URI` | `bolt://neo4j:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `password` |

---

### 2.4 Storm Parent Incident

The storm parent is created as a `WorkflowState` record with the following characteristics:

| Field | Value |
|-------|-------|
| `workflow_type` | `incident` |
| `is_storm_parent` (in `context`) | `true` |
| `lifecycle_state` | `awaiting_manual` |
| `storm_id` | its own `workflow_id` (self-referential) |
| `approval_type` on approval record | `"cab"` |

**Context keys populated on creation:**

```json
{
    "is_storm_parent": true,
    "storm_analysis": { ... },
    "storm_children": ["uuid1", "uuid2", ...],
    "storm_detected_at": "2026-05-24T10:00:00Z"
}
```

A single CAB-level approval record is created for the parent. Individual child approvals are cancelled.

---

### 2.5 Storm Children

When an incident is adopted into a storm (whether at creation time via Phase 1, expanded via Phase 2, or merged via the merge path):

- `lifecycle_state` → `storm_hold`
- `storm_id` → parent's `workflow_id`
- Any pending individual remediation approvals are cancelled (see §4)
- Individual pipeline processing is suppressed (see §3)

---

## 3. Race Condition Handling

Storm detection and the normal 7-agent pipeline run concurrently. Three guards prevent double-processing:

### Pre-pipeline guard (execute_workflow_task)

At the very start of `execute_workflow_task`, **before** `engine.execute()` is called, the task checks whether `storm_id` is set on the incident. If it is, the task exits immediately without running any pipeline steps.

```python
# Pseudocode — actual location: celery_app.py
incident = repo.get(workflow_id)
if incident.storm_id is not None:
    logger.info("Incident already in storm — skipping pipeline")
    return
```

### End-of-pipeline storm guard

After `engine.execute()` returns (i.e., the pipeline completed), the task checks `storm_id` again. If the incident was adopted into a storm **mid-pipeline**, `lifecycle_state` is overridden to `storm_hold`. This prevents the pipeline's own lifecycle transition from overwriting the storm-managed state.

### Storm guard in repo.save()

The repository layer checks `storm_id` before persisting any `lifecycle_state` change from within the pipeline. If the incident is in a storm at save time, the lifecycle update is blocked, preserving `storm_hold`.

### Pre-storm remediation documentation

If the pipeline ran tool steps (enrichment, remediation commands) **before** the incident was adopted into a storm, the system writes a warning note to the storm parent's record:

```
WARNING: Incident <workflow_id> had already executed pipeline steps before storm adoption.
Review auto-remediation actions before proceeding with storm-level remediation.
```

---

## 4. Approval Cancellation

Whenever an incident is adopted into a storm — via any of the three paths (Phase 1 new storm, Phase 2 expansion, merge path) — any **pending** individual remediation approvals for that incident are cancelled:

```sql
UPDATE approvals
SET status         = 'cancelled',
    decided_at     = NOW(),
    decided_by     = 'system',
    decision_notes = 'Incident adopted into storm <storm_id>. '
                     'Individual approval superseded by storm CAB approval.'
WHERE workflow_id = :incident_id
  AND status = 'pending'
```

The storm parent receives a single CAB-level approval record (`requires_cab = true`).

---

## 5. Merge Path

A **merge path** handles the case where a second storm analysis task races with an already-running first task. When the second task completes its Phase 1 detection and attempts to create a storm parent, it first checks whether a storm parent already exists for the same time window. If one is found, the new incidents are merged into the existing storm rather than creating a duplicate parent.

The merge path also cancels pending approvals and sets `storm_hold` on the newly merged children.

---

## 6. Event Type Correlation Groups

**Source:** `CORRELATED_GROUPS` in `storm_detection.py`; `PATTERN_MAP` in `storm_agent.py`

### Phase 1 correlation groups

Phase 1 uses `CORRELATED_GROUPS` to determine whether observed event types suggest a shared root cause. A storm is considered correlated if **2 or more types from the same group** appear across resources (or if a single type blasts across enough resources to meet the `min_resources` threshold).

```python
CORRELATED_GROUPS = [
    # Group 1 — Network / connectivity failures
    # Includes app-tier cascades: high_error_rate from services that cannot
    # reach a failed data-tier resource; connection_spike when connection
    # pools exhaust due to upstream failure.
    {
        "service_unresponsive", "health_check_failed", "high_latency",
        "connection_spike", "network_anomaly", "service_down",
        "high_error_rate"
    },

    # Group 2 — Resource exhaustion
    {
        "high_cpu", "high_memory", "disk_full", "high_syscall_intensity"
    },

    # Group 3 — Generic service cascade
    {
        "service_down", "service_unresponsive", "health_check_failed"
    },
]
```

### Storm Agent pattern classification

The Storm Agent classifies the storm into a named pattern, stored in `event_type_pattern`:

| Pattern label | Triggering event types |
|---------------|----------------------|
| `network_partition` | Any of: `service_unresponsive`, `health_check_failed`, `network_anomaly`, `high_latency`, `connection_spike`, `service_down` |
| `resource_exhaustion` | Any of: `high_cpu`, `high_memory`, `disk_full`, `high_syscall_intensity` |
| `service_cascade` | Any of: `service_down`, `service_unresponsive` |
| `distributed_<type>` | A single event type not matching any group above |
| `mixed_signal_storm` | Multiple types not matching any group above |

Pattern matching stops at the first group that has at least one match, so `network_partition` takes priority over `service_cascade`.

---

## 7. Confidence Scoring

The Storm Agent produces a confidence score in the range **0.0–1.0** using the following additive formula:

| Component | Points |
|-----------|--------|
| Baseline (any storm detection) | +0.50 |
| Topology evidence: `(top_candidate.affected_count / total_affected_resources) × 0.30` | up to +0.30 |
| Single event type across all resources (high coherence) | +0.15 |
| 2–3 distinct event types (moderate coherence) | +0.05 |
| 5 or more affected resources (scale) | +0.05 |
| **Maximum** | **1.00** (capped) |

**Examples:**

- 3 incidents, 3 resources, 1 event type, common upstream CI shared by all 3 → 0.50 + 0.30 + 0.15 = **0.95**
- 3 incidents, 2 resources, 2 event types, no Neo4j topology → 0.50 + 0.05 = **0.55**
- 6 incidents, 6 resources, 1 event type, CI shared by 4 of 6 → 0.50 + (4/6 × 0.30) + 0.15 + 0.05 = **0.90**

---

## 8. Lifecycle States

| State | Who is in it | Description |
|-------|-------------|-------------|
| `storm_hold` | Child incidents | Waiting for the storm parent's CAB decision. Individual pipeline is suppressed. |
| `awaiting_manual` | Storm parent | Storm has been created and analysed; human coordination is required before any remediation is attempted. Individual pipeline automation is suppressed. |
| `storm_pending` | (reserved) | Pre-pipeline buffer state — not yet implemented. Reserved for a future pre-creation hold mechanism. |

---

## 9. Platform Settings

All settings live in the `platform_settings` table under `category = 'storm'`. They are read **live on every call** — no service restart is required.

**Seed / reset endpoint:** `POST /api/settings/storm/reset`

| Key | Type | Default (DB seed) | Description |
|-----|------|-------------------|-------------|
| `storm.enabled` | bool | `true` | Enable/disable storm detection entirely. When disabled, all incidents go through the individual 7-agent pipeline. |
| `storm.window_seconds` | int | `120` | Look-back window in seconds for Phase 1 detection. Increase for slower-developing storms. |
| `storm.min_incidents` | int | `3` | Minimum incidents in the detection window to trigger a storm. Lower = more sensitive; higher = fewer false positives. |
| `storm.min_resources` | int | `2` | Minimum distinct resources affected. Ensures storms are multi-resource events, not a single noisy service. |
| `storm.merge_window_minutes` | int | `5` | Time window (minutes) during which concurrent storm detections are merged into a single parent. |
| `storm.require_cab_approval` | bool | `true` | Storm parents always require CAB-level approval before coordinated remediation proceeds. |
| `storm.auto_hold_children` | bool | `true` | Auto-transition child incidents to `storm_hold`, suppressing individual pipeline processing. |
| `storm.llm_hypothesis_enabled` | bool | `true` | Use the configured LLM provider for root cause hypothesis. Falls back to rule-based text if disabled or unavailable. |
| `storm.neo4j_topology_enabled` | bool | `true` | Query Neo4j CMDB for upstream dependency analysis. Disable if Neo4j is not deployed. |
| `storm.pipeline_hold_seconds` | int | `0` | Delay the incident pipeline N seconds after creation so storm detection can win. 0 = no delay. Recommended 30–120 for environments with frequent correlated storms. |

### Environment variable fallbacks

The following environment variables are used as fallback defaults **only** when the corresponding DB row does not exist (e.g., before the seed has run):

| Variable | Corresponding setting |
|----------|----------------------|
| `STORM_WINDOW_SECONDS` | `storm.window_seconds` |
| `STORM_MIN_INCIDENTS` | `storm.min_incidents` |
| `STORM_MIN_RESOURCES` | `storm.min_resources` |
| `STORM_PIPELINE_HOLD_SECONDS` | `storm.pipeline_hold_seconds` |

Note: The env-var fallback for `window_seconds` defaults to **300 s** (5 minutes) when the env var is not set, whereas the DB seed value is **120 s**. Once the DB is seeded, the DB value takes precedence.

---

## 10. API Endpoints

**Source:** `backend/src/agentic_os/api/routes/storms.py` and `platform_settings.py`

### Storm management

#### `GET /api/storms`

List storm parent incidents.

Query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `active_only` | bool | `true` | If true, excludes resolved/closed storms. |
| `limit` | int | `50` | Maximum storms to return. |

Response: array of `StormSummary` objects.

```json
[
  {
    "storm_id": "uuid",
    "incident_number": "INC0001234",
    "title": "Storm: service_unresponsive across db-primary, cache-cluster, msg-broker",
    "lifecycle_state": "awaiting_manual",
    "severity": "critical",
    "pattern": "network_partition",
    "confidence": 0.95,
    "hypothesis": "A failure in core-switch-01 ...",
    "affected_count": 6,
    "child_count": 5,
    "detected_at": "2026-05-24T10:00:00Z",
    "created_at": "2026-05-24T10:00:02Z"
  }
]
```

---

#### `GET /api/storms/{storm_id}`

Full storm detail including analysis results and child incidents.

Returns a `StormDetail` object (extends `StormSummary`) with:

- `children` — list of `StormChild` objects, each with `workflow_id`, `title`, `lifecycle_state`, `severity`, `resource_name`, `event_type`, `created_at`, `incident_number_str`, `source_connector`, `signal_value`, `signal_threshold`
- `root_cause_candidates` — from storm agent analysis
- `topology_evidence` — per-resource dependency chains from Neo4j
- `affected_resources` — list of resource names
- `event_types` — distinct event types in the storm
- `llm_used` — whether the LLM was used for hypothesis generation
- `neo4j_available` — whether Neo4j was reachable during analysis

Error responses: `400` for invalid UUID format, `404` if storm not found.

---

#### `POST /api/storms/{storm_id}/release`

Dismiss the storm — release all children to proceed through their individual pipelines independently.

Use this when the operator determines the incidents are **not** actually correlated (false positive), or when each incident should be handled on its own merits.

Request body (optional):

```json
{ "notes": "False positive — unrelated maintenance events" }
```

Actions performed:
1. Children in `storm_hold` → `open`, `storm_id` cleared
2. Storm parent → `resolved`
3. Storm CAB approval → `cancelled`
4. System note written to storm parent
5. Children re-queued via `execute_workflow_task`

Response:

```json
{
  "status": "released",
  "storm_id": "uuid",
  "children_released": 5,
  "message": "Storm dismissed — incidents released to individual pipelines"
}
```

---

#### `POST /api/storms/{storm_id}/resolve`

Manually resolve all child incidents and close the storm parent.

Use this after the root cause has been addressed (e.g., the network team fixed the partition) and all affected services have recovered.

Request body (optional):

```json
{ "resolution_note": "Network partition on core-switch-01 resolved by NOC at 10:42Z" }
```

Actions performed:
1. Children not already resolved → `resolved`, `resolution_source = manual`
2. Storm parent → `resolved`
3. Storm CAB approval → `cancelled`
4. System note written to storm parent

Response:

```json
{
  "status": "resolved",
  "storm_id": "uuid",
  "children_resolved": 5,
  "message": "Network partition on core-switch-01 resolved by NOC at 10:42Z"
}
```

---

### Platform settings — storm category

#### `GET /api/settings/storm`

Returns all storm settings with metadata (key, value, value_type, label, description, updated_at). Auto-seeds defaults if no rows exist.

#### `PUT /api/settings/storm`

Update one or more storm settings. Only provided fields are updated.

```json
{
  "window_seconds": 300,
  "min_incidents": 4,
  "pipeline_hold_seconds": 60
}
```

Response:

```json
{
  "saved": { "window_seconds": 300, "min_incidents": 4, "pipeline_hold_seconds": 60 },
  "message": "Storm Agent settings saved successfully."
}
```

#### `POST /api/settings/storm/reset`

Restore all storm settings to factory defaults (DB seed values from `STORM_DEFAULTS`).

---

## 11. Typical Storm Scenario

The following walkthrough illustrates a 6-incident storm caused by a data-tier failure cascading into application-tier services.

### Incident arrivals

| # | Source | Resource | Event Type | Time |
|---|--------|----------|------------|------|
| 1 | watcher_brain | `db-primary` | `service_unresponsive` | T+0s |
| 2 | watcher_brain | `cache-cluster` | `health_check_failed` | T+2s |
| 3 | watcher_brain | `msg-broker` | `service_unresponsive` | T+4s |
| 4 | splunk | `api-gateway` | `high_error_rate` | T+15s |
| 5 | splunk | `payment-service` | `high_error_rate` | T+18s |
| 6 | splunk | `auth-service` | `connection_spike` | T+22s |

### Phase 1 — Storm detection fires on incident #3

After incident #3 is created, the background task queries for recent incidents:

- 3 incidents in the 120-second window: `db-primary`, `cache-cluster`, `msg-broker`
- 3 incidents >= `min_incidents` (3) ✓
- 3 distinct resources >= `min_resources` (2) ✓
- Event types `{service_unresponsive, health_check_failed}` match Group 1 ✓

`execute_storm_analysis_task` fires. Storm parent created. Incidents #1–3 enter `storm_hold`.

### Storm Agent analysis

- Neo4j query: `db-primary`, `cache-cluster`, and `msg-broker` all share an upstream dependency on `core-switch-01` (or similar shared network CI)
- Pattern: `network_partition`
- Root cause candidate: `core-switch-01` (`affected_count: 3`)
- Confidence: 0.50 + 0.30 + 0.15 = **0.95**
- LLM hypothesis generated (or rule-based fallback)

### Phase 2 — Immediate expansion (T+5s)

CMDB is queried for services that `DEPEND_ON` `db-primary`, `cache-cluster`, `msg-broker`. `api-gateway`, `payment-service`, and `auth-service` are returned.

At this point, incidents #4–6 may not exist yet (Splunk events arrive at T+15–22s).

### Phase 2 — Delayed expansion (T+50s)

The 45-second delayed sweep runs. Incidents #4–6 now exist. They are within the `merge_window_minutes` window (5 minutes) and their resources are in the CMDB downstream set.

Incidents #4–6 are adopted:
- `storm_id` → storm parent's `workflow_id`
- `lifecycle_state` → `storm_hold`
- Individual pipeline tasks exit (pre-pipeline guard fires)
- Any pending individual approvals → `cancelled`

### Final state

| Incident | State | storm_id |
|----------|-------|----------|
| Storm parent | `awaiting_manual` | self |
| `db-primary` | `storm_hold` | → parent |
| `cache-cluster` | `storm_hold` | → parent |
| `msg-broker` | `storm_hold` | → parent |
| `api-gateway` | `storm_hold` | → parent |
| `payment-service` | `storm_hold` | → parent |
| `auth-service` | `storm_hold` | → parent |

CAB is presented with a single approval request covering all 6 incidents, with a confidence 0.95 root cause hypothesis pointing to the shared network dependency.

---

## 12. Known Limitations & Operational Notes

### Neo4j CMDB dependency

Phase 2 dependency expansion requires a Neo4j CMDB with `DEPENDS_ON` edges populated for the relevant services. Without this:

- Phase 2 will find no downstream services and skip adoption
- The Storm Agent will still run but produce no `root_cause_candidates`
- The storm will contain only the Phase 1 incidents
- Confidence will be lower (no topology bonus)
- The system degrades gracefully — no errors are raised, and `neo4j_available: false` is recorded in the analysis

To disable Neo4j queries explicitly (e.g., during CMDB maintenance): set `storm.neo4j_topology_enabled = false`.

### LLM hypothesis requirement

LLM hypothesis generation requires a configured LLM provider in `summary_service`. When the LLM is unavailable or the setting is disabled, the Storm Agent automatically falls back to deterministic rule-based hypothesis text. The `llm_used: false` flag in the storm analysis indicates which path was taken.

### Detection window boundary

Incidents older than `storm.window_seconds` at the time Phase 1 runs will not be included in the storm. In slow-developing storms (e.g., gradual cascade over several minutes), increase `storm.window_seconds`. Note that increasing the window also increases the risk of unrelated incidents being grouped together.

### Pre-storm remediations

If auto-remediation steps were executed by the individual pipeline **before** storm detection ran (i.e., the incident passed through the pipeline before being adopted), a warning note is written to the storm parent:

```
WARNING: Incident <workflow_id> had already executed pipeline steps before storm adoption.
Review auto-remediation actions before proceeding with storm-level remediation.
```

To minimise this risk in environments with frequent correlated storms, set `storm.pipeline_hold_seconds` to 30–120 seconds. This delays the pipeline long enough for storm detection to fire and suppress processing.

### 45-second delayed expansion window

The second Phase 2 pass at T+45s is designed to catch external connector events (Splunk, Sentinel, etc.) that typically arrive 10–30 seconds after the monitoring platform first fires. If your connectors have higher latency, consider adjusting the expansion task delay or increasing `storm.merge_window_minutes`.

### Storm detection does not retry

Phase 1 runs once per new incident (as a background task). If the detection SQL query fails (e.g., DB timeout), it logs an error and returns `None` — no retry is scheduled. The next incoming incident will trigger a fresh scan.
