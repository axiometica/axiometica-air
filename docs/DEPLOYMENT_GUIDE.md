# Axiometica AIR v2 — Deployment Guide

**Platform version:** v1.1.2  
**Last updated:** 2026-06-07

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [First-Run Verification](#4-first-run-verification)
5. [Default Credentials](#5-default-credentials)
6. [Post-Install Configuration](#6-post-install-configuration)
7. [LLM Provider Setup](#7-llm-provider-setup)
8. [Connector Hub](#8-connector-hub)
9. [Slack ChatOps](#9-slack-chatops)
10. [Reverse Proxy & TLS](#10-reverse-proxy--tls)
11. [Resource Limits & Scaling](#11-resource-limits--scaling)
12. [Backup & Recovery](#12-backup--recovery)
13. [Upgrading](#13-upgrading)
14. [Environment Variable Reference](#14-environment-variable-reference)
15. [Pre-Production Checklist](#15-pre-production-checklist)

---

## 1. Architecture Overview

Axiometica AIR v1.0.0 runs as a nine-container Docker Compose stack.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser / API clients                                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP / WebSocket
┌──────────────────────────▼──────────────────────────────────────────┐
│  agentic_os_frontend  (React + Vite, port 3000)                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ REST / WebSocket
┌──────────────────────────▼──────────────────────────────────────────┐
│  agentic_os_backend   (FastAPI, port 8000, 2 uvicorn workers)        │
│  • 7-agent incident pipeline (SentinelAgent → VerifierAgent)        │
│  • StormDetectionService (background task on every event)           │
│  • Connector Hub webhook ingest + 7 certified adapters              │
│  • Slack ChatOps (Socket Mode or webhook)                           │
│  • JWT authentication                                               │
├──────────────────────────┬──────────────────────────────────────────┤
│  agentic_os_celery_worker│  agentic_os_flower (Celery UI, port 5555)│
│  (async task executor,   │                                          │
│   2 concurrent workers)  │                                          │
└───────────┬──────────────┴──────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────────────┐
│  Data tier                                                       │
│  ┌──────────────────┐  ┌────────────────┐  ┌──────────────────┐ │
│  │ agentic_os_       │  │ agentic_os_    │  │ agentic_os_neo4j │ │
│  │ postgres          │  │ redis          │  │ (CMDB graph DB,  │ │
│  │ (PostgreSQL 15,   │  │ (Redis 7,      │  │  ports 7474/7687)│ │
│  │  port 5432)       │  │  port 6379)    │  └──────────────────┘ │
│  └──────────────────┘  └────────────────┘                        │
└──────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  Monitoring tier                                                   │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐   │
│  │ sentinel_senses           │  │ watcher_brain                │   │
│  │ (eBPF / bpftrace on host  │  │ (anomaly detection,          │   │
│  │  kernel, privileged)      │  │  container discovery,        │   │
│  │                           │  │  Docker stats polling)       │   │
│  └──────────────────────────┘  └──────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

**Inbound event paths:**
- **Watcher brain** — eBPF signals from sentinel_senses, direct Docker stats
- **Connector Hub** — webhook ingest from Datadog, Dynatrace, Splunk, Prometheus/Alertmanager, PagerDuty, Zabbix, ServiceNow
- **Manual API** — direct `POST /api/workflows/incident` from operators or scripts

---

## 2. System Requirements

### Minimum (development / evaluation)

| Resource | Minimum |
|----------|---------|
| CPU | 4 cores |
| RAM | 8 GB |
| Disk | 20 GB SSD |
| OS | Linux (kernel 5.4+), Windows 10/11 with WSL2, macOS 12+ |

### Recommended (production)

| Resource | Recommended |
|----------|------------|
| CPU | 8+ cores |
| RAM | 16 GB |
| Disk | 100 GB SSD (for database volumes) |
| OS | Linux (Ubuntu 22.04 LTS or RHEL 9) |

### Required software

| Dependency | Version | Notes |
|------------|---------|-------|
| Docker Engine | 24.0+ | Or Docker Desktop |
| Docker Compose | v2.20+ | Bundled with Docker Desktop |
| Node.js | 18+ | Frontend dev server only |

> **eBPF note:** `sentinel_senses` runs `bpftrace` on the host kernel with `privileged: true` and `pid: host`. This requires Linux. On Windows/macOS, the container starts but eBPF telemetry is unavailable — the watcher falls back to Docker stats only, which is sufficient for CPU/memory monitoring.

---

## 3. Installation

### Step 1 — Clone or extract the project

```bash
cd /opt     # or wherever you want the installation
# clone, or extract the archive:
unzip axiometica-air.zip
cd axiometica-air
```

### Step 2 — Run the installer

The installer generates required secrets and writes them to `.env`.

**Linux / macOS:**
```bash
chmod +x install.sh
./install.sh
```

**Windows (Command Prompt as Administrator):**
```cmd
install.bat
```

The installer:
- Generates a random `JWT_SECRET` (32-byte hex)
- Generates a random `WATCHER_API_KEY`
- Creates the `.env` file with both secrets
- Verifies Docker and Docker Compose are available

> **Do not skip the installer.** The `docker-compose.yml` will refuse to start if `JWT_SECRET` or `WATCHER_API_KEY` are missing.

### Step 3 — Start the stack

```bash
docker compose up -d
```

First run pulls images and builds the backend/frontend/watcher containers — allow 5–10 minutes. Subsequent starts take ~30 seconds.

### Step 4 — Start the frontend (optional local dev server)

The frontend is included as a containerised service in Docker Compose (`agentic_os_frontend`, port 3000). For development, you can also run it directly:

```bash
cd frontend
npm install     # first time only
npm run dev
```

---

## 4. First-Run Verification

Check that all containers reach the `healthy` state:

```bash
docker compose ps
```

Expected output (all services `Up`, backend/postgres/redis/neo4j showing `(healthy)`):

```
NAME                       STATUS
agentic_os_backend         Up (healthy)
agentic_os_celery_worker   Up (healthy)
agentic_os_flower          Up
agentic_os_frontend        Up
agentic_os_neo4j           Up (healthy)
agentic_os_postgres        Up (healthy)
agentic_os_redis           Up (healthy)
sentinel_senses            Up (healthy)
watcher_brain              Up
```

> Neo4j + APOC takes up to 60 seconds on first run. If it shows `starting` for longer than 90 seconds, check `docker logs agentic_os_neo4j`.

**Smoke tests:**

```bash
# Backend health
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", ...}

# Backend readiness (all subsystems)
curl http://localhost:8000/api/ready

# Watcher is running
docker logs watcher_brain --tail 20

# Frontend
# Open http://localhost:3000 in your browser
```

---

## 5. Default Credentials

### Platform UI / API

| Account | Email | Default password | Role |
|---------|-------|-----------------|------|
| System Admin | `admin@platform.local` | `Admin@1234!` | `admin` |
| ITOM Admin | `itomadmin@platform.local` | `ITOMAdmin@1234!` | `itom_admin` |
| Operator | `operator@platform.local` | `Operator@1234!` | `operator` |
| Viewer | `viewer@platform.local` | `Viewer@1234!` | `viewer` |

> **Change all passwords immediately after first login** via **Settings → Users**.

### Other services

| Service | URL | Credential |
|---------|-----|-----------|
| Celery Flower | http://localhost:5555 | `admin` / `changeme` |
| Neo4j Browser | http://localhost:7474 | `neo4j` / `agentic_os_neo4j` |
| PostgreSQL | localhost:5432 | `postgres` / `agentic_os` |

Flower credentials are set via `FLOWER_USER` / `FLOWER_PASSWORD` in `.env`.

---

## 6. Post-Install Configuration

### Change the admin password

1. Open http://localhost:3000
2. Log in as `admin@platform.local` / `admin`
3. Navigate to **Settings → Users**
4. Edit the admin user and set a strong password

### Review governance policies

Three default policies are seeded at startup:

| Policy | Match | Action |
|--------|-------|--------|
| High CPU Auto-Restart | `high_cpu`, any service, production | `restart_container` (requires approval for critical) |
| Disk Full Escalation | `disk_full`, high severity | Escalate to operator |
| Service Down Investigation | `service_down`, critical | CAB approval required |

Navigate to **Policies** to review and customise these before enabling auto-remediation.

### Configure runbook approvals

By default, high-risk runbooks require CAB approval. Tune approval thresholds in **Policies** or **Settings → Governance**.

### Set the Platform Intelligence tuning schedule

**Settings → Platform Intelligence** controls the TuningAgent that reviews resolved incidents and adjusts scoring weights. Enable and set a schedule (default: daily at 02:00).

---

## 7. LLM Provider Setup

An LLM provider is **optional** — the platform operates fully without one using deterministic fallback summaries. Connecting a provider enables richer incident summaries, root cause hypotheses, and storm analysis.

**Settings → LLM Provider:**

| Field | OpenAI | Anthropic |
|-------|--------|-----------|
| Provider | `openai` | `anthropic` |
| Model | `gpt-4o` or `gpt-4-turbo` | `claude-opus-4-5` or `claude-sonnet-4-5` |
| API Key | `sk-…` | `sk-ant-…` |

Click **Save** and then **Test Connection**. LLM is used for:
- Incident summaries (Overview tab)
- Storm root cause hypothesis
- MechanicAgent Tier 4 runbook synthesis

---

## 8. Connector Hub

The Connector Hub allows any monitoring tool with webhook support to feed events into the platform's full AI pipeline. Seven certified adapters are included:

| Connector | Direction | Notes |
|-----------|-----------|-------|
| ServiceNow | Bidirectional | Ingest alerts; write back resolution status |
| Splunk | Inbound | Alert webhook |
| Datadog | Inbound | Webhook monitor alert |
| Dynatrace | Inbound | Problem webhook |
| Prometheus / Alertmanager | Inbound | AlertManager webhook receiver |
| PagerDuty | Inbound | EventBridge webhook |
| Zabbix | Inbound | Action webhook |

### Configure a connector

**Settings → Connector Hub → Add Connector:**

1. Select the **connector type**
2. Enter the **name** and optional description
3. Copy the generated **webhook URL** (format: `/api/webhooks/{connector-type}/{uuid}`)
4. Configure your monitoring tool to POST alerts to that URL
5. Set per-connector governance flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `allow_auto_remediation` | off | Whether automated runbooks can execute for events from this connector |
| `allow_storm_detection` | on | Whether events from this connector are eligible for storm correlation |
| `default_criticality` | medium | Criticality to assign when the inbound alert has no severity mapping |
| `webhook_secret` | — | Optional HMAC secret for request verification |

### Per-connector webhook security

Set a `webhook_secret` to enable HMAC-SHA256 signature verification. The connector will reject requests that do not carry a valid `X-Webhook-Signature` header.

### ServiceNow bidirectional sync

ServiceNow requires additional configuration for write-back:
- **ServiceNow instance URL** — your `company.service-now.com` URL
- **Username / password** — a service account with `itil` role
- **Table** — `incident` (default)
- **Sync resolved status** — enables writing resolution details back to the ServiceNow ticket

---

## 9. Slack ChatOps

See **[SLACK_SETUP.md](./SLACK_SETUP.md)** for the full setup walkthrough.

Summary:

1. Create a Slack app at https://api.slack.com/apps
2. Add bot scopes: `chat:write`, `app_mentions:read`, `im:read`, `im:write`, `users:read`, `users:read.email`
3. Install to workspace; copy the `xoxb-…` Bot User OAuth Token
4. Enable Socket Mode (for self-hosted) or configure webhook URLs (for cloud deployments)
5. **Settings → Slack ChatOps** in the platform UI: paste Bot Token, Signing Secret, and (if Socket Mode) App-Level Token
6. Restart the backend: `docker compose restart backend`
7. `/invite @<bot-name>` in the channels where you want notifications

**Outbound notifications** (critical incidents, approvals, resolutions) are posted automatically.

**Inbound chat** — operators can query incidents, approvals, and MTTR, or say `approve INC0042` / `reject INC0042` directly in Slack.

---

## 10. Reverse Proxy & TLS

For production, front the backend (port 8000) and frontend (port 3000) behind a TLS-terminating reverse proxy. Example nginx configuration:

```nginx
# /etc/nginx/sites-available/axiometica-air
server {
    listen 443 ssl;
    server_name ops.company.com;

    ssl_certificate     /etc/ssl/certs/ops.company.com.crt;
    ssl_certificate_key /etc/ssl/private/ops.company.com.key;

    # Frontend
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # API + WebSocket
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
    }

    location /ws/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    # Connector webhooks
    location /api/webhooks/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }
}

server {
    listen 80;
    server_name ops.company.com;
    return 301 https://$host$request_uri;
}
```

**Update CORS:** Add your public hostname to the `ALLOWED_ORIGINS` environment variable:

```
ALLOWED_ORIGINS=https://ops.company.com,http://localhost:3000
```

Restart the backend after changing `ALLOWED_ORIGINS`:
```bash
docker compose restart backend
```

---

## 11. Resource Limits & Scaling

### Default Docker resource limits

| Service | CPU limit | Memory limit |
|---------|-----------|-------------|
| postgres | 1.0 | 512 MB |
| redis | 0.5 | 512 MB |
| neo4j | 1.0 | 2 GB |
| backend | 2.0 | 2 GB |
| celery_worker | 1.5 | 1 GB |
| flower | 0.5 | 512 MB |
| frontend | 1.0 | 512 MB |
| watcher | 1.0 | 512 MB |

Neo4j requires ~1.2 GB at startup with APOC — do not reduce its memory limit below 1.5 GB.

### Scaling the backend

The backend runs two uvicorn workers by default. To handle higher API throughput, increase workers in `docker-compose.yml`:

```yaml
command: uvicorn agentic_os.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Scaling Celery workers

Increase `--concurrency` in the celery_worker command for higher incident throughput:

```yaml
command: celery -A agentic_os.tasks worker -l info --concurrency=4 --prefetch-multiplier=1
```

Each incident pipeline run is CPU-bound during LLM calls; horizontal scaling (multiple celery_worker containers) is more effective than high concurrency on a single container.

### Horizontal scaling (multiple backend instances)

For HA deployments with a load balancer:

1. Point the load balancer at multiple backend containers (all on the same Docker network or separate hosts sharing the same PostgreSQL + Redis)
2. Sticky sessions are **not** required — WebSocket connections are managed per-process using PostgreSQL LISTEN/NOTIFY
3. Connector webhook URLs are stable — inbound events are processed by whichever backend instance receives them

---

## 12. Backup & Recovery

### PostgreSQL (primary data store)

All incidents, policies, approvals, runbooks, users, connector configs, and settings are stored in PostgreSQL.

**Daily backup:**
```bash
docker exec agentic_os_postgres pg_dump -U postgres agentic_os \
  | gzip > /backups/agentic_os_$(date +%Y%m%d).sql.gz
```

**Restore:**
```bash
gunzip -c /backups/agentic_os_20260530.sql.gz \
  | docker exec -i agentic_os_postgres psql -U postgres agentic_os
```

### Neo4j (CMDB graph)

```bash
# Stop neo4j, copy the data volume, restart
docker compose stop neo4j
docker run --rm -v axiometica-air_v2_neo4j_data:/data \
  -v /backups:/backup alpine \
  tar czf /backup/neo4j_$(date +%Y%m%d).tar.gz /data
docker compose start neo4j
```

### Redis

Redis holds the Celery task queue and ephemeral cache. It does not need point-in-time backup — queued tasks are safe to lose on restart (Celery will retry). For task result persistence, Redis AOF is configured by the container image defaults.

### Backup retention

| Data | Recommended retention |
|------|-----------------------|
| PostgreSQL daily dumps | 30 days |
| Neo4j snapshots | 7 days |
| Platform logs | 90 days |

---

## 13. Upgrading

### Standard upgrade (no database schema changes)

```bash
# 1. Pull latest code / extract new archive
cd axiometica-air
git pull  # or extract zip

# 2. Rebuild images (backend, frontend, watcher)
docker compose build --no-cache backend frontend watcher

# 3. Restart services with new images
docker compose up -d --force-recreate backend celery_worker watcher frontend

# 4. Verify health
docker compose ps
curl http://localhost:8000/api/health
```

### Upgrade with database migrations

```bash
# 1. Back up the database first (see §12)

# 2. Build new images
docker compose build --no-cache backend

# 3. Apply Alembic migrations
docker compose run --rm backend alembic upgrade head

# 4. Restart
docker compose up -d --force-recreate
```

### Rolling back

```bash
# Stop new containers
docker compose down

# Restore database from backup (if schema changed)
gunzip -c /backups/agentic_os_<date>.sql.gz \
  | docker exec -i agentic_os_postgres psql -U postgres agentic_os

# Check out previous version and rebuild
git checkout <previous-tag>
docker compose build backend frontend watcher
docker compose up -d
```

---

## 14. Environment Variable Reference

All variables are read at container startup. Restart the relevant container after changing any value.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET` | **Yes** | *(installer-generated)* | 32-byte hex secret for JWT signing |
| `WATCHER_API_KEY` | **Yes** | *(installer-generated)* | API key for watcher → backend authentication |
| `DATABASE_URL` | Yes | `postgresql://postgres:agentic_os@postgres:5432/agentic_os` | PostgreSQL connection string |
| `REDIS_URL` | Yes | `redis://redis:6379` | Redis connection string |
| `CELERY_BROKER_URL` | Yes | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | Yes | `redis://redis:6379/1` | Celery result store |
| `ADMIN_EMAIL` | No | `admin@platform.local` | Default admin account email |
| `ADMIN_INITIAL_PASSWORD` | No | `admin` | Admin account password (change after first login) |
| `ITOM_ADMIN_INITIAL_PASSWORD` | No | `ITOMAdmin@1234!` | ITOM Admin password |
| `OPERATOR_INITIAL_PASSWORD` | No | `Operator@1234!` | Operator password |
| `VIEWER_INITIAL_PASSWORD` | No | `Viewer@1234!` | Viewer password |
| `JWT_EXPIRY_HOURS` | No | `8` | JWT token lifetime |
| `ALLOWED_ORIGINS` | No | `http://localhost:3000,http://localhost:3001,http://localhost:8000` | CORS allowed origins |
| `SLACK_BOT_TOKEN` | No | *(empty)* | `xoxb-…` Slack bot token (leave blank to disable Slack) |
| `SLACK_SIGNING_SECRET` | No | *(empty)* | Slack app signing secret |
| `FLOWER_USER` | No | `admin` | Celery Flower basic-auth username |
| `FLOWER_PASSWORD` | No | `changeme` | Celery Flower basic-auth password |

### Watcher-specific variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHER_POLL_INTERVAL` | `10` | Seconds between monitoring polls |
| `WATCHER_CPU_THRESHOLD` | `80.0` | CPU % threshold for anomaly detection |
| `WATCHER_MEMORY_THRESHOLD` | `90.0` | Memory % threshold |
| `WATCHER_DISK_THRESHOLD` | `90.0` | Disk usage % threshold |
| `WATCHER_CONNECTION_THRESHOLD` | `1000` | Network connection count threshold |
| `WATCHER_ANOMALY_THRESHOLD` | `1000` | Syscall/5s threshold |
| `WATCHER_MIN_CONSECUTIVE_POLLS` | `3` | Consecutive anomalous polls before opening an incident |
| `WATCHER_COOLDOWN_SECONDS` | `60` | Post-incident cooldown period |
| `WATCHER_DISCOVERY_ENABLED` | `true` | Auto-discover containers into Neo4j CMDB |
| `WATCHER_DISCOVERY_INTERVAL_POLLS` | `15` | Run CMDB discovery every N polls |

---

## 15. Pre-Production Checklist

Before promoting to production:

**Security**
- [ ] All default passwords changed (admin, itom_admin, operator, viewer)
- [ ] Flower credentials changed (`FLOWER_USER` / `FLOWER_PASSWORD`)
- [ ] `JWT_SECRET` is a randomly generated 32-byte hex string (not the dev placeholder)
- [ ] TLS termination configured on reverse proxy
- [ ] `ALLOWED_ORIGINS` restricted to actual hostnames
- [ ] Connector `webhook_secret` values configured on all active connectors

**Operations**
- [ ] Daily PostgreSQL backup scheduled and tested
- [ ] Neo4j backup procedure documented
- [ ] Log retention policy set
- [ ] Monitoring alerts configured for container health
- [ ] Celery Flower basic-auth not exposed publicly (or behind VPN)
- [ ] Neo4j Browser not exposed publicly (or behind VPN)

**Platform**
- [ ] Governance policies reviewed and tuned for your environment
- [ ] LLM provider connected and tested (optional but recommended)
- [ ] At least one connector configured for your primary monitoring tool
- [ ] Slack ChatOps connected and bot invited to #incidents channel
- [ ] Runbook YAML files reviewed and adapted for your infrastructure
- [ ] Storm detection thresholds tuned (`Settings → Storm Detection`)
- [ ] Platform Intelligence tuning schedule enabled

**Team readiness**
- [ ] Operator accounts created for all team members
- [ ] Role assignments verified (viewers cannot approve)
- [ ] Runbook for CAB approval process documented
- [ ] On-call team knows how to approve/reject from Slack
