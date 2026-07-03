# Axiometica AIR (Autonomous Incident Response)
## AI-Powered Autonomous IT Operations

**Platform Capabilities — Complete Feature Reference**
*June 2026 | 1.2.0 | Confidential*

---

## Platform Overview

Axiometica AIR is a fully autonomous IT operations platform that detects anomalies, qualifies signals, reasons about risk, generates and learns from remediation runbooks, enforces governance policy, executes approved actions, and verifies resolution — end to end, without requiring a human to initiate or orchestrate any step.

What distinguishes the platform is not any single capability but how each layer feeds the next: the monitoring layer provides real context to the qualification engine; the qualification engine ensures only meaningful events become incidents; the agent pipeline converts incidents into resolved outcomes; and each outcome feeds back into the platform to improve how the next incident is handled. Every cycle, the system gets more accurate.

| Metric | Value |
|---|---|
| Anomaly types detected | 13 |
| Agents in the pipeline | 8 |
| Risk scoring factors | 9 |
| Tiers of runbook intelligence | 3 |
| Deployment platforms supported | 5 (Docker, Kubernetes, Cloud VM, VMware, Linux/Windows bare-metal) |
| Certified monitoring tool connectors | 7 |
| ServiceNow CI classes synced | 5 |
| Storm detection burst threshold | ≥3 incidents / ≥2 resources |

### Eleven Defining Capabilities

1. **Multi-Vector Observability** — 13 anomaly types across kernel, container, application, and external layers
2. **Real-Time Monitoring Dashboard** — live CPU/memory/disk graphs, accurate disk reporting, and hot-reload threshold controls
3. **Intelligent Signal Qualification** — 3-layer false-positive suppression before events reach the incident queue
4. **8-Agent Incident Resolution Pipeline** — from detection to verified resolution with full audit trail
5. **Correlated Storm Detection** — burst grouping, child containment, and AI-generated root-cause hypothesis for multi-resource events
6. **Self-Improving Runbook Intelligence** — AI generates, validates, and learns from every remediation; platform-aware command variants per deployment target; searchable library with live confidence and trend indicators
7. **Embedded Governance & Policy Engine** — fail-closed, approval-gated, with diagnostics-only mode
8. **Operational CMDB** — live force-graph of configuration, health, and relationships driving every AI decision
9. **Bidirectional ServiceNow Integration** — CMDB pull, incident push, and live state synchronisation
10. **Multi-Platform Deployment** — watcher runs on Docker, cloud VMs (GCP, AWS, Azure), VMware, bare Linux, and Windows/macOS; multi-host via registered watcher fleet
11. **External Monitoring Tool Integration** — Connector Hub ingests alerts from Datadog, Dynatrace, Splunk, Prometheus, PagerDuty, Zabbix, and ServiceNow into the full AI pipeline

---

## 1 | Multi-Vector Observability

Most monitoring platforms see what the application reports about itself. Axiometica AIR sees the system from three independent vantage points simultaneously — the kernel, the container runtime, and the network edge — so failures that are invisible from inside the application are detected from outside it.

### Kernel-Level Telemetry with eBPF

The Sentinel component runs as a dedicated container with host-level kernel access, using eBPF tracepoints (via bpftrace on `raw_syscalls:sys_enter`) to observe every system call across every process on the host. A 5-second sampling window identifies the top offending process by syscall rate. A curated exclusion list of known-safe platform processes (databases, message brokers, runtimes, orchestrators) is applied so that anomaly attribution is meaningful from the first detection.

- One privileged eBPF container provides host-wide syscall visibility — no per-application instrumentation required
- Cross-container process attribution: after identifying the anomalous process by name, the platform pins it to its source container via `pgrep` across all running containers
- Detects syscall intensity anomalies that are completely invisible to application-level metrics and logs

### Container Runtime Monitoring

The Watcher polls all running containers simultaneously at a configurable interval, collecting live CPU utilisation, memory used and percentage of limit, network I/O, block I/O, and process count. For CPU and memory spikes, it immediately identifies the culprit process inside the container via `docker exec top` — so the incident arrives at the agent pipeline already knowing which process is responsible.

- Per-container metrics: CPU %, memory MB / %, network in/out MB, block read/write MB, PID count
- Culprit process identification for CPU and memory anomalies before escalation
- Automatic container discovery keeps the monitored inventory current without manual updates

### External and Application Health Probes

The Watcher runs all external health checks from its own network position — providing a genuine outside-in availability view that is independent of what the application thinks is happening.

- HTTP/HTTPS endpoint health with expected status code validation and latency threshold enforcement
- TLS/SSL certificate expiry monitoring — configurable warning window, default 30 days before expiry
- ICMP ping reachability for internet gateways and DNS servers
- TCP port connectivity for arbitrary host:port targets
- DNS resolution verification for monitored domain names
- Application-level health probes (HTTP and TCP) for platform services
- Container log scanning for error patterns

