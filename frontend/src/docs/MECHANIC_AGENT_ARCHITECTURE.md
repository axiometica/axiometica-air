# Mechanic Agent - Multi-Layer Remediation Logic

## Overview

The Mechanic Agent (MechanicAgent) selects the best remediation runbook through a **5-tier confidence waterfall**. It always uses the highest-confidence result available and attaches the selected runbook, tier, and confidence score to the `IncidentWorkflowContext`.

```
┌─────────────────────────────────────────────────────────┐
│              MechanicAgent (Runbook Selection)           │
└─────────────────────────────────────────────────────────┘
         │
         ├─→ TIER 1: Runbook library — exact match
         │    anomaly_type + service + environment → 90-100% confidence
         │
         ├─→ TIER 2: Playbook library — broader match
         │    anomaly_type + service (no env constraint) → 70-85%
         │
         ├─→ TIER 3: Historical outcomes
         │    Runbook that resolved similar past incidents → 60-80%
         │
         ├─→ TIER 4: LLM synthesis
         │    Generated runbook from incident context (OpenAI / Anthropic) → 50-75%
         │
         └─→ TIER 5: Fallback runbook
              Generic safe-mode diagnostics-only runbook → 30%
```

---

## Tier 1: Runbooks Database (Highest Priority)

**Source**: `_lookup_runbook(alert_type, resource_name)`

**Decision Logic**:
- Ops-authored playbooks, manually created by incident response team
- **Highest confidence** (explicit human knowledge)
- **Highest priority** - takes precedence over all other layers
- Includes diagnostic steps, action steps, verification steps

**Example Runbook**:
```python
runbook = {
    "id": "rb-high-cpu-webapp",
    "name": "High CPU - Web Application",
    "alert_type": "high_cpu",
    "resource_type": "web-server",
    "confidence": 0.95,  # 95% confidence
    "diagnostics": [
        {"tool": "trace_syscalls_ebpf", "parameters": {"duration": 10}},
        {"tool": "process_detail", "parameters": {"process": "<PID>"}},
    ],
    "actions": [
        {"tool": "process_kill", "parameters": {"process_name": "<ANOMALY_PROCESS>"}},
    ],
    "verification_steps": [
        {"tool": "cpu_usage_per_core", "parameters": {"samples": 3}},
    ],
    "blast_radius": "low",
}
```

**Activation Trigger**:
```python
runbook = _lookup_runbook(alert_type="high_cpu", resource_name="api-server")
if runbook:
    proposal = self._generate_runbook_proposal(runbook, cmdb, alert)
    # ✅ Use runbook, skip Tier 2 & 3
    return state
```

**Output**:
```python
{
    "action": "process_kill",
    "target": "api-server",
    "rationale": "Runbook: High CPU - Web Application",
    "runbook_id": "rb-high-cpu-webapp",
    "runbook_name": "High CPU - Web Application",
    "runbook_steps": {
        "diagnostics": [...],
        "actions": [...],
        "verification": [...]
    }
}
```

**Key Feature**: Parameter Substitution
```python
# If alert contains anomaly_process from watcher:
detected_process = alert.get("anomaly_process")  # e.g., "yes" process
if detected_process and main_action == "process_kill":
    main_args["process_name"] = detected_process  # Override hardcoded value
    # Result: Kill the actual detected process, not generic placeholder
```

---

## Tier 2: CMDB Playbooks (Historical Success Rate)

**Source**: `self.cmdb.get_playbooks(resource_type, alert_type)`

**Decision Logic**:
- Playbooks from CMDB with historical success rates
- **Selection based on success rate** - pick the best-performing playbook
- Learns from past similar incidents
- Falls back if no exact playbook exists

