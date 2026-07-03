# Axiometica AIR v2 - Data Flows & Integration Points

## Table of Contents
1. [Incident Submission & Execution Flow](#incident-submission--execution-flow)
2. [Approval Workflow Flow](#approval-workflow-flow)
3. [Change Deployment Flow](#change-deployment-flow)
4. [Real-Time Update Flow](#real-time-update-flow)
5. [External System Integration](#external-system-integration)
6. [Database Persistence Flow](#database-persistence-flow)
7. [Event Bus & Pub/Sub Flow](#event-bus--pubsub-flow)
8. [Background Job Flow](#background-job-flow)

---

## Incident Submission & Execution Flow

### Complete Flow Diagram

```
USER/MONITORING SYSTEM
  │
  └─ Send monitoring signal
     │
     ├─ POST /api/monitoring-events          ← primary ingest path (watcher / connectors)
     │  {
     │    "source": "watcher_brain",
     │    "event_type": "service_unresponsive",
     │    "resource_name": "api-server-1",
     │    "raw_criticality": "critical"
     │  }
     │
     │  CONDITION-STATE DEDUP CHECK
     │  └─ If event_condition_state[resource, event_type].status == 'open'
     │     → return existing event_id (no new row, no qualification)
     │
     │  QUALIFICATION SCORING
     │  └─ score = criticality_score × event_type_multiplier × environment_multiplier
     │     qualified = (score ≥ threshold) AND (score ≥ criticality_floor)
     │
     │  OPEN CONDITION
     │  └─ INSERT/UPDATE event_condition_state SET status='open', last_event_id=...
     │
     │  IF qualified → create incident workflow (see below)
     │  IF not qualified → status='dismissed', condition stays open (dedup absorbs retries)
     │
     ├─ POST /api/workflows/incident          ← manual direct incident creation (legacy)
     │  {
     │    "severity": "high",
     │    "type": "high_cpu",
     │    "resource_name": "api-server-1",
     │    "description": "CPU 89%"
     │  }
     │
     ↓
FASTAPI SUBMIT_INCIDENT ENDPOINT
  │
  ├─ Request validation (Pydantic)
  │  └─ Check severity ∈ {critical, high, medium, low, info}
  │  └─ Check type is valid event_type
  │  └─ Check resource_name not empty
  │
  ├─ Create WorkflowState object
  │  {
  │    workflow_id: UUID,
  │    workflow_type: INCIDENT,
  │    lifecycle_state: OPEN,
  │    context: {alert_payload: {...}},
  │    severity: HIGH,
  │    risk_score: None,
  │    reasoning_trace: [],
  │    created_at: now(),
  │    summary: None
  │  }
  │
  ├─ Save to database (Repository pattern)
  │  └─ WorkflowRepository.save(state)
  │     └─ INSERT INTO workflow_states (...)
  │        VALUES (workflow_id, workflow_type, ...)
  │
  ├─ Queue background task (Celery)
  │  └─ execute_workflow_task.delay(
  │       workflow_id=wf_abcde,
  │       workflow_type="INCIDENT"
  │     )
  │
  ├─ Generate summary (background task)
  │  ├─ If LLM available → Call OpenAI/Anthropic API
  │  │  └─ Update database: summary = "AI-generated summary"
  │  │
  │  └─ Else → Fall back to platform context
  │     └─ Update database: summary = "Platform-generated summary"
  │
  ├─ Return WorkflowResponse
  │  {
  │    "workflow_id": "wf_abcde",
  │    "workflow_type": "INCIDENT",
  │    "lifecycle_state": "open",
  │    "severity": "high",
  │    "summary": null  (will be filled async)
  │  }
  │
  └─ Client receives 201 Created + workflow_id

CELERY BACKGROUND WORKER (execute_workflow_task)
  │
  ├─ Load workflow from database
  │  └─ WorkflowRepository.get(UUID(wf_abcde))
  │
  ├─ Load workflow definition (YAML)
  │  └─ workflows/incident_v1.yaml
  │
  ├─ Initialize WorkflowEngine
  │  └─ Create event bus instance
  │  └─ Register all agents
  │
  ├─ Execute steps sequentially
  │  │
  │  ├─ STEP 1: SentinelAgent
  │  │  ├─ Parse incident: type=high_cpu, severity=high
  │  │  ├─ Classify anomaly type, severity, service, environment
  │  │  ├─ Create IncidentWorkflowContext dataclass
  │  │  └─ Return updated context
  │  │
  │  ├─ STEP 2: LibrarianAgent
  │  │  ├─ Query incident history for similar events on api-server
  │  │  ├─ Retrieve relevant runbook metadata
  │  │  ├─ Attach prior remediation outcomes for RiskAssessor
  │  │  └─ Return updated context
  │  │
  │  ├─ STEP 3: RiskAssessor
  │  │  ├─ Query CMDB: what depends on api-server?
  │  │  ├─ Calculate blast_radius from Neo4j dependency graph
  │  │  ├─ Produce 0–100 composite risk score (multi-factor model)
  │  │  ├─ Update context: risk_score, risk_factors, risk_breakdown
  │  │  └─ Return updated context
  │  │
  │  ├─ STEP 4: MechanicAgent
  │  │  ├─ 5-tier runbook selection waterfall (exact match → LLM synthesis → fallback)
  │  │  ├─ Select highest-confidence runbook available
  │  │  ├─ Update context: selected_runbook, selection_tier, confidence_score
  │  │  └─ Return updated context
  │  │
  │  ├─ STEP 5: PolicyBrokerAgent
  │  │  ├─ Query policies: anomaly=high_cpu, service=api-server
  │  │  ├─ Match found: determine approval_required, blast_radius_limit, allowed_actions
  │  │  ├─ Update context: policy_match, approval_required
  │  │  └─ Return updated context (or pause for CAB approval if required)
  │  │
  │  ├─ STEP 6: ToolRegistryAgent
  │  │  ├─ Dispatch runbook steps in order
  │  │  ├─ Honour on_failure: abort (default) or continue per step
  │  │  ├─ Update context: execution_log, step_results, remediation_outcome
  │  │  └─ Return updated context
  │  │
  │  └─ STEP 7: VerifierAgent
  │     ├─ Run post-remediation verification steps from runbook
  │     ├─ Check metric thresholds and health endpoints
  │     ├─ Set remediation_outcome = succeeded or failed
  │     └─ Set final lifecycle_state
  │
  ├─ Publish state change events
  │  ├─ EVENT: sentinel_agent_complete
  │  ├─ EVENT: risk_assessed (risk_score=0-100)
  │  ├─ EVENT: policy_matched
  │  ├─ EVENT: remediation_executed
  │  ├─ EVENT: verification_complete
  │  └─ EVENT: workflow_completed (state=resolved|failed|aborted)
  │
  ├─ Update database
  │  └─ workflow_states.update()
  │     ├─ lifecycle_state = COMPLETED
  │     ├─ reasoning_trace = [steps]
  │     ├─ updated_at = now()
  │     └─ final summary stored
  │
  ├─ Close condition state (if terminal: resolved / closed)
  │  └─ event_condition_state[resource_name].status = 'closed'
  │     → next alert on this resource fires a fresh event (dedup reset)
  │
  └─ Return task result
     {
       "status": "completed",
       "workflow_id": "wf_abcde",
       "duration_ms": 2450,
       "steps_executed": 6
     }

EVENT BUS (PostgreSQL LISTEN)
  │
  └─ For each published event:
     │
     ├─ Event stored in events table
     │  INSERT INTO events (event_id, workflow_id, event_type, data, ...)
     │
     ├─ NOTIFY subscribers
     │  NOTIFY workflow_updates, 'wf_abcde:incident_triage_complete'
     │
     └─ Subscribers receive notification
        ├─ WebSocket broadcaster
        └─ Metrics aggregator

WEBSOCKET BROADCASTER
  │
  └─ For each connected client to workflow wf_abcde:
     │
     ├─ Send state update
     │  {
     │    "type": "state_change",
     │    "workflow_id": "wf_abcde",
     │    "new_state": {...},
     │    "agent_trace": "Triage complete",
     │    "timestamp": "2024-01-15T14:32:45Z"
     │  }
     │
     └─ Client WebSocket receives and updates UI

FRONTEND (React)
  │
  └─ WebSocket message received
     │
     ├─ Update state: workflow.lifecycle_state = "COMPLETED"
     ├─ Update metric: workflow.risk_score = 120
     ├─ Add trace: workflow.reasoning_trace.push("Triage complete")
     │
     ├─ Re-render components:
     │  ├─ IncidentCard: status badge → green checkmark
     │  ├─ MetricsPanel: risk_score updated
     │  ├─ TracePanel: new trace line added
     │  └─ StatusBadge: "Auto-Remediated" appears
     │
     └─ User sees real-time updates without polling
```

### Data at Each Stage

**At Submission:**
```json
{
  "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_type": "INCIDENT",
  "lifecycle_state": "open",
  "context": {
    "alert_payload": {
      "severity": "high",
      "type": "high_cpu",
      "resource_name": "api-server-1",
      "description": "CPU 89%, load average 12.5"
    }
  },
  "severity": null,
  "risk_score": null,
  "summary": null,
  "reasoning_trace": [],
  "created_at": "2024-01-15T14:32:00Z"
}
```

**After SentinelAgent:**
```json
{
  // ... same as above, plus:
  "severity": "high",
  "reasoning_trace": [
    "SentinelAgent: high_cpu classified as HIGH severity",
    "Correlated with service api-server (critical impact)"
  ]
}
```

**After RiskAssessor:**
```json
{
  // ... previous, plus:
  "risk_score": 87,
  "context": {
    // ... previous context, plus:
    "blast_radius": 0.30,
    "affected_users": 3000,
    "estimated_recovery_time": 120
  },
  "reasoning_trace": [
    // ... previous, plus:
    "RiskAssessor: Blast radius 30%, Risk=87/100"
  ]
}
```

**Final (Completed):**
```json
{
  // ... all previous, plus:
  "lifecycle_state": "resolved",
  "remediation_outcome": "succeeded",
  "resolution_source": "automated_remediation",
  "summary": "High CPU on api-server-1 resolved via automated runbook. Container restarted successfully. CPU normalised within 2 minutes.",
  "reasoning_trace": [
    "SentinelAgent: high_cpu classified as HIGH severity",
    "LibrarianAgent: 2 similar incidents found; runbook restart_high_cpu_container retrieved",
    "RiskAssessor: Blast radius 30%, Risk=87/100",
    "MechanicAgent: Selected restart_high_cpu_container (Tier 1, confidence 94%)",
    "PolicyBrokerAgent: Policy matched — approval not required at risk 87",
    "ToolRegistryAgent: collect_diagnostics OK; docker_restart OK",
    "VerifierAgent: CPU 14% (< 80% threshold) — verification passed"
  ],
  "updated_at": "2024-01-15T14:34:34Z"
}
```

---

## Approval Workflow Flow

```
REMEDIATION REQUIRES APPROVAL (from agent)
  │
  └─ Agent determines action needs CAB approval
     │
     ├─ Check policy: action not in approved_actions
     ├─ Risk score > 50: high-risk change
     └─ Escalate approval needed

CREATE APPROVAL RECORD
  │
  ├─ INSERT INTO approvals (
  │    approval_id = UUID,
  │    workflow_id = wf_abcde,
  │    status = 'pending',
  │    requested_at = now(),
  │    requested_action = 'restart_service (api-server)',
  │    risk_score = 75
  │  )
  │
  └─ Publish: approval_requested event
     │
     ├─ EVENT: approval_requested
     │  {
     │    approval_id: apr_xyz,
     │    workflow_id: wf_abcde,
     │    action_description: "Restart api-server (Production)",
     │    risk_score: 75,
     │    affected_services: ["api-server"],
     │    requested_by: "system",
     │    requested_at: now()
     │  }
     │
     └─ Notify CAB team
        ├─ Slack message to #change-board
        ├─ Email to cab-members@company.com
        └─ Frontend approval queue updated

CAB MEMBER REVIEWS & DECIDES
  │
  ├─ Opens frontend approval queue
  ├─ Sees pending approvals sorted by priority
  ├─ Clicks on approval card
  │
  ├─ Reads details:
  │  ├─ Workflow ID
  │  ├─ Requested action
  │  ├─ Risk assessment
  │  ├─ Affected services
  │  ├─ Runbook (if available)
  │  └─ Previous similar decisions
  │
  └─ Makes decision: [Approve] [Reject]

APPROVAL DECISION RECORDED
  │
  ├─ POST /api/approvals/{id}/approve
  │  {
  │    "decision": "approved",
  │    "notes": "Verified runbook, low risk for prod",
  │    "decided_by": "john.doe@company.com"
  │  }
  │
  ├─ UPDATE approvals SET
  │    status = 'approved',
  │    decided_by = 'john.doe@company.com',
  │    decision_notes = 'Verified runbook, low risk...',
  │    decided_at = now()
  │
  └─ Publish: approval_decided event

WORKFLOW RESUMES EXECUTION
  │
  ├─ Workflow engine checks approval status
  ├─ Status = 'approved' → Resume execution
  │  │
  │  └─ Continue from where it left off
  │     │
  │     ├─ Execute approved action
  │     │  └─ ToolRegistryAgent resumes runbook execution
  │     │     ├─ Execute approved runbook step (e.g., docker_restart)
  │     │     ├─ Honour on_failure: abort policy per step
  │     │     └─ Update context: execution_log, remediation_outcome
  │     │
  │     └─ VerifierAgent runs post-remediation checks
  │        ├─ Check metric thresholds from runbook
  │        ├─ Probe health endpoints
  │        └─ Set final lifecycle_state (resolved / failed / aborted)
  │
  ├─ Publish: execution_resumed event
  ├─ Publish: action_executed event
  ├─ Workflow transitions to COMPLETED
  │
  └─ Send notification
     ├─ Slack: "Approved action executed successfully"
     ├─ Dashboard: Workflow marked complete
     └─ Email: Requestor notified

APPROVAL TIMEOUT (72 hours)
  │
  ├─ Celery scheduled task: handle_approval_timeout
  ├─ Check: is status still 'pending' AND created_at + 72h < now()?
  │
  ├─ If yes:
  │  ├─ UPDATE approvals SET status = 'rejected'
  │  ├─ UPDATE approvals SET decided_by = 'system'
  │  ├─ UPDATE approvals SET decision_notes = 'Auto-rejected: 72h timeout'
  │  │
  │  ├─ Publish: approval_expired event
  │  └─ Notify: Requestor
  │     ├─ Slack: "Approval expired, action cancelled"
  │     └─ Email: Requestor and workflow creator
  │
  └─ Workflow transitions to FAILED
```

---

## Change Deployment Flow

```
CHANGE SUBMISSION
  │
  └─ POST /api/workflows/change
     {
       "change_type": "standard",
       "description": "Deploy API v2.4.1",
       "affected_services": ["api-server"],
       "rollback_plan": "Revert image, restart"
     }

VALIDATION & RISK ASSESSMENT
  │
  ├─ Validate change details (Pydantic)
  ├─ Assess risk level
  │  └─ change_type="standard" → low risk
  │  └─ Risk score calculated based on services
  │
  ├─ Create ChangeWorkflow
  │  └─ lifecycle_state = OPEN
  │  └─ status = PENDING_APPROVAL
  │
  └─ Save to database

APPROVAL ROUTING
  │
  ├─ Route to appropriate CAB
  │  └─ affected_services = [api-server]
  │  └─ Find CAB owner for api-server
  │  └─ Assign approval to that person/team
  │
  ├─ Send approval notification (same flow as incident approval)
  └─ Wait for decision (72h timeout applies)

DEPLOYMENT SCHEDULING (After Approval)
  │
  ├─ Schedule deployment window
  │  ├─ Not during peak hours
  │  ├─ Not within N hours of previous deployment
  │  └─ Notify operations team
  │
  ├─ Pre-deployment checks
  │  ├─ All services healthy
  │  ├─ Dependencies available
  │  ├─ Backups completed
  │  ├─ Rollback plan verified
  │  └─ On-call team ready
  │
  └─ lifecycle_state = SCHEDULED

DEPLOYMENT EXECUTION
  │
  ├─ Start blue-green deployment
  │  │
  │  ├─ GREEN environment (new version)
  │  │  ├─ Pull new image: api-server:v2.4.1
  │  │  ├─ Start containers
  │  │  ├─ Run smoke tests
  │  │  ├─ Verify health checks pass
  │  │  └─ Wait for ready signal
  │  │
  │  ├─ Traffic routing
  │  │  ├─ Canary: route 10% traffic to green
  │  │  ├─ Monitor: Error rates, latency
  │  │  ├─ Duration: 5 minutes
  │  │  │  ├─ If errors spike → Rollback to blue (OLD)
  │  │  │  └─ Else → Continue
  │  │  │
  │  │  ├─ Gradual shift: 25%, 50%, 75%, 100%
  │  │  ├─ Monitor at each step (2 min intervals)
  │  │  └─ Final: 100% traffic on green (new)
  │  │
  │  └─ Blue environment (old version) shutdown
  │     └─ Remove old containers
  │
  ├─ Post-deployment verification
  │  ├─ Smoke tests (API endpoints)
  │  ├─ Integration tests
  │  ├─ Performance baselines (latency, throughput)
  │  ├─ Error logs (no increase in errors)
  │  └─ User feedback (health check dashboard)
  │
  ├─ Update state
  │  ├─ lifecycle_state = EXECUTING
  │  ├─ deployment_status = SUCCESS
  │  ├─ deployment_end_time = now()
  │  └─ downtime_duration = 0 (blue-green has zero downtime)
  │
  └─ Publish events
     ├─ deployment_started
     ├─ deployment_progress (canary routing updates)
     ├─ deployment_complete
     └─ deployment_verified

POST-DEPLOYMENT CLOSURE
  │
  ├─ Update workflow
  │  ├─ lifecycle_state = COMPLETED
  │  ├─ duration = end_time - start_time
  │  └─ summary = "Deployed API v2.4.1 successfully, zero downtime"
  │
  ├─ Notify stakeholders
  │  ├─ Slack: "Deployment complete, all checks passed"
  │  ├─ Email: Change details, results, timeline
  │  └─ Dashboard: Change marked as completed
  │
  ├─ Store audit record
  │  ├─ Who: approver, deployer
  │  ├─ What: deployment details
  │  ├─ When: timestamps
  │  ├─ Result: success/failure
  │  └─ Evidence: metrics before/after
  │
  └─ Archive workflow
     └─ Available for compliance review

ROLLBACK (If Needed)
  │
  ├─ Detect: Errors spike during deployment
  │  └─ Error rate > threshold or latency > SLA
  │
  ├─ Automatic rollback triggered
  │  ├─ Route 100% traffic back to blue (old)
  │  ├─ Drain connections from green
  │  ├─ Verify rollback complete
  │  └─ Publish: deployment_rolled_back event
  │
  ├─ Update state
  │  ├─ lifecycle_state = ROLLED_BACK
  │  ├─ deployment_status = FAILED
  │  └─ failure_reason = "Error rate spike detected"
  │
  └─ Notify & investigate
     ├─ Alert: Engineering team
     ├─ Page: On-call engineer
     ├─ Store: Failure logs for investigation
     └─ Schedule: Post-mortem
```

---

## Real-Time Update Flow

```
STATE CHANGE IN WORKFLOW ENGINE
  │
  └─ Agent updates WorkflowState
     │
     ├─ Example: SentinelAgent completes
     │  ├─ state.severity = Severity.HIGH
     │  ├─ state.reasoning_trace.append("Classified as HIGH")
     │  └─ return state
     │
     └─ Workflow engine persists state

DATABASE UPDATE
  │
  └─ Repository saves updated state
     │
     ├─ UPDATE workflow_states SET
     │    severity = 'HIGH',
     │    reasoning_trace = ARRAY['...', '...'],
     │    updated_at = now()
     │  WHERE workflow_id = 'wf_abcde'
     │
     └─ Transaction committed

EVENT PUBLISHED
  │
  └─ Workflow engine publishes event
     │
     ├─ event = Event(
     │    event_id = uuid(),
     │    workflow_id = 'wf_abcde',
     │    event_type = 'incident_triage_complete',
     │    data = {
     │      severity: 'HIGH',
     │      agent: 'SentinelAgent',
     │      trace: 'Classified as HIGH'
     │    },
     │    timestamp = now(),
     │    source = 'workflow_engine'
     │  )
     │
     ├─ INSERT INTO events (event_id, workflow_id, ...)
     │
     └─ NOTIFY 'workflow_updates', 'wf_abcde:incident_triage_complete'
        └─ PostgreSQL broadcasts to all listeners

POSTGRESQL LISTEN SUBSCRIBERS
  │
  └─ Event bus subscribers receive notification
     │
     ├─ Subscriber 1: WebSocket Broadcaster
     │  └─ "We have a state change for wf_abcde"
     │
     └─ Subscriber 2: Metrics Aggregator
        └─ "Update incident metrics dashboard"

WEBSOCKET BROADCAST
  │
  └─ For each client connected to workflow wf_abcde:
     │
     ├─ Prepare update message
     │  {
     │    "type": "state_change",
     │    "workflow_id": "wf_abcde",
     │    "change": {
     │      "severity": "HIGH",
     │      "agent": "SentinelAgent"
     │    },
     │    "reasoning_added": "Classified as HIGH",
     │    "timestamp": "2024-01-15T14:32:15Z"
     │  }
     │
     ├─ Send via WebSocket
     │  └─ await client.send_json(update_message)
     │
     └─ Repeat for all connected clients

FRONTEND WEBSOCKET HANDLER
  │
  └─ Client receives message
     │
     ├─ Parse message
     │  {
     │    type: 'state_change',
     │    change: { severity: 'HIGH', ... }
     │  }
     │
     ├─ Update local state (React)
     │  └─ setState({
     │       workflow: {
     │         ...prevState,
     │         severity: 'HIGH',
     │         reasoning_trace: [
     │           ...prevTrace,
     │           'Classified as HIGH'
     │         ]
     │       }
     │     })
     │
     └─ Re-render affected components

UI UPDATES (React Components)
  │
  ├─ IncidentCard
  │  ├─ Severity badge → updates color (to red for HIGH)
  │  ├─ Animated transition (smooth color change)
  │  └─ Re-renders in < 16ms (60 FPS)
  │
  ├─ TracePanel
  │  ├─ New trace line appears
  │  ├─ "Triage Agent: Classified as HIGH"
  │  ├─ Slides in from left with fade animation
  │  └─ Scroll to show latest entry
  │
  └─ Dashboard MetricsPanel
     ├─ Incident count updated
     ├─ Severity distribution chart updated
     └─ Animated bar chart transition

TOTAL LATENCY
  │
  ├─ Agent execution: 100-500ms
  ├─ Database update: <10ms
  ├─ Event publish: <5ms
  ├─ PostgreSQL NOTIFY: <1ms
  ├─ WebSocket broadcast: <10ms
  ├─ Network latency: 20-50ms
  ├─ Browser message receipt: <1ms
  ├─ React state update: <16ms
  ├─ Component re-render: <16ms
  │
  └─ TOTAL: < 100ms (typical)
     └─ User sees update almost instantly
```

---

## External System Integration

### ServiceNow Integration
```
Incident in Axiometica AIR
  ↓ (API Call)
ServiceNow REST API
  ├─ POST /api/now/incident
  │  {
  │    short_description: "High CPU on api-server-1",
  │    description: "Full incident details",
  │    severity: 2 (HIGH),
  │    urgency: 2 (HIGH),
  │    impact: 2 (MEDIUM),
  │    cmdb_ci: "api-server-1",
  │    external_reference: "wf_abcde"  // Link back
  │  }
  │
  └─ Response: incident_id (e.g., INC0012345)
     └─ Store: workflow.context.servicenow_incident_id = INC0012345

Incident Resolved in Axiometica AIR
  ↓ (API Call)
ServiceNow REST API
  ├─ PATCH /api/now/incident/INC0012345
  │  {
  │    state: "resolved",
  │    close_notes: "Auto-remediation: Scaled pods",
  │    resolution_code: "Fixed by Axiometica AIR",
  │    work_end: now()
  │  }
  │
  └─ ServiceNow updates incident status
```

### LLM Integration (Summaries)
```
Incident completed
  ↓
SummaryService.generate_summary_async(
  incident_id=wf_abcde,
  event_type="high_cpu",
  resource_name="api-server-1",
  severity="high",
  impact_description="5000 users affected"
)

OpenAI API Call
  ├─ POST https://api.openai.com/v1/chat/completions
  │  {
  │    model: "gpt-4",
  │    messages: [
  │      {
  │        role: "user",
  │        content: "Generate incident summary: high_cpu, 5000 users..."
  │      }
  │    ],
  │    max_tokens: 150
  │  }
  │
  └─ Response:
     {
       "choices": [{
         "message": {
           "content": "Critical CPU spike on api-server-1 during peak 
           traffic. Automatic scaling to 4 replicas resolved the issue 
           within 2 minutes. Root cause: cache invalidation."
         }
       }]
     }

Store Summary
  └─ UPDATE workflow_states SET
      summary = "Critical CPU spike on api-server...",
      summary_generated_at = now()
     WHERE workflow_id = wf_abcde

WebSocket broadcast
  └─ Frontend displays summary in incident details
```

### Neo4j CMDB Integration
```
During Risk Assessment
  ├─ Query CMDB for dependencies
  │  
  └─ Neo4j Query:
     MATCH (s:Service {name: "api-server"})<-[:DEPENDS_ON]-(dependent)
     RETURN dependent.name, count(DISTINCT dependent)
     
     Response:
     ├─ frontend-service (depends on api-server)
     ├─ notification-service (depends on api-server)
     ├─ analytics-service (depends on api-server)
     └─ Total: 3 services

Get User Impact
  ├─ Neo4j Query:
     MATCH (r:Resource {name: "api-server-1"})-[:HOSTS]->(s:Service)
     WITH s MATCH (s)-[:SERVES]->(u:User)
     RETURN count(DISTINCT u)
     
     Response: 5000 users

Update Risk Calculation
  └─ state.context.blast_radius = {
       direct_impact: 5000,
       indirect_impact: [frontend, notification, analytics],
       total_affected_services: 3
     }
```

### eBPF Monitoring Integration
```
System sends metrics via eBPF
  ├─ Syscall-level telemetry
  │  ├─ Process: api-server PID 4521
  │  ├─ CPU time: 89%
  │  ├─ Memory: 2.5GB / 4GB
  │  ├─ File handles: 1000 / 2000
  │  └─ Network connections: 500
  │
  └─ Metrics arrive at Sentinel collector

Axiometica AIR consumes metrics
  ├─ Query Sentinel API
  │  GET /api/metrics/node/api-server-1?window=5m
  │  Response: [
  │    {timestamp: 14:32:00, cpu: 89, memory: 2.5},
  │    {timestamp: 14:32:30, cpu: 85, memory: 2.5},
  │    ...
  │  ]
  │
  └─ Use for:
     ├─ Determine if remediation effective
     ├─ Calculate risk score
     └─ Populate metrics dashboard
```

---

## Database Persistence Flow

```
Repository Layer (Data Access Abstraction)
  │
  ├─ WorkflowRepository.save(state: WorkflowState)
  │  │
  │  ├─ Check if workflow exists
  │  │  └─ Query: SELECT * FROM workflow_states WHERE workflow_id = ?
  │  │
  │  ├─ If exists → UPDATE
  │  │  └─ SQL:
  │  │     UPDATE workflow_states SET
  │  │       workflow_type = ?,
  │  │       lifecycle_state = ?,
  │  │       severity = ?,
  │  │       risk_score = ?,
  │  │       summary = ?,
  │  │       context = ?,
  │  │       reasoning_trace = ?,
  │  │       updated_at = NOW()
  │  │     WHERE workflow_id = ?
  │  │
  │  └─ Else → INSERT
  │     └─ SQL:
  │        INSERT INTO workflow_states (
  │          workflow_id, workflow_type, lifecycle_state,
  │          severity, risk_score, summary, context,
  │          reasoning_trace, created_at, updated_at
  │        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
  │
  └─ Session.commit() → PostgreSQL transaction

Query Layer
  │
  └─ WorkflowRepository.get(workflow_id)
     │
     ├─ SELECT * FROM workflow_states WHERE workflow_id = ?
     │
     ├─ Map database row to WorkflowState object
     │  └─ _model_to_state() method
     │     ├─ state.workflow_id = model.workflow_id
     │     ├─ state.severity = Severity(model.severity)
     │     ├─ state.context = json.loads(model.context)
     │     ├─ state.reasoning_trace = model.reasoning_trace
     │     ├─ state.summary = model.summary  ← Critical mapping
     │     └─ ... more mappings
     │
     └─ Return state object to application

Index Strategy
  │
  └─ Optimize query performance
     │
     ├─ PRIMARY KEY (workflow_id)
     ├─ INDEX (workflow_type, lifecycle_state, created_at)
     │  └─ Used for: list_workflows with filters
     │
     ├─ INDEX (created_at DESC)
     │  └─ Used for: recent workflows dashboard
     │
     ├─ INDEX (severity, lifecycle_state)
     │  └─ Used for: filter by severity
     │
     └─ INDEX (context JSONB gin_index)
        └─ Used for: query context fields
           e.g., WHERE context->>'alert_payload'->>'type' = 'high_cpu'
```

---

## Event Bus & Pub/Sub Flow

```
EVENT PUBLISHED BY WORKFLOW ENGINE
  │
  └─ event_bus.publish(event)
     │
     ├─ Create Event object
     │  {
     │    event_id: UUID,
     │    workflow_id: wf_abcde,
     │    event_type: 'incident_triage_complete',
     │    data: {...},
     │    timestamp: now(),
     │    source: 'workflow_engine'
     │  }
     │
     └─ Persist to database
        └─ INSERT INTO events (event_id, workflow_id, event_type, ...)

POSTGRESQL NOTIFY
  │
  └─ NOTIFY 'workflow_updates', 'wf_abcde:incident_triage_complete'
     │
     └─ Broadcasts to all LISTEN subscribers (in-process)

SUBSCRIBER 1: WEBSOCKET BROADCASTER
  │
  ├─ @event_bus.subscribe('*')  // Listen for all events
  │  async def on_event(event):
  │
  ├─ Get connected clients for workflow_id
  │  clients = websocket_manager.get_clients(event.workflow_id)
  │
  ├─ For each client:
  │  └─ send_json({
  │       type: 'state_change',
  │       data: state,
  │       timestamp: event.timestamp
  │     })
  │
  └─ Clients receive update < 100ms

SUBSCRIBER 2: METRICS AGGREGATOR
  │
  ├─ @event_bus.subscribe('incident_*', 'change_*')
  │
  ├─ Update metrics counters
  │  ├─ total_incidents += 1
  │  ├─ incidents_by_severity[high] += 1
  │  └─ average_resolution_time = weighted_avg(...)
  │
  └─ Cache updated in Redis
     └─ Used for dashboard metrics display

SUBSCRIBER 3: NOTIFICATION SYSTEM
  │
  ├─ @event_bus.subscribe('approval_requested', 'approval_expired')
  │
  ├─ If approval_requested:
  │  ├─ Send Slack message to #change-board
  │  ├─ Send email to cab-members@company.com
  │  └─ Update approval queue in database
  │
  └─ If approval_expired:
     ├─ Auto-reject approval in database
     └─ Notify requestor of timeout

Multiple Subscribers (Fan-out)
  │
  ├─ One event published
  │  └─ INSERT INTO events (event_id, workflow_id, ...)
  │
  ├─ Multiple subscribers notified
  │  ├─ WebSocket broadcaster (update frontend)
  │  ├─ Metrics aggregator (update dashboard)
  │  ├─ Notification system (send alerts)
  │  └─ Audit logger (log for compliance)
  │
  └─ Each subscriber processes independently
     └─ Decoupled architecture
```

---

## Background Job Flow

```
TASK QUEUED BY API
  │
  └─ execute_workflow_task.delay(
      workflow_id='wf_abcde',
      workflow_type='INCIDENT'
    )
     │
     └─ Celery serializes task to JSON
        └─ Task message:
           {
             "task": "agentic_os.tasks.celery_app.execute_workflow_task",
             "args": [],
             "kwargs": {
               "workflow_id": "wf_abcde",
               "workflow_type": "INCIDENT"
             },
             "id": "task_12345"
           }

REDIS MESSAGE QUEUE
  │
  └─ LPUSH agentic_os:workflows <task_message>
     │
     └─ Message stored in Redis (in-memory)
        └─ Available for workers to consume

CELERY WORKER PROCESS
  │
  ├─ Worker pool: 4 concurrent workers (configurable)
  │
  ├─ Worker 1: Idle → Blocking wait on queue
  │  └─ BRPOP agentic_os:workflows (timeout: 1s)
  │
  ├─ Message available
  │  └─ Worker 1 receives task message
  │
  ├─ Deserialize task
  │  └─ Import: agentic_os.tasks.celery_app.execute_workflow_task
  │
  ├─ Execute task function
  │  └─ execute_workflow_task(workflow_id='wf_abcde', ...)
  │     ├─ Initialize WorkflowEngine
  │     ├─ Load workflow state from DB
  │     ├─ Execute agent steps
  │     ├─ Update database
  │     └─ Return result
  │
  ├─ Store result in Redis
  │  └─ SET celery-task:task_12345 <result_json>
  │     └─ TTL: 1 hour (configurable)
  │
  └─ Mark task complete
     └─ HDEL celery-taskset:... (bookkeeping)

TASK MONITORING (Flower UI)
  │
  └─ Flower web interface (http://localhost:5555)
     │
     ├─ Query Redis
     │  └─ Get task status, results, worker pool stats
     │
     └─ Display
        ├─ Running tasks (in progress)
        ├─ Completed tasks (with duration)
        ├─ Failed tasks (with exceptions)
        ├─ Worker pool health
        └─ Queue depth

SCHEDULED TASKS (Celery Beat)
  │
  └─ health_check task (every 1 minute)
     ├─ Publish task: health_check.delay()
     └─ Result: {"status": "healthy"}
     
  └─ generate_missing_summaries task (every 30 minutes)
     ├─ Query: SELECT * FROM workflows WHERE summary IS NULL
     ├─ For each workflow:
     │  └─ Call SummaryService.generate_summary(...)
     │
     └─ Result: {"processed": 42, "successful": 41, "failed": 1}

TASK RETRY & ERROR HANDLING
  │
  └─ If task fails:
     │
     ├─ Exception caught
     │  └─ log.error(f"Task {task_id} failed: {exception}")
     │
     ├─ Retry logic
     │  ├─ retry_count = 0
     │  ├─ max_retries = 3
     │  ├─ retry_backoff = exponential (2^retry_count seconds)
     │  │
     │  └─ execute_workflow_task.retry(
     │       exc=exception,
     │       countdown=2^retry_count
     │     )
     │     └─ Task re-queued after countdown
     │
     ├─ After max retries exceeded:
     │  ├─ Update workflow state → FAILED
     │  ├─ Set failure reason in context
     │  ├─ Publish event: workflow_failed
     │  └─ Notify user/team
     │
     └─ Store failure details
        └─ exceptions table for debugging
```

---

## Conclusion

These data flows illustrate how information moves through the Axiometica AIR v2 system:

1. **Incident Submission** - From user/monitoring through validation, persistence, async execution
2. **Approval Routing** - Critical decisions escalated to CAB with time limits and notifications
3. **Change Deployment** - Blue-green strategy with canary testing and automatic rollback
4. **Real-Time Updates** - Sub-100ms state changes broadcast to frontend via WebSocket
5. **External Integration** - Seamless data exchange with ServiceNow, LLM APIs, Neo4j, eBPF
6. **Persistence** - Repository pattern abstracts database, repository method ensures consistency
7. **Event Bus** - PostgreSQL LISTEN/NOTIFY enables pub/sub for loose coupling
8. **Background Jobs** - Celery workers handle long-running tasks with retry logic and monitoring

Each flow is designed for reliability, observability, and scalability.

