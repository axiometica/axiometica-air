# Axiometica AIR v1.2.0

> Fundamental Intelligence for Autonomous Operations — enterprise AI-driven IT ops platform with autonomous incident detection, triage, enrichment, remediation, and resolution with operator-controlled governance.

[![Status](https://img.shields.io/badge/Status-v1.2.0-brightgreen)]()
[![License](https://img.shields.io/badge/License-Source%20Available-blue)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue)]()
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-blue)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green)]()
[![React](https://img.shields.io/badge/React-18+-61dafb)]()
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791)]()
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ed)]()
[![Community](https://img.shields.io/badge/Community-Testing-orange)]()
[![GitHub](https://img.shields.io/badge/GitHub-axiometica--air-333)](https://github.com/axiometica/axiometica-air)

**Release Date:** June 2026 | **Status:** Production Ready ✅

---

## Platform Overview

Axiometica AIR is an autonomous IT operations platform that detects, investigates, and resolves incidents in real-time. Using AI agents, deep infrastructure visibility, and team-controlled governance, it reduces MTTR from hours to minutes while maintaining full operator oversight.

Built for teams suffering from **alert fatigue**, **on-call burnout**, and the cost of manual incident response. Axiometica AIR is a self-hosted AIOps platform that closes the gap between detection and resolution — so your team focuses on the problems that actually need them.

---

## Why Axiometica AIR is Different: Agent-Native Architecture

### Not AI Bolted Onto Legacy Ticketing

Most "AI-powered" ITSM tools are decades-old ticketing systems with a chatbot added on top. **Axiometica AIR was built the opposite way:** agents are the primary worker from the ground up.

**Incidents don't wait in a queue for someone to pick them up — they're worked the moment they're detected.**

### How It's Different

| Aspect | Legacy ITSM + AI | Axiometica AIR |
|--------|---|---|
| **Architecture** | Ticket queue → engineer assigns → AI assists | Event stream → 7 agents investigate & act immediately |
| **Incident Response** | Engineers wait for alerts; AI provides suggestions | Agents detect, triage, score, remediate autonomously |
| **Decision Flow** | Sequential manual steps with AI widgets | Parallel agent pipelines with built-in governance gates |
| **Time to Resolution** | Hours to days (manually dependent) | Minutes (autonomous + approval gates) |
| **Learning** | Static AI models, manual tuning | Continuous feedback loop from runbook outcomes |
| **Governance** | Post-mortem approval (change was already made) | Pre-execution approval gates (control before action) |
| **Scale** | Headcount grows with infrastructure | Scales with agents, not headcount |

### Agent-Native Means

- **Sentinel Agent** — Classifies every anomaly the moment it's detected
- **Librarian Agent** — Enriches with CMDB context automatically (not after a ticket is created)
- **Risk Assessor** — Scores impact instantly; no manual estimation delay
- **Mechanic Agent** — Selects the best runbook based on historical outcomes
- **Policy Broker** — Applies governance rules before execution (CAB gates for high-risk)
- **Tool Registry** — Executes remediation with step-level abort policies
- **Verifier Agent** — Confirms resolution; feeds learning back into the system

**Each agent runs autonomously**, working incidents around the clock so your team focuses on strategic work — not routine remediation. Operators retain full control through approval gates on high-risk changes.

---

### What It Does

Watch your entire infrastructure with **Sentinel** (kernel-level eBPF monitoring), detect anomalies with the **Watcher** service, correlate related incidents with **Storm Detection**, and automatically remediate using a **7-agent AI pipeline** — all with built-in approval workflows for critical changes.

**In Simple Terms:**
- 🔍 **See everything** — kernel syscalls, container health, resource usage, logs
- 🤖 **Detect automatically** — anomalies, patterns, correlated events
- 💡 **Investigate intelligently** — AI agents analyze context and CMDB relationships
- ✅ **Remediate safely** — execute runbooks with approval gates for high-risk actions
- 📊 **Track outcomes** — MTTA/MTTR reduction, remediation success rate, audit trail

**Typical results:** MTTR under 10 minutes for known incident types. On-call engineers review AI-generated worknotes instead of fighting alerts at 3am.

---

## Screenshots & Platform Gallery

### Dashboard & Incident Management

| | | |
|---|---|---|
| ![Dashboard](./docs/screenshots/Screenshot%20%281%29.png) | ![Incident List](./docs/screenshots/Screenshot%20%282%29.png) | ![Real-time Updates](./docs/screenshots/Screenshot%20%283%29.png) |
| Real-time Dashboard | Incident Queue | Live Updates |

### Incident Analysis & Workflow

| | | |
|---|---|---|
| ![Incident Detail](./docs/screenshots/Screenshot%20%284%29.png) | ![Timeline View](./docs/screenshots/Screenshot%20%285%29.png) | ![CMDB Relations](./docs/screenshots/Screenshot%20%286%29.png) |
| Incident Details | Timeline & History | CMDB Graph |

### Runbook Management & Automation

| | | |
|---|---|---|
| ![Runbook Library](./docs/screenshots/Screenshot%20%289%29.png) | ![Execution Flow](./docs/screenshots/Screenshot%20%2810%29.png) | ![Approval Queue](./docs/screenshots/Screenshot%20%2811%29.png) |
| Runbook Library | Execution Flow | Approval Gates |

### Administrative Controls

| | | |
|---|---|---|
| ![Admin Settings](./docs/screenshots/Screenshot%20%2820%29.png) | ![User Management](./docs/screenshots/Screenshot%20%2821%29.png) | ![Policy Config](./docs/screenshots/Screenshot%20%2822%29.png) |
| Settings Panel | User Management | Governance Policies |

**Full screenshot gallery:** [`docs/screenshots/`](./docs/screenshots/) (42 images)

**Product Overview:** [`Axiometica_AIR_Product_Overview.pdf`](./docs/Axiometica_AIR_Product_Overview.pdf) (detailed feature guide)

---

## Key Features

### 1. **Real-Time Monitoring & Anomaly Detection**
- **Sentinel (eBPF):** Kernel-level syscall monitoring on all containers
- **Watcher Service:** Detects CPU spikes, memory leaks, disk full, network failures, health check failures
- **Log Monitoring:** Error pattern detection from application logs
- **Health Checks:** External HTTP, ping, and custom health probes
- **Time-windowed Analysis:** Automatically correlates events within configurable time windows

### 2. **Intelligent Storm Detection**
- Detects correlated incident bursts (≥3 incidents across ≥2 resources)
- Groups related incidents under a parent "storm" ticket with child incidents placed in `storm_hold`
- Prevents redundant remediation when root cause is shared
- **AI Storm Hypothesis:** LLM-generated root cause analysis and remediation recommendation on every storm parent
- **Storm Overview UI:** Redesigned incident detail tabs — Overview shows storm timeline and affected resources; AI Insights tab surfaces the LLM hypothesis and confidence score
- Parent incidents use `awaiting_manual` state for operator coordination before any remediation is attempted

### 3. **7-Agent AI Pipeline**
Automatically triage and remediate incidents using specialized AI agents:

```
[Incident] → [1. Sentinel] → [2. Librarian] → [3. RiskAssessor] → [4. Mechanic]
                              (classify)       (enrich CMDB)      (score)        (runbook)
                                                                                      ↓
                                                                              [5. PolicyBroker]
                                                                                   (govern)
                                                                                      ↓
                                                            ┌─ [CAB Approval Gate] ─ approval_required?
                                                            │
                                                           YES (wait) / NO (proceed)
                                                            │
                                                            ↓
                                                    [6. ToolRegistry]
                                                    (execute runbook)
                                                            ↓
                                                   [7. VerifierAgent]
                                                   (validate outcome)
                                                            ↓
                                                      [Resolved]
```

**What Each Agent Does:**
1. **Sentinel Agent** — Classifies incident type, severity, affected resource
2. **Librarian Agent** — Enriches with CMDB context, dependencies, service ownership
3. **Risk Assessor** — Scores impact 0-100 (9-factor model: criticality, blast radius, urgency, etc.)
4. **Mechanic Agent** — Selects optimal runbook from 5-tier ranking (exact match → CMDB → historical → LLM → fallback)
5. **Policy Broker** — Applies governance rules; marks high-risk for approval queue
6. **Tool Registry** — Executes approved runbook steps; handles step-level abort policies
7. **Verifier Agent** — Confirms the incident is resolved; sets resolution_source

### 4. **Operator-Controlled Governance**
- **CAB Approval Workflow:** Halts execution for high-risk changes pending operator sign-off
- **Runbook Control:** Step-level abort policies prevent cascading failures
- **Audit Trail:** Complete history of all decisions and actions
- **Manual Overrides:** Operators can pause, resume, or cancel automation at any time

### 5. **Configuration Management (CMDB)**
- **Neo4j Graph Database:** Stores relationships between infrastructure components
- **Auto-Discovery:** Container discovery with health routing to CMDB on every watcher poll
- **Context Enrichment:** Incidents automatically enriched with related CIs
- **Storm Relationships:** Graphs show which incidents are related and why
- **Force Graph UI:** Interactive relationship explorer — nodes distribute naturally on load rather than collapsing to a single point; `HOSTED_ON` and `PART_OF` traversal surfaces shared upstream hosts

### 6. **Automated Backup & Recovery**
- **Scheduled Rotating Backups:** Daily PostgreSQL, Neo4j, and config snapshots
- **Configurable Retention:** Keep 7-365 days of backups
- **Point-in-Time Recovery:** Restore to any backed-up state
- **Celery Beat Scheduler:** Runs backups without manual intervention

### 7. **Performance at Scale**
- **Phase 1 Optimizations:** 25-50x faster incident queries, 4-5x capacity increase
- **PostgreSQL Indexes:** 9 performance indexes on common queries
- **Redis Caching:** 5-second cache on metrics (50x faster dashboard)
- **Connection Pooling:** Tuned for 3-instance backend scaling
- **Syscall Sampling:** Configurable sampling reduces watcher CPU by 44-66%

### 8. **Runbook Library**
- **Searchable Catalogue:** Browse all runbooks by name, service, or tags
- **Confidence Scoring:** Each runbook displays an AI-calibrated confidence % that updates with every execution outcome
- **Execution Stats:** Live success/failure bar showing historical run results per runbook
- **Trend Badges:** Visual indicators (↑ improving / ↓ declining / → stable) based on recent execution trend
- **Runbook Editor:** Edit step definitions, thresholds, and abort policies directly from the UI

### 9. **Real-Time Infrastructure Monitoring**
- **Watcher Metrics Dashboard:** CPU, memory, and disk usage graphed over a rolling 20-sample window
- **Accurate Disk Reporting:** Disk usage read directly from `df -B1` per container — no name-mapping errors
- **Alert Threshold Controls:** Adjust CPU, memory, disk, and syscall thresholds live; changes pushed to the watcher within 30 seconds without restart
- **Connection Health:** TCP connection count monitored per container
- **Multi-Watcher Support:** Register and manage multiple watcher agents from a single platform

### 10. **Slack ChatOps Integration**
- **Notifications:** Incident creation, storm detection, approval required, resolution
- **Interactive Buttons:** Approve/reject from Slack without logging in
- **Threaded Discussions:** Thread discussions per incident
- **Bot Commands:** Query incidents, get status, trigger manual actions

### 11. **Comprehensive Documentation**
- **Installation Guide:** Step-by-step setup for all platforms
- **Architecture Deep-Dive:** System design, data flow, component relationships
- **Runbook Templates:** Pre-built remediation playbooks
- **API Reference:** Complete OpenAPI 3.1.0 documentation
- **Troubleshooting:** Common issues and solutions

### 12. **Enterprise Features**
- **Role-Based Access Control:** Admin, Operator, Viewer roles
- **JWT Authentication:** Secure token-based API access
- **SAML/OIDC Ready:** Enterprise directory integration
- **Audit Logging:** Complete administrative action history
- **SSL/TLS Support:** HTTPS-ready via reverse proxy
- **Source-Available License:** Free for internal use; commercial licensing available at axiometica.com

---

## What It Does (In Depth)

Axiometica AIR watches your infrastructure in real-time and autonomously manages incidents from detection to resolution:

1. **Sentinel** (eBPF) reads every syscall on the host kernel — sees all containers
2. **Watcher** detects anomalies (syscall bombs, health failures, CPU/memory/disk/network spikes, error patterns)
3. **Storm Agent** detects correlated event bursts across multiple resources and groups them into a single parent incident — preventing redundant remediations when the root cause is shared
4. **7-agent pipeline** triages, enriches, risk-scores, proposes, governs, executes, and verifies each incident
5. **CAB approval queue** halts execution for operator sign-off on high-risk changes; storm parents use `awaiting_manual` — the operator coordinates investigation before any remediation is attempted
6. **All-clear mechanism** closes incidents when the watcher confirms the condition has normalised

---

## Architecture at a Glance

```
sentinel_senses  ──► watcher_brain ──► POST /api/monitoring-events
(eBPF bpftrace)       splunk webhook               │
                                                   ▼
                                          EventQualificationService
                                                   │ (score ≥ threshold)
                                        ┌──────────┴──────────┐
                                        ▼                     ▼
                              StormDetectionService    (no storm)
                                        │ (≥3 incidents,       │
                                        │  ≥2 resources)       │
                                        ▼                     ▼
                                  StormAgent          7-agent pipeline
                                  (LLM + Neo4j)   ┌──────────────────────────────┐
                                        │         │ 1. SentinelAgent  (classify) │
                                        │         │ 2. LibrarianAgent (CMDB)     │
                                        │         │ 3. RiskAssessor   (0-100)    │
                                        │         │ 4. MechanicAgent  (runbook)  │
                                        │         │ 5. PolicyBroker   (govern)   │
                                        │         │ 6. ToolRegistry   (execute)  │
                                        │         │ 7. VerifierAgent  (verify)   │
                              storm_hold children  └──────────────────────────────┘
                              awaiting_manual (parent)
                                        │
                                        └──────────┬──────────┘
                                                   ▼
                                          WebSocket → React UI
```

---

## Quick Start (5 Minutes)

**Want to see it in action?** Follow the [Quick Start Guide](./docs/QUICKSTART.md) to get running in under 10 minutes with Docker Compose.

```bash
# Clone repository
git clone https://github.com/axiometica/axiometica-air.git
cd axiometica-air

# Set required secrets (JWT_SECRET and WATCHER_API_KEY have no default)
cp .env.example .env
# Edit .env and replace the CHANGE_ME values — generate secrets with: openssl rand -hex 32

# Start all services
docker compose up -d

# Frontend at http://localhost:3000
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Production Deployment

The recommended way to deploy for production is with the installation guide — it handles prerequisites, configuration, migrations, and seeding automatically.

**See [docs/DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md)** for full production installation and hardening instructions.

### Default Access After Install

| Service | URL | Default Credentials |
|---|---|---|
| Platform UI | http://localhost:3000 | admin@platform.local / admin |
| API Docs | http://localhost:8000/docs | — |
| Task Queue Monitor | http://localhost:5555 | admin / (set during install) |
| Neo4j CMDB Browser | http://localhost:7474 | neo4j / agentic_os_neo4j |

### Verify Everything Is Running

```bash
# All containers healthy
docker ps

# API health
curl http://localhost:8000/api/health

# List incidents
curl http://localhost:8000/api/workflows?workflow_type=incident&limit=5
```

---

## Key Concepts

### Decoupled Incident State (v1.1.0 Schema)

Incidents track five separate dimensions — all stored as dedicated columns:

| Field | Values | Meaning |
|-------|--------|---------|
| `lifecycle_state` | `open` → `in_progress` → `waiting_approval` → `executing` → `resolved` | Overall incident status |
| `remediation_outcome` | `succeeded` / `failed` / `aborted` / `skipped` / `pending` / `rejected` | How automation performed |
| `resolution_source` | `automated_remediation` / `watcher_all_clear` / `manual` | What cleared the condition |
| `all_clear_received_at` | ISO timestamp | When the watcher confirmed the condition cleared |
| `incident_number` / `incident_number_str` | `42` / `INC0042` | Operator-friendly incident identifiers |

**Example:** An incident with `lifecycle_state=resolved` + `remediation_outcome=aborted` + `resolution_source=watcher_all_clear` means: automation was stopped (a step timed out), but the condition cleared naturally and the watcher confirmed it.

**Active filter:** Use `lifecycle_state=active` on the API (or "Active (All Open)" in the UI) to return all non-terminal states in one query — `open`, `in_progress`, `waiting_approval`, `approved`, `executing`, `awaiting_manual`, `storm_hold`.

### Step Abort Policy

Runbook steps default to `on_failure: abort`. If a step fails or times out, remaining steps are **not** executed. This prevents cascading damage (e.g., a failed process_kill should not proceed to restart_service).

### Watcher All-Clear

The watcher tracks which resources have active anomaly conditions. When a resource returns to normal, it immediately sends a `condition_cleared` event — **per resource**, independent of what other containers are doing. The backend closes any open incidents for that resource.

---

## Tech Stack

```
┌─────────────────────────────────────────────────────────────┐
│ FRONTEND LAYER                                              │
│ React 18 • TypeScript • Vite • Responsive Design            │
│ WebSocket (real-time) • PWA-ready                           │
└─────────────────┬───────────────────────────────────────────┘
                  │ REST API / WebSocket
┌─────────────────▼───────────────────────────────────────────┐
│ BACKEND LAYER                                               │
│ Python 3.11 • FastAPI • SQLAlchemy ORM                      │
│ Async Workers • Real-time Event Bus                         │
└──────────┬──────────────────────────────┬──────────────────┘
           │                              │
┌──────────▼──────────┐    ┌──────────────▼─────────────────┐
│ TASK QUEUE          │    │ MONITORING AGENTS               │
│ Celery Workers      │    │ eBPF (bpftrace) • Python agent │
│ Beat Scheduler      │    │ Syscall telemetry • Watcher    │
└─────────────────────┘    └────────────────────────────────┘
           │
┌──────────▴──────────────────────────────────────────────────┐
│ DATA LAYER                                                  │
│ PostgreSQL 15 (events, workflows, audit log)                │
│ Redis 7 (cache, task queue)                                 │
│ Neo4j 5 (CMDB graph — relationships)                        │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Technologies |
|-------|-----------|
| **Frontend** | React 18, TypeScript, Vite, CSS Grid |
| **Backend** | Python 3.11, FastAPI, Pydantic, SQLAlchemy |
| **Task Queue** | Celery, Redis, Celery Beat scheduler |
| **Databases** | PostgreSQL 15 (primary), Redis 7, Neo4j 5 (CMDB) |
| **Monitoring** | eBPF/bpftrace (Sentinel), Python (Watcher) |
| **Infrastructure** | Docker Compose, volume mounts, health checks |
| **Real-Time** | PostgreSQL LISTEN/NOTIFY, WebSocket |

---

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/Axiometica AIR Product_Capabilities.md](./docs/Axiometica%20AIR%20Product_Capabilities.md) | **Start here** — complete platform capabilities reference for new users and evaluators |
| [docs/DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md) | Installation, configuration, and production hardening |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | System architecture deep-dive and component relationships |
| [docs/FEATURES.md](./docs/FEATURES.md) | Full feature catalog with implementation status |
| [docs/WATCHER_SETUP.md](./docs/WATCHER_SETUP.md) | Watcher agent setup and eBPF monitoring configuration |
| [docs/SLACK_SETUP.md](./docs/SLACK_SETUP.md) | Slack ChatOps integration guide |
| [CHANGELOG.md](./CHANGELOG.md) | Release history |

---

## Testing a Live Incident

With the platform running, simulate a syscall bomb in the neo4j container:

```bash
# Start a syscall-intensive process (kill it manually after ~30s)
docker exec -d agentic_os_neo4j sh -c "yes > /dev/null"

# Watch the watcher detect it
docker logs watcher_brain -f

# Watch incidents appear in the UI
# http://localhost:3000

# Kill the process (simulates condition clearing)
docker exec agentic_os_neo4j pkill yes

# Watcher sends all-clear → incident resolves automatically
```

---

## Feature Comparison Matrix

| Feature | Axiometica AIR | Traditional ITSM | Monitoring Tools |
|---------|---|---|---|
| **Real-time Monitoring** | ✅ | ❌ | ✅ |
| **Automated Anomaly Detection** | ✅ | ❌ | ⚠️ (limited) |
| **AI-Driven Triage** | ✅ | ❌ | ❌ |
| **Auto-Remediation with Approval Gates** | ✅ | ❌ | ❌ |
| **Incident Correlation & Storms** | ✅ | ⚠️ (manual) | ⚠️ (threshold-based) |
| **CMDB Integration** | ✅ (Neo4j graph) | ⚠️ (static) | ❌ |
| **eBPF Kernel Monitoring** | ✅ | ❌ | ⚠️ (agent-based) |
| **Operator-Controlled Governance** | ✅ | ✅ | ❌ |
| **Runbook Automation** | ✅ (visual editor) | ✅ (workflow builder) | ⚠️ (scripts) |
| **Slack ChatOps** | ✅ | ⚠️ (integrations) | ✅ |
| **Source Available** | ✅ (free for internal use) | ❌ | ✅ |
| **Self-Hosted** | ✅ | ✅ | ✅ |
| **API-First** | ✅ (OpenAPI 3.1) | ⚠️ | ✅ |
| **Sub-Minute Detection** | ✅ | ❌ | ⚠️ |

---

## Community Testing & Feedback

**We're in active community testing phase!** We'd love your feedback:

- 🧪 **Try it out:** Follow the [Quick Start Guide](./docs/QUICKSTART.md) (10 minutes)
- 📝 **Share feedback:** [GitHub Discussions](https://github.com/axiometica/axiometica-air/discussions) — Testing & Feedback category
- 🐛 **Found a bug?** [GitHub Issues](https://github.com/axiometica/axiometica-air/issues) — use bug report template
- ⭐ **Like it?** Star the repo and follow development

**What we're looking for:**
- Does it work in your environment?
- Is the UI intuitive?
- Do the incidents detect correctly?
- Are the remediation recommendations helpful?
- What features matter most to you?

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed testing guidelines.

---

## Getting Help

- **Installation Issues:** See [docs/DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md) troubleshooting section
- **Architecture Questions:** Read [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
- **Watcher & Monitoring:** See [docs/WATCHER_TROUBLESHOOTING.md](./docs/WATCHER_TROUBLESHOOTING.md)
- **GitHub Issues:** https://github.com/axiometica/axiometica-air/issues

## License

Axiometica AIR is source-available under the [Axiometica AIR Source License](./LICENSE).

- **Free** for internal use by organizations with fewer than 25 employees
- **Commercial license** required for larger organizations or MSP/reseller use — [axiometica.com/pricing](https://axiometica.com/#pricing) or email licensing@axiometica.com

The full license text is in [LICENSE](./LICENSE). [NOTICE](./NOTICE) contains third-party attribution.

## Repository

- **GitHub:** https://github.com/axiometica/axiometica-air
- **Branch:** main
- **Version:** v1.2.0 — June 2026
- **Status:** ✅ Production Ready