**Example Playbooks**:
```python
playbooks = [
    {
        "id": "pb-scale-001",
        "name": "Scale Up Web Tier",
        "resource_type": "web-server",
        "alert_type": "high_cpu",
        "success_rate": 0.92,  # 92% success
        "estimated_time_min": 2,
        "steps": ["wait 30s", "add 3 replicas", "monitor metrics"],
    },
    {
        "id": "pb-optimize-001",
        "name": "Optimize Cache Settings",
        "resource_type": "web-server",
        "alert_type": "high_cpu",
        "success_rate": 0.78,  # 78% success
        "estimated_time_min": 5,
        "steps": ["adjust cache_ttl", "restart service", "verify"],
    },
]

# Select best by success rate
selected_playbook = max(playbooks, key=lambda p: p.get("success_rate", 0))
# Result: "Scale Up Web Tier" (92% > 78%)
```

**Activation Trigger**:
```python
playbooks = self.cmdb.get_playbooks(
    resource_type="web-server",
    alert_type="high_cpu"
)

if playbooks:
    selected_playbook = max(playbooks, key=lambda p: p.get("success_rate", 0))
    proposal = self._generate_proposal(alert_type, cmdb, selected_playbook)
    # ✅ Use best-performing playbook
    return state
```

**Output**:
```python
{
    "action": "scale_up",
    "playbook_name": "Scale Up Web Tier",
    "target": "api-server",
    "steps": ["wait 30s", "add 3 replicas", "monitor metrics"],
    "estimated_time_min": 2,
    "rationale": "Using playbook: Scale Up Web Tier",
}
```

**Learning Mechanism**: 
```
After remediation completes:
- Track outcome (success/failure)
- Update playbook.success_rate in CMDB
- Next time this scenario occurs, Tier 2 selects updated playbook
```

---

## Tier 3: Default Hardcoded Rules (Fallback)

**Source**: `_generate_proposal()` - hardcoded dictionary

**Decision Logic**:
- Static mapping: alert_type → recommended action
- When no runbook or playbook found
- Lowest confidence, but reliable baseline
- Ensures some action is always proposed

**Hardcoded Mapping**:
```python
proposals = {
    "high_cpu": {
        "action": "scale_up",
        "target": resource_name,
        "replicas": 3,
        "rationale": "High CPU usage detected, scaling up to handle load",
    },
    "disk_full": {
        "action": "cleanup_logs",
        "target": resource_name,
        "days_to_retain": 7,
        "rationale": "Disk space critical, archiving old logs",
    },
    "service_down": {
        "action": "restart_service",
        "target": resource_name,
        "restart_mode": "graceful",
        "rationale": "Service unresponsive, attempting graceful restart",
    },
    # ... more alert types ...
}

# Default if alert_type not found
default = {
    "action": "escalate",
    "target": resource_name,
    "rationale": f"Unknown alert type {alert_type}, requires manual review",
}
```

**Activation Trigger**:
```python
if not runbook and not playbooks:
    proposal = self._generate_proposal(alert_type, cmdb, playbook=None)
    # ✅ Use hardcoded default
    return state
```

---

## Risk Assessor - Historical Context Integration

The **Risk Assessor Agent** feeds historical incident data into risk scoring:

```python
# Risk Assessor reads historical incidents
historical_incidents = cmdb.get_historical_incidents(resource_name, limit=5)

# Historical factor influences risk score
history_factor = min(10.0, len(historical_incidents) * 2.0)

# Risk breakdown includes historical weight
risk_breakdown = {
    ...
    "history": {
        "value": history_factor,
        "weight": 10,
        "incidents": len(historical_incidents)
    }
}

# Higher risk if this resource has frequent incidents
total_risk_score = weighted_sum(all_factors)
```

**Impact**:
- More historical incidents → higher risk score
- Higher risk → more likely to require manual approval
- Helps identify problematic services needing investment

---

## Multi-Layer Decision Flow Example

### Scenario 1: Ops-Authored Runbook Exists

