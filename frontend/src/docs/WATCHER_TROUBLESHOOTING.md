# Watcher Brain-and-Senses Troubleshooting Guide

## Common Issues and Solutions

### Issue 1: Watcher Container Crashes Immediately

**Symptoms:**
```bash
docker logs watcher_brain
Error: Cannot connect to Sentinel container
```

**Root Causes:**
1. Sentinel container not running
2. Sentinel container name mismatch
3. Docker socket not mounted properly

**Solutions:**

**Step 1: Verify Sentinel is running**
```bash
docker ps | grep sentinel_senses
# Should show: quay.io/iovisor/bpftrace:latest
```

**Step 2: Verify container name matches configuration**
```bash
# In docker-compose.yml, check:
sentinel:
  container_name: sentinel_senses  # This must match SENTINEL_CONTAINER env var

watcher:
  environment:
    SENTINEL_CONTAINER: sentinel_senses  # These must match
```

**Step 3: Verify Docker socket access**
```bash
# Inside Watcher container
docker exec watcher_brain docker ps

# Should list containers without error
```

If this fails:
```bash
# Check host socket permissions
ls -la /var/run/docker.sock

# Fix permissions (on host)
sudo chmod 666 /var/run/docker.sock
```

---

### Issue 2: No Telemetry from Sentinel

**Symptoms:**
```bash
docker logs watcher_brain
[TELEMETRY] Failed to get kernel telemetry
```

**Root Causes:**
1. eBPF support not available in kernel
2. bpftrace not working properly
3. Container not in privileged mode

**Solutions:**

**Step 1: Check kernel eBPF support**
```bash
# Run inside Sentinel container
docker exec sentinel_senses uname -r
# Result should be Linux 4.7+

# Check for tracepoint support
docker exec sentinel_senses ls /sys/kernel/debug/tracing/events/raw_syscalls/sys_enter/
# Should exist
```

**Step 2: Test bpftrace directly**
```bash
docker exec -it sentinel_senses bash
bpftrace -l 'tracepoint:raw_syscalls*'
# Should list syscall tracepoints

# Try a simple trace
bpftrace -e 'BEGIN { print("bpftrace works"); }'
```

**Step 3: Verify privileged mode**
```bash
# In docker-compose.yml
sentinel:
  privileged: true  # Must be present
  volumes:
    - /sys:/sys
    - /dev:/dev
    - /lib/modules:/lib/modules:ro
```

**Step 4: If still failing - check kernel debug filesystem**
```bash
# Inside Sentinel
docker exec sentinel_senses mount | grep debugfs
# Should show: debugfs on /sys/kernel/debug type debugfs

# If not mounted, the issue is with kernel configuration
```

---

### Issue 3: Anomalies Detected but No Incidents Created

**Symptoms:**
```bash
docker logs watcher_brain
🚨 [ANOMALY] Process 'python': 25000 syscalls
📞 [PLATFORM CALL] Creating incident INC-WATCHER-...
❌ [INCIDENT CREATION FAILED] Status: 500
```

**Root Causes:**
1. Backend API not responding
2. API URL is incorrect
3. Incident creation endpoint failing

**Solutions:**

**Step 1: Verify API is running**
```bash
curl http://localhost:8000/api/health
# Should return 200 OK {"status": "healthy", ...}
```

**Step 2: Verify API URL in Watcher**
```bash
docker exec watcher_brain env | grep WATCHER_API_URL
# Should show: WATCHER_API_URL=http://backend:8000

# Test from inside Watcher container
docker exec watcher_brain curl -v http://backend:8000/api/health
```

**Step 3: Check backend logs for errors**
```bash
docker logs agentic_os_backend | grep -i error | tail -20
```

**Step 4: Manually test incident creation**
```bash
curl -X POST http://localhost:8000/workflows/incident \
  -H "Content-Type: application/json" \
  -d '{
    "severity": "critical",
    "type": "high_syscall_intensity",
    "resource_name": "test",
    "description": "Test"
  }'

# Should return 200 with workflow_id
```

---

### Issue 4: Remediation Not Executing

**Symptoms:**
```bash
docker logs watcher_brain
✓ [INCIDENT CREATED]
but process still running
```

**Root Causes:**
1. PolicyBrokerAgent not approving remediation
2. Permission denied on pkill
3. Process already terminated

**Solutions:**

**Step 1: Check PolicyBrokerAgent decision**
```bash
# Query the workflow to see the decision
curl http://localhost:8000/workflows/{workflow_id} | jq '.reasoning_trace[-2]'

# Look for: "decision_result": "auto_execute" or "manual_approval"
```

