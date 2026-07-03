# Axiometica AIR (Autonomous Incident Response) — Administrator Guide

**Version:** 1.2.0 · **Last updated:** June 2026  
**Audience:** Platform administrators, ITOM admins, infrastructure engineers

---

## Table of Contents

1. [Overview](#1-overview)
2. [Container Architecture](#2-container-architecture)
3. [Agent Pipeline](#3-agent-pipeline)
4. [Incident Management](#4-incident-management)
   - 4.5 [Monitoring Events Page](#45-monitoring-events-page)
5. [Prerequisites](#5-prerequisites)
6. [Installation](#6-installation)
7. [First Login & Initial Setup](#7-first-login--initial-setup)
8. [User Management](#8-user-management)
9. [LLM Provider Configuration](#9-llm-provider-configuration)
10. [Connector Hub](#10-connector-hub)
11. [Slack ChatOps](#11-slack-chatops)
12. [Governance — Policies & Approved Actions](#12-governance--policies--approved-actions)
13. [Storm Detection](#13-storm-detection)
14. [CMDB — Neo4j & ServiceNow Sync](#14-cmdb--neo4j--servicenow-sync)
   - 14.3 [CMDB Editor (UI)](#143-cmdb-editor-ui)
15. [Runbook Management](#15-runbook-management)
16. [Event Type Taxonomy](#16-event-type-taxonomy)
17. [Virtual Chat — AI Ops Assistant](#17-virtual-chat--ai-ops-assistant)
18. [Platform Intelligence](#18-platform-intelligence)
19. [Environment Variables Reference](#19-environment-variables-reference)
20. [Day-2 Operations](#20-day-2-operations)
21. [Backup & Recovery](#21-backup--recovery)
22. [Upgrading](#22-upgrading)
23. [Security Hardening](#23-security-hardening)
24. [Troubleshooting](#24-troubleshooting)

---

## 1. Overview

Axiometica AIR (Autonomous Incident Response) is an AI-powered AIOps platform that autonomously detects, diagnoses, and remediates infrastructure incidents. The platform ingests events from an internal watcher agent and external monitoring connectors, qualifies them through a scoring engine, and routes qualified incidents through a multi-agent AI pipeline that selects and executes the appropriate remediation runbook.

All platform state is persisted in named Docker volumes (PostgreSQL, Redis, Neo4j). Live configuration changes made in the Settings UI take effect without a service restart. Only environment variable changes or `docker-compose.yml` modifications require a container restart.

### Services at a Glance

| Container | Role | Port(s) |
|---|---|---|
| `agentic_os_backend` | FastAPI REST + WebSocket API server | 8000 |
| `agentic_os_celery_worker` | Celery task worker — runs the AI agent pipeline and background jobs | — |
| `agentic_os_postgres` | PostgreSQL 15 — primary relational database | 5432 |
| `agentic_os_redis` | Redis 7 — Celery broker, result backend, rate-limit cache | 6379 |
| `agentic_os_neo4j` | Neo4j 5 — CMDB graph (CI topology, blast radius) | 7474, 7687 |
| `agentic_os_flower` | Celery Flower — task queue monitor UI | 5555 |
| `agentic_os_frontend` | React 18 SPA (nginx in production, Vite dev server otherwise) | 3000 |
| `sentinel_senses` | eBPF kernel telemetry agent (privileged, host network) | — |
| `watcher_brain` | Monitoring and discovery agent | — |

### Key Concepts

| Term | Definition |
|---|---|
| **Event** | A single metric breach or alert from the watcher or a connector webhook |
| **Incident** | A qualified event promoted to an AI pipeline workflow (INCxxxxx) |
| **Runbook** | A structured workflow of Diagnostic, Action, and Verification steps |
| **Policy** | A rule governing whether an incident requires human approval |
| **Storm** | A correlated group of incidents sharing a common root cause |
| **CI** | Configuration Item — a resource tracked in the Neo4j CMDB |
| **Blast Radius** | The set of CIs impacted if the primary CI of an incident fails |
| **Risk Score** | A 0–100 score computed from weighted factors (severity, CI tier, blast radius, SLA, etc.) |

---

## 2. Container Architecture

All containers share a single Docker bridge network (`agentic_os_network`) and communicate using their service names as hostnames.

### 2.1 agentic_os_backend

The backend is a FastAPI application serving as the single control plane. On startup it:

- Connects to PostgreSQL and runs any pending Alembic database migrations
- Connects to Redis and verifies Celery broker availability
- Connects to Neo4j and seeds CMDB nodes for all running Docker containers (first run)
- Loads the agent registry, runbook catalogue, policy set, and event taxonomy into memory
- Opens a Socket Mode WebSocket connection to Slack if ChatOps is enabled

**Key environment variables:**

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection for caching and session data |
| `CELERY_BROKER_URL` | Redis URL for Celery task submission |
| `JWT_SECRET` | Signs and verifies JWT access tokens |
| `WATCHER_API_KEY` | Authenticates webhook calls from watcher_brain |
| `ALLOWED_ORIGINS` | CORS origins (set to the frontend URL in production) |

**Health endpoints:**

```
GET /api/health  →  {"status":"healthy"}  (always 200)
GET /api/ready   →  200 when DB, Redis, and Neo4j are all live
```

### 2.2 agentic_os_celery_worker

The Celery worker pulls tasks from three Redis queues concurrently. Stopping the worker does not lose tasks — they remain in Redis and are picked up on restart.

| Queue | What runs on it | Default concurrency |
|---|---|---|
| `WORKFLOWS` | Full AI agent pipeline — one task per incident | 2 |
| `DEFAULT` | CMDB sync, ServiceNow polling, backups, health checks | 4 |
| `APPROVALS` | Approval timeout monitoring, Slack notification delivery | 2 |

> **Agent code changes require restarting the Celery worker:** `docker compose restart celery_worker`

**Scaling concurrency** — edit the worker command in `docker-compose.yml`:

```yaml
command: celery -A app.celery_app worker
         --queues=WORKFLOWS,DEFAULT,APPROVALS
         --concurrency=8
         --loglevel=info
```

### 2.3 agentic_os_postgres

PostgreSQL 15 is the primary relational database. It uses an event-sourcing pattern — all state changes are written as immutable event records in addition to updating the current-state tables.

| Table | Purpose |
|---|---|
| `incidents` | Current state of every incident |
| `events` | Immutable event log — every state change, approval, agent action |
| `runbooks` | Runbook definitions, step JSON, confidence score, execution stats |
| `policies` | Governance policies |
| `principals` | User accounts and API key principals |
| `monitoring_events` | Raw events before qualification |
| `event_storms` | Storm parent incidents and child associations |
| `connectors` | Connector configuration (credentials stored encrypted) |

Volume: `postgres_data` → `/var/lib/postgresql/data`

### 2.4 agentic_os_redis

Redis 7 serves three distinct roles:

- **Celery broker** — task messages for all three queues (database 0)
- **Celery result backend** — task execution results and status (database 1)
- **Application cache** — rate limiting, event deduplication, session data (database 2)

Redis is not a primary data store. If flushed or restarted, in-flight tasks are lost but all incident history remains intact in PostgreSQL.

Volume: `redis_data`

### 2.5 agentic_os_neo4j

Neo4j 5 Community Edition with APOC plugin stores the Configuration Item graph. Used for:

- Blast radius calculation (graph traversal upstream from the affected CI)
- Storm root cause analysis (shared upstream dependencies)
- AI context enrichment (feeding CI relationships to the LLM)
- CMDB browser UI

Neo4j takes 30–60 seconds to fully initialise on startup. The backend retries for up to 120 seconds.

```
Volumes: neo4j_data (graph), neo4j_logs (query and server logs)
Browser: http://localhost:7474
Default credentials: neo4j / agentic_os_neo4j
```

### 2.6 agentic_os_frontend

React 18 SPA built with Vite. In development it runs the Vite dev server with HMR. In production the build artifact is served by nginx. Communicates exclusively with the backend on port 8000, using WebSockets for real-time incident state updates.

### 2.7 agentic_os_flower

Celery monitoring UI at `http://localhost:5555`. Provides real-time queue depths, active tasks, task history, and worker status. Restrict to admin networks in production.

### 2.8 watcher_brain

Autonomous monitoring and discovery agent, authenticating each report with `WATCHER_API_KEY`.

**Monitoring cycle** (every `WATCHER_POLL_INTERVAL` seconds, default 10s):
- Collects CPU %, memory %, disk %, and network I/O for every container and the host
- Collects syscall rates per container if `sentinel_senses` is available
- Runs configured External Connectivity Checks (HTTP/TCP probes)
- Tails configured Log Monitors for pattern matches
- Posts qualifying anomalies to the backend qualification endpoint

**Discovery cycle** (every 15 polls ≈ 2.5 minutes):
- Queries the Docker Engine API for all running containers
- Creates or updates Neo4j CMDB nodes for new containers
- Removes CMDB nodes for containers that no longer exist
- Resolves container inter-dependencies from Docker network topology

**Adapter modes** — selected per CI based on the `adapter_mode` attribute in the CMDB:

| Adapter | Target | How actions are executed |
|---|---|---|
| `docker` | Docker containers on the local host | `docker exec` / `docker restart` via Docker Engine API |
| `ssh` | Remote Linux hosts | SSH command execution |
| `kubernetes` | Kubernetes pods and nodes | `kubectl exec` / `kubectl rollout restart` |
| `vmware` | VMware vCenter VMs | VMware vSphere API guest operations |
| `aws` | EC2 instances | AWS Systems Manager Run Command (SSM) |
| `azure` | Azure VMs | Azure Run Command API |

### 2.9 sentinel_senses

Runs as a privileged container on the host network. Uses `bpftrace` (eBPF) to instrument the Linux kernel and captures:

- Syscall rates per process and per container (`high_syscall_intensity` events)
- File I/O anomalies (unusual write bursts, `/etc` or `/proc` access patterns)
- Network connection events
- Process creation events

If `sentinel_senses` is unavailable (e.g., WSL2 without eBPF support), `watcher_brain` logs a warning and operates in reduced mode. Container runtime metrics still work; only syscall-based event types are unavailable.

> **WARNING:** `sentinel_senses` requires `--privileged` and `--network=host`. Do not remove these flags.

### 2.10 Docker Volumes

| Volume | Used By | Contains |
|---|---|---|
| `postgres_data` | agentic_os_postgres | All incident data, event log, runbooks, policies, users |
| `neo4j_data` | agentic_os_neo4j | CMDB graph (CI nodes and relationships) |
| `neo4j_logs` | agentic_os_neo4j | Neo4j server and query logs |
| `redis_data` | agentic_os_redis | Celery task queue state and cache (recoverable) |

```bash
docker volume ls                                  # list all volumes
docker volume inspect postgres_data               # show mount path on host
docker system df -v                               # show volume disk usage
```

> **WARNING:** Never run `docker compose down -v` in production. The `-v` flag removes all named volumes, permanently destroying all data.

---

## 3. Agent Pipeline

When an event is qualified into an incident, the backend submits a pipeline task to the Celery `WORKFLOWS` queue. The Celery worker executes a sequence of five specialised agents, each writing its decisions to the immutable events log.

### 3.1 Stage 1: TriageAgent

Enriches the raw incident with CMDB context and computes the risk score:

- Queries Neo4j for the affected CI: tier, environment, criticality, failover status, SLA compliance
- Traverses the dependency graph to compute the blast radius
- Computes the risk score (0–100) using configured factor weights from **Settings → Risk Assessment**
- Sets the incident severity based on risk score and per-severity thresholds

### 3.2 Stage 2: GovernanceAgent

Evaluates which policy applies and determines execution constraints:

- Evaluates all active policies in priority order (highest priority number first)
- Matches on: minimum severity, environment, risk score range, specific service
- If approval is required: transitions incident to `pending_approval`, sends Slack notification with approval buttons, halts the pipeline
- If no policy matches: applies system default (no approval required, all non-intrusive actions permitted)

### 3.3 Stage 3: MechanicAgent

Selects the best runbook using a 5-tier hierarchy:

| Tier | Strategy | When it fires |
|---|---|---|
| 1 | **Exact match** | Runbook matches both `event_type` AND `service_name` |
| 2 | **Type match** | Runbook matches `event_type`, any service — uses highest-confidence runbook |
| 3 | **Semantic similarity** | Embedding cosine similarity above threshold (default 0.75) |
| 4 | **LLM generation** | No match above threshold — LLM generates a new runbook and saves it to the catalogue |
| 5 | **Fallback** | LLM unavailable — uses the seeded generic-diagnostic runbook |

> Tier 4 requires an LLM provider to be configured in **Settings → LLM Provider**.

### 3.4 Stage 4: ToolRegistryAgent

Executes the selected runbook step by step. For each node:

1. Resolves the tool command against the Approved Actions catalogue (hard gate — unlisted tools are rejected)
2. Selects the correct command variant for the CI's `adapter_mode`
3. Substitutes runtime variables: `{target}`, `{namespace}`, `{environment}`, and captured outputs from prior steps
4. Executes the command against the target CI
5. Captures stdout/stderr and extracts named output variables
6. Evaluates Decision node conditions against captured outputs
7. For intrusive actions: pauses and requests secondary approval

### 3.5 Stage 5: ValidationAgent

Runs after the final remediation action:

- Executes Verification nodes (health checks, metric re-sampling, endpoint probes)
- On success: transitions incident to `resolved` or `deployed`; updates runbook confidence score upward
- On failure: transitions to `rolled_back`; executes rollback steps if defined; updates confidence downward; alerts operator via Slack

### 3.6 Confidence Score Lifecycle

Every runbook has a confidence score (0–100%) updated after every execution using a weighted moving average:

```
new_score = (old_score × 0.7) + (outcome × 0.3 × 100)
```

MechanicAgent uses this as a tiebreaker when multiple runbooks match at the same tier. The Runbook Library card shows the current confidence % and a trend badge (improving / stable / declining) based on the last 10 executions.

### 3.7 Pipeline Hold for Storm Grouping

When **Pipeline Hold Buffer** (Settings → Storm Agent) is set above 0, new incident pipelines pause for the configured number of seconds. This gives the Storm Agent time to detect a correlated burst and group incidents before individual pipelines begin running.

---

## 4. Incident Management

### 4.1 Incidents List

Navigate to **Incidents** in the left sidebar. Each incident card shows: incident number (INCxxxxx), title, event type badge, resource name, severity badge, risk score, MTTR, blast radius count, lifecycle state badge, and a "Via [connector]" badge for connector-sourced incidents.

### 4.2 Incident Lifecycle States

| State | Meaning |
|---|---|
| `received` | Event qualified; pipeline task submitted to Celery WORKFLOWS queue |
| `in_progress` | Agent pipeline actively executing |
| `pending_approval` | GovernanceAgent requires approval; waiting for operator decision |
| `deploying` | ToolRegistryAgent executing remediation actions |
| `resolved` | ValidationAgent confirmed the condition is resolved |
| `deployed` | Remediation applied and validated; service restored |
| `rolled_back` | ValidationAgent failed; rollback steps executed |
| `rejected` | Operator rejected the proposed remediation |
| `failed` | Pipeline encountered an unrecoverable error |

### 4.3 Incident Detail Tabs

Click any incident card to open the detail view. Nine tabs:

**Tab 1: Overview**  
Agent reasoning trace, selected runbook, confidence %, affected CI with CMDB topology mini-map, blast radius count, and a summary of the qualification decision.

**Tab 2: Events**  
The raw monitoring event that triggered this incident: alert type, resource, source, criticality, qualification score breakdown, detected time, and confidence level. Includes the escalation reason written by the qualification engine. For events below the incident detail, see **Events** in the left sidebar.

**Tab 3: Governance**  
Full policy evaluation record: every policy considered in priority order, whether it matched or was skipped, match criteria that did or did not apply, and the final governance decision.

**Tab 4: Remediation**  
Selected runbook, confidence %, risk level, blast radius, and the full step-by-step execution log. Each step shows its node type, tool name, status (pending / running / completed / failed / skipped), duration, and captured terminal output. A progress bar shows step counts in real time.

**Tab 5: AI Insights**  
Populated by the configured LLM: Executive Summary, Technical Detail, Remediation Reasoning, and Root Cause Hypothesis cross-referenced with CMDB topology. Requires an LLM provider — see §9.

**Tab 6: Risk**  
Overall risk score (0–100) broken down by individual factor. Each row shows: configured weight, scored value, and contribution to the total.

**Tab 7: Timeline**  
Chronological event stream for the entire incident lifecycle — event arrival, qualification, each pipeline stage, approval request and decision, each remediation step, and terminal state. Timestamps are UTC.

**Tab 8: Audit**  
Structured log of all human operator actions: approval decisions (who, what notes, timestamp), manual state overrides, work note additions, and Slack-sourced actions.

**Tab 9: Work Notes**  
Free-text scratchpad. Operators add notes during investigation. Each note records author and timestamp. Included in ServiceNow incident sync if configured.

### 4.4 Approval Queue

Navigate to **Approvals** in the left sidebar. Shows all incidents currently in `pending_approval` state. Click **Approve**, **Diagnostics Only**, or **Reject** (with a reason). Decisions are recorded in the Audit tab and Slack (if configured).

### 4.5 Monitoring Events Page

Navigate to **Events** in the left sidebar to see all raw signals received by the platform — before, during, and independently of incident creation.

#### Event cards

Each card shows:
- **Event type** (monospace tag) and status badge (`New`, `Dismissed`, `Incident`, `Escalated`)
- **Severity** badge (`Critical`, `Warning`, `Info`) and **Confidence** badge (`✓ High Confidence` / `Low Confidence`)
- **Score** (`20.0 /100`) — hover over it to see the full qualification breakdown: `critical ×2 → 100 × test (×0.2) → 20.0 | threshold 50`
- **Alert title** and description from the payload
- **Resource, source, severity** and detected timestamp in the footer

#### Confidence levels

| Value | Meaning |
|---|---|
| **100%** | CI found in CMDB — scoring used real environment, criticality, and tier |
| **60%** | CI not in CMDB — scoring used default environment multiplier (×0.75) |
| **0%** | CI dismissed by policy (`unknown_ci_behavior = dismiss`) |

High confidence = ≥ 70%. Low confidence events may still qualify as incidents but their scores are based on defaults rather than real CMDB data. Remedy: add the CI to the CMDB.

#### Backend deduplication

The platform deduplicates events at the condition level, not just the incident level. When an event fires for `(resource_name, event_type)`:
- **First occurrence** — stored as a new row, qualification runs, condition marked open
- **Subsequent identical events** — deduplicated: the original event_id is returned, no new row is written, no qualification re-runs
- This applies whether the first event qualified (became an incident) **or** was dismissed below threshold

The condition resets (next event fires fresh) when:
1. A `condition_cleared` signal is received for the resource (watcher recovery)
2. The linked incident is resolved or closed
3. The safety TTL expires (24 hours)

#### Manual escalation / dismiss

Events in `New` state have **Escalate** and **Dismiss** action buttons. Dismissed events can be manually escalated to an incident from the event card.

---

## 5. Prerequisites

### Hardware (Single-Host Deployment)

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8 cores |
| RAM | 8 GB | 16 GB |
| Disk | 40 GB | 100 GB SSD |
| OS | Linux (kernel 5.8+ for eBPF) | Ubuntu 22.04 / RHEL 9 |

> **Windows / WSL2 note:** Docker Desktop on Windows runs containers inside a Linux VM. eBPF via `sentinel_senses` requires the WSL2 kernel to support `bpftrace`. Verify: `docker exec sentinel_senses bpftrace -e "BEGIN{print(1)}"`. All features except syscall-based events remain functional if `bpftrace` is unavailable.

### Software

| Software | Minimum Version | Notes |
|---|---|---|
| Docker Engine | 24+ | |
| Docker Compose | v2 (plugin) | `docker compose` (no dash) |
| Git | Any | For cloning the repository |

```bash
docker --version              # Docker version 24.x.x
docker compose version        # Docker Compose version v2.x.x
```

### Inbound Network Ports

| Port | Service | Who Needs It |
|---|---|---|
| 3000 | Frontend UI | Users and operators |
| 8000 | Backend REST API | Users, connector webhooks from monitoring tools |
| 5555 | Celery Flower | Admins only — restrict in production |
| 7474 | Neo4j Browser | Admins only — restrict in production |

> Ports 5432 (PostgreSQL), 6379 (Redis), and 7687 (Neo4j Bolt) must NOT be exposed externally.

---

## 6. Installation

### 6.1 Get the Source

```bash
git clone <repository-url>
cd axiometica-air
```

### 6.2 Run the Installer

The installer generates required secrets and writes them to `.env`:

**Linux / macOS:**
```bash
chmod +x install.sh && ./install.sh
```

**Windows:**
```cmd
install.bat
```

The installer creates `.env` with:
- `JWT_SECRET` — 32-byte random hex string
- `WATCHER_API_KEY` — shared key for watcher authentication

> The backend refuses to start if `JWT_SECRET` or `WATCHER_API_KEY` are missing. Never check `.env` into source control.

### 6.3 Optional Pre-Configuration

```bash
# LLM provider
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Slack ChatOps
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
```

These can also be configured via **Settings** after startup.

### 6.4 Start All Services

```bash
docker compose up -d
```

First run pulls images and builds backend and watcher images (3–5 minutes). Subsequent starts take 30–60 seconds.

### 6.5 Verify Startup

```bash
docker compose ps                          # all containers should be Up
curl http://localhost:8000/api/health      # {"status":"healthy"}
curl http://localhost:8000/api/ready       # 200 when all dependencies live
```

### 6.6 Database Initialisation

Alembic migrations run automatically on backend startup. All tables, indexes, and seed data (default users, seeded runbooks, event taxonomy) are created on first run. No manual step is required.

### 6.7 Docker Compose Configuration

**Restricting port binding to localhost:**
```yaml
services:
  postgres:
    ports:
      - "127.0.0.1:5432:5432"   # was "5432:5432"
```

**Adding resource limits:**
```yaml
services:
  celery_worker:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
```

**Applying compose changes:**
```bash
docker compose up -d                              # apply service definition changes
docker compose up -d --no-deps celery_worker      # restart one service only
```

---

## 7. First Login & Initial Setup

### 7.1 Access Points

| URL | Purpose |
|---|---|
| `http://localhost:3000` | Main operations UI |
| `http://localhost:8000/docs` | Swagger / OpenAPI interactive API docs |
| `http://localhost:5555` | Celery Flower task queue monitor |
| `http://localhost:7474` | Neo4j CMDB browser |

### 7.2 Default Accounts

| Role | Default Email | Default Password | Capabilities |
|---|---|---|---|
| `admin` | `admin@platform.local` | `admin` | Full access including user management |
| `itom_admin` | `itomadmin@platform.local` | `ITOMAdmin@1234!` | All operational capabilities, no user management |
| `operator` | `operator@platform.local` | `Operator@1234!` | Approve/reject remediations, manage policies |
| `viewer` | `viewer@platform.local` | `Viewer@1234!` | Read-only access |

> **Change all default passwords immediately.** Navigate to Users → select user → Edit → set new password.

### 7.3 Settings Accordions

Navigate to **Settings** in the left sidebar.

#### Environment Settings

| Field | Effect |
|---|---|
| Active Environment | Label shown in the header and used in policy matching (production / staging / development) |
| Debug Mode | Enables verbose backend logging — do not leave enabled in production |

#### Incident Qualification

| Field | Effect |
|---|---|
| Qualification Threshold | 0–100 score an event must reach to become an incident. Lower = more sensitive. Default: 50. |
| Confidence Threshold | Minimum detection confidence % required from the watcher. Default: 60%. |
| Severity Thresholds | Risk score ranges that map to Info / Warning / Critical severity |
| Event Type Multipliers | Per-event-type score multipliers applied before threshold comparison |
| Environment Multipliers | Per-environment damping factors (`production`=1.0, `staging`=0.6, `qa`=0.4, `development`=0.3, `test`=0.2). Environment is read from the CI's `environment` attribute in the CMDB — it cannot be set manually here. |
| Unknown CI Behavior | Policy applied when the alerting resource is not found in CMDB: `qualify_normal` (default), `qualify_as_low` (cap score), or `dismiss`. |

> **Environment standardisation:** the `environment` value used for scoring comes exclusively from the CI record in CMDB. Only the canonical names (`production`, `staging`, `development`, `test`, `qa`, `unknown`) appear in this grid. If a CI carries a non-standard environment value it is treated as `unknown` (×0.75).

#### Risk Assessment

Configure the weight for each factor contributing to the 0–100 **post-incident risk score** (computed by RiskAssessorAgent after the incident is opened — distinct from the qualification score used to decide whether to open one):

| Factor | What it Measures |
|---|---|
| Event Severity | Base score from the raw event severity |
| CI Tier | Criticality tier of the affected CI (tier 1 = most critical) |
| Deployment Environment | Higher score for production vs staging vs dev |
| Business Criticality | CI `business_criticality` attribute |
| User Impact | Number or scope of users affected |
| Blast Radius | Count of dependent CIs that would be impacted |
| Failover Availability | Whether a failover exists (reduces score if yes) |
| Single Point of Failure | Whether the CI has no redundancy (increases score if true) |
| SLA Compliance | Current SLA state (increases score if breached) |

> **Environment multipliers** in this section affect the post-incident risk score. The same canonical environment names apply (`production`, `staging`, etc.) — alias rows (`prod`, `stage`, `dev`) are not shown here because environment is always read from the CMDB CI record.

#### Performance Settings

| Field | Default | Effect |
|---|---|---|
| Max Concurrent Pipelines | 10 | Maximum incident pipeline tasks running simultaneously |
| Agent Timeout (s) | 300 | Max seconds a single agent stage may run |
| Max Pipeline Retries | 2 | Auto-retry count for a failed pipeline stage |
| Retry Backoff (s) | 30 | Wait between retry attempts (exponential) |

#### Notification Settings

| Field | Effect |
|---|---|
| In-App Alerts | Bell-icon notifications for new incidents and approvals |
| Alert Sound | Audio alert in the browser when a critical incident is created |
| Desktop Notifications | OS-level browser notifications (requires permission grant) |
| Notification Retention (d) | Days read notifications are kept before purge. Default: 30. |

#### Backup Settings

| Field | Effect |
|---|---|
| Enable Auto Backup | Trigger scheduled backups on the configured cron schedule |
| Backup Schedule | Cron expression. Default: `0 2 * * *` (daily at 02:00 UTC) |
| Retention Days | Number of backup archives to keep. Default: 7. |
| Backup Path | Host path where archives are written. Default: `./backups/` |

#### Security Settings

| Field | Default | Effect |
|---|---|---|
| Session Timeout (min) | 480 | Idle session duration before logout |
| Min Password Length | 8 | Minimum character count for new passwords |
| Require Special Chars | Off | Enforce at least one special character |
| API Rate Limit (req/min) | 300 | Maximum requests per minute per IP |
| Max Login Attempts | 5 | Failed attempts before temporary lockout (15 min) |

#### Database Settings

| Field | Default | Effect |
|---|---|---|
| Connection Pool Size | 10 | SQLAlchemy pool size per backend process |
| Pool Overflow | 20 | Additional connections allowed above pool size during spikes |
| Query Timeout (s) | 30 | Maximum seconds a single database query may run |

---

## 8. User Management

Navigate to **Users** in the left sidebar (Admin role required). The page has two tabs: **Principals** and **Audit Log**.

### 8.1 Role Permissions

| Role | View | Approve | Edit Settings | Manage Users | API Access |
|---|---|---|---|---|---|
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `itom_admin` | ✓ | ✓ | ✓ | ✗ | ✓ |
| `operator` | ✓ | ✓ | ✗ | ✗ | ✓ |
| `viewer` | ✓ | ✗ | ✗ | ✗ | Read-only |
| `automation` | API | ✗ | ✗ | ✗ | API key only |

> The `automation` role is for API key principals used by integrations. These authenticate with `X-API-Key` header rather than JWT Bearer tokens.

### 8.2 Creating a User

Click **New Principal → type: User**. Enter name, email, role, and initial password. Check **Force Password Change** to require the user to set their own password on first login.

### 8.3 API Key Principals

Click **New Principal → type: API Key**. Enter a name and assign a role. Click **Create** and copy the generated key immediately — it is shown only once. Pass it in the `X-API-Key` request header. API keys do not expire but can be revoked by deleting the principal.

### 8.4 Audit Log

Records every login and logout event with actor email, source IP, timestamp, and result (success or failure).

### 8.5 Slack Role Enforcement

When Slack ChatOps is enabled, the bot resolves a Slack user's email to their platform account. Users without a platform account receive "Access restricted." Operators needing Slack access must have a platform account first.

---

## 9. LLM Provider Configuration

The LLM provider enables: AI Insights tab content, MechanicAgent Tier 4 novel runbook generation, storm root cause hypothesis, and full generative responses in the Virtual Chat assistant.

Navigate to **Settings → LLM Provider**.

### Supported Providers

| Provider | Default Model | Key Format | Notes |
|---|---|---|---|
| OpenAI | `gpt-3.5-turbo` | `sk-...` | Switchable to `gpt-4` or `gpt-4-turbo` |
| Anthropic | `claude-3-haiku-20240307` | `sk-ant-...` | Switchable to Sonnet or Opus |

### Configuration Steps

1. Expand the LLM Provider accordion in Settings
2. Select Provider and Model
3. Paste the API Key
4. Click **Test Connection** — the platform sends a minimal test prompt to verify
5. Click **Save Configuration** — the green "Configured" badge appears

### Operating Without an LLM

| Feature | Without LLM |
|---|---|
| AI Insights tab | Deterministic text derived from incident metadata |
| Novel runbook generation | Tier 4 skipped; falls back to Tier 5 generic diagnostic runbook |
| Storm root cause hypothesis | Rule-based text listing shared upstream CMDB dependencies |
| Virtual Chat | Data-query mode only — factual answers about incidents and platform state |

---

## 10. Connector Hub

The Connector Hub integrates existing monitoring tools with the qualification and AI pipeline. Navigate to **Connectors** in the left sidebar.

### Supported Connectors

| Connector | Direction | Notes |
|---|---|---|
| ServiceNow | Bidirectional | CMDB pull (5 CI classes) + incident push back on resolution |
| Splunk | Inbound | Token-authenticated webhook |
| Datadog | Inbound | Recovery events filtered automatically |
| Dynatrace | Inbound | Problem notification webhooks |
| Prometheus / Alertmanager | Inbound | Grouped and individual alert webhooks |
| PagerDuty | Inbound | Incident trigger webhooks |
| Zabbix | Inbound | Trigger problem notifications |

### Adding a Connector

1. Click **Configure** on the connector card
2. Fill in connector-specific credentials and settings
3. Copy the generated **Webhook URL** and configure it in your monitoring tool
4. Set a **Webhook Secret** — the platform validates HMAC-SHA256 signatures on every payload
5. Configure per-connector governance flags
6. Click **Save Configuration** then **Test Connection**

### Per-Connector Governance Flags

| Flag | Effect |
|---|---|
| Allow Auto Remediation | When off, all incidents from this connector require manual approval |
| Allow Storm Detection | When off, events from this connector are excluded from storm correlation |
| Default Criticality | Criticality assigned when the affected CI has no criticality set in the CMDB |

### ServiceNow Configuration

| Field | Where to Find It |
|---|---|
| Instance URL | `https://<instance>.service-now.com` |
| Username | Integration user with read/write on `cmdb_ci_*` and `incident` tables |
| Password | Integration user password |

Run the initial CMDB sync after saving:

```bash
curl -X POST http://localhost:8000/api/connectors/servicenow/sync \
  -H "Authorization: Bearer $TOKEN"
```

The sync pulls CI data for: `cmdb_ci_service`, `cmdb_ci_business_app`, `cmdb_ci_server`, `cmdb_ci_app_server`, and their relationships.

---

## 11. Slack ChatOps

Connects the AI Ops Assistant to Slack via Socket Mode. No public webhook URL is required. Navigate to **Settings → Slack ChatOps**.

### Creating the Slack App

- Create New App at `api.slack.com/apps` → From scratch
- Enable Socket Mode → generates the App-Level Token (`xapp-...`)
- Add Bot Token Scopes: `app_mentions:read`, `channels:join`, `chat:write`, `im:history`, `im:read`, `im:write`, `users:read`, `users:read.email`
- Install to workspace → copy the Bot User OAuth Token (`xoxb-...`)
- Copy the Signing Secret from Basic Information → App Credentials

### Configuration Steps

1. Toggle **Enable Slack ChatOps**
2. Paste Bot Token (`xoxb-...`), Signing Secret, App-Level Token (`xapp-...`)
3. Set Default Channel (e.g., `#incidents`)
4. Click **Test Connection** then **Save Slack Settings**
5. Restart the backend to activate Socket Mode:

```bash
docker compose restart backend
docker logs agentic_os_backend 2>&1 | grep SlackSocket
# Expected: [SlackSocket] Connected to Slack via Socket Mode
```

### Notification Toggles

| Toggle | Default | What it Sends |
|---|---|---|
| New Incident | On | Critical and high incidents with link |
| Incident Resolved | On | Terminal state transitions |
| Approval Required | On | Interactive message with Approve / Diagnostics Only / Reject buttons |
| Event Storm | On | Storm created with child count and AI root cause hypothesis |

### Slack Bot Commands

| Command | What it Does |
|---|---|
| `@bot incidents` | Lists active incidents |
| `@bot incident INC0001` | Shows detail for a specific incident |
| `@bot approve INC0001` | Approves the pending remediation (operator role required) |
| `@bot approve INC0001 diag` | Diagnostics Only — runs diagnostics, skips remediation |
| `@bot reject INC0001 <reason>` | Rejects with a reason recorded in the audit log |
| `@bot storms` | Lists active storms |
| `@bot help` | Lists all available commands |

---

## 12. Governance — Policies & Approved Actions

### 12.1 Policies

Policies control whether incidents require approval before remediation and which actions are permitted. Navigate to **Policies** in the left sidebar. Policies are evaluated in descending priority order — the first matching policy wins.

**Match Criteria:**

| Field | Effect |
|---|---|
| Minimum Severity | Minimum incident severity to trigger this policy |
| Environment | Restrict to a specific environment label (blank = match all) |
| Minimum Risk Score | 0–100 lower bound |
| Maximum Risk Score | 0–100 upper bound (blank = no upper bound) |
| Specific Service | Pin to one service name (blank = catch-all) |

**Enforcement Fields:**

| Field | Effect |
|---|---|
| Requires Manual Approval | Matching incidents pause at the Approval Queue |
| Allow All Remediation Actions | When unchecked, restrict to a specific action allowlist |
| Approval Priority | Higher number = evaluated first. Default: 50. |

**Recommended initial policy set:**

1. Production + Critical severity: Requires Approval = true, restrict to safe actions only
2. Production + High severity: Requires Approval = true, allow all actions
3. Staging and dev: Requires Approval = false, allow all actions

### 12.2 Approved Actions Catalogue

The Approved Actions catalogue is the hard allowlist of tools ToolRegistryAgent may execute. Navigate to **Actions** in the left sidebar.

Three categories:
- **Diagnostic** — read-only data collection, never requires approval
- **Remediation Safe** — low blast-radius mutations (restart a process, clear a cache)
- **Remediation Intrusive** — high blast-radius (drain a node, scale down, restart a host) — always requires secondary approval

Each action defines command strings for each adapter mode (`docker` / `ssh` / `kubernetes` / `vmware` / `aws` / `azure`). The correct variant is selected at runtime based on the target CI's `adapter_mode` attribute.

**Process Allow / Deny Rules:** Each action can define a process allowlist (only these process names may be targeted) and a denylist (these names are always blocked).

> Actions not listed in the catalogue cannot be executed — this is a hard gate in ToolRegistryAgent with no override.

---

## 13. Storm Detection

Storm detection identifies correlated bursts of incidents sharing a root cause. Navigate to **Event Storms** in the left sidebar.

### Storm Agent Settings

Navigate to **Settings → Storm Agent**.

| Setting | Default | Description |
|---|---|---|
| Detection Window (s) | 120 | Look-back window for correlated burst detection |
| Minimum Incidents | 3 | Minimum incidents in window to trigger storm analysis |
| Minimum Resources | 2 | Minimum distinct resources to qualify as a storm |
| Storm Merge Window (s) | 5 | Concurrent detections within this window merge into one storm |
| Pipeline Hold Buffer (s) | 0 | Delay new incident pipelines N seconds to allow storm grouping |

**Behaviour Settings:**

| Setting | Effect |
|---|---|
| Require CAB Approval | Storm parent incidents require CAB approval regardless of child policies |
| Auto-Hold Children | Child incidents are blocked from individual execution while the storm is active |
| Exclude External Connector Events | Connector events excluded from storm detection |

**Root Cause Analysis Settings:**

| Setting | Effect |
|---|---|
| LLM Hypothesis | Generate a natural-language root cause hypothesis using the configured LLM |
| Neo4j Topology | Query CMDB for shared upstream CIs and show as Root Cause Candidates |

### Tuning for False Positives

- Increase Minimum Incidents threshold (try 4 or 5)
- Decrease Detection Window (try 60 seconds)
- Enable **Exclude External Connector Events** on connectors doing batch imports
- Set `allow_storm_detection = false` on specific noisy connectors

---

## 14. CMDB — Neo4j Graph & ServiceNow Sync

The CMDB stores CI topology, drives blast radius calculation, storm root cause analysis, and AI context enrichment. Navigate to **CMDB** in the left sidebar.

### 14.1 Node Labels and Attributes

| Label | Represents |
|---|---|
| `ConfigurationItem` | Base label on every node |
| `Service` | Application service or API |
| `Database` | Database instance |
| `Infrastructure` | Host, VM, load balancer, switch |
| `Container` | Docker container (auto-discovered) |

**Key CI Attributes:**

| Attribute | Used For |
|---|---|
| `name` | Unique identifier; matches Docker container name or ServiceNow `sys_name` |
| `type` | CI type (service / database / infrastructure / container) |
| `business_criticality` | Business criticality (tier_1 / tier_2 / tier_3) — feeds risk score |
| `environment` | Deployment environment — used in policy matching |
| `ci_tier` | CI tier (1 = most critical) — feeds risk score |
| `platform` | Underlying platform (docker, kubernetes, linux, windows) — selects remediation adapter |
| `failover_available` | Whether a failover exists — reduces risk score when true |
| `sla_percent` | SLA availability target — affects risk score when breached |
| `is_spof` | Single Point of Failure flag — increases risk score |
| `user_count` | Approximate users affected — drives business impact score |

**ITOps Routing Attributes** (added v1.3):

| Attribute | Used For |
|---|---|
| `support_group` | Team that handles operational support for this CI — synced from ServiceNow `support_group` |
| `assignment_group` | Team that incidents are routed to for this CI — platform-managed, not synced from SN |
| `managed_by` | Manager or operational contact — synced from ServiceNow `managed_by` |
| `data_center` | Data centre or cloud region (e.g. `us-east-1`, `dc-london-01`) — synced from ServiceNow `location` |

**Relationship Types:**

| Relationship | Meaning |
|---|---|
| `DEPENDS_ON` | A depends on B — B failure propagates impact to A |
| `RUNS_ON` | Service runs on a host or VM |
| `HOSTED_ON` | Container is hosted on a host |
| `PART_OF` | CI is a component of a larger system |
| `CONNECTS_TO` | Network dependency |

### 14.2 Neo4j Direct Access

```
Browser: http://localhost:7474
Default credentials: neo4j / agentic_os_neo4j
```

**Useful Cypher queries:**

```cypher
// All CIs
MATCH (n:ConfigurationItem) RETURN n.name, n.type, n.criticality LIMIT 50

// Blast radius for a CI
MATCH (n {name:"payment-service"})<-[:DEPENDS_ON*1..5]-(dep)
RETURN dep.name, dep.type ORDER BY dep.criticality

// CIs missing adapter_mode (cannot execute remediation)
MATCH (n:ConfigurationItem) WHERE n.adapter_mode IS NULL RETURN n.name, n.type
```

### 14.3 CMDB Editor (UI)

Navigate to **CMDB → Editor tab** for an in-platform way to view and edit CI records without opening Neo4j directly.

#### Layout

The editor is a two-panel view:
- **Left panel** — searchable CI list. Click any row to load its detail.
- **Right panel** — CI detail with all attributes and relationships.

#### Viewing CIs

All users can browse CIs and follow relationship chips to navigate between connected records. Relationship chips appear at the bottom of the detail panel; clicking one loads that CI.

#### Editing CIs (`admin` and `itom_admin` roles)

Click **Edit** to enter edit mode. The following attributes are editable:

**Standard fields:**

| Field | Notes |
|---|---|
| `type` | CI type (service, database, infrastructure, container) |
| `status` | operational / degraded / maintenance / decommissioned |
| `environment` | Must use canonical values: `production`, `staging`, `development`, `test`, `qa`. This drives qualification scoring — set it correctly. |
| `owner` | Team or individual responsible |
| `description` | Free-text description |
| `business_criticality` | tier_1 / tier_2 / tier_3 |
| `ci_tier` | 1 (most critical) to 3 |
| `platform` | Underlying platform (kubernetes, docker, linux, windows) |
| `is_spof` | Single Point of Failure flag — increases risk score |
| `failover_available` | Reduces risk score when true |
| `user_count` | Approximate number of users affected by an outage |
| `sla_percent` | SLA target — affects risk score when breached |

**ITOps routing fields** (v1.3):

| Field | Notes |
|---|---|
| `support_group` | Operational support team (auto-populated from ServiceNow if synced) |
| `assignment_group` | Team incidents are routed to — set manually, used for manual assignment routing |
| `managed_by` | Manager or primary contact (auto-populated from ServiceNow if synced) |
| `data_center` | Data centre / cloud region (auto-populated from ServiceNow `location` if synced) |

**Custom fields** (v1.3):

Operators can define organisation-specific fields directly on any CI. Custom fields are stored in Neo4j with a `u_` prefix (following the ServiceNow convention for user-defined fields).

To add a custom field:
1. Enter edit mode and scroll to **Custom Fields**.
2. Click **+ Define field**.
3. Enter a display name (e.g. `Cost Center`) — the internal key is auto-derived (`u_cost_center`) and shown read-only.
4. Select a field type: **Text**, **Number**, **Date**, **Boolean (Yes/No)**, or **URL**.
5. Click **Add** — the field appears immediately in the Custom Fields section.
6. Enter the value and click **Save**.

Custom fields appear with a violet left-border strip and a `custom` chip to distinguish them from platform-managed fields. URL-type fields render as clickable links in read mode.

To remove a custom field, click **×** beside it in edit mode and save. The property is fully removed from the Neo4j node.

Read-only fields (populated by discovery or live metrics): `health_status`, `container_status`, `docker_image`, `ip_address`, `exposed_ports`, `cpu_percent`, `memory_mb`, `discovery_source`, `last_discovered_at`.

#### Creating a CI (`admin` and `itom_admin` roles)

Click **+ New CI** to open the Create CI modal. Provide at minimum: `name`, `type`, `environment`. The optional fields `support_group`, `assignment_group`, `managed_by`, and `data_center` can also be set at creation time. The CI is created with `discovery_source = manual`.

#### Decommissioning a CI (`admin` role only)

Click **Decommission** on any CI that is not already decommissioned. This is a soft-delete: it sets `status = decommissioned` and the CI remains in the graph for audit and blast-radius history. Hard-delete requires direct Neo4j access.

> Decommissioning a CI does not automatically update the environment multiplier tables — manually verify that no active monitoring checks target the decommissioned resource.

### 14.4 Adding CIs via Cypher

```cypher
CREATE (s:ConfigurationItem:Service {
  name: "payment-service", type: "service",
  criticality: "critical", environment: "production",
  tier: 1, adapter_mode: "kubernetes"
})

MATCH (a {name:"payment-service"}), (b {name:"agentic_os_postgres"})
CREATE (a)-[:DEPENDS_ON]->(b)
```

### 14.6 CMDB Coverage

Platform Intelligence (§18) tracks CMDB Coverage — the % of CIs with all attributes needed for full risk scoring. Low coverage reduces risk score accuracy.

---

## 15. Runbook Management

Runbooks define the automated remediation procedure MechanicAgent selects and executes. The platform ships with a seeded library. Navigate to **Runbook Library** in the left sidebar.

### 15.1 Seeded Runbook Categories

| Category | Event Types Covered |
|---|---|
| Container restarts | `container.availability.container_crash_loop`, `container_stopped` |
| OOM kills | `container.resource.oom_killed`, `infrastructure.memory.memory_surge` |
| CPU spike | `infrastructure.compute.cpu_high`, `container.resource.cpu_throttling` |
| Disk pressure | `infrastructure.storage.disk_full`, `container.storage.volume_full` |
| Service unresponsive | `application.availability.service_unresponsive`, `service_down` |
| Database performance | `database.performance.slow_query`, `deadlock_detected` |
| Database connectivity | `database.connectivity.connection_pool_exhausted`, `connection_refused` |
| Network | `network.connectivity.packet_loss`, `connection_timeout` |
| Log spike | `log.volume.error_spike`, `exception_flood` |
| High syscall intensity | `infrastructure.kernel.high_syscall_intensity` |
| Generic diagnostic | Tier 5 fallback — diagnostics only for any unmatched event type |

### 15.2 Visual Workflow Editor

Navigate to **Runbook Editor** in the left sidebar, or click **Edit** on any runbook card.

**Node Types:**

| Node Type | Purpose | Approval Behaviour |
|---|---|---|
| Diagnostic | Read-only data collection | Never requires approval |
| Action | Mutating remediation step | Intrusive category always requires secondary approval |
| Verification | Validates outcome of a prior Action | Runs automatically after its Action |
| Decision | Branches on a condition expression | Evaluates condition; routes to True or False edge |
| Notify | Sends Slack/webhook notification mid-workflow | Non-blocking |

**Connecting Nodes:** Drag from the output port (right side) to the input port (left side). Decision nodes have two output ports: True (green) and False (red).

**Output Variable Capture:** Each Diagnostic and Action node has an Output Capture section. Configure:

| Field | Description |
|---|---|
| Variable Name | Name of the captured variable (e.g., `cpu_usage`) |
| Source | `stdout` or `stderr` |
| Pattern | Regex with one capture group |
| Type | `string`, `integer`, `float`, or `boolean` |

Example — capture CPU usage from output `CPU: 87.3%`:
```
Variable Name: cpu_usage   Source: stdout
Pattern: CPU: ([0-9.]+)%   Type: float
```

**Decision Node Conditions:**

| Syntax | Example |
|---|---|
| `variable == value` | `deadlock_detected == true` |
| `variable != value` | `error_count != 0` |
| `variable > / < / >= / <=` | `cpu_usage > 80.0` |
| `variable contains string` | `process_name contains nginx` |
| `variable is null` | `replica_count is null` |

**Verification Node Wiring:** Connect a Verification node to an Action node. Configure a success condition expression. If it evaluates to `true`, ValidationAgent considers the action successful; if `false`, rollback is triggered.

**Live Test Execution:** Click **Test Run** in the editor toolbar. Select a target incident or enter a test CI name and event type. Each node animates in real time.

### 15.3 Creating a Runbook via Form Editor

| Field | Description |
|---|---|
| Name | Unique identifier shown in the library and agent logs |
| Event Type | The event type code this runbook handles |
| Platform | `any`, `docker`, `linux`, `windows`, or `kubernetes` |
| Service | Blank = catch-all; set to pin to one service (enables Tier 1 matching) |
| Risk Level | 1 (diagnostics only) to 5 (highly destructive) |
| Description | Plain-text description — used by MechanicAgent Tier 3 semantic similarity |

### 15.4 Importing and Exporting

Export via the runbook card three-dot menu → **Export** (downloads JSON). Import via **Import** at the top of the Runbook Library. The JSON includes all node definitions, edges, capture rules, and metadata.

### 15.5 Disabling a Runbook

Toggle the **Enabled** switch on the runbook card. Disabled runbooks are excluded from all MechanicAgent tiers. History is preserved. Prefer disabling over deleting.

---

## 16. Event Type Taxonomy

The event type taxonomy defines the canonical event type strings used throughout the platform. Navigate to **Event Types** in the left sidebar.

### Taxonomy Structure

Event types follow the pattern: `domain.subdomain.event_name`

| Domain | Subdomains | Example Types |
|---|---|---|
| `infrastructure` | compute, memory, storage, kernel, network | `infrastructure.compute.cpu_high`, `infrastructure.kernel.high_syscall_intensity` |
| `container` | availability, resource, storage | `container.availability.container_crash_loop`, `container.resource.oom_killed` |
| `application` | availability, performance | `application.availability.service_unresponsive` |
| `database` | performance, connectivity | `database.performance.slow_query`, `database.connectivity.connection_pool_exhausted` |
| `network` | connectivity, latency | `network.connectivity.packet_loss` |
| `log` | volume, pattern | `log.volume.error_spike`, `log.pattern.exception_flood` |
| `security` | access, integrity | `security.access.unauthorized_access` |

### Adding Custom Event Types

1. Navigate to **Event Types** and click **New Event Type**
2. Enter the full dotted name (`domain.subdomain.event_name`)
3. Add a description (used by MechanicAgent Tier 3 semantic search)
4. Set a default risk score multiplier (0.1–3.0; 1.0 = no change)
5. Click **Save**

### Connector Event Mapping

Each connector normalises its raw alert format to a platform event type under the connector's **Event Mapping** settings. Unmapped events are processed as `application.availability.service_unresponsive` by default.

---

## 17. Virtual Chat — AI Ops Assistant

The Virtual Chat assistant gives operators natural-language access to platform data, incident context, and operational guidance. Click the chat icon in the bottom-right corner to open it.

### Capabilities

| Capability | Example Queries |
|---|---|
| Incident queries | "Show me all critical incidents in production in the last 24 hours" |
| Incident detail | "What is the root cause hypothesis for INC0042?" |
| CMDB queries | "Which services depend on agentic_os_postgres?" |
| Runbook guidance | "What runbook handles cpu_high events for linux servers?" |
| Platform status | "How many incidents were auto-remediated successfully this week?" |
| Storm analysis | "Tell me about the active storm and which CIs are involved" |
| LLM-powered insights | Open-ended analysis and cause-and-effect reasoning (requires LLM) |

### Data Access Boundaries

The assistant queries the platform's internal API using the authenticated user's session token. It respects role permissions — a viewer cannot obtain data they cannot access through the UI. No outbound calls to external systems.

### Without an LLM

In data-query mode, the assistant answers structured factual questions by querying PostgreSQL and Neo4j. Queries requiring reasoning return a message prompting you to configure an LLM provider.

### With an LLM

When an LLM is configured (§9), the assistant gains full generative capability: comparative analysis, timeline reconstruction, remediation recommendations, and plain-language explanations of complex system events. Context is maintained within the chat session.

> Chat history is session-only and not persisted. Each new session starts with no prior context.

---

## 18. Platform Intelligence

Platform Intelligence aggregates operational metrics, reliability KPIs, and system health. Navigate to **Platform Intel** in the left sidebar.

### Analytics Overview

- Incident volume over time (hourly/daily/weekly buckets with trend line)
- Resolution rate % (auto-resolved vs human-resolved vs open)
- Mean time to detect (MTTD) and mean time to resolve (MTTR) with week-over-week comparison
- Top 5 services by incident count and top 5 event types
- Auto-remediation success rate breakdown by runbook

### System Health

- Agent pipeline health: stage-by-stage throughput, error rate, and p50/p95 latency
- Celery queue depths (WORKFLOWS, DEFAULT, APPROVALS) with active worker count
- CMDB Coverage: % of CIs with complete attributes for full risk scoring
- Connector health: last event received timestamp and payload error rate

### KPI Dashboard

Configurable KPI tiles that track operator-defined SLOs. Click **Add KPI** to define a target (e.g., MTTR < 15 min, Auto-remediation rate > 70%). Current value vs target is shown with RAG (red/amber/green) status.

### Audit Trail

Full audit trail of: all incident state transitions, approval decisions with actor and reason, runbook executions with success/failure, and settings changes (actor, field, old value, new value). Filterable by date range, actor, and event type. Exportable as CSV.

---

## 19. Environment Variables Reference

All configuration is done through `.env`. Variables set in Settings UI take precedence over `.env` for LLM and Slack settings (they are written to the database). All others require a service restart to apply.

### Required Variables

| Variable | Description |
|---|---|
| `JWT_SECRET` | HMAC secret for signing JWT tokens. Generate: `openssl rand -hex 32` |
| `WATCHER_API_KEY` | Shared key for watcher_brain authentication. Generate: `openssl rand -hex 32` |
| `DATABASE_URL` | PostgreSQL DSN. Default: `postgresql://agentic_os_user:agentic_os_pass@postgres:5432/agentic_os_db` |
| `NEO4J_URI` | Neo4j Bolt URI. Default: `bolt://neo4j:7687` |
| `NEO4J_USER` | Neo4j username. Default: `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password. Default: `agentic_os_neo4j` |
| `REDIS_URL` | Redis DSN. Default: `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | Celery broker. Default: `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Celery results. Default: `redis://redis:6379/0` |

### Optional Variables

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key for LLM integration | (empty — LLM disabled) |
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM integration | (empty — LLM disabled) |
| `LLM_MODEL` | Default model ID to use with the provider | `gpt-3.5-turbo` |
| `SLACK_BOT_TOKEN` | Slack Bot Token (`xoxb-...`) | (empty — Slack disabled) |
| `SLACK_SIGNING_SECRET` | Slack app signing secret | (empty) |
| `SLACK_APP_TOKEN` | Slack App-Level Token for Socket Mode (`xapp-...`) | (empty) |
| `DEFAULT_SLACK_CHANNEL` | Default channel for incident notifications | `#incidents` |
| `CORS_ORIGINS` | Comma-separated allowed origins for CORS | `http://localhost:3000` |
| `LOG_LEVEL` | Backend log level: DEBUG/INFO/WARNING/ERROR | `INFO` |
| `MAX_WORKERS` | Celery worker concurrency | `4` |
| `WORKER_PREFETCH_MULTIPLIER` | Celery prefetch multiplier | `1` |

### Applying Changes

```bash
docker compose restart backend celery_worker     # most variables
docker compose restart backend                   # JWT_SECRET, LLM, Slack
docker compose restart celery_worker             # MAX_WORKERS, PREFETCH_MULTIPLIER
docker compose up -d                             # new infrastructure variables (DATABASE_URL, REDIS_URL)
```

---

## 20. Day-2 Operations

### 20.1 Monitoring Platform Health

Celery Flower provides a live view of task queues, worker status, and task history at `http://localhost:5555`.

| Metric to Watch | Warning Threshold | Action |
|---|---|---|
| WORKFLOWS queue depth | > 50 | Scale celery_worker: `docker compose up -d --scale celery_worker=2` |
| Backend container restarts | > 3/hour | Check `docker logs agentic_os_backend` for recurring error |
| Neo4j heap usage | > 80% | Increase `NEO4J_dbms_memory_heap_max_size` in compose file |
| PostgreSQL connections | > 80% of max | Increase Connection Pool Size in Settings → Database |

### 20.2 Common Maintenance Tasks

```bash
# Restart a single service
docker compose restart celery_worker

# View recent logs
docker logs agentic_os_backend --tail 100 -f
docker logs celery_worker --tail 100 -f

# Check database
docker exec agentic_os_postgres psql -U agentic_os_user agentic_os_db \
  -c "SELECT incident_number_str, lifecycle_state FROM incidents ORDER BY created_at DESC LIMIT 20;"
```

### 20.3 Log Monitors

Log Monitors continuously scan container stdout and stderr for pattern matches. They create synthetic incidents through the full qualification and agent pipeline. Navigate to **Log Monitors** in the left sidebar.

| Field | Description |
|---|---|
| Monitor Name | Display name for the monitor |
| Target Container | Container name to tail (e.g., `agentic_os_backend`) |
| Pattern | Regex pattern to match against log lines |
| Event Type | Platform event type to generate on match |
| Cooldown (s) | Minimum seconds between incidents for the same pattern. Default: 120. |
| CI Override | Force-assign the synthetic incident to a specific CI |

**Recommended monitors:**

| Target | Pattern | Event Type | Cooldown |
|---|---|---|---|
| `celery_worker` | `CRITICAL\|Exception\|Traceback` | `log.pattern.exception_flood` | 120 |
| `agentic_os_backend` | `OperationalError\|pool.*timeout` | `database.connectivity.connection_pool_exhausted` | 300 |
| `agentic_os_backend` | `50[0-9] Internal Server Error` | `application.availability.service_unresponsive` | 60 |

### 20.4 External Connectivity Checks

Perform scheduled HTTP/HTTPS probes against external URLs. Navigate to **Connectivity Checks** in the left sidebar.

| Field | Description |
|---|---|
| Check Name | Display name |
| URL | Target URL (reachable from the backend container) |
| Interval (s) | Probe frequency. Default: 60. |
| Timeout (s) | HTTP request timeout. Default: 10. |
| Expected Status | HTTP status code that counts as healthy. Default: 200. |
| Event Type | Event type to generate on failure. Default: `network.connectivity.packet_loss`. |
| CI Override | CI to attach the generated incident to |

### 20.5 Scheduled Tasks

| Task | Default Schedule | Purpose |
|---|---|---|
| `watcher_health_check` | Every 30 seconds | Verify all watcher_brain adapters are reachable |
| `cmdb_discovery_cycle` | Every 2.5 minutes | Docker container discovery and CMDB graph update |
| `incident_timeout_check` | Every 5 minutes | Transition stale `pending_approval` incidents to `timed_out` |
| `connectivity_probe_cycle` | Configurable | Execute all enabled External Connectivity Checks |
| `log_monitor_cycle` | Every 30 seconds | Tail container logs and match patterns |
| `backup_cycle` | Configurable cron | Execute database and volume backup if auto-backup is enabled |
| `audit_log_purge` | Daily at 03:00 UTC | Purge audit log entries older than retention period |

---

## 21. Backup & Recovery

### 21.1 What Is Backed Up

| Component | Backup Method | Contents |
|---|---|---|
| PostgreSQL | `pg_dump` (SQL format) | All incidents, users, policies, actions, settings, audit log |
| Neo4j | `neo4j-admin database dump` | All CMDB CI nodes and relationships |
| Redis | BGSAVE `.rdb` snapshot | Celery task state, session cache |
| Config | `.env` and `docker-compose.yml` | All deployment configuration |

### 21.2 Manual Backup

```bash
curl -X POST http://localhost:8000/api/admin/backup \
  -H "Authorization: Bearer $TOKEN"
```

The backup is written to the Backup Path configured in Settings (default: `./backups/`) as a timestamped `tar.gz` archive.

**PostgreSQL backup directly:**
```bash
docker exec agentic_os_postgres pg_dump -U agentic_os_user agentic_os_db \
  | gzip > backup_$(date +%Y%m%d).sql.gz
```

**Neo4j backup:**
```bash
docker stop agentic_os_neo4j
docker run --rm -v agentic_os_neo4j_data:/data -v $(pwd)/backup:/backup \
  alpine tar czf /backup/neo4j_$(date +%Y%m%d).tar.gz /data
docker start agentic_os_neo4j
```

### 21.3 Restore Procedure

> **WARNING:** Restore replaces current data. Ensure no active incident pipelines are running before restoring.

```bash
# 1. Stop services (keeps volumes intact)
docker compose stop backend celery_worker

# 2. Restore PostgreSQL
docker exec -i agentic_os_postgres psql -U agentic_os_user agentic_os_db < backup/pg_dump.sql

# 3. Restore Neo4j
docker exec agentic_os_neo4j neo4j-admin database load neo4j \
  --from-path=/backups/neo4j_dump --overwrite-destination

# 4. Restore Redis
docker cp backup/redis.rdb agentic_os_redis:/data/dump.rdb

# 5. Restart services
docker compose start backend celery_worker
```

### 21.4 Docker Volume Management

```bash
docker volume ls | grep agentic          # list platform volumes
docker volume inspect postgres_data       # show mount point
```

> **WARNING:** Never run `docker compose down -v` in production. Use `docker compose down` (no `-v`) to stop and remove containers while preserving data.

---

## 22. Upgrading

### 22.1 Upgrade Prerequisites

- Review the release notes for the target version — breaking schema changes are flagged there
- Take a full backup (§21.2) before starting
- Check that all pending approval incidents are either approved or rejected

### 22.2 Minor Version Upgrade (no schema change)

```bash
git pull
docker compose build backend
docker compose up -d backend celery_worker
```

Alembic runs on startup and confirms no migrations to apply.

### 22.3 Major Version Upgrade (schema change)

```bash
git pull
docker compose stop backend celery_worker
# Take a backup
curl -X POST http://localhost:8000/api/admin/backup -H "Authorization: Bearer $TOKEN"
docker compose build backend
docker compose up -d backend
# Watch migration
docker logs agentic_os_backend -f | grep alembic
# Confirm: "Running upgrade ... -> done"
docker compose up -d celery_worker
```

### 22.4 Rolling Back

```bash
docker compose stop backend celery_worker
# Edit docker-compose.yml to pin backend image to previous tag
docker compose up -d backend celery_worker
```

If the schema changed, restore the PostgreSQL backup before restarting the old backend.

---

## 23. Security Hardening

### 23.1 Network Perimeter

- Bind database ports to `127.0.0.1` (§6.7)
- Place the backend (8000) and frontend (3000) behind a TLS-terminating reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name platform.company.com;
    ssl_certificate     /etc/ssl/certs/platform.crt;
    ssl_certificate_key /etc/ssl/private/platform.key;

    location / { proxy_pass http://localhost:3000; }
    location /api/ { proxy_pass http://localhost:8000; }
}
```

- Restrict Flower (5555) and Neo4j Browser (7474) to admin IPs via firewall or nginx `auth_basic`
- Set `CORS_ORIGINS` to your exact frontend domain

### 23.2 Secrets Management

- Rotate `JWT_SECRET` and `WATCHER_API_KEY` quarterly
- Store `.env` in a secrets manager (AWS Secrets Manager, HashiCorp Vault) for production
- Never commit `.env` to source control — verify `.gitignore` includes `.env`
- Rotate LLM provider API keys if they appear in any log

### 23.3 Authentication

- Force all users to change their initial password on first login (**Force Password Change** checkbox)
- Set session timeout to 60 minutes for production (Settings → Security)
- Enable Max Login Attempts (5) to prevent brute-force attacks
- Audit the Principals list quarterly and remove inactive accounts

**JWT secret rotation:**
```bash
openssl rand -hex 32   # generate new secret
# Update JWT_SECRET in .env
docker compose restart backend   # all existing sessions will be invalidated
```

### 23.4 RBAC — Principle of Least Privilege

- Assign `viewer` to read-only stakeholders
- Assign `operator` to on-call engineers who need to approve incidents
- Reserve `itom_admin` and `admin` for platform administrators
- Use API key principals with `automation` role for webhook integrations — never share admin JWT tokens

### 23.5 eBPF and Container Security

`sentinel_senses` requires `CAP_SYS_ADMIN` and `CAP_BPF` (or `privileged: true`). Review the minimal capability set required for your kernel version. On Kubernetes, use a dedicated `SecurityContext` rather than privileged mode.

### 23.6 Audit Log Review

Review the Audit Log (§8.4) monthly for failed login bursts, login from unexpected IPs, and API key usage from unexpected clients. Review the Platform Intelligence Audit Trail (§18) for policy bypasses or unusual approval patterns.

### 23.7 Webhook Security

For each inbound connector, set a **Webhook Secret** (HMAC-SHA256). This prevents unauthenticated actors from injecting fake alerts. Configure in **Connectors → [Connector] → Webhook Secret**.

---

## 24. Troubleshooting

### 24.1 Service Will Not Start

| Symptom | Probable Cause | Resolution |
|---|---|---|
| Backend exits immediately | Missing `JWT_SECRET` or `WATCHER_API_KEY` | Run `install.sh`; check `docker logs agentic_os_backend` |
| Database connection refused | PostgreSQL not ready or wrong `DATABASE_URL` | `docker compose ps` — postgres must be healthy first |
| Neo4j connection timeout | Neo4j still initialising (60–90s for fresh volume) | Wait and re-run `docker compose up -d backend` |
| celery_worker exits after start | Invalid `CELERY_BROKER_URL` or Redis not ready | `docker compose ps redis`; check `REDIS_URL` in `.env` |

### 24.2 Incidents Not Created From Webhooks

1. Check the connector is enabled: Connectors page → status badge
2. Verify the Webhook URL and Secret match what the monitoring tool sends
3. Check signature validation: `docker logs agentic_os_backend | grep "signature"`
4. Check the Qualification Threshold — the event may be scoring below it
5. Check the event type multiplier — a `0.1` multiplier will suppress almost all events
6. Verify the connector's Allow Auto Remediation and Allow Storm Detection flags

### 24.3 Runbook Execution Fails

| Symptom | Resolution |
|---|---|
| Action step fails: command not found | Check the action's command is correct for the container OS; verify `adapter_mode` on the CI |
| Action step fails: permission denied | The watcher_brain adapter needs elevated permissions; check Docker socket mount |
| Action step times out | Increase Agent Timeout in Settings → Performance |
| Verification step always fails | Verify the output capture regex matches actual command output; use Test Run in the editor |
| Tier 4 never selects a runbook | LLM is not configured or the API key has expired; check Settings → LLM Provider |

### 24.4 Storm Detection Creates Too Many False Storms

- Increase Minimum Incidents threshold (Settings → Storm Agent)
- Reduce Detection Window
- Enable **Exclude External Connector Events** for batch-import connectors
- Disable storm detection per connector (Connectors → Allow Storm Detection toggle)

### 24.5 CMDB Graph Empty or Stale

- Verify watcher_brain is running: `docker compose ps sentinel_brain`
- Check watcher logs: `docker logs sentinel_brain --tail 100`
- Verify `WATCHER_API_KEY` matches in backend `.env`
- For ServiceNow CMDB: trigger a manual sync (§10)
- Navigate to CMDB and confirm the Node Labels filter includes the expected CI types

### 24.6 Approval Queue Shows No Items

Incidents requiring approval will not appear if: no policy with **Requires Manual Approval** is active, or the incident risk score is outside the policy's score range. Check **Settings → Policies** and verify the policy priority order.

### 24.7 Getting Support

Collect the following for a support request:

```bash
docker compose logs --no-color > platform_logs.txt
docker compose ps
git log --oneline -1    # platform version
```

Also include: the incident ID and event type if the issue is incident-specific, and `.env` (redact all secrets and API keys before sharing).

---

*For architecture details, see [ARCHITECTURE.md](./ARCHITECTURE.md).*  
*For Slack setup, see [SLACK_SETUP.md](./SLACK_SETUP.md).*  
*For storm detection internals, see [storm-detection.md](./storm-detection.md).*  
*For the complete API reference, see [API_REFERENCE.md](./API_REFERENCE.md).*  
*For the visual runbook editor guide, see [VISUAL_RUNBOOK_EDITOR.md](./VISUAL_RUNBOOK_EDITOR.md).*