### Full Anomaly Taxonomy

| Category | Anomaly Types Detected |
|---|---|
| Kernel / System | `high_syscall_intensity` — sudden spike in system call rate attributed to a specific process |
| Container | `cpu_spike` — sustained CPU overrun; `memory_surge` — memory threshold breach; `disk_full` — filesystem utilisation |
| Health Probes | `health_check_failed` — application-level health endpoint returning non-200 or timing out |
| Connectivity | `connection_spike` — network connection count anomaly; `log_error` — error pattern in container logs |
| External (Ping) | `ping_failed` — ICMP reachability failure to gateway or DNS targets |
| External (HTTP) | `external_http_failed` — HTTP/HTTPS endpoint returning unexpected status or exceeding latency threshold |
| External (TCP) | `external_tcp_failed` — TCP port connectivity failure for arbitrary host:port targets |
| External (DNS) | `dns_failed` — DNS resolution failure for monitored domain names |
| Certificate | `tls_expiry` — TLS/SSL certificate approaching or past expiry (configurable warning window) |
| Application | `metrics_anomaly` — application-level metric threshold breach |

### Operational Monitoring Dashboard

The Watcher exposes a real-time metrics dashboard giving operators a live view of infrastructure health across all monitored containers — without requiring log access or CLI tools.

- **Rolling time-series graphs:** CPU, memory, and disk utilisation graphed over a 20-sample rolling window — trend-at-a-glance during an active incident
- **Accurate disk reporting:** Disk utilisation is read directly from `df -B1` inside each container rather than inferred from container runtime stats — eliminates name-mapping errors that cause zero or stale readings
- **Live threshold controls:** CPU %, memory %, disk %, and syscall rate thresholds are adjustable from the UI and pushed to the watcher within 30 seconds — no restart, no redeployment
- **TCP connection monitoring:** Connection count tracked per container alongside resource metrics, providing network pressure signals without requiring a separate tool
- **Multi-watcher inventory:** Each watcher self-registers on startup; the platform UI shows all registered watcher agents across hosts, supporting multi-host deployments from a single control plane

### Deployment Platform Coverage

The watcher subsystem runs wherever your infrastructure runs. A single instance monitors all containers on its host; multiple instances form a fleet — each feeding the same central AI pipeline.

| Deployment Target | eBPF Kernel Telemetry | Container & Resource Metrics | Notes |
|---|---|---|---|
| **Linux bare metal** | ✅ Full | ✅ Full | Kernel 5.4+; all 13 anomaly types including syscall intensity |
| **Linux VM (any hypervisor)** | ✅ Full | ✅ Full | Standard Docker install on guest OS — watcher is hypervisor-agnostic |
| **VMware vSphere guest** | ✅ Full | ✅ Full | Install Docker on the Linux or Windows guest VM; no ESXi-level agent required |
| **Cloud VM (GCP, AWS, Azure)** | ✅ Full | ✅ Full | Documented for GCP Compute Engine; same Docker Compose deployment on any cloud VM |
| **Windows (Docker Desktop + WSL2)** | ⚠️ Partial | ✅ Full | eBPF unavailable outside the WSL2 kernel; CPU, memory, disk, network, and health check anomalies are fully monitored |
| **macOS (Docker Desktop)** | ⚠️ Partial | ✅ Full | Same as Windows; container stats monitoring fully functional |
| **Kubernetes (workload target)** | — | — | kubectl-based remediation is fully supported for K8s workloads; watcher DaemonSet deployment is a planned capability |

**Multi-host watcher fleet:** Deploy one watcher per host (or per network segment) and register all instances to the same platform. Every watcher's events flow through the same qualification engine, storm detection, and 8-agent pipeline — providing a unified operational picture across heterogeneous infrastructure from a single UI.

> **No per-application agents:** All monitoring is done from outside the application — via the host kernel (eBPF), the container runtime (Docker stats), and network probes. No SDKs, no sidecars, no code changes required in monitored applications.

---

## 2 | Intelligent Signal Qualification

Alert fatigue is not solved by better dashboards — it is solved by never creating the alert in the first place. Axiometica AIR applies a three-layer qualification system that suppresses transient noise before any event reaches the incident pipeline.

### Layer 1 — Sustained Duration Gate

A raw metric breach on a single poll is not an incident. The Watcher requires a condition to persist across a configurable minimum number of consecutive polls (default 3, approximately 30 seconds at the default poll interval) before the event is eligible for escalation. Transient spikes — the vast majority of threshold crossings in healthy systems — are filtered entirely.

