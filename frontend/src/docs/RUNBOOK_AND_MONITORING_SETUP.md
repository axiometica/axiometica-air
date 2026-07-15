# Runbook & Monitoring System Setup Guide

## Overview

This guide covers:
1. **High-Intensity Calls Runbook** - Automated remediation for Yes service traffic spikes
2. **Sentinel** - eBPF kernel monitor for system-level telemetry
3. **Watcher** - Brain container for anomaly detection and orchestration
4. **Approval Workflow** - New "Allow Diagnostics" partial approval flow
5. **Synthetic Transaction Monitoring** - HAR-based scripted user journey replay with page-content assertions

---

## Part 1: Runbook - Yes Service High-Intensity Calls

### Runbook Details

**Name:** Yes Service - High Intensity Calls Remediation  
**Event Type:** `high_cpu`  
**Service:** `yes-service`  
**Environment:** `prod`  
**Confidence:** 92%  
**Blast Radius:** Medium (2/3)

### 2 Diagnostic Steps

1. **Analyze Request Patterns** (Diagnostic)
   - Checks request queue depth, latency percentiles (P95), traffic rate
   - Tools: Prometheus queries on request metrics
   - Output: Real-time traffic and latency data

2. **Check Dependency Health** (Diagnostic)
   - Verifies database, cache, auth service, data service availability
   - Tools: Health check probes with 5-second timeout
   - Output: Dependency health status

### 8 Remediation Steps (Sequential)

1. **Scale Up Service Replicas** - Increase pods by 50% (max 50 replicas)
2. **Clear Connection Pools** - Flush stale DB and cache connections
3. **Increase Connection Limits** - Expand pool size to 500 with 100 overflow
4. **Enable Request Coalescing** - Batch similar requests (50ms window)
5. **Activate Circuit Breaker** - Fail fast on optional dependencies (50% threshold)
6. **Enable Response Compression** - Gzip responses (>1KB)
7. **Route Traffic to Secondary Region** - 70% primary / 30% secondary
8. **Enable Auto-Scaling Alerts** - Aggressive scaling (60% CPU target)

### 3 Verification Steps

- P95 latency < baseline (0.5s)
- Error rate < 1%
- CPU < 80% AND Memory < 85%

---

## Part 2: Monitoring System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Production System                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Yes App  │  │ Auth SVC │  │ Data SVC │ ...          │
│  └────┬─────┘  └──────────┘  └──────────┘              │
└───────┼──────────────────────────────────────────────────┘
        │ System Calls
        ▼
┌─────────────────────────────────────────────────────────┐
│  Sentinel (eBPF Kernel Monitor)                         │
│  - Tracks syscalls in real-time                         │
│  - Detects anomalies (high syscall rate, patterns)      │
│  - Outputs: JSON event stream                           │
└─────────────────────────────────────────────────────────┘
        │ Metrics
        ▼
┌─────────────────────────────────────────────────────────┐
│  Watcher Brain (AI-Driven Orchestration)                │
│  - Monitors Sentinel telemetry                          │
│  - Detects anomaly patterns                             │
│  - Creates incidents via backend API                    │
│  - Proposes remediation (runbooks)                      │
│  - Exposes kill-API on port 8080                        │
└─────────────────────────────────────────────────────────┘
        │ Incidents
        ▼
┌─────────────────────────────────────────────────────────┐
│  Backend (Incident Workflow Engine)                     │
│  - Processes incident & governance policies             │
│  - Executes diagnostic steps                            │
│  - Creates approval request for remediation             │
│  - Waits for approval (or "Allow Diagnostics" option)   │
└─────────────────────────────────────────────────────────┘
        │ Diagnostic Results
        ▼
┌─────────────────────────────────────────────────────────┐
│  Frontend (User Approval UI)                            │
│  - Shows incident summary                               │
│  - Shows diagnostic findings                            │
│  - 3 Approval Options:                                  │
│    1. "Reject" - Cancel remediation                     │
│    2. "Allow Diagnostics Only" - Run diagnostics only  │
│    3. "Approve & Remediate" - Full execution            │
└─────────────────────────────────────────────────────────┘
```

### Sentinel Container

**Image:** `quay.io/iovisor/bpftrace:latest`

**Purpose:** Low-level system telemetry via eBPF  
**Mode:** Privileged container with host PID namespace  
**Monitoring:**
- System call frequency and patterns
- Process behavior anomalies
- Resource exhaustion signals

**Output:** JSON formatted event stream to Watcher

### Watcher Container

**Build:** Custom (`Dockerfile.watcher`)  
**Entrypoint:** `watcher_main.py`

**Purpose:** AI-driven anomaly detection and orchestration

**Responsibilities:**
1. Listen to Sentinel telemetry
2. Detect anomaly patterns (high syscall rate, CPU spikes, etc.)
3. Create incidents via backend API
4. Propose matching runbooks based on event type
5. Expose kill-API for process termination (port 8080)

**Key Environment Variables:**
```
SENTINEL_CONTAINER=sentinel_senses
WATCHER_API_URL=http://backend:8000
WATCHER_POLL_INTERVAL=10
WATCHER_ANOMALY_THRESHOLD=20000      # Syscalls/5sec
WATCHER_CPU_THRESHOLD=80.0            # CPU %
WATCHER_MEMORY_THRESHOLD=90.0         # Memory %
WATCHER_DISK_THRESHOLD=90.0          # Disk %
WATCHER_CONNECTION_THRESHOLD=1000    # Network connections
```

---

## Part 3: Approval Workflow Enhancement

### New "Allow Diagnostics" Option

When a remediation incident reaches approval, users now have 3 choices:

#### Option 1: Reject
- Cancel the entire workflow
- Incident marked as rejected
- No diagnostics or remediation runs

#### Option 2: Allow Diagnostics (NEW)
- **Type:** Partial Approval
- **Effect:** Workflow executes diagnostic steps only
- **Stops Before:** Remediation actions
- **User Can:** Review diagnostic findings, then approve full remediation later
- **Use Case:** Safety measure for high-risk changes

**Flow:**
```
Incident Received
    ↓
