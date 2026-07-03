# Axiometica AIR v2 - Complete API Reference

## Table of Contents
1. [Overview](#overview)
2. [Authentication Endpoints](#authentication-endpoints)
3. [Workflow Endpoints](#workflow-endpoints)
4. [Approval Endpoints](#approval-endpoints)
5. [Policy Endpoints](#policy-endpoints)
6. [Connector Hub Endpoints](#connector-hub-endpoints)
7. [Event Storm Endpoints](#event-storm-endpoints)
8. [Storm Detection Settings](#storm-detection-settings)
9. [User Management Endpoints](#user-management-endpoints)
10. [Monitoring Events Endpoint](#monitoring-events-endpoint)
11. [Metrics Endpoints](#metrics-endpoints)
12. [Admin Endpoints](#admin-endpoints)
13. [Health Endpoints](#health-endpoints)
14. [WebSocket](#websocket)
15. [Runbooks Endpoints](#runbooks-endpoints)
16. [Approved Actions Endpoints](#approved-actions-endpoints)
17. [Event Type Taxonomy Endpoints](#event-type-taxonomy-endpoints)
18. [Error Handling](#error-handling)

---

## Overview

### Base URL
```
Development: http://localhost:8000
Production: https://api.company.com
```

### API Documentation
- **Swagger UI:** `GET /docs`
- **ReDoc:** `GET /redoc`
- **OpenAPI JSON:** `GET /openapi.json`

### Authentication

The platform uses **JWT (JSON Web Token)** authentication. All API endpoints except `/api/health`, `/api/ready`, and `/api/auth/login` require a valid bearer token.

**Obtain a token:**
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@platform.local", "password": "admin"}'
# Response: {"access_token": "eyJ...", "token_type": "bearer", "expires_in": 28800}
```

**Use the token:**
```bash
curl http://localhost:8000/api/workflows \
  -H "Authorization: Bearer eyJ..."
```

Tokens expire after `JWT_EXPIRY_HOURS` hours (default: 8). Obtain a new token by logging in again.

**Roles and permissions:**

| Role | Read incidents | Approve / reject | Manage policies | Manage users | Manage connectors |
|------|---------------|------------------|-----------------|--------------|-------------------|
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `itom_admin` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `operator` | ✓ | ✓ | ✓ | — | — |
| `viewer` | ✓ | — | — | — | — |

**Watcher-to-backend authentication:**
The watcher uses an API key (`WATCHER_API_KEY`) passed in the `X-API-Key` header, not JWT.

### Content-Type
All requests and responses use `application/json`.

---

## Authentication Endpoints

### Login

**Endpoint:** `POST /api/auth/login`

**Request Body:**
```json
{"email": "admin@platform.local", "password": "admin"}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 28800
}
```

**Error (401):**
```json
{"detail": "Invalid email or password"}
```

### Get Current User

**Endpoint:** `GET /api/auth/me`

**Response (200 OK):**
```json
{
  "id": "usr_123",
  "email": "admin@platform.local",
  "role": "admin",
  "is_active": true
}
```

---

## Workflow Endpoints

### Submit Incident

**Endpoint:** `POST /api/workflows/incident`

**Description:** Submit a new incident for automated triage and remediation.

**Request Body:**
```json
{
  "severity": "high",
  "type": "high_cpu",
  "resource_name": "api-server-1",
  "description": "CPU usage at 89%, memory at 3.2GB"
}
```

**Request Parameters:**
| Field | Type | Required | Constraints | Example |
|-------|------|----------|-------------|---------|
| severity | string | Yes | One of: critical, high, medium, low, info | "high" |
| type | string | Yes | Valid event type | "high_cpu" |
| resource_name | string | Yes | Max 255 chars | "api-server-1" |
| description | string | No | Max 5000 chars | "CPU spike during traffic" |

**Response (201 Created):**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_type": "INCIDENT",
  "lifecycle_state": "open",
  "severity": "high",
  "risk_score": null,
  "summary": null,
  "context": {
    "alert_payload": {
      "severity": "high",
      "type": "high_cpu",
      "resource_name": "api-server-1",
      "description": "CPU usage at 89%, memory at 3.2GB"
    }
  },
  "reasoning_trace": [],
  "created_at": "2024-01-15T14:32:00Z",
  "updated_at": "2024-01-15T14:32:00Z"
}
```

**Error Responses:**
```json
// 400 Bad Request - Invalid severity
{
  "detail": "value is not a valid enumeration member; permitted: 'critical', 'high', 'medium', 'low', 'info'"
}

// 500 Internal Server Error
{
  "detail": "An unexpected error occurred: ..."
}
```

**Example Requests:**

Using curl:
```bash
curl -X POST http://localhost:8000/api/workflows/incident \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "high",
    "type": "high_cpu",
    "resource_name": "api-server-1",
    "description": "CPU spike detected"
  }'
```

Using Python:
```python
import requests

response = requests.post(
    'http://localhost:8000/api/workflows/incident',
    json={
        'severity': 'high',
        'type': 'high_cpu',
        'resource_name': 'api-server-1',
        'description': 'CPU spike'
    }
)
workflow_id = response.json()['workflow_id']
```

---

### Submit Change Request

**Endpoint:** `POST /api/workflows/change`

**Description:** Submit a new change request for CAB review and deployment.

**Request Body:**
```json
{
  "change_type": "standard",
  "description": "Deploy API v2.4.1 with bug fixes",
  "affected_services": ["api-server", "api-cache"],
  "rollback_plan": "Revert to previous image tag, restart pods"
}
```

**Request Parameters:**
| Field | Type | Required | Constraints | Example |
|-------|------|----------|-------------|---------|
| change_type | string | Yes | standard, normal, emergency | "standard" |
| description | string | Yes | Max 5000 chars | "Deploy API v2.4.1" |
| affected_services | array | Yes | Min 1 service | ["api-server"] |
| rollback_plan | string | Yes | Max 2000 chars | "Revert to previous image" |

**Response (201 Created):**
```json
{
  "workflow_id": "660f9511-f30c-52e5-b827-557766551111",
  "workflow_type": "CHANGE",
  "lifecycle_state": "open",
  "severity": null,
  "risk_score": null,
  "summary": null,
  "context": {
    "change_context": {
      "change_type": "standard",
      "description": "Deploy API v2.4.1",
      "affected_services": ["api-server"],
      "rollback_plan": {
        "instructions": "Revert to previous image"
      }
    }
  },
  "reasoning_trace": [],
  "created_at": "2024-01-15T14:32:00Z",
  "updated_at": "2024-01-15T14:32:00Z"
}
```

---

### Get Workflow Details

**Endpoint:** `GET /api/workflows/{workflow_id}`

**Description:** Retrieve detailed status and history of a specific workflow.

**URL Parameters:**
| Parameter | Type | Example |
|-----------|------|---------|
| workflow_id | UUID string | "550e8400-e29b-41d4-a716-446655440000" |

**Response (200 OK):**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_type": "INCIDENT",
  "lifecycle_state": "completed",
  "severity": "high",
  "risk_score": 75.5,
  "summary": "High CPU incident on api-server-1. Automatic scaling to 4 replicas resolved issue. CPU normalized within 2 minutes.",
  "context": {
    "alert_payload": {...},
    "blast_radius": 0.30,
    "affected_users": 3000,
    "estimated_recovery_time": 120
  },
  "reasoning_trace": [
    "SentinelAgent: high_cpu classified as HIGH severity",
    "LibrarianAgent: 2 similar incidents found; runbook restart_high_cpu_container retrieved",
    "RiskAssessor: Blast radius 30%, Risk=75/100",
    "MechanicAgent: Selected restart_high_cpu_container (Tier 1, confidence 94%)",
    "PolicyBrokerAgent: Policy matched — no approval required",
    "ToolRegistryAgent: collect_diagnostics OK; docker_restart OK",
    "VerifierAgent: CPU 14% (threshold 80%) — verification passed"
  ],
  "created_at": "2024-01-15T14:32:00Z",
  "updated_at": "2024-01-15T14:34:30Z"
}
```

**Error Responses:**
```json
// 400 Bad Request - Invalid UUID
{ "detail": "Invalid workflow ID format" }

// 404 Not Found
{ "detail": "Workflow not found" }
```

---

### List Workflows

**Endpoint:** `GET /api/workflows`

**Description:** List workflows with optional filtering and pagination.

**Query Parameters:**
| Parameter | Type | Default | Example |
|-----------|------|---------|---------|
| workflow_type | string | None | "INCIDENT" or "CHANGE" |
| lifecycle_state | string | None | "open", "completed", etc. |
| limit | integer | 10 | 50 |
| offset | integer | 0 | 100 |

**Example Requests:**
```bash
# Get first 10 workflows
curl http://localhost:8000/api/workflows

# Get open incidents
curl "http://localhost:8000/api/workflows?workflow_type=INCIDENT&lifecycle_state=open&limit=50"

# Get completed changes (pagination)
curl "http://localhost:8000/api/workflows?workflow_type=CHANGE&lifecycle_state=completed&offset=50&limit=25"
```

**Response (200 OK):**
```json
[
  {
    "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
    "workflow_type": "INCIDENT",
    "lifecycle_state": "completed",
    "severity": "high",
    "risk_score": 75.5,
    "summary": "High CPU incident...",
    "context": {...},
    "reasoning_trace": [...],
    "created_at": "2024-01-15T14:32:00Z",
    "updated_at": "2024-01-15T14:34:30Z"
  },
  { ... }
]
```

---

## Approval Endpoints

### List Pending Approvals

**Endpoint:** `GET /api/approvals`

**Description:** Get list of pending CAB approvals.

**Query Parameters:**
| Parameter | Type | Default | Example |
|-----------|------|---------|---------|
| status | string | "pending" | "pending", "approved", "rejected" |
| limit | integer | 10 | 50 |
| offset | integer | 0 | 0 |

**Response (200 OK):**
```json
[
  {
    "approval_id": "apr_xyz123",
    "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
    "workflow_type": "INCIDENT",
    "requested_action": "restart_service",
    "risk_score": 75.5,
    "affected_services": ["api-server"],
    "requested_at": "2024-01-15T14:32:00Z",
    "status": "pending",
    "decided_at": null,
    "decided_by": null
  }
]
```

---

### Approve Request

**Endpoint:** `POST /api/approvals/{approval_id}/approve`

**Description:** Approve a pending CAB request.

**URL Parameters:**
| Parameter | Type | Example |
|-----------|------|---------|
| approval_id | string | "apr_xyz123" |

**Request Body:**
```json
{
  "notes": "Verified runbook, low risk for prod"
}
```

**Response (200 OK):**
```json
{
  "approval_id": "apr_xyz123",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "approved",
  "decided_by": "john.doe@company.com",
  "decision_notes": "Verified runbook, low risk for prod",
  "decided_at": "2024-01-15T14:35:00Z"
}
```

**Error Responses:**
```json
// 404 Not Found
{ "detail": "Approval not found" }

// 400 Bad Request - Already decided
{ "detail": "Approval already decided" }
```

---

### Reject Request

**Endpoint:** `POST /api/approvals/{approval_id}/reject`

**Description:** Reject a pending CAB request.

**Request Body:**
```json
{
  "reason": "Requires more testing in staging first"
}
```

**Response (200 OK):**
```json
{
  "approval_id": "apr_xyz123",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "rejected",
  "decided_by": "jane.smith@company.com",
  "decision_notes": "Requires more testing in staging first",
  "decided_at": "2024-01-15T14:35:00Z"
}
```

---

## Policy Endpoints

### List Policies

**Endpoint:** `GET /api/policies`

**Description:** Get all incident response policies.

**Query Parameters:**
| Parameter | Type | Example |
|-----------|------|---------|
| limit | integer | 50 |
| offset | integer | 0 |

**Response (200 OK):**
```json
[
  {
    "policy_id": "pol_12345",
    "name": "High CPU Auto-Scale",
    "priority": 10,
    "rules": {
      "anomaly_type": "high_cpu",
      "services": ["api-server", "api-cache"],
      "environment": "prod",
      "min_severity": "high"
    },
    "approved_actions": ["scale_pods", "drain_node"],
    "requires_approval": false,
    "constraints": {
      "max_blast_radius": 30,
      "max_restart_frequency": 5
    }
  }
]
```

---

### Create Policy

**Endpoint:** `POST /api/policies`

**Description:** Create a new incident response policy.

**Request Body:**
```json
{
  "name": "Database High Latency Response",
  "approval_priority": 5,
  "rules": {
    "anomaly_type": ["high_latency", "database.connection.timeout"],
    "service": "database-primary",
    "environment": "prod",
    "min_severity": "high",
    "min_risk_score": 60
  },
  "approved_actions": ["restart_db", "failover"],
  "requires_manual_approval": true,
  "constraints": {
    "max_blast_radius": 20,
    "max_restart_frequency": 2
  },
  "confidence_gate_threshold": 0.9,
  "confidence_gate_min_runs": 10,
  "confidence_gate_runbook_id": "rb_98765"
}
```

`approval_priority` is the only field that decides precedence when multiple policies match the same incident — only the single lowest-priority-number match is applied (no merging across matches); more specific `rules` do **not** automatically outrank a generic policy. `confidence_gate_runbook_id` is optional — when omitted, the confidence gate (if enabled via `confidence_gate_threshold`/`confidence_gate_min_runs`) evaluates whichever runbook the event_type/service/platform lookup cascade resolves at execution time; setting it pins the gate to one named runbook instead.

**Response (201 Created):**
```json
{
  "policy_id": "pol_54321",
  "name": "Database High Latency Response",
  "confidence_gate_threshold": 0.9,
  "confidence_gate_min_runs": 10,
  "confidence_gate_runbook_id": "rb_98765",
  ...
}
```

---

### Update Policy

**Endpoint:** `PUT /api/policies/{policy_id}`

**Description:** Update an existing policy.

**Response (200 OK):**
```json
{
  "policy_id": "pol_12345",
  "name": "Updated Policy Name",
  ...
}
```

---

### Delete Policy

**Endpoint:** `DELETE /api/policies/{policy_id}`

**Description:** Delete a policy.

**Response (204 No Content)**

---

## Metrics Endpoints

### Get Incident Metrics

**Endpoint:** `GET /api/metrics/incidents`

**Description:** Get incident statistics and trends.

**Response (200 OK):**
```json
{
  "total_incidents": 1247,
  "active_incidents": 52,
  "avg_resolution_time_minutes": 45,
  "approval_rate": 0.68,
  "incidents_by_severity": {
    "critical": 234,
    "high": 567,
    "medium": 345,
    "low": 101
  },
  "incidents_by_type": {
    "high_cpu": 312,
    "disk_full": 198,
    "service_down": 156,
    "database_error": 145
  },
  "top_affected_services": [
    { "service": "api-server", "count": 234 },
    { "service": "database", "count": 198 },
    { "service": "cache", "count": 165 }
  ],
  "timestamp": "2024-01-15T14:40:00Z"
}
```

---

### Get Remediation Metrics

**Endpoint:** `GET /api/metrics/remediation`

**Description:** Get auto-remediation effectiveness metrics.

**Response (200 OK):**
```json
{
  "remediation_success_rate": 0.87,
  "auto_remediation_percentage": 0.68,
  "mttr_minutes": 38,
  "remediation_by_type": {
    "scale_pods": { "attempted": 234, "successful": 203, "success_rate": 0.87 },
    "restart_service": { "attempted": 156, "successful": 132, "success_rate": 0.85 },
    "drain_node": { "attempted": 89, "successful": 78, "success_rate": 0.88 }
  },
  "timestamp": "2024-01-15T14:40:00Z"
}
```

---

## Admin Endpoints

### Get System Statistics

**Endpoint:** `GET /api/admin/statistics`

**Description:** Get system-wide statistics.

**Response (200 OK):**
```json
{
  "total_incidents": 1247,
  "total_workflows": 1456,
  "active_incidents": 52,
  "timestamp": "2024-01-15T14:40:00Z"
}
```

---

### Get System Status

**Endpoint:** `GET /api/admin/system-status`

**Description:** Check system health status.

**Response (200 OK):**
```json
{
  "database_status": "healthy",
  "redis_status": "healthy",
  "timestamp": "2024-01-15T14:40:00Z"
}
```

**Response (503 Service Unavailable - if any component unhealthy)**
```json
{
  "database_status": "unhealthy: connection refused",
  "redis_status": "healthy",
  "timestamp": "2024-01-15T14:40:00Z"
}
```

---

### Delete All Incidents

**Endpoint:** `POST /api/admin/incidents/delete-all`

**Description:** ⚠️ DESTRUCTIVE - Delete all incident data. Requires confirmation.

**Request Body:**
```json
{
  "confirm": "DELETE_ALL_INCIDENTS"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "deleted_count": 1247,
  "message": "Successfully deleted 1247 incidents and all related records",
  "timestamp": "2024-01-15T14:40:00Z"
}
```

---

### Health Check

**Endpoint:** `GET /api/health`

**Description:** Simple health check endpoint.

**Response (200 OK):**
```json
{
  "status": "healthy"
}
```

---

## WebSocket

### Real-Time Workflow Updates

**Endpoint:** `WS /ws/workflows/{workflow_id}`

**Description:** Subscribe to real-time updates for a specific workflow.

**Connection:**
```javascript
const socket = new WebSocket('ws://localhost:8000/ws/workflows/550e8400-e29b-41d4-a716-446655440000');

socket.addEventListener('open', (event) => {
  console.log('Connected to workflow updates');
});

socket.addEventListener('message', (event) => {
  const update = JSON.parse(event.data);
  console.log('Update received:', update);
});

socket.addEventListener('close', (event) => {
  console.log('Disconnected from workflow');
  // Implement reconnection logic
});

socket.addEventListener('error', (event) => {
  console.error('WebSocket error:', event);
});
```

**Message Types:**

**state_change**
```json
{
  "type": "state_change",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "new_state": {
    "lifecycle_state": "in_progress",
    "risk_score": 75.5,
    "severity": "high"
  },
  "agent": "RiskAssessor",
  "reasoning_added": "RiskAssessor: Blast radius 30%, Risk=75/100",
  "timestamp": "2024-01-15T14:32:15Z"
}
```

**approval_requested**
```json
{
  "type": "approval_requested",
  "approval_id": "apr_xyz123",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "action": "restart_service",
  "risk_score": 75.5,
  "timestamp": "2024-01-15T14:32:30Z"
}
```

**execution_complete**
```json
{
  "type": "execution_complete",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "final_state": "completed",
  "duration_seconds": 154,
  "summary": "High CPU incident...",
  "timestamp": "2024-01-15T14:34:30Z"
}
```

**Reconnection with Exponential Backoff:**
```javascript
function connectWebSocket(workflowId) {
  let attempt = 0;
  const maxAttempts = 5;
  
  function connect() {
    const socket = new WebSocket(`ws://localhost:8000/ws/workflows/${workflowId}`);
    
    socket.addEventListener('close', () => {
      if (attempt < maxAttempts) {
        const delay = Math.pow(2, attempt) * 1000; // Exponential backoff
        console.log(`Reconnecting in ${delay}ms...`);
        setTimeout(connect, delay);
        attempt++;
      }
    });
    
    return socket;
  }
  
  return connect();
}
```

---

## Connector Hub Endpoints

### List Connectors

**Endpoint:** `GET /api/connectors`

**Response (200 OK):**
```json
[
  {
    "id": "cnn_abc123",
    "name": "Prod Datadog",
    "connector_type": "datadog",
    "enabled": true,
    "webhook_url": "/api/webhooks/datadog/cnn_abc123",
    "allow_auto_remediation": false,
    "allow_storm_detection": true,
    "default_criticality": "high",
    "created_at": "2026-05-01T10:00:00Z"
  }
]
```

---

### Create Connector

**Endpoint:** `POST /api/connectors`

**Request Body:**
```json
{
  "name": "Prod Splunk",
  "connector_type": "splunk",
  "enabled": true,
  "allow_auto_remediation": false,
  "allow_storm_detection": true,
  "default_criticality": "medium",
  "webhook_secret": "optional-hmac-secret"
}
```

**Connector types:** `servicenow`, `splunk`, `datadog`, `dynatrace`, `prometheus`, `pagerduty`, `zabbix`, `grafana`, `generic`

**PagerDuty outbound escalation:** *(v1.5.0)* set `routing_key` (the PagerDuty Events API v2 integration key, stored encrypted) on a `pagerduty` connector to enable real `notify`/`alert_escalate`/`alert_update` calls — trigger / acknowledge / resolve — against that PagerDuty service. Leave it unset to keep the connector as inbound-ingest only.

**Response (201 Created):**
```json
{
  "id": "cnn_xyz999",
  "webhook_url": "/api/webhooks/splunk/cnn_xyz999",
  ...
}
```

---

### Update Connector

**Endpoint:** `PUT /api/connectors/{connector_id}`

**Request Body:** Any subset of connector fields.

**Response (200 OK):** Updated connector object.

---

### Delete Connector

**Endpoint:** `DELETE /api/connectors/{connector_id}`

**Response (204 No Content)**

---

### Alert Ingest Webhook Endpoints

Alert ingest connectors expose dedicated webhook endpoints. Enable the connector in Connector Hub, copy the webhook URL shown, and configure your monitoring tool to POST to it.

| Connector | Endpoint | Auth Header |
|---|---|---|
| Datadog | `POST /api/connectors/datadog/webhook` | `X-Datadog-Webhook-Secret` |
| Dynatrace | `POST /api/connectors/dynatrace/webhook` | `X-Dynatrace-Webhook-Secret` |
| Prometheus | `POST /api/connectors/prometheus/webhook` | `X-Prometheus-Webhook-Secret` |
| PagerDuty | `POST /api/connectors/pagerduty/webhook` | `X-PagerDuty-Webhook-Secret` |
| Zabbix | `POST /api/connectors/zabbix/webhook` | `X-Zabbix-Webhook-Secret` |
| Grafana | `POST /api/connectors/grafana/webhook` | `X-Grafana-Webhook-Secret` |
| Generic | `POST /api/connectors/generic/webhook` | `X-Webhook-Secret` |

All endpoints normalise the inbound payload and route it through the standard qualification and incident pipeline.

**Grafana note:** Accepts Unified Alerting batch payloads. Firing alerts are ingested; resolved alerts are dropped.

**Generic note:** Payload must include `source`, `event_type`, `resource_name`, and `severity`. The `source` field identifies the originating system — a single Generic connector handles any number of source systems simultaneously.

---

## Notification Teams Endpoints

*(v1.5.0)* A Notification Team is a standalone routing target for the `notify` action (and its legacy aliases `alert_escalate` / `alert_update` / `send_alert`) — independent of ServiceNow/CMDB. A runbook step's `team` argument matches a team's `name` (case-insensitive); if no team is given, the team is unknown, or it's disabled, the platform's global Slack/PagerDuty/email defaults are used instead.

### List Notification Teams

**Endpoint:** `GET /api/notification-teams`

**Response (200 OK):**
```json
[
  {
    "team_id": "f1e2d3c4-...",
    "name": "infraTeam",
    "slack_channel": "#infrateam",
    "email_recipients": "infra-oncall@example.com",
    "pagerduty_routing_key_set": true,
    "webhook_url": "https://hooks.example.com/infra",
    "webhook_secret_set": true,
    "enabled": true,
    "created_at": "2026-06-29T00:00:00Z"
  }
]
```

Secret fields (`pagerduty_routing_key`, `webhook_secret`) are never returned in plaintext — only a `*_set` boolean indicating whether a value is configured.

---

### Create Notification Team

**Endpoint:** `POST /api/notification-teams`

**Request Body:**
```json
{
  "name": "infraTeam",
  "slack_channel": "#infrateam",
  "email_recipients": "infra-oncall@example.com,infra-lead@example.com",
  "pagerduty_routing_key": "R0ABC123XYZ",
  "webhook_url": "https://hooks.example.com/infra",
  "webhook_secret": "optional-shared-secret",
  "enabled": true
}
```

All channel fields are optional and independent — configure any combination.

**Response (201 Created):** Created team object (secrets masked as above).

---

### Update Notification Team

**Endpoint:** `PUT /api/notification-teams/{team_id}`

**Request Body:** Any subset of team fields. For secret fields (`pagerduty_routing_key`, `webhook_secret`): omit/blank to keep the existing value, send `"-"` to clear it, or send a new value to replace it.

---

### Delete Notification Team

**Endpoint:** `DELETE /api/notification-teams/{team_id}`

**Response (204 No Content)**

---

## Event Storm Endpoints

### List Storms

**Endpoint:** `GET /api/storms`

**Query Parameters:**
| Parameter | Type | Default | Example |
|-----------|------|---------|---------|
| status | string | — | `active`, `resolved`, `released` |
| limit | integer | 20 | 50 |

**Response (200 OK):**
```json
[
  {
    "id": "storm_001",
    "lifecycle_state": "awaiting_manual",
    "pattern": "resource_exhaustion",
    "hypothesis": "Shared upstream Redis reaching memory limit — cascading OOM across 4 services",
    "affected_incident_count": 5,
    "root_cause_candidates": ["redis-primary"],
    "confidence": 0.82,
    "created_at": "2026-05-30T09:15:00Z"
  }
]
```

---

### Get Storm Details

**Endpoint:** `GET /api/storms/{storm_id}`

**Response (200 OK):** Full storm object including `child_incidents`, `neo4j_analysis`, `llm_hypothesis`.

---

### Release Storm (False Positive)

**Endpoint:** `POST /api/storms/{storm_id}/release`

Dismisses the storm as a false positive. Returns child incidents to open state and re-queues them through individual pipelines.

**Response (200 OK):**
```json
{"detail": "Storm released. 5 children returned to open state."}
```

---

### Resolve Storm

**Endpoint:** `POST /api/storms/{storm_id}/resolve`

Marks root cause as fixed. Bulk-closes all child incidents and the storm parent.

**Response (200 OK):**
```json
{"detail": "Storm and 5 child incidents resolved."}
```

---

## Storm Detection Settings

### Get Storm Settings

**Endpoint:** `GET /api/settings/storm`

**Response (200 OK):**
```json
{
  "storm.enabled": true,
  "storm.window_seconds": 120,
  "storm.min_incidents": 3,
  "storm.min_resources": 2,
  "storm.merge_window_minutes": 5,
  "storm.require_cab_approval": true,
  "storm.auto_hold_children": true,
  "storm.llm_hypothesis_enabled": true,
  "storm.neo4j_topology_enabled": true,
  "storm.pipeline_hold_seconds": 0,
  "storm.exclude_external_events": false
}
```

### Update Storm Settings

**Endpoint:** `PUT /api/settings/storm`

**Request Body:** Any subset of the settings keys shown above. Changes take effect immediately — no restart required.

**Response (200 OK):** Updated settings object.

---

## User Management Endpoints

### List Users

**Endpoint:** `GET /api/users`  
**Required role:** `admin` or `itom_admin`

**Response (200 OK):**
```json
[
  {
    "id": "usr_001",
    "email": "jane.smith@company.com",
    "role": "operator",
    "is_active": true,
    "created_at": "2026-04-01T09:00:00Z"
  }
]
```

---

### Create User

**Endpoint:** `POST /api/users`  
**Required role:** `admin` or `itom_admin`

**Request Body:**
```json
{
  "email": "jane.smith@company.com",
  "password": "SecureP@ssw0rd!",
  "role": "operator"
}
```

**Roles:** `admin`, `itom_admin`, `operator`, `viewer`

**Response (201 Created):** Created user object (password not returned).

---

### Update User

**Endpoint:** `PUT /api/users/{user_id}`  
**Required role:** `admin` or `itom_admin`

**Request Body:** Any subset of `email`, `role`, `is_active`, `password`.

---

### Delete User

**Endpoint:** `DELETE /api/users/{user_id}`  
**Required role:** `admin`

**Response (204 No Content)**

---

## Monitoring Events Endpoint

### Ingest Monitoring Event

**Endpoint:** `POST /api/monitoring-events`

Receives monitoring signals from `watcher_brain`, external connectors, or scripts. Runs the qualification pipeline and, if the event scores above threshold, opens an incident workflow automatically.

**Authentication:** Bearer JWT or `X-API-Key` header.

**Request Body:**
```json
{
  "source": "watcher_brain",
  "event_type": "service_unresponsive",
  "resource_name": "agentic_nginx_prod_test",
  "raw_criticality": "critical",
  "signal_value": 101.0,
  "signal_threshold": 200.0,
  "anomaly_process": null,
  "raw_payload": {
    "title": "HTTP endpoint down: agentic_nginx_prod_test",
    "description": "[HTTP] agentic_nginx_prod_test (101ms): [Errno 111] Connection refused"
  }
}
```

| Field | Required | Description |
|---|---|---|
| `source` | ✓ | Sender identifier (`watcher_brain`, `prometheus`, `zabbix`, etc.) |
| `event_type` | ✓ | Alert category — see event type list below |
| `resource_name` | ✓ | CI name. Must match CMDB for full confidence scoring |
| `raw_criticality` | ✓ | `info` / `warning` / `critical` |
| `signal_value` | — | Observed metric value (e.g. response time ms) |
| `signal_threshold` | — | Limit that was exceeded |
| `anomaly_process` | — | Process name if applicable |
| `raw_payload` | — | Arbitrary JSON for audit trail; `title` and `description` keys shown in the Events UI |

**Event types:** `high_cpu`, `cpu_spike`, `high_memory`, `memory_surge`, `disk_full`, `service_down`, `service_unresponsive`, `health_check_failed`, `connection_spike`, `high_latency`, `high_syscall_intensity`, `log_error`, `high_error_rate`, `metrics_anomaly`, `certificate_expiry`, `database_error`, `network_issue`, `condition_cleared`

**Response (201 Created):**
```json
{
  "event_id": "38fb996d-cb0a-42b7-93c1-07b26e5ac57e",
  "source": "watcher_brain",
  "event_type": "service_unresponsive",
  "resource_name": "agentic_nginx_prod_test",
  "raw_criticality": "critical",
  "qualification_score": 20.0,
  "qualified_as_incident": false,
  "incident_workflow_id": null,
  "status": "dismissed",
  "detected_at": "2026-06-19T13:12:15.921668",
  "qualification_reason": "DISMISSED: 20.0/50.0 — critical(×2.0) → base 100 × test(×0.2) = 20.0",
  "confidence": 100.0
}
```

**Deduplication:** The endpoint is **idempotent for active conditions**. If `(resource_name, event_type)` is already open (a prior event was received and the condition has not cleared), subsequent submissions return the original `event_id` without creating a new database row or re-running qualification. The dedup state resets when:
- A `condition_cleared` event is received for the resource, OR
- The linked incident is resolved or closed (by operator or automation)

**Special case — `condition_cleared`:** Closes all open (non-terminal) incidents for `resource_name`. Also resets the condition-state dedup so the next alert on that resource fires fresh.

```json
{
  "source": "watcher_brain",
  "event_type": "condition_cleared",
  "resource_name": "agentic_nginx_prod_test",
  "raw_criticality": "info",
  "raw_payload": { "original_event_type": "service_unresponsive" }
}
```

---

## Health Endpoints

### Health Check

**Endpoint:** `GET /api/health`  
**Authentication:** Not required

**Response (200 OK):**
```json
{"status": "healthy", "version": "1.5.0", "timestamp": "2026-05-30T10:00:00Z"}
```

### Readiness Check

**Endpoint:** `GET /api/ready`  
**Authentication:** Not required

Returns 200 when all subsystems (PostgreSQL, Redis, Neo4j) are reachable.

**Response (200 OK):**
```json
{
  "status": "ready",
  "postgres": "ok",
  "redis": "ok",
  "neo4j": "ok"
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "not_ready",
  "postgres": "ok",
  "redis": "ok",
  "neo4j": "timeout after 5s"
}
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | OK | Request succeeded |
| 201 | Created | Resource created successfully |
| 204 | No Content | Delete successful |
| 400 | Bad Request | Invalid parameters |
| 404 | Not Found | Resource doesn't exist |
| 500 | Internal Server Error | Unexpected error |
| 503 | Service Unavailable | Database/Redis down |

### Error Response Format

```json
{
  "detail": "Human-readable error message"
}
```

### Example Error Responses

**Invalid Input (400):**
```json
{
  "detail": "value is not a valid enumeration member; permitted: 'critical', 'high', 'medium', 'low', 'info'"
}
```

**Resource Not Found (404):**
```json
{
  "detail": "Workflow not found"
}
```

**Server Error (500):**
```json
{
  "detail": "An unexpected error occurred: database connection lost"
}
```

---

## Rate Limiting

**Note:** Rate limiting not implemented in v2.0.

**Recommendation for Production:**
- Add middleware to limit requests per IP/user
- Suggest: 1000 requests/minute per IP
- Suggest: 100 requests/minute for write operations

---

## API Versioning

**Current Version:** v1 (no prefix in v2.0)

**Future Strategy:**
- v2 endpoints at `/api/v2/...` (when breaking changes introduced)
- Maintain backward compatibility with v1 during transition period
- Document migration guide for clients

---

## Client Libraries

### Python Client
```python
import requests

class Axiometica AIRClient:
    def __init__(self, base_url='http://localhost:8000'):
        self.base_url = base_url
    
    def submit_incident(self, severity, type_, resource_name, description=None):
        response = requests.post(
            f'{self.base_url}/api/workflows/incident',
            json={
                'severity': severity,
                'type': type_,
                'resource_name': resource_name,
                'description': description
            }
        )
        return response.json()
    
    def get_workflow(self, workflow_id):
        response = requests.get(
            f'{self.base_url}/api/workflows/{workflow_id}'
        )
        return response.json()

# Usage
client = Axiometica AIRClient()
workflow = client.submit_incident('high', 'high_cpu', 'api-server-1')
print(workflow['workflow_id'])
```

### JavaScript/TypeScript Client
```typescript
class Axiometica AIRClient {
  constructor(private baseUrl = 'http://localhost:8000') {}
  
  async submitIncident(incident: {
    severity: string;
    type: string;
    resource_name: string;
    description?: string;
  }) {
    const response = await fetch(`${this.baseUrl}/api/workflows/incident`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(incident)
    });
    return response.json();
  }
  
  async getWorkflow(workflowId: string) {
    const response = await fetch(`${this.baseUrl}/api/workflows/${workflowId}`);
    return response.json();
  }
}
```

---

## Runbooks Endpoints

### List Runbooks

**Endpoint:** `GET /api/runbooks`

**Query Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `event_type` | string | — | Filter by event type code |
| `platform` | string | — | Filter by platform (`docker`, `kubernetes`, `linux`, `any`) |
| `enabled` | boolean | false | When true, return only enabled runbooks |
| `limit` | integer | 100 | Max results |

**Response (200 OK):** Array of runbook objects including `runbook_id`, `name`, `event_type`, `platform`, `confidence`, `success_rate`, `total_executions`, `is_seeded`, `enabled`.

---

### Get Runbook

**Endpoint:** `GET /api/runbooks/{runbook_id}`

**Response (200 OK):** Full runbook object including `diagnostics`, `actions`, `verification_steps`, `source_steps`.

---

### Create Runbook

**Endpoint:** `POST /api/runbooks`

**Request Body:**
```json
{
  "name": "High CPU — Diagnose and Remediate",
  "event_type": "infrastructure.compute.cpu_high",
  "platform": "docker",
  "service": "checkout",
  "diagnostics": [],
  "actions": [],
  "verification_steps": [],
  "confidence": 0.80,
  "blast_radius": 2,
  "enabled": true
}
```

**Response (201 Created):** Created runbook object.

---

### Update Runbook

**Endpoint:** `PUT /api/runbooks/{runbook_id}`

**Request Body:** Any subset of runbook fields.

**Response (200 OK):** Updated runbook object.

---

### Delete Runbook

**Endpoint:** `DELETE /api/runbooks/{runbook_id}`

**Response (204 No Content)**

**Note:** Seeded (built-in) runbooks return `403 Forbidden`. Disable them instead via the `enabled` field.

---

### Generate Runbook (AI)

**Endpoint:** `POST /api/runbooks/generate`

**Request Body:**
```json
{
  "description": "Restart a high-memory container and verify recovery",
  "event_type": "infrastructure.memory.high",
  "platform": "docker"
}
```

**Response (200 OK):** AI-generated runbook in graph editor format (`steps`, `graph_edges`). Import directly into the Visual Runbook Editor.

---

## Approved Actions Endpoints

### List Actions

**Endpoint:** `GET /api/approved-actions`

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `category` | string | `diagnostic`, `remediation_safe`, `remediation_intrusive`, `notify` |
| `enabled_only` | boolean | Return only enabled actions |

**Response (200 OK):** Array of action objects including `action_id`, `tool_name`, `name`, `category`, `blast_radius`, `is_builtin`, `enabled`.

---

### Get Action

**Endpoint:** `GET /api/approved-actions/{action_id}`

---

### Create Action

**Endpoint:** `POST /api/approved-actions`

**Request Body:**
```json
{
  "tool_name": "custom_health_check",
  "name": "Custom Health Check",
  "description": "Runs a custom health probe against the target",
  "command": "curl -sf http://{resource_name}/health",
  "category": "diagnostic",
  "blast_radius": 1,
  "requires_approval": false,
  "enabled": true,
  "parameters": []
}
```

**Response (201 Created):** Created action object.

---

### Update Action

**Endpoint:** `PUT /api/approved-actions/{action_id}`

**Request Body:** Any subset of action fields. `output_fields` is locked for built-in actions.

---

### Delete Action

**Endpoint:** `DELETE /api/approved-actions/{action_id}`

**Response (204 No Content)**

**Note:** Built-in actions (`is_builtin=true`) return `403 Forbidden`. Disable them via `enabled=false` instead.

---

### Validate Process

**Endpoint:** `POST /api/approved-actions/validate-process`

Checks whether a process name is permitted by an action's allow/deny rules before execution.

**Request Body:**
```json
{
  "tool_name": "kill_process",
  "process_name": "nginx"
}
```

**Response (200 OK):**
```json
{
  "allowed": true,
  "matched_rule": { "priority": 1, "allow": true, "pattern": "^nginx$", "description": "Allow nginx" },
  "reason": "Allowed by rule (priority 1): Allow nginx"
}
```

---

## Event Type Taxonomy Endpoints

The event type taxonomy is the canonical registry of all alert/event codes the platform understands. System-defined types (`is_system=true`) cannot be deleted; operators can add custom types.

### List Event Types

**Endpoint:** `GET /api/event-types`

**Query Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by domain, e.g. `infrastructure`, `application`, `container` |
| `enabled_only` | boolean | true | Return only enabled types |
| `q` | string | — | Search label, code, or aliases |

**Response (200 OK):** Array of `{ code, label, description, category, aliases, is_system, enabled }`.

---

### List Domains

**Endpoint:** `GET /api/event-types/domains`

**Response (200 OK):**
```json
[
  { "category": "application", "count": 24, "enabled_count": 24 },
  { "category": "infrastructure", "count": 18, "enabled_count": 17 }
]
```

---

### Get Event Type

**Endpoint:** `GET /api/event-types/{code}`

Example: `GET /api/event-types/infrastructure.compute.cpu_high`

---

### Create Custom Event Type

**Endpoint:** `POST /api/event-types`

**Request Body:**
```json
{
  "code": "custom.app.payment_timeout",
  "label": "Payment Timeout",
  "description": "Payment service exceeded response threshold",
  "aliases": ["payment_slow", "checkout_timeout"]
}
```

Code must match `^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*){1,3}$`. `is_system` is always `false` for API-created types.

**Response (201 Created):** Created event type object.

---

### Update Event Type

**Endpoint:** `PATCH /api/event-types/{code}`

Updates `label`, `description`, `aliases`, or `enabled`. Works on both system and custom types.

---

### Delete Event Type

**Endpoint:** `DELETE /api/event-types/{code}`

**Response (204 No Content)**

**Note:** System types (`is_system=true`) return `403 Forbidden`. Set `enabled=false` to hide them instead.

---

## Conclusion

This API reference provides comprehensive documentation for all Axiometica AIR v2 endpoints. For interactive testing, use the built-in Swagger UI at `/docs`.