- Per-resource, per-anomaly-type breach counters track duration independently
- Hysteresis on clearance: conditions only reset when metrics drop to 80% of the alert threshold, preventing oscillation at the boundary from re-firing repeatedly

### Layer 2 — Per-Resource Deduplication and Cooldown

Once an incident is open for a resource, that resource enters a configurable cooldown period (default 60 seconds) during which duplicate events are suppressed. An in-memory active-condition map prevents the same resource from flooding the queue while a condition is already being worked. State persists across watcher restarts via a local status file, so recovery oscillation does not restart the counter.

- DB reconciliation loop runs every 3 polls to cross-check open conditions against actual workflow states
- Stale conditions are automatically cleared when an incident has been resolved, rejected, or closed in the UI — conditions cannot get permanently stuck open

### Layer 3 — Event Qualification Scoring

Events that pass the duration and deduplication gates are scored against seven weighted factors by the EventQualificationService before a workflow is created. Low-scoring events are dismissed silently. The qualification threshold (default score >= 50 of 100) is configurable, and per-criticality floors prevent low-severity signals on important CIs from being artificially promoted.

| Scoring Factor | Weight / Logic |
|---|---|
| Event type severity | Type-specific multiplier: service_down 2.5x, disk_full 2.0x, health_check 1.8x, cpu_spike 1.5x, log_error 0.8x |
| CI tier | Infrastructure layer weighting: Tier 1 (core) scores higher than Tier 3 (peripheral) |
| Business criticality | 2.0x multiplier for mission-critical or business-critical services |
| User impact | Normalised user count from CMDB — more users = higher score ceiling |
| Failover availability | Reduces score by 0.3x if a redundant path exists — auto-healing reduces urgency |
| SPOF status | +0.5x penalty for single points of failure — no fallback means higher urgency |
| SLA exposure | Score contribution from proximity to contractual availability threshold |

> Every qualification decision is recorded with a full factor breakdown and a confidence score — reflecting how much of the scoring was based on known CMDB data versus defaults. Unknown fields are identified by name in the qualification reason, giving operators precise visibility into data gaps that affect scoring accuracy.

---

## 3 | The 8-Agent Incident Resolution Pipeline

When a qualified event enters the incident pipeline, eight specialised agents execute in sequence. Each agent receives the full context accumulated by every agent before it — decisions compound rather than repeat. The pipeline is stateful and deterministic: every step is logged, every decision is auditable, and every outcome feeds back into the platform.

| # | Agent | Core Responsibility |
|---|---|---|
| 1 | **Sentinel Agent** | Classifies severity (critical → info) from the raw event payload. Sets initial confidence score (0.95 for watcher-sourced alerts). Constructs a meaningful incident title encoding resource, anomaly type, and offending process before any further reasoning begins. |
| 2 | **Librarian Agent** | Queries the Neo4j CMDB for the affected CI: type, tier, criticality, SLA %, SPOF flag, failover, compliance scope, dependency graph to depth-2, blast radius (services that depend on this resource), and last 3 incident records. Immediately writes a `degraded` health status to the CMDB so the live graph reflects the active incident. |
| 3 | **Risk Assessor Agent** | Computes a 0–100 composite risk score across 9 weighted factors. Derives assessed severity (may escalate beyond the raw signal) and incident priority (P1–P5). Tracks a CMDB fidelity confidence score — the share of risk inputs sourced from known vs. defaulted data — surfaced alongside the score for operator transparency. |
| 4 | **Mechanic Agent** | Applies 3-tier remediation selection: (1) exact-match runbook from the ops-authored library; (2) CMDB playbook selected by highest historical success rate; (3) AI-generated runbook fallback for novel incident types. Dynamically substitutes the detected anomaly process name into runbook arguments — no hardcoded process names. |
| 5 | **Runbook Generator Agent** | Activates only when no existing runbook or playbook covers the incident type. Uses the platform's configured LLM (OpenAI or Anthropic) to generate a full runbook: diagnostics, remediation, rollback, verification, blast-radius estimate, and approval requirements. Validates against the approved tool registry before saving. All AI-generated runbooks require human approval before entering the active library. |
| 6 | **Policy Broker Agent** | Evaluates all active governance policies against the incident context (environment, severity, anomaly type, service, risk score, blast radius). Fail-closed: any ambiguity defaults to requiring human approval, never to auto-execution. Supports two approval modes: full approval (diagnostics + remediation) and diagnostics-only (safe inspection without action authority). |
| 7 | **Tool Registry Agent** | Executes approved steps from the selected runbook in declared order with inter-step delays and output chaining — diagnostic step results feed directly into remediation step parameters via named references. Hard approval gate: checks for an explicit approval record before any execution. Validates process kill targets against a per-action regex whitelist before delegating to the watcher Kill-API. |
| 8 | **Verifier Agent** | Validates resolution by cross-checking execution outcomes and performing live process-termination verification (real `pgrep` with retry). Sets the terminal lifecycle state to RESOLVED or FAILED. Writes the post-incident health status back to the Neo4j CMDB — closing the feedback loop between the agent pipeline and the live configuration graph. |