Policy Check → Requires Approval
    ↓
Approval Created → [Reject] [Allow Diagnostics] [Approve & Remediate]
    ↓ (User clicks "Allow Diagnostics")
Diagnostics Run (2 steps for Yes service)
    ↓
Results Shown to User
    ↓
User Reviews Findings
    ↓
Option: Approve Remediation OR Reject
```

#### Option 3: Approve & Remediate
- Full workflow execution
- All diagnostics + all remediation steps
- End-to-end automation

### Frontend Implementation

**ApprovalQueue Component:**
```typescript
// Three buttons shown to user:
<button onClick={() => rejectApproval()}>
  Reject
</button>

<button onClick={() => approveApproval('diagnostics_only')}>
  Allow Diagnostics Only
</button>

<button onClick={() => approveApproval('full')}>
  Approve & Remediate
</button>
```

### Backend Implementation

**Approval Decision Tracking:**
```python
approval_decision = {
  "approval_id": "...",
  "decision": "approve",      # reject | approve | diagnostics_only
  "approved_by": "user_id",
  "approved_at": "timestamp",
  "decision_reason": "..."
}
```

**Workflow Logic:**
```python
if approval_decision == "diagnostics_only":
    # Run only diagnostic steps
    for step in runbook.diagnostics:
        execute_step(step)
    # Stop here - don't run remediation
    workflow.lifecycle_state = "waiting_remediation_approval"
else if approval_decision == "full":
    # Run all steps (diagnostics + remediation)
    for step in runbook.diagnostics + runbook.actions:
        execute_step(step)
else:  # "reject"
    workflow.lifecycle_state = "rejected"
```

---

## Part 4: Deployment Instructions

### Quick Setup (Automated)

```bash
chmod +x scripts/setup-runbook-and-monitoring.sh
./scripts/setup-runbook-and-monitoring.sh
```

**What it does:**
1. Builds all Docker images
2. Starts core services (postgres, redis, neo4j, backend, celery)
3. Seeds the Yes service runbook into database
4. Starts Sentinel and Watcher containers
5. Verifies all services are healthy
6. Displays final status and service URLs

### Manual Setup

#### Step 1: Build Docker Images
```bash
docker-compose build --no-cache backend celery_worker sentinel watcher frontend
```

#### Step 2: Start Core Services
```bash
docker-compose up -d postgres redis neo4j backend celery_worker
```

#### Step 3: Seed Runbook
```bash
docker-compose exec postgres psql -U postgres -d agentic_os < backend/seeds/runbooks.sql
```

#### Step 4: Start Monitoring
```bash
docker-compose up -d sentinel watcher
```

#### Step 5: Verify
```bash
docker-compose ps
docker-compose logs watcher | grep -i "connected\|ready"
```

---

## Part 5: Verification & Testing

### Check Runbook in Database
```bash
docker-compose exec postgres psql -U postgres -d agentic_os -c \
  "SELECT name, event_type, service, enabled FROM runbooks WHERE name LIKE 'Yes%';"
```

**Expected Output:**
```
                          name                          | event_type |   service   | enabled
─────────────────────────────────────────────────────────┼────────────┼─────────────┼─────────
 Yes Service - High Intensity Calls Remediation          | high_cpu   | yes-service | t
