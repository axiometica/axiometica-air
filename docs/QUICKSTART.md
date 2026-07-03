# Quick Start Guide

Get Axiometica AIR v2 running in under 10 minutes.

---

## Prerequisites

- **Docker Desktop** (Linux containers mode on Windows)
- **Docker Compose v2** (bundled with Docker Desktop)
- **Node.js 18+** (for the local Vite frontend dev server)
- ~4 GB free RAM (all containers combined)

---

## 1. Start Backend Services

```bash
cd axiometica-air

# Set required secrets (JWT_SECRET and WATCHER_API_KEY have no default)
cp .env.example .env
# Edit .env and replace the CHANGE_ME values — generate secrets with: openssl rand -hex 32

# Start all backend containers
docker-compose up -d

# Verify all containers are healthy (may take 30-60s on first run)
docker ps
```

Expected containers:

| Container | Role | Port |
|-----------|------|------|
| `agentic_os_backend` | FastAPI API server | 8000 |
| `agentic_os_celery_worker` | Async task executor | — |
| `agentic_os_postgres` | PostgreSQL database | 5432 |
| `agentic_os_redis` | Cache + task queue | 6379 |
| `agentic_os_neo4j` | CMDB graph database | 7474, 7687 |
| `agentic_os_flower` | Celery task monitor | 5555 |
| `sentinel_senses` | eBPF syscall telemetry | — |
| `watcher_brain` | Anomaly detection agent | — |

---

## 2. Start the Frontend

The frontend runs as a local Vite dev server (not in Docker):

```bash
cd axiometica-air/frontend
npm install        # first time only
npm run dev
```

---

## 3. Verify

| Service | URL |
|---------|-----|
| Frontend UI | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger API Docs | http://localhost:8000/docs |
| Celery Flower | http://localhost:5555 |
| Neo4j Browser | http://localhost:7474 |

```bash
# Quick health check
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", ...}

# Check comprehensive health
curl http://localhost:8000/api/ready
```

---

## 4. Trigger a Test Incident

Simulate a syscall-intensive process in the neo4j container (watcher will detect it):

```bash
# Start the load (detected within ~10-20 seconds)
docker exec -d agentic_os_neo4j sh -c "yes > /dev/null"

# Watch watcher detect the anomaly
docker logs watcher_brain -f

# Watch the backend process the incident
docker logs agentic_os_backend -f

# Watch the frontend — an incident (INC000X) will appear automatically
# Open http://localhost:3000

# Kill the process after 30-60 seconds (triggers all-clear)
docker exec agentic_os_neo4j pkill yes

# Watcher detects condition cleared → incident auto-resolves
```

---

## 5. Submit an Incident Manually

```bash
curl -X POST http://localhost:8000/api/workflows/incident \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "high",
    "type": "high_syscall_intensity",
    "resource_name": "agentic_os_neo4j",
    "description": "Manual test incident"
  }'
```

---

## Common Operations

### Restart a service after code changes

Backend and Celery are **volume-mounted** — no rebuild needed for Python changes:

```bash
docker restart agentic_os_backend agentic_os_celery_worker
```

Watcher is also volume-mounted:

```bash
docker restart watcher_brain
```

Frontend uses **Vite HMR** — changes apply instantly in the browser.

### View logs

```bash
docker logs agentic_os_backend -f         # API logs
docker logs agentic_os_celery_worker -f   # Incident pipeline execution
docker logs watcher_brain -f              # Anomaly detection
docker logs sentinel_senses -f            # eBPF telemetry
```

### Check the database

```bash
docker exec agentic_os_postgres psql -U postgres -d agentic_os -c \
  "SELECT incident_number_str, lifecycle_state, remediation_outcome, resolution_source FROM workflow_states ORDER BY created_at DESC LIMIT 10;"
```

### Clear all incidents (admin endpoint)

```bash
curl -X POST http://localhost:8000/api/admin/incidents/delete-all
```

### Manually send a watcher all-clear

```bash
curl -X POST http://localhost:8000/api/monitoring-events \
  -H "Content-Type: application/json" \
  -d '{
    "source": "watcher_brain",
    "event_type": "condition_cleared",
    "resource_name": "agentic_os_neo4j",
    "raw_criticality": "info",
    "raw_payload": {
      "original_event_type": "high_syscall_intensity",
      "description": "Condition cleared manually"
    }
  }'
```

---

## Troubleshooting

### Containers not starting
```bash
docker-compose logs        # See startup errors
docker-compose down -v     # Nuclear reset (deletes volumes)
docker-compose up -d       # Fresh start
```

### Backend unhealthy
```bash
docker logs agentic_os_backend --tail 50
# Check for missing env vars, DB connection errors
```

### Watcher not detecting anomalies
```bash
docker logs watcher_brain --tail 30
# Check if sentinel_senses is running and bpftrace works
docker exec sentinel_senses bpftrace -e 'BEGIN { print("ok\n"); exit(); }'
```

### Celery not processing tasks
```bash
docker logs agentic_os_celery_worker --tail 30
# Check http://localhost:5555 for task queue status
```

---

## 6. Log In

Open http://localhost:3000 and log in with the default admin account:

| Field | Value |
|-------|-------|
| Email | `admin@platform.local` |
| Password | `Admin@1234!` |

Other default accounts:

| Role | Email | Password |
|------|-------|----------|
| ITOM Admin | `itomadmin@platform.local` | `ITOMAdmin@1234!` |
| Operator | `operator@platform.local` | `Operator@1234!` |
| Viewer | `viewer@platform.local` | `Viewer@1234!` |

> **Change all default passwords immediately** via **Settings → Users** before using the platform in any shared or production environment.

---

## 7. Connect Your Monitoring Tools (Optional)

The Connector Hub lets any webhook-capable monitoring tool feed into the platform's full AI pipeline:

**Settings → Connector Hub → Add Connector**

Supported connectors: Datadog, Dynatrace, Splunk, Prometheus/Alertmanager, PagerDuty, Zabbix, Grafana, Generic Webhook, ServiceNow (bidirectional).

Copy the generated webhook URL and configure your monitoring tool to POST alerts to it. All inbound events are normalised, risk-scored, and processed through the same 7-agent pipeline as watcher-detected events.

---

## 8. Configure an LLM Provider (Optional)

The platform works without an LLM — deterministic summaries are generated from the typed incident context. Connecting a provider enables richer narratives, storm hypothesis, and Tier 4 runbook synthesis.

**Settings → LLM Provider** → select OpenAI or Anthropic → enter your API key → **Test Connection**.

---

## Next Steps

- [ADMIN_GUIDE.md](./ADMIN_GUIDE.md) — complete installation and configuration guide
- [ARCHITECTURE.md](./ARCHITECTURE.md) — understand how the system works
- [SLACK_SETUP.md](./SLACK_SETUP.md) — set up Slack ChatOps notifications
- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) — production deployment and security hardening