```
Incident: high_cpu on api-server

Step 1: Check Tier 1 (Runbooks)
  └─→ Found: "High CPU - API Server" (confidence: 95%)
      ├─ Diagnostics: trace_syscalls_ebpf, process_detail
      ├─ Action: process_kill (with anomaly process substitution)
      └─ Verification: cpu_usage_per_core

Decision: ✅ Use Runbook (STOP - don't check Tier 2 or 3)

Output:
{
  "action": "process_kill",
  "runbook_id": "rb-high-cpu-api",
  "runbook_steps": { diagnostics, actions, verification },
  "rationale": "Runbook: High CPU - API Server"
}
```

### Scenario 2: No Runbook, CMDB Playbooks Available

```
Incident: high_memory on cache-server

Step 1: Check Tier 1 (Runbooks)
  └─→ Not found

Step 2: Check Tier 2 (CMDB Playbooks)
  └─→ Found 2 playbooks:
      - "Increase Cache TTL": success_rate=0.88
      - "Clear Cache": success_rate=0.82
      
      Selected: "Increase Cache TTL" (88% > 82%)

Decision: ✅ Use Playbook (STOP - don't check Tier 3)

Output:
{
  "action": "scale_up",
  "playbook_name": "Increase Cache TTL",
  "estimated_time_min": 3,
  "rationale": "Using playbook: Increase Cache TTL"
}
```

### Scenario 3: No Runbook, No Playbook → Fallback

```
Incident: database_replication_lag on postgres-primary

Step 1: Check Tier 1 (Runbooks)
  └─→ Not found

Step 2: Check Tier 2 (CMDB Playbooks)
  └─→ Not found (no playbook for database_replication_lag)

Step 3: Check Tier 3 (Hardcoded Rules)
  └─→ alert_type "database_replication_lag" not in hardcoded proposals
      └─→ Fall back to default: "escalate"

Decision: ✅ Use Default (Escalate)

Output:
{
  "action": "escalate",
  "target": "postgres-primary",
  "rationale": "Unknown alert type database_replication_lag, requires manual review"
}
```

---

## Data Sources & Enrichment

### CMDB Context (Librarian Agent)
The Mechanic receives enriched context from Librarian:

```python
cmdb_context = {
    "resource_name": "api-server",
    "resource_info": {
        "type": "web-server",
        "tier": 2,
        "criticality": "high",
    },
    "dependencies": [...],  # What this service depends on
    "dependents": [...],     # What depends on this service
    "historical_incidents": [  # ← Tier 2 uses this
        {"type": "high_cpu", "resolved_by": "scale_up", "duration": "5m"},
        {"type": "high_cpu", "resolved_by": "process_kill", "duration": "2m"},
        {"type": "disk_full", "resolved_by": "cleanup_logs", "duration": "3m"},
    ]
}
```

---

## Confidence & Priority Matrix

```
┌──────────────────────────────────────┐
│ Tier │ Source         │ Confidence   │
├──────────────────────────────────────┤
│  1   │ Runbooks DB    │ 85-95%       │
│      │ (Ops-authored) │ (High)       │
├──────────────────────────────────────┤
│  2   │ CMDB Playbooks │ 70-85%       │
│      │ (Success rate) │ (Medium-High)│
├──────────────────────────────────────┤
│  3   │ Hardcoded      │ 50-70%       │
│      │ (Fallback)     │ (Medium)     │
└──────────────────────────────────────┘

Higher confidence = Earlier tier checked first
```

---

## Summary

The Mechanic Agent is **NOT just simple hardcoded rules**. It's a **3-layer decision system**:

1. **Ops Knowledge**: Runbooks (explicit, human-authored expertise)
2. **Learned Behavior**: CMDB Playbooks (historical success rates)
3. **Safe Fallback**: Default Rules (when all else fails)

This allows:
- ✅ Leveraging expert knowledge (runbooks)
- ✅ Learning from historical patterns (playbook success rates)
- ✅ Always having a decision (hardcoded defaults)
- ✅ Improving over time (playbook success tracking)
- ✅ Adapting to detected anomalies (parameter substitution from watcher)

The Mechanic is **more sophisticated than simple if/elif logic**—it's a **knowledge-driven remediation engine** that improves with operational experience.