### Pipeline Design Principles

- **Sequential and stateful**: each agent extends the shared context object; no agent operates in isolation from what was learned before it
- **Fail-closed at every governance gate**: ambiguity always routes to human approval, never to autonomous execution
- **Step output chaining**: diagnostic step results (e.g., top offending process name) are referenced by name in subsequent remediation step parameters — no hardcoded values, no brittle scripts
- **On-failure policies**: each runbook step declares whether a failure should abort the sequence or allow continuation — non-critical cleanup steps do not block resolution
- **Resumable after approval**: when governance requires human authorisation, the workflow pauses and resumes from the correct step after approval — prior agents do not re-execute

### Correlated Incident Storm Detection

When multiple qualified events arrive within a configurable time window and span multiple resources, the Storm Detection layer activates upstream of the standard pipeline — preventing the 8-agent pipeline from treating each symptom as an isolated incident when a shared root cause is the real problem.

- **Burst threshold:** ≥3 incidents across ≥2 distinct resources within the detection window triggers storm grouping — configurable without restart
- **Storm parent incident:** A parent incident is created in `awaiting_manual` state, requiring the operator to coordinate the investigation before any automated remediation is attempted on the shared condition
- **Child incident containment:** Related incidents are placed in `storm_hold` — suppressing redundant remediations until the parent is resolved, and preventing individual runbooks from masking the root cause
- **AI Storm Hypothesis:** An LLM-generated root cause analysis and remediation recommendation is produced automatically for every storm parent, surfaced in the incident detail AI Insights tab alongside a confidence score
- **Storm Overview UI:** The redesigned incident detail view includes a storm timeline showing affected resources and incident sequence; the AI Insights tab shows the full hypothesis text and its LLM confidence rating

---

## 4 | Self-Improving Runbook Intelligence

Every incident is an opportunity to improve the platform's response to the next one. Axiometica AIR operates a continuous improvement loop — from AI-generated runbooks for novel incident types, through confidence-scored execution, to drift detection when previously effective remediation begins to degrade.

### Three-Tier Remediation Selection

| Tier | Source & Selection Logic |
|---|---|
| **Tier 1 — Ops Runbooks** | Exact-match lookup in the runbook library (event_type + service). Falls back to event_type-only match. Human-authored, highest confidence. |
| **Tier 2 — CMDB Playbooks** | Resource-type and alert-type matched playbooks from the CMDB. When multiple playbooks match, the one with the highest historical success rate is selected. |
| **Tier 3 — AI Generation** | RunbookGeneratorAgent activates only when no runbook or playbook covers the incident type. Generates a full runbook via LLM, validates it, and saves it pending human review. |

### AI Runbook Generation

When the platform encounters an incident type it has not handled before, the RunbookGeneratorAgent takes over from the Mechanic Agent. It searches the runbook library for similar incidents to use as generation examples, then uses the configured LLM (OpenAI GPT-4o or Anthropic Claude) to produce a complete runbook JSON structure.

- Generated content includes: diagnostics steps, remediation steps, rollback procedure, verification steps, blast-radius estimate, and approval requirements
- Validation before saving: checks required fields, verifies all referenced tools exist in the approved tool registry, flags missing rollback procedures, warns on blast-radius appropriateness for severity
- All AI-generated runbooks are saved with `approval_status: pending_human_review` — they are never used autonomously until a human explicitly approves them
- After approval, generated runbooks enter the active library and accumulate success rate history, exactly like ops-authored runbooks

### Confidence Scoring Throughout the Pipeline

Rather than hiding uncertainty behind a single outcome, Axiometica AIR surfaces confidence at every decision point:

- **Event qualification confidence**: percentage of the score derived from known CMDB data vs. defaults — unknown fields named explicitly
- **Risk assessment confidence**: share of the 9 scoring factors populated from live CMDB data rather than fallback values
- **Runbook selection confidence**: based on the source tier (ops runbook = high; AI fallback = lower) and the CMDB playbook's tracked success rate
- All confidence scores are stored with the incident record and visible to operators — not post-hoc explanations, but real-time uncertainty disclosure

### Feedback Loop and Drift Detection

Effectiveness is tracked, not assumed. Every remediation execution updates the success rate of the runbook or playbook that was used. The platform's ML insights engine monitors these rates for degradation.

