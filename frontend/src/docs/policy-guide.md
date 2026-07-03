# Policy Guide

Policies are the primary control plane for deciding **when incidents need human approval** and **which remediation actions are permitted**. A policy matches an incident, applies its rules, and either allows automation to run freely or gates it behind an approval step.

---

## How Policies Are Matched

Every active incident is evaluated against all enabled policies. A policy matches only when **all** of its configured rules are satisfied (AND logic). If multiple policies match, the one with the **lowest approval_priority number** wins.

| Rule | Description |
|---|---|
| `min_severity` | Incident severity must be at or above this level (low → medium → high → critical) |
| `environment` | Exact match — `dev`, `staging`, or `prod` |
| `service` | Exact match on the resource name |
| `min_risk_score` | Risk score (0–100) must meet or exceed this threshold |

> **No matching policy** → the platform defaults to requiring manual approval. This is the safe fallback.

---

## Approval Settings

### Requires Manual Approval

When enabled, a human operator must approve the proposed remediation before it runs. The incident appears in the **Approval Queue** with the full context: proposed action, blast radius, risk score, runbook steps, and matched policy.

### Approval Priority

Controls queue ordering when multiple incidents are pending approval simultaneously. Lower number = higher priority. Default is 50. Use 1–10 for critical production policies, 80–100 for low-priority dev policies.

---

## Confidence Gate

The confidence gate allows a policy that **requires manual approval** to automatically bypass that approval once a runbook has proven itself reliable in production.

### How It Works

When an incident matches a policy with a confidence gate configured, the platform looks up the matched runbook's live execution stats:

- `runbook.confidence` — a 0–1 score derived from recent outcomes, weighted toward recency
- `runbook.successful_executions` — total count of successful runs

If **both** thresholds are met, `requires_manual_approval` is overridden and remediation runs automatically.

```
approval_required = requires_manual_approval
                    AND NOT (confidence ≥ threshold AND successful_executions ≥ min_runs)
```

### Configuration

| Field | Description | Example |
|---|---|---|
| `confidence_gate_threshold` | Minimum runbook confidence (0–1) | `0.90` = 90% |
| `confidence_gate_min_runs` | Minimum successful executions before gate can trigger | `10` |

### Recommended Starting Values

- **Conservative**: threshold=0.95, min_runs=20 — high bar, only very proven runbooks bypass
- **Balanced**: threshold=0.90, min_runs=10 — good default for most production services
- **Permissive**: threshold=0.80, min_runs=5 — faster automation for lower-risk environments

> The confidence gate only applies when a runbook exists for the incident type. Ad-hoc AI-generated plans cannot satisfy the gate.

---

## Approved Actions

Controls which remediation actions the platform is permitted to execute when this policy matches. Set to `*` to allow all actions, or select specific ones.

| Action | Description |
|---|---|
| `restart_service` | Restart the affected service process |
| `force_restart` | Hard restart (SIGKILL + restart) |
| `scale_pods` | Scale Kubernetes pod count |
| `scale_up` | Increase resource allocation |
| `cleanup_logs` | Free disk space by rotating/archiving logs |
| `drain_node` | Safely evict pods from a node before maintenance |
| `pause_workload` | Suspend a deployment or job |
| `escalate` | Escalate to on-call without auto-remediation |

---

## Constraints

Optional limits that apply regardless of the matched action.

| Constraint | Description |
|---|---|
| `max_blast_radius` | Maximum number of resources that can be affected by a single remediation |
| `max_restart_frequency` | Maximum restarts allowed in a time window |
| `requires_post_monitoring` | Watch the resource after remediation to confirm the fix held |

---

## Example: Production Critical Policy

A typical policy for production services requiring human oversight but allowing proven runbooks to self-heal:

- **Matching rules**: environment=prod, min_severity=high
- **Approved actions**: restart_service, scale_pods, cleanup_logs
- **Requires manual approval**: yes
- **Confidence gate**: threshold=90%, min_runs=10
- **Constraints**: max_blast_radius=3, requires_post_monitoring=true
- **Priority**: 10

This means: for high-severity production incidents, require approval — but once a runbook reaches 90% confidence over 10+ successful runs, it auto-remediates without waiting for a human.
