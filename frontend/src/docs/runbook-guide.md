# Runbook Guide

Runbooks are the platform's remediation playbooks — structured step-by-step procedures that tell the automation engine exactly what to do when a specific incident type occurs. A well-configured runbook library is what allows the platform to move from requiring human approval on every incident to safely self-healing.

---

## How Runbooks Are Selected

When an incident is created, the platform runs a **4-pass cascade lookup** to find the most specific matching runbook:

| Pass | Match Criteria | Example |
|---|---|---|
| 1 | event_type + service + platform | `service_unresponsive` + `api-server` + `kubernetes` |
| 2 | event_type + service + any platform | `service_unresponsive` + `api-server` |
| 3 | event_type + no service + platform | `service_unresponsive` on `docker` |
| 4 | event_type + no service + any platform | `service_unresponsive` (generic) |

Within each pass, runbooks are ranked by `success_rate DESC`, then `confidence DESC`. The first match found is used.

> **No match found** → the platform's AI agent generates an ad-hoc remediation plan. This plan is not tracked for confidence stats and cannot satisfy a confidence gate.

---

## Runbook Structure

Each runbook has three ordered phases:

### 1. Diagnostics
Read-only steps that gather information before taking any action. These run first to establish a baseline and confirm the incident type.

- Check service status
- Read recent logs
- Inspect resource utilisation
- Query dependent services

### 2. Remediation Steps
The actual fix actions, executed in order. Each step specifies a tool (shell command, API call, platform action) and its parameters.

### 3. Validation Steps
Post-remediation checks that confirm the fix worked. If validation fails, the incident may re-trigger.

---

## Runbook Confidence & Stats

Every time a runbook executes, the outcome is recorded. These stats drive the **confidence gate** in policies.

| Stat | Description |
|---|---|
| `confidence` | 0–1 score derived from recent outcomes, weighted toward recency (recent runs count more) |
| `success_rate` | Raw ratio: successful_executions / total_executions |
| `successful_executions` | Cumulative count of successful runs |
| `failed_executions` | Cumulative count of failed runs |
| `total_executions` | successful + failed |
| `confidence_trend` | `improving`, `stable`, or `declining` based on recent outcome direction |

### Confidence vs. Success Rate

`success_rate` is a raw lifetime average. `confidence` is recency-weighted — if a runbook had early failures but recent runs are all successful, confidence will be higher than success_rate. This means a runbook that has been improved and re-tested can "earn back" confidence faster.

---

## Platform (OS) Targeting

Runbooks can be scoped to a specific execution platform. The platform is derived from CMDB data for the affected resource.

| Platform | Applies To |
|---|---|
| `docker` | Docker containers |
| `kubernetes` | Kubernetes pods and deployments |
| `linux` | Bare metal or VM Linux hosts |
| `windows` | Windows servers |
| `any` | Matches any platform — used for generic runbooks |

A platform-specific runbook (e.g. `kubernetes`) always takes priority over a generic `any` runbook for the same event type.

---

## Service Targeting

Set the `service` field to scope a runbook to a specific resource name (e.g. `api-server`, `payments-worker`). Leave it empty for a runbook that applies to all resources of a given event type.

**Specificity wins**: a runbook matching both service and platform beats a generic one. Use service-specific runbooks for services with non-standard restart procedures or unusual dependencies.

---

## Building a Reliable Runbook

To maximise confidence and eventually satisfy a confidence gate:

1. **Start with diagnostics** — understand the incident before acting. Diagnostics that confirm root cause prevent unnecessary remediation.
2. **Keep steps idempotent** — running a step twice should be safe. Avoid destructive one-way operations where possible.
3. **Add validation** — always end with a health check. A runbook that restarts a service but doesn't confirm it came back healthy is incomplete.
4. **Scope narrowly first** — create a service-specific runbook before a generic one. Specificity gives you better signal on whether the runbook actually fits the incident pattern.
5. **Let it run** — confidence is earned through executions. A new runbook starts with no stats. After 10+ successful runs, it becomes eligible to satisfy a confidence gate.

---

## Runbook vs. Ad-Hoc AI Plan

| | Runbook | AI-generated plan |
|---|---|---|
| Source | Ops-authored, version-controlled | Generated at incident time by LLM |
| Consistency | Same steps every time | Varies per incident |
| Confidence tracking | Yes — feeds the confidence gate | No |
| Confidence gate eligible | Yes | No |
| Appropriate for | Known, recurring incident patterns | Novel or one-off incidents |

The platform always prefers a runbook over an AI plan if a match exists.