- Runbook and playbook success rates are updated on every outcome (RESOLVED / FAILED)
- Mechanic Agent runbook selection is influenced by historical success rate — runbooks that work consistently are preferred over those that do not
- **Drift alerts** surface when a previously effective remedy degrades in success rate below a threshold — prompting human review before the runbook causes more failures
- ML feedback ingestion API accepts explicit remediation effectiveness signals per workflow from human operators — enabling supervised improvement
- ML insights endpoint surfaces: pattern counts, generated runbook volume, approval rates, success rates, and actionable recommendations
- CMDB health writeback closes the loop: the live configuration graph always reflects the most recent incident outcome, providing accurate context for the next event on the same CI

### Runbook Library

All runbooks — both ops-authored and AI-generated — are accessible through a searchable library UI designed for day-to-day operator and on-call use.

- **Searchable catalogue:** Filter runbooks by name, service, or tags — find the right runbook without knowing its exact identifier; full-text search across step content
- **Live confidence scoring:** Each runbook card displays its AI-calibrated confidence %, updated after every execution outcome — operators see at a glance how reliable each runbook has been in practice, not just what it claims at creation time
- **Execution stats bar:** A colour-coded success/failure progress bar shows the complete historical run record per runbook (e.g., 7 succeeded, 1 failed out of 8 runs) — ground truth about automation effectiveness
- **Trend badges:** Visual indicators (↑ improving / ↓ declining / → stable) reflect the recent execution trajectory — flagging runbooks that are degrading before they cause incident resolution failures
- **Runbook editor:** Edit step definitions, thresholds, tool arguments, and abort policies directly from the library UI without touching the database or configuration files

### Platform-Aware Runbook Selection

The Mechanic Agent automatically detects the deployment platform of the affected resource and selects the runbook variant whose commands are correct for that runtime. A `docker kill` is the right command in a containerised environment; `kubectl delete pod` is right in Kubernetes; `kill -9` is right on bare Linux. The same incident type never executes the wrong command for its environment.

**Platform detection — priority order:**

| Priority | Source | Example |
|---|---|---|
| 1 | Explicit CMDB declaration | CI carries `platform: kubernetes` — always wins |
| 2 | Resource type inference | `graph-database`, `microservice`, `cache` → Docker; `pod` → Kubernetes; `vm`, `host` → Linux |
| 3 | Universal fallback | `any` platform variant — always present as backstop |

**Command variants by deployment platform:**

| Anomaly / Action | Docker | Kubernetes | Linux / VM | Windows |
|---|---|---|---|---|
| **Kill anomalous process** | `docker kill <container_id>` | `kubectl delete pod <pod>` | `kill -9 <pid>` | `Stop-Process -Id <pid> -Force` |
| **Scale out on CPU pressure** | `docker service scale <svc>=N` | `kubectl scale deployment <name> --replicas=N` | `systemctl restart <svc>` | `Restart-Service <svc>` |
| **Disk usage diagnostics** | `docker exec <c> df -h` | `kubectl exec <pod> -- df -h` | `df -h && du -sh /*` | `Get-PSDrive` |
| **Trace syscalls** | `docker exec <c> strace -c <proc>` | `kubectl exec <pod> -- strace -c <proc>` | `strace -c -p <pid>` | Event Tracing for Windows (ETW) |
| **Container/process restart** | `docker restart <container>` | `kubectl rollout restart deployment/<name>` | `systemctl restart <svc>` | `Restart-Service <svc>` |

**4-pass runbook cascade:**
1. **Exact match** — event type + detected platform (e.g., `high_cpu` on `docker`) ranked by success rate
2. **Platform-agnostic** — event type + `any` platform (universal variant, always exists)
3. **Cross-platform fallback** — alternative platform variant if available
4. **AI generation** — Tier 3 RunbookGeneratorAgent creates a novel runbook when no existing match is found

Tools in the approved actions catalog carry `commandVariants` keyed by platform. The correct variant is substituted automatically at execution time — runbook step definitions contain no platform-specific branching logic.

---

## 5 | Governance, Policy, and Change Management

Automation without governance is not autonomous operations — it is autonomous risk. Axiometica AIR embeds policy enforcement directly in the agent pipeline, not as a post-hoc audit layer. Governance is evaluated before any action is taken, every time.

### Policy Engine

The Policy Broker Agent evaluates all enabled policies against the incident context before authorising execution. Policies are created through a UI policy editor, stored in the database, and take effect immediately — no deployment, no restart.

- Policy rules match on: environment, anomaly type (single or list), service, minimum severity, and minimum risk score
- Per-policy approval priority (1–100): lower number wins when multiple policies match the same incident
- Policy constraints: maximum blast radius, maximum restart frequency, post-monitoring requirements, approved action list (or wildcard)
- Two-layer fallback: user-facing policy editor (primary) → legacy governance policy table (secondary) → conservative default (require approval)
- **Fail-closed by design**: any evaluation exception or ambiguity defaults to requiring human approval — the platform never auto-approves on uncertainty