**Step 2: Check if process actually exists**
```bash
# Inside the container
docker exec sentinel_senses ps aux | grep python

# Should show the process
```

**Step 3: Test pkill manually**
```bash
docker exec sentinel_senses pkill -9 python

# Check if it worked
docker exec sentinel_senses ps aux | grep python
# Process should be gone
```

**Step 4: Check permissions**
```bash
# pkill should work in all containers due to privileged mode
# If it doesn't, there's a deeper issue

# Try from watcher_brain
docker exec watcher_brain docker exec sentinel_senses pkill -9 sleep
```

---

### Issue 5: High False Positive Rate

**Symptoms:**
- Too many incidents created
- Processes being terminated unexpectedly
- Watcher in constant cooldown

**Solutions:**

**1. Increase anomaly threshold:**
```yaml
# In docker-compose.yml
watcher:
  environment:
    WATCHER_ANOMALY_THRESHOLD: "30000"  # Was 20000
```

Rebuild and restart:
```bash
docker-compose build --no-cache watcher
docker-compose up -d watcher
```

**2. Increase polling interval:**
```yaml
watcher:
  environment:
    WATCHER_POLL_INTERVAL: "30"  # Was 10, poll less frequently
```

**3. Increase cooldown period:**
```yaml
watcher:
  environment:
    WATCHER_COOLDOWN_SECONDS: "300"  # Was 60, longer cooldown
```

**4. Analyze telemetry baseline:**
```bash
# Get typical syscall counts
docker exec sentinel_senses bpftrace -f json -e \
  'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } \
   interval:s:5 { exit(); }' 
   
# Run multiple times to see range
# Set threshold to 50% above the normal peak
```

---

### Issue 6: Watcher Container Uses Excessive CPU

**Symptoms:**
```bash
docker stats watcher_brain
# CPU% is consistently high (>30%)
```

**Root Causes:**
1. Polling interval too short
2. Large telemetry from Sentinel
3. Network latency in API calls

**Solutions:**

**1. Increase polling interval:**
```yaml
watcher:
  environment:
    WATCHER_POLL_INTERVAL: "30"  # Increase from 10
```

**2. Reduce telemetry collection:**
```bash
# Inside Sentinel, limit to fewer processes
# (This requires modifying the bpftrace command)
```

**3. Use resource limits:**
```yaml
watcher:
  deploy:
    resources:
      limits:
        cpus: "0.5"
        memory: 256M
      reservations:
        cpus: "0.25"
        memory: 128M
```

---

### Issue 7: Network Issues Between Containers

**Symptoms:**
```bash
curl http://backend:8000/health
curl: (7) Failed to connect to backend port 8000: Connection refused
```

**Root Causes:**
1. Containers on different networks
2. Services not started yet
3. Firewall blocking traffic

**Solutions:**

**Step 1: Verify network setup**
```bash
docker network ls | grep agentic_os_network
docker network inspect agenticplatform_v2_agentic_os_network

# Should show all containers connected
```

**Step 2: Check if backend is listening**
```bash
docker exec agentic_os_backend curl http://localhost:8000/health
# Should work from inside the container
```

**Step 3: Verify network connectivity**
```bash
# Ping from watcher to backend
docker exec watcher_brain ping -c 3 backend
# Should get responses

# If fails, containers aren't on same network
```

**Step 4: Fix network issues**
```bash
# Ensure all services are on the same network
docker-compose down
docker-compose up -d

# Verify
docker network inspect agenticplatform_v2_agentic_os_network
```

---

### Issue 8: Sentinel Logs Show Warnings

**Symptoms:**
```bash
docker logs sentinel_senses
WARNING: bpftrace cannot access debugfs (requires root/elevated privileges)
```

**Solutions:**

**Verify privileged mode:**
```bash
docker inspect sentinel_senses | grep -i "privileged"
# Should show "Privileged": true
```

**If not privileged, fix docker-compose.yml:**
```yaml
sentinel:
  privileged: true
  volumes:
    - /sys:/sys
    - /dev:/dev
    - /lib/modules:/lib/modules:ro
```

**Restart:**
```bash
docker-compose up -d sentinel
```

---

### Issue 9: Watcher Status File Not Updating

**Symptoms:**
```bash
docker exec watcher_brain ls -la /app/.state/
# watcher_status.json not created or very old
```

**Root Causes:**
1. Watcher process not running
2. Permission issues on /app directory
3. Telemetry collection failing

