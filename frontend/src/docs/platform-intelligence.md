# Platform Intelligence

Platform Intelligence is the platform's self-learning layer. It watches how incidents actually played out, looks for patterns a human would have to dig through dashboards to find, and turns those patterns into specific, reviewable configuration changes — instead of leaving you to manually tune thresholds, multipliers, and approval policies by feel.

It never changes anything on its own unless you explicitly turn that on (see [Auto-Apply](#auto-apply--earning-trust) below). By default, everything it finds goes into a review queue: you accept it, reject it, or ignore it.

---

## The core idea

Too many raw signals become too many incidents, which becomes a backlog, which drives up MTTR, which buries the incidents that actually matter under noise. Platform Intelligence exists to shrink that gap — not by being smarter in the moment an alert fires, but by learning from *every incident's outcome* and feeding that back into the scoring and automation rules that decide what becomes an incident in the first place.

The loop, every time it runs:

```
Resolved incidents (last 30 days)
        ↓
Compress into a ~2KB statistics summary
        ↓
Ask the configured LLM to recommend changes
        ↓ (if LLM unavailable, or it finds nothing)
Run 11 deterministic rule checks instead
        ↓
Filter out anything already accepted/rejected recently (cooldown)
        ↓
Recommendation queue — pending review
        ↓
You accept or reject
        ↓
Accepted change is written to the live config
        ↓
7 days later: did the metric actually improve?
        ↓
Recorded — feeds whether this parameter can eventually skip review entirely
```

---

## How it runs

| Trigger | What happens |
|---|---|
| **Manual** — "Run Analysis Now" button on the Platform Intelligence page | Full analysis cycle runs immediately against the last 30 days (configurable) |
| **Daily, automatic** — 04:00 UTC | A lighter background job checks whether any *previously applied* recommendation is old enough to verify (see below). This does **not** generate new recommendations — only on-demand analysis does that today. |

Every analysis run starts by clearing out whatever's still sitting in the **pending** queue from the last run — you always see a fresh read of current data, not an accumulating pile of stale suggestions. Anything you've already **accepted** or **rejected** is preserved permanently for history and dedup purposes.

---

## What it looks at

Each run pulls every incident resolved in the analysis window (30 days by default) and compresses it into a compact summary — deliberately small (~2KB) so the LLM call stays fast and cheap regardless of whether you have 50 incidents or 5,000. The summary includes:

- **Per-domain stats** — volume, noise rate, automation rate, average qualification score, MTTR, for each event domain (infrastructure, application, database, security, etc.)
- **Per-event-type stats** — same breakdown for your top 20 event types by volume
- **System-wide health** — overall false-positive rate, overall automation rate
- **Current config** — your live qualification threshold, domain multipliers, event-type multipliers
- **Recent decisions** — the last 8 things you accepted or rejected, so the LLM doesn't repeat a suggestion you just turned down

Beyond that ~2KB summary (sent to the LLM), the rule-based checks separately look at:

- **Governance/approval history** — every approval request, whether it was approved or rejected, and how long it sat waiting
- **Runbook step outcomes** — every individual step of every runbook execution (not just whether the whole runbook succeeded)
- **CMDB coverage** — how often incidents are missing reliable CMDB data, which forces pessimistic risk-scoring defaults

---

## The 11 things it checks

If the LLM is unavailable (no provider configured) or returns nothing, these deterministic checks run instead. They're also a good map of what Platform Intelligence is actually capable of noticing:

| Check | What it's watching for | What it recommends |
|---|---|---|
| **False positive rate** | ≥20% of incidents closed as noise/duplicate, or self-healed in under 2 minutes | Raise the qualification threshold |
| **Automation rate** | Under 30% of incidents resolved automatically | Flag for runbook coverage review |
| **MTTR** | P1/P2 incidents averaging over 4 hours to resolve | Flag for governance/runbook review |
| **Priority automation coverage** | Under 50% of P1/P2 incidents auto-remediated | Flag for high-priority runbook gaps |
| **CMDB priority coverage** | P1/P2 incidents averaging under 50% CMDB confidence | Switch pessimistic missing-data factors to neutral |
| **Event-type multipliers** | A specific event type with ≥5 incidents and ≥40% noise rate | Lower that type's multiplier |
| **Domain multipliers** | A whole domain with ≥5 incidents and ≥45% noise rate (and noise isn't dominated by one resource — see below) | Lower that domain's multiplier |
| **Resource-level noise** | One resource responsible for ≥30% of a domain's noise | Add a *resource-specific* override instead of penalizing the whole domain |
| **CMDB coverage** | Overall CMDB confidence below the configured threshold | Flag general data-quality issue |
| **Governance/policy effectiveness** | A policy approved ~100% of the time over a meaningful sample | Suggest a confidence gate so proven actions stop waiting on manual approval |
| **Runbook step health** | A specific step in a specific runbook failing/timing out ≥25% of the time (≥5 runs) | Names that exact step, with a sample error and a root-cause hint (CMDB staleness vs. tool reliability vs. runbook logic) |

### Why the resource-level and step-level checks matter

Two of these checks exist specifically to fix a blunt-instrument problem:

- **Domain multipliers** are a sledgehammer — if one flapping server is the real cause of a domain's noise, lowering the domain multiplier mutes *every other resource* in that domain too. The **resource-level check** catches this and recommends a targeted override instead, and the domain-wide check automatically steps aside when one resource accounts for ≥30% of the noise.
- **Runbook success/failure** is usually tracked per-runbook, which tells you "this 5-step runbook fails 30% of the time" but not *which step*. The **runbook step health check** names the specific step, shows a real error message, and lists which incidents were affected — turning "this runbook is unreliable" into "step 3's connection to the target times out."

---

## Recommendation categories

Every recommendation falls into one of these, shown as a badge on its card:

| Category | Touches |
|---|---|
| **Threshold** | The global qualification threshold |
| **Event Multiplier** | A specific event type's scoring multiplier |
| **Domain Multiplier** | A whole domain's scoring multiplier |
| **Resource Override** | A specific resource's scoring multiplier |
| **Missing Data Policy** | Whether a CMDB factor defaults pessimistic or neutral when data is absent |
| **Governance** | Policy approval-gate configuration (informational — see note below) |
| **Runbook Step** | A specific step of a specific runbook |
| **General** | Informational findings with no direct config change to make (MTTR, automation rate, CMDB coverage) |

**Note on Governance recommendations**: these are informational only. Setting a confidence gate on a policy means changing fields on that policy directly (in the policy editor), not a config value Platform Intelligence can write for you — so accepting one acknowledges the finding rather than applying anything.

---

## Accept, Reject, and why things don't repeat themselves

- **Accept** — for recommendations with an actual config value to change, accepting writes it immediately to the live risk-weight configuration. For informational ones, accepting just records that you reviewed it.
- **Reject** — records your decision with an optional reason.
- **Cooldown** — an accepted-and-applied recommendation won't resurface for **30 days**. A rejected one won't resurface with the *same* suggested value for **14 days** — but if new data produces a meaningfully different suggested value, it can come back sooner, since that's new information, not a repeat.
- **Pattern persistence** — if you reject something and the same underlying pattern shows up again after the 14-day window, the new recommendation's rationale is annotated: *"Previously rejected Nd ago — pattern persists."* You're not starting from a blank slate each time.

---

## Auto-Apply — earning trust

This is the one place Platform Intelligence can act without you clicking Accept — and it's **off by default**.

**Where to control it**: Settings → Platform Intelligence.

| Setting | Default | What it controls |
|---|---|---|
| **Auto-Apply** | Off | Master switch. Off = every recommendation always waits for manual review, full stop. |
| **Trust Threshold (cycles)** | 3 | How many consecutive accept → apply → verified-improved cycles a specific parameter needs before it's allowed to skip review |
| **Verification Delay (days)** | 7 | How long to wait after applying a change before checking whether it actually worked |

**How a parameter earns auto-apply**: every time you accept and apply a recommendation, 7 days later (configurable) the system re-checks the metric that recommendation targeted and records whether it genuinely improved. Once a *specific parameter* (e.g. a specific event type's multiplier) has three consecutive accept → apply → verified-improved cycles in a row, the next time that exact parameter would be recommended, it applies itself — no review needed — instead of going back into the queue.

**What happens if it regresses**: this isn't a one-way door. The trust calculation is re-checked from scratch every single time, using the most recent cycles — it isn't a sticky flag. If even one cycle comes back "didn't improve," the streak is broken and that parameter falls straight back to requiring manual review on its very next recommendation. There's no separate "revoke" step and no scenario where a bad automated change keeps reapplying itself.

**Audit trail**: an auto-applied change still creates a full recommendation record — visible in the recommendation list with a distinct **⚡ Auto-Applied** badge, and in Config History — it's just not blocking on a click.

---

## How this connects to the rest of the platform

| Area | How Platform Intelligence touches it |
|---|---|
| **Qualification scoring** | Adjusts the threshold and multipliers that decide whether a raw signal becomes an incident at all — the most direct lever on noise volume |
| **Runbooks** | Step-level health checks point at specific broken steps; MTTR and automation-rate checks flag where runbook coverage is thin |
| **MTTR** | Tracked directly (P1/P2 average) and indirectly — every other improvement (less noise, more automation, fewer approval delays) feeds into faster resolution times |
| **Approval gates** | Governance effectiveness checks identify policies that are pure latency with no safety value, pointing you toward confidence gates that let proven-reliable actions bypass approval |
| **CMDB data quality** | Coverage checks flag when missing CMDB data is forcing pessimistic, inflated risk scores |
| **Tools/connectors** | Runbook step failures are classified by root cause — a `target_not_found` pattern points at CMDB staleness, a `tool_error` pattern points at the tool or connector itself, not the runbook's logic |

---

## Why this makes the platform better over time

Without this layer, every one of these levers — the qualification threshold, twelve-plus domain multipliers, dozens of event-type overrides, every policy's approval gate, every runbook's step reliability — has to be tuned by a human noticing a problem, digging through incident history to confirm it, and manually editing config. That doesn't scale past a handful of event types or policies, and it never happens proactively — only after someone gets frustrated enough to go looking.

Platform Intelligence turns that into a standing process: every analysis run is a fresh, evidence-backed pass over real outcomes, specific enough to name an exact resource or an exact runbook step rather than a vague "this domain is noisy." Nothing is ever silently changed — you stay the decision-maker — but the *finding* part of the work, which used to require deliberately going and looking, now happens automatically and continuously. And for the patterns that prove themselves repeatedly correct, the system can eventually stop asking you to re-approve the same well-established judgment call over and over, while staying fail-safe the moment that judgment turns out to be wrong.