### Approval Modes

| Approval Mode | What the Platform Is Authorised to Do |
|---|---|
| **Full Approval** | Execute all steps in the approved runbook: diagnostics, remediation, rollback, and verification. Used when the operator is confident in the selected remediation. |
| **Diagnostics-Only** | Execute safe read-only inspection steps only (gather metrics, check process state, profile syscalls). Remediation steps are suppressed. Used when the operator wants to understand the incident before granting remediation authority. |

### Approved Actions Catalog

Every tool that the Tool Registry Agent can execute must exist in the platform's approved actions catalog — a database-backed registry of permitted remediation operations.

- Actions are categorised: `diagnostic`, `remediation_safe`, or `remediation_intrusive`
- Per-action blast radius classification and `requires_approval` flag
- **Process kill safety**: each kill action carries a regex-based process name whitelist with priority-ordered rules — first match wins, default deny
- Individual actions can be enabled or disabled without deletion — changes take effect immediately
- Validation endpoint: operators can check a process name against the rules without executing, for safe pre-flight verification

### Change Management Pipeline

Alongside the incident pipeline, Axiometica AIR runs a parallel change and deployment workflow with dedicated agents covering the full change lifecycle: risk assessment, scheduling, pre-deployment validation, deployment execution, post-deployment verification, and automated documentation. Change workflows share the same governance infrastructure as incident workflows — policies, approvals, and audit trails are unified.

- Seven specialised change agents: ChangeRiskAssessor, DeploymentScheduler, DeploymentChecker, Deployer, DeploymentVerifier, ValidationAgent, DocumentationServiceAgent
- Terminal state handlers: Deployed, RolledBack, Rejected, Escalated — each with appropriate CMDB and notification actions

---

## 6 | Operational CMDB

The platform CMDB is a live, continuously-reconciled graph of the operational environment. Every AI agent that makes a decision queries it. Every incident that resolves writes back to it. It is not a documentation artefact — it is the platform's shared memory.

### Graph-Native Configuration Model

Configuration items are nodes with typed sub-labels (Service, Server, Container, Database, Application) and typed relationships. The graph structure means blast-radius traversal — finding every downstream service affected by a failure — is a native graph query, not a join chain.

- **DEPENDS_ON**: service-to-service dependency edges, traversable in both directions for blast-radius and root-cause analysis
- **RUNS_ON / HOSTED_ON**: container and service to host topology — correlate co-located failures without manual mapping
- **PART_OF**: platform hierarchy — containers grouped under logical services under platform roots
- All relationships created and updated automatically by the discovery and agent pipeline — no manual edge management

### Governance Properties as First-Class Graph Data

Every CI node carries its full governance context: CI tier, business criticality, SLA %, SPOF status, failover availability, compliance scope (PCI, GDPR, HIPAA, SOC2), owner, environment, and user count. These are embedded in the graph, not fetched from a separate system.

- Risk Assessor, Policy Broker, and Librarian agents all read from the same live graph — one source of truth, no stale caches
- Discovery never overwrites governance properties on manually-seeded or ServiceNow-sourced CIs — human-defined policy survives every automated reconciliation cycle
- Compliance scope multipliers in risk scoring (PCI, GDPR, HIPAA, SOC2) amplify all factor scores — regulated CIs are treated with commensurate urgency

### Continuous Discovery and Health Writeback

The Watcher discovery loop runs every N polls (configurable), inspecting all running containers and reconciling their runtime configuration directly into the graph. New containers are auto-created with governance defaults and flagged for human review. Health status is updated by both discovery (container state) and the Verifier Agent (incident outcome).

- Auto-discovered properties per container: image version, CPU/memory limits, IP address, exposed ports, environment classification, platform and OS
- **Health status coalesce pattern**: discovery respects incident-driven health overrides — a CI marked degraded by an active incident is not silently reset by the next discovery cycle
- Verifier Agent marks CI recovered on RESOLVED outcome, or maintains degraded state on FAILED — CMDB always reflects the current operational reality
- New discovered containers tagged with `discovery_source: watcher_discovery`, distinguishing them from manually curated or SN-sourced CIs

### Configuration Graph Visualisation

The CMDB exposes its relationship graph through an interactive force-layout explorer in the platform UI — giving operators a spatial understanding of their environment without writing Cypher queries.