**Solutions:**

**Step 1: Check if Watcher is running**
```bash
docker exec watcher_brain ps aux | grep watcher_main
# Should see: python watcher_main.py
```

**Step 2: Check Watcher logs**
```bash
docker logs watcher_brain -n 50
# Look for error messages
```

**Step 3: Verify /app permissions**
```bash
docker exec watcher_brain ls -la /app/
# .state directory should be writable
```

**Step 4: Test telemetry collection**
```bash
# Run the telemetry collection manually
docker exec watcher_brain python -c "
from agentic_os.services.watcher_service import WatcherService
w = WatcherService()
telemetry = w.get_kernel_telemetry()
print(telemetry)
"
```

---

### Issue 10: Docker Build Fails with Permission Denied

**Symptoms:**
```
Error response from daemon: permission denied while trying to connect to Docker daemon
```

**Solutions:**

**Step 1: Check Docker daemon is running**
```bash
docker ps
# Should work
```

**Step 2: Check user permissions**
```bash
# On Linux/Mac
groups $USER
# docker group should be listed

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

**Step 3: On Windows with WSL2**
```powershell
# Ensure Docker Desktop is running
# Check it's using WSL2 backend
docker --version
```

---

### Issue 11: Disk Usage Always Shows 0% in Monitoring Dashboard

**Symptoms:**
- Monitoring dashboard shows DISK at 0% for all containers
- Watcher logs show disk polling active but values remain zero

**Root Cause (fixed in v1.1.2):**
Container name from `docker stats` JSON (e.g., `agentic_os_neo4j`) did not match the name used by `docker exec df` enrichment — so `disk_percent` was never set and silently defaulted to 0.

**Resolution:**
This is fixed in v1.1.2 (`2284809`). Upgrade to v1.1.2 or later. If running an older version, the workaround is to ensure you have rebuilt the watcher container after applying the fix:

```bash
docker-compose up -d --build --no-deps watcher
```

Verify in watcher logs:
```bash
docker logs watcher_brain --tail 20 | grep -i disk
# Should show: [Watcher] disk monitoring active, threshold=90.0%
```

---

## Debugging Commands

### View Watcher Logs in Real-Time
```bash
docker logs -f watcher_brain --tail=20
```

### Watch Telemetry Collection
```bash
docker exec -it watcher_brain bash
python -c "
from agentic_os.services.watcher_service import WatcherService
w = WatcherService()
while True:
    tel = w.get_kernel_telemetry()
    print(f'Telemetry: {tel}')
    import time
    time.sleep(5)
"
```

### Monitor Sentinel bpftrace
```bash
docker exec -it sentinel_senses bpftrace -f json -e \
  'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } \
   interval:s:5 { print(@); exit(); }'
```

### Check API Response Manually
```bash
curl -v http://localhost:8000/workflows/incident \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"severity":"critical","type":"test","resource_name":"test","description":"test"}'
```

### View Watcher Status File
```bash
docker exec watcher_brain cat /app/.state/watcher_status.json | jq .
```

### List All Networks
```bash
docker network ls
docker network inspect agenticplatform_v2_agentic_os_network
```

### Inspect Container Environment
```bash
docker exec watcher_brain env | grep WATCHER
docker exec watcher_brain env | sort
```

---

## Performance Diagnostics

### Memory Usage
```bash
docker stats watcher_brain --no-stream
```

### Network I/O
```bash
docker stats --no-stream | grep watcher
```

### Check Container Limits
```bash
docker inspect watcher_brain | grep -A 10 "MemoryLimit"
```

---

## Re-initializing Everything

If things are really broken:

```bash
# Stop all services
docker-compose down -v

# Remove all stopped containers
docker container prune -f

# Remove dangling images
docker image prune -f

# Rebuild everything fresh
docker-compose build --no-cache

# Start fresh
docker-compose up -d

# Verify
docker-compose ps
```

---

## Getting Help

If you're stuck, gather this information:

```bash
# System info
uname -a
docker --version
docker-compose --version

# Service status
docker-compose ps
docker network ls

# Logs from all relevant containers
docker logs agentic_os_backend > backend.log
docker logs sentinel_senses > sentinel.log
docker logs watcher_brain > watcher.log

# Configuration
docker exec watcher_brain env | grep WATCHER > watcher_config.log

# Telemetry snapshot
docker exec sentinel_senses bpftrace -f json -e \
  'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); } \
   interval:s:2 { exit(); }' > telemetry.log 2>&1
```

Attach these files when reporting issues.