```

### Check Watcher Status
```bash
docker-compose logs watcher -f | grep -E "Connected|Ready|Polling|Anomaly"
```

**Expected Logs:**
```
Connected to Sentinel container: sentinel_senses
Polling interval: 10 seconds
CPU threshold: 80.0%
Memory threshold: 90.0%
Listening on port 8080 for kill requests
```

### Check Sentinel Status
```bash
docker-compose logs sentinel -f
```

**Expected Output:**
```
tracepoint:raw_syscalls:sys_enter event stream active
```

### Test Full Flow

1. **Create Test Incident**
   - Frontend → "Create Incident"
   - Type: `high_cpu`, Resource: `yes-service`

2. **Verify Governance Policy Match**
   - Incident should trigger governance policy
   - Approval request should be created

3. **View Approval Options**
   - Frontend → Approvals Queue
   - Should see 3 buttons: Reject, Allow Diagnostics, Approve & Remediate

4. **Test Partial Approval**
   - Click "Allow Diagnostics"
   - Diagnostics should execute
   - Results should be shown
   - User can then choose full remediation

---

## Part 6: Synthetic Transaction Monitoring

Synthetic Transaction Monitors replay a scripted multi-page user journey against a real target (login, navigate, submit) and validate both HTTP status codes and page content — catching failures a plain up/down health check misses. They run from the same Watcher container as the checks in Part 2, on their own schedule.

### How It Works

1. **Record** the journey in Chrome DevTools: Network tab → perform the flow → right-click → **Export HAR with content**.
2. **Upload** the HAR in Monitoring Setup → Synthetic Transaction Monitoring → New Monitor. The platform parses it into pages and requests and suggests credential keys (base URL, email, password, tokens) found in the recording.
3. **Fill in credentials** — stored encrypted, injected into the replay as environment variables at run time.
4. **Add page assertions** (optional) — a regex checked against every response body captured on that page; leave blank to check status codes only.
5. **Generate Script** compiles the parsed pages into a runnable Python script deterministically — no LLM call. **Test Script** runs it once immediately.
6. **Save** with a **Run every (minutes)** interval (default 15).

### Execution Model

Unlike the Yes-service runbook in Part 1, synthetic monitors are **not Celery-scheduled**. `watcher_brain` evaluates every enabled monitor on each of its own poll cycles (`WATCHER_POLL_INTERVAL`, default 10s) but only actually runs a monitor's script once `schedule_mins` has elapsed since its last run — so the watcher's fast internal poll loop doesn't mean the monitor runs that often. Each run executes as a subprocess with a 120-second timeout.

### Failure Alerting

After `WATCHER_SYNTHETIC_MIN_CONSECUTIVE_FAILS` consecutive failing runs (default 1), the watcher raises a `synthetic.transaction.failed` monitoring event at `critical` criticality through the same qualification and incident pipeline used everywhere else in this guide — a failed transaction is treated the same as a hard script error/timeout, since both mean the monitored journey is broken right now. It auto-clears the next time the monitor passes.

### Verifying a Monitor

```bash
# Tail per-request/per-page detail as the watcher runs a monitor
docker logs watcher_brain -f | grep "\[SYNTHETIC\]"
```

**Expected output** (one line per request, one summary line per page):
```
🔬 [SYNTHETIC] Running monitor 'Axiometica WebSite'
🔬 [SYNTHETIC]     Start Page 1: Login
🔬 [SYNTHETIC]       GET   /                     [200]  143ms
🔬 [SYNTHETIC]       POST  /api/auth/login        [200]  385ms
🔬 [SYNTHETIC]     End Page 1 - PASSED (729ms)
🔬 [SYNTHETIC]     RESULT : PASS -- 1/1 pages passed
🔬 [SYNTHETIC] 'Axiometica WebSite' → pass
```

The same output is available in the UI via the **Log** button next to each monitor, without needing container log access.

---

## Troubleshooting

### Issue: Sentinel container won't start
**Solution:** 
```bash
docker-compose logs sentinel
# Likely cause: bpftrace requires Linux kernel with BPF support
# Check: `docker run quay.io/iovisor/bpftrace:latest bpftrace -v`
```

### Issue: Watcher not detecting anomalies
**Check:**
```bash
docker-compose exec watcher curl http://localhost:8080/health
# Should return 200
```

### Issue: Runbook not found when creating incident
**Check:**
```bash
docker-compose exec postgres psql -U postgres -d agentic_os -c \
  "SELECT COUNT(*) FROM runbooks WHERE enabled = true;"
# Should return > 0
```

### Issue: Approval workflow broken
**Solution:** Verify backend API
```bash
curl http://localhost:8000/api/health
# Should return 200 with status info
```

### Issue: Synthetic monitor never runs / stays stuck on old "Last Run"
**Check:**
```bash
docker logs watcher_brain -f | grep "\[SYNTHETIC\]"
```
- Confirm the monitor is **Enabled** and has a saved script (an unsaved/never-generated script is skipped)
- The watcher only executes a monitor once `schedule_mins` has elapsed since `last_run_at` — a 15-minute monitor will not run again 2 minutes after its last run, even though the watcher itself polls every ~10 seconds
- `watcher_brain` bind-mounts backend source in dev but does **not** hot-reload — after a code change under `backend/src`, restart it: `docker restart watcher_brain`

---

## Files Created

- `scripts/setup-runbook-and-monitoring.sh` - Automated setup script
- `backend/seeds/runbooks.sql` - Runbook SQL seed
- `RUNBOOK_AND_MONITORING_SETUP.md` - This guide

## Next Steps

1. ✅ Run `./scripts/setup-runbook-and-monitoring.sh`
2. ✅ Verify all services running: `docker-compose ps`
3. ✅ Test approval workflow with "Allow Diagnostics" option
4. ✅ Monitor incident execution in ApprovalQueue
5. ✅ Review diagnostic results before approving remediation

---

**Created:** 2026-05-11  
**Version:** 1.0  
**System:** Agentic Platform v2