- **Force graph layout:** Nodes distribute naturally using a physics-based layout engine — no stacking, no nodes collapsing to a single point on load; the graph settles to a readable state automatically
- **Relationship traversal:** `HOSTED_ON` and `PART_OF` edges are explorable from any CI node — click any container to surface its upstream host and its logical service parent, revealing shared-host failure risk that is invisible in flat inventory lists
- **Live health overlay:** Node colour reflects the current health status from the graph — CIs marked degraded by an active agent pipeline outcome are visually distinct, so the impact of an incident is immediately spatial rather than text-only
- **Blast radius exploration:** Starting from any CI, traverse `DEPENDS_ON` edges forward to see which downstream services would be affected by a failure — matching the exact traversal the Librarian Agent performs during incident enrichment

---

## 7 | Bidirectional ServiceNow Integration

Axiometica AIR integrates with ServiceNow in both directions — pulling CMDB context into the operational graph and pushing incident state back to the ServiceNow incident record — creating a live data bridge between the platform's autonomous pipeline and the organisation's ITSM workflow.

| Direction | Capability |
|---|---|
| **Inbound — CMDB Pull** | Bulk sync from 5 ServiceNow CI classes: Services, Service Offerings, Servers, Linux Servers, Windows Servers |
| **Inbound — Relationships** | `cmdb_rel_ci` relationship sync — creates DEPENDS_ON edges in the Neo4j graph, preserving SN topology |
| **Inbound — Pagination** | Concurrent async fetch (all classes in parallel), 200 records/page, up to 5,000 per class |
| **Inbound — Upsert Logic** | MERGE on CI name: creates on first sync, updates on subsequent — no duplicates, no data loss |
| **Inbound — Data Safety** | Governance properties (SPOF, SLA, failover, compliance, user count) on manually-seeded CIs are never overwritten by SN sync |
| **Outbound — Incident Push** | Creates ServiceNow incidents from platform workflows with full field mapping: severity → SN priority, lifecycle → SN state |
| **Outbound — Idempotency** | Stored mapping prevents duplicate SN incidents on re-push; returns existing record reference |
| **Outbound — Sync-Back** | State changes (severity, lifecycle, work notes) pushed back to ServiceNow as the platform incident progresses |
| **Outbound — Back-Link** | Platform incident URL written to ServiceNow work notes — operators can jump directly from the SN ticket to the platform |

### CMDB Sync Architecture

All five ServiceNow CI classes are fetched concurrently via `asyncio.gather` — reducing sync time to that of the slowest single class rather than the sum of all classes. MERGE semantics on CI name ensure that existing platform governance data is never clobbered on subsequent syncs.

- Sync logs record every run: start time, finish time, records pulled, status, and error detail — full audit trail from the Connector Hub UI
- Sub-label stamping on sync: SN-sourced nodes receive `:Service` or `:Server` sub-labels, making them compatible with all graph queries that the agent pipeline runs
- Field normalisation: SN operational_status numeric codes and business_criticality string variants are mapped to platform-standard values at ingest time

---

## 8 | External Monitoring Tool Integration

Axiometica AIR is not a replacement for existing monitoring tools — it is the AI reasoning and remediation layer on top of them. The Connector Hub provides a certified, webhook-based ingest path from any monitoring tool into the platform's full AI pipeline. Every alert that enters through a connector is normalised, qualified, storm-correlated, and acted upon exactly as if it had been detected by the native watcher.

### Connector Hub

Seven certified adapters are included, covering the monitoring tools most commonly deployed in enterprise environments:

| Connector | Direction | Capability |
|---|---|---|
| **ServiceNow** | Bidirectional | Ingest alerts; write resolution status and work notes back to the SN ticket |
| **Splunk** | Inbound | Alert webhook from Splunk saved searches and alert actions |
| **Datadog** | Inbound | Monitor alert webhook — maps Datadog severity to platform criticality |
| **Dynatrace** | Inbound | Problem webhook — maps Dynatrace problem lifecycle to platform incident states |
| **Prometheus / Alertmanager** | Inbound | AlertManager webhook receiver — ingests firing and resolved alerts |
| **PagerDuty** | Inbound | EventBridge webhook — converts PagerDuty incidents to platform events |
| **Zabbix** | Inbound | Action webhook — maps Zabbix trigger severity to platform criticality |

Each connector generates a unique, stable webhook URL. Configure your monitoring tool to POST alerts to that URL — the platform normalises the inbound payload, applies the qualification engine, and feeds qualifying events into the incident pipeline and storm detection with no further configuration.

### Per-Connector Governance

Each connector carries independent governance controls, allowing different trust levels for different source systems:

| Control | Description |
|---|---|
| `allow_auto_remediation` | Whether automated runbooks can execute for events from this source (default: off — require human approval until trust is established) |
| `allow_storm_detection` | Whether events from this connector participate in cross-source storm correlation |
| `default_criticality` | Criticality level when the inbound alert carries no mappable severity |
| `webhook_secret` | Optional HMAC-SHA256 signature verification — the connector rejects unsigned requests |

