# Service Health Check & Troubleshooting Guide

## Quick Health Check Commands

### 1. Check Backend API Health
```bash
# Basic liveness check
curl http://localhost:8000/api/health

# Comprehensive readiness check (all services)
curl http://localhost:8000/api/ready

# Detailed performance metrics
curl http://localhost:8000/api/health/detailed
```

### 2. Check Docker Services Status
```bash
# View all running containers
docker ps

# View backend logs (most recent first)
docker logs agentic_os_backend --tail 50

# Follow backend logs in real-time
docker logs agentic_os_backend -f

# Full Docker Compose logs
docker-compose logs -f
```

### 3. Check Database Connectivity
```bash
# Connect to PostgreSQL directly
docker exec -it agentic_os_postgres psql -U postgres -d agentic_os -c "SELECT version();"

# List all tables
docker exec -it agentic_os_postgres psql -U postgres -d agentic_os -c "\dt"

# Check if workflow_states table exists
docker exec -it agentic_os_postgres psql -U postgres -d agentic_os -c "SELECT COUNT(*) FROM workflow_states;"
```

## Common Issues & Solutions

### Issue: Failed to load workflows/incidents (500 error)

**Possible Causes:**
1. Database tables not created
2. Database connection string incorrect
3. Import error in new PolicyModel
4. Missing environment variables

**Solutions:**

#### A. Restart with Fresh Database
```bash
# Stop all services
docker-compose down

# Remove database volume to start fresh
docker volume rm agenticplatform_v2_postgres_data

# Rebuild and start
docker-compose up --build
```

#### B. Check Backend Logs
```bash
# Watch logs as they happen
docker-compose logs -f backend

# Look for errors like:
# - "ModuleNotFoundError"
# - "sqlalchemy.exc.OperationalError" 
# - "ImportError"
```

#### C. Verify Database Tables
```bash
# Check if tables were created
docker exec -it agentic_os_postgres psql -U postgres -d agentic_os -c "\dt"

# Expected tables: workflow_states, events, approvals, monitoring_events, policies, etc.
```

#### D. Verify API Endpoints
```bash
# Test each endpoint to find which one is failing
curl -v http://localhost:8000/api/workflows
curl -v http://localhost:8000/api/monitoring-events
curl -v http://localhost:8000/api/policies
```

## Health Check Response Examples

### Healthy Response (/api/ready - 200 OK)
```json
{
  "status": "ready",
  "timestamp": "2026-05-11T12:34:56.789012",
  "service": "agentic_os",
  "version": "2.0.0",
  "checks": {
    "database": {
      "status": "connected",
      "error": null
    },
    "database_tables": {
      "status": "accessible",
      "error": null
    },
    "api_routes": {
      "status": "available",
      "endpoints": ["/workflows", "/approvals", "/policies", ...],
      "error": null
    }
  },
  "summary": {
    "total_checks": 7,
    "passed": 7,
    "failed": 0
  }
}
```

### Degraded Response (/api/ready - 503 Service Unavailable)
```json
{
  "status": "degraded",
  "timestamp": "2026-05-11T12:34:56.789012",
  "checks": {
    "database": {
      "status": "disconnected",
      "error": "could not connect to server..."
    },
    "database_tables": {
      "status": "unavailable",
      "error": "relation \"workflow_states\" does not exist"
    }
  },
  "summary": {
    "total_checks": 7,
    "passed": 2,
    "failed": 5
  }
}
```

## Pre-Deployment Checklist

Before considering the system healthy, verify:

- [ ] `curl http://localhost:8000/api/ready` returns status 200
- [ ] All checks show "passed": 7, "failed": 0
- [ ] Database shows tables: workflow_states, policies, monitoring_events
- [ ] Frontend can access `/api/workflows` without 500 errors
- [ ] WebSocket connection working: `ws://localhost:8000/ws/workflows/{id}`
- [ ] All API routes responding:
  - [ ] GET /api/workflows
  - [ ] GET /api/policies
  - [ ] GET /api/monitoring-events
  - [ ] GET /api/approvals
  - [ ] POST /api/workflows/incident (with sample data)
- [ ] Frontend loads without API errors
- [ ] Health check runs every 30 seconds without errors

## Test End-to-End Flow

```bash
# 1. Verify health
curl http://localhost:8000/api/ready | jq '.status'
# Should return: "ready"

# 2. Submit test incident
curl -X POST http://localhost:8000/api/workflows/incident \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "high",
    "type": "high_cpu",
    "resource_name": "api-server",
    "description": "Test incident"
  }' | jq '.workflow_id'
# Should return a UUID

# 3. Get incident details
WORKFLOW_ID=$(curl -s -X POST ... | jq -r '.workflow_id')
curl http://localhost:8000/api/workflows/$WORKFLOW_ID | jq '.lifecycle_state'
# Should return: "open"

# 4. Check frontend can load
curl http://localhost:3000 -s | grep -q "Agentic" && echo "✓ Frontend loads"
```

## Enabling Detailed Logging

Set environment variables for verbose logging:

```bash
# In docker-compose.yml, add to backend service:
environment:
  - SQL_ECHO=true        # Log all SQL queries
  - LOG_LEVEL=DEBUG      # Verbose logging
```

Then restart: `docker-compose restart backend`

Check logs: `docker-compose logs backend | grep -i error`

## Contact for Support

If health check still fails after these steps:
1. Provide output of: `docker-compose logs backend | tail -100`
2. Provide output of: `curl http://localhost:8000/api/ready | jq .`
3. Provide: `docker ps` output