### Universal Event Normalisation

Regardless of which monitoring tool raised the alert, every inbound event is processed by the same `EventQualificationService` that the native watcher uses. The 3-layer qualification system (duration gate, deduplication, 7-factor scoring) applies equally to connector-sourced events — external tools cannot flood the incident queue any more than the native watcher can.

> **Open ingest API:** Operators and scripts can also inject events directly via `POST /api/monitoring-events` — enabling custom integrations with any tool that supports HTTP webhooks, not just the seven certified connectors.

---

## 9 | Platform Operations and Configuration

Axiometica AIR is designed to operate at runtime without restarts or redeployments. Thresholds, external checks, policies, approved actions, and LLM provider settings are all configurable through the UI and take effect on the next poll cycle.

### Live Configuration Hot-Reload

The Watcher reads configuration from a three-tier priority stack: environment variables (startup defaults), a file-based config with file modification time monitoring, and the platform Settings API (polled every 30 seconds). The API layer takes precedence and is the primary operational control plane.

- All watcher thresholds configurable at runtime: CPU %, memory %, disk %, syscall rate, connection count, cooldown seconds, poll interval, consecutive poll requirement
- External check targets (HTTP, TCP, ping, DNS, TLS) hot-reload from the DB on every config refresh — new checks activate without restart
- Discovery interval (polls between discovery runs) and enabled state configurable at runtime

### LLM Provider and AI Configuration

- Supports OpenAI (GPT-4o and variants) and Anthropic (Claude) — provider and model configurable via UI
- Provider configuration persisted to DB and shared across all AI consumers (summary generation and runbook generation use the same provider)
- AI-generated incident summaries: both an executive narrative and a structured technical bullet-point summary generated after pipeline completion
- Risk weight configuration API allows adjustment of all 9 risk scoring factor weights from the UI

### Analytics and Operational Metrics

- Total incidents, active incidents, resolved today, average resolution time (seconds)
- Approval rate: what fraction of incidents required human authorisation
- Remediation success rate: automatic vs. manual attempts and outcomes
- ML insights: patterns discovered, generated runbook count, drift alerts, and actionable recommendations for threshold or runbook adjustment
- Real-time workflow state streaming via WebSocket — no polling required for live incident tracking

### Multi-Watcher Architecture

- Each watcher instance self-registers with the platform on startup (hostname, poll interval, sentinel container) — supporting multi-watcher deployments across multiple hosts
- Registered watcher inventory discoverable from the platform UI

---

## Summary

Axiometica AIR represents a shift from reactive monitoring to truly autonomous operations. Each capability reinforces the others: the observability layer provides accurate signal; the qualification engine ensures only meaningful signals become incidents; the agent pipeline resolves incidents with governed, auditable AI reasoning; the self-improvement loop ensures each resolution makes the next one better; and the CMDB ties every decision to the real configuration of the live environment.

| Capability | What It Delivers |
|---|---|
| **Multi-Vector Observability** | 13 anomaly types across kernel, container, application, and external layers — no blind spots |
| **Multi-Platform Deployment** | Watcher runs on Linux, VMware, Cloud VMs (GCP/AWS/Azure), Windows, and macOS; multi-host fleet from a single control plane |
| **Operational Monitoring Dashboard** | Real-time CPU/memory/disk graphs, accurate disk reporting, live threshold controls, multi-watcher support |
| **Signal Qualification Engine** | 3-layer false-positive suppression + 7-factor scoring ensures only qualified events become incidents |
| **8-Agent Resolution Pipeline** | Detection to verified resolution in a single, governed, auditable agentic workflow |
| **Storm Detection** | Correlated burst detection groups related incidents under a parent, suppresses redundant remediations, and generates an AI root-cause hypothesis for the operator |
| **Self-Improving Runbook Intelligence** | AI generates runbooks for novel incidents; platform-aware command variants (Docker, Kubernetes, Linux, Windows); searchable library with confidence scores, execution stats, and trend badges |
| **Governance & Policy Engine** | Fail-closed, approval-gated, with diagnostics-only mode — automation without uncontrolled autonomy |
| **Operational CMDB** | Live force-graph of configuration, health, and relationships that provides context and risk to every AI decision |
| **Bidirectional ServiceNow** | CMDB pull + incident push + live state sync — unified ITSM and autonomous ops without duplicate records |
| **External Tool Integration** | Connector Hub ingests alerts from Datadog, Dynatrace, Splunk, Prometheus, PagerDuty, Zabbix, and ServiceNow into the same AI pipeline as native watcher events |

---

*For technical integration details, architecture documentation, or a live demonstration, contact your Axiometica AIR account team.*
