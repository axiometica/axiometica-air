"""
Demo-mode API routes.

Provides a single endpoint to trigger 7 proven demo incidents through the
full 7-agent pipeline. Only active when DEMO_MODE=true. Creates real
anomalies on safe containers (via watcher /exec API) so runbook remediation
succeeds end-to-end.

Also runs a background cleanup loop (every 10 minutes) that kills leftover
`yes` processes and removes `dd` fill files on safe containers, in case
auto-remediation fails to clean them up.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter()

WATCHER_EXEC_URL = "http://watcher_brain:8080/exec"
EVENT_INTERVAL_SECONDS = 10
CLEANUP_INTERVAL_SECONDS = 600

# Anomaly commands paired with the event index they support.
# Events without an anomaly (memory_high, service_down, cache.memory_high)
# have no entry here — they rely on service-level remediation (restart, etc.).
ANOMALY_FOR_EVENT = {
    0: {"container": "agentic_os_neo4j",  "command": "sh -c 'yes > /dev/null &'", "mode": "container", "timeout": 5},
    1: {"container": "umami",             "command": "sh -c 'yes > /dev/null &'", "mode": "container", "timeout": 5},
    2: {"container": "umami_db",          "command": "sh -c 'dd if=/dev/zero of=/tmp/fillup bs=1M count=100 2>/dev/null &'", "mode": "container", "timeout": 5},
    5: {"container": "agentic_os_flower", "command": "sh -c 'yes > /dev/null &'", "mode": "container", "timeout": 5},
}

CLEANUP_COMMANDS = [
    {"container": "agentic_os_neo4j",  "command": "sh -c 'ps -o pid,stat,comm | grep yes | grep -v grep | while read pid state name; do test $state != Z && kill -9 $pid 2>/dev/null; done; true'", "mode": "container", "timeout": 10},
    {"container": "umami",             "command": "sh -c 'ps -o pid,stat,comm | grep yes | grep -v grep | while read pid state name; do test $state != Z && kill -9 $pid 2>/dev/null; done; true'", "mode": "container", "timeout": 10},
    {"container": "agentic_os_flower", "command": "sh -c 'ps -o pid,stat,comm | grep yes | grep -v grep | while read pid state name; do test $state != Z && kill -9 $pid 2>/dev/null; done; true'", "mode": "container", "timeout": 10},
    {"container": "umami_db",          "command": "sh -c 'rm -f /tmp/fillup /tmp/diskfill /tmp/diskfill_demo 2>/dev/null; true'", "mode": "container", "timeout": 5},
]

DEMO_EVENTS = [
    {
        "source": "watcher_brain",
        "event_type": "infrastructure.compute.cpu_high",
        "resource_name": "agentic_os_neo4j",
        "raw_criticality": "critical",
        "signal_value": 97.2,
        "signal_threshold": 85.0,
        "anomaly_process": "yes",
        "raw_payload": {
            "host": "agentic_os_neo4j",
            "metric": "cpu_percent",
            "value": 97.2,
            "threshold": 85.0,
            "duration_seconds": 120,
            "top_process": "yes",
            "cpu_cores": 4,
            "load_avg_1m": 3.92,
            "description": "Sustained CPU utilisation above 95% for 120s on Neo4j graph database container. Top consumer: yes (fork-bomb pattern). Risk of query timeouts and CMDB enrichment delays.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "infrastructure.compute.syscall_intensity_high",
        "resource_name": "umami",
        "raw_criticality": "critical",
        "signal_value": 85000,
        "signal_threshold": 50000,
        "anomaly_process": "yes",
        "raw_payload": {
            "host": "umami",
            "metric": "syscalls_per_sec",
            "value": 85000,
            "threshold": 50000,
            "dominant_syscall": "write",
            "baseline_rate": 31200,
            "deviation_pct": 172.4,
            "description": "Abnormal syscall rate on Umami analytics container. write() at 85k/sec, 172% above baseline. Runaway process generating excessive I/O.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "infrastructure.storage.disk_full",
        "resource_name": "umami_db",
        "raw_criticality": "critical",
        "signal_value": 96.8,
        "signal_threshold": 90.0,
        "raw_payload": {
            "host": "umami_db",
            "metric": "disk_used_percent",
            "value": 96.8,
            "threshold": 90.0,
            "mount": "/tmp",
            "total_gb": 48.3,
            "used_gb": 46.7,
            "growth_rate_mb_min": 500,
            "description": "Disk usage on /tmp reached 96.8% on Umami analytics DB container. 500MB written in 60s. Risk of PostgreSQL WAL write failures and data loss.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "infrastructure.compute.memory_high",
        "resource_name": "umami",
        "raw_criticality": "critical",
        "signal_value": 94.5,
        "signal_threshold": 80.0,
        "anomaly_process": "node",
        "raw_payload": {
            "host": "umami",
            "metric": "mem_percent",
            "value": 94.5,
            "threshold": 80.0,
            "rss_mb": 756,
            "container_limit_mb": 800,
            "growth_rate_mb_min": 12.3,
            "oom_risk_minutes": 10,
            "description": "Memory at 94.5% on Umami analytics container. RSS growing steadily, possible Node.js memory leak. OOM kill risk within 10 minutes at current rate.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "application.availability.service_down",
        "resource_name": "axiometica-air-demo-gateway-1",
        "raw_criticality": "critical",
        "signal_value": 0,
        "signal_threshold": 1,
        "raw_payload": {
            "host": "axiometica-air-demo-gateway-1",
            "metric": "instances_healthy",
            "value": 0,
            "threshold": 1,
            "health_check_url": "http://localhost:8080/health",
            "last_healthy": datetime.utcnow().isoformat() + "Z",
            "downtime_seconds": 360,
            "http_status": "connection_refused",
            "description": "Demo API gateway health check failing for 300+ seconds. Container reports unhealthy. HTTP probe returns connection refused on port 8080. Zero healthy instances.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "infrastructure.compute.cpu_high",
        "resource_name": "agentic_os_flower",
        "raw_criticality": "critical",
        "signal_value": 94.1,
        "signal_threshold": 85.0,
        "anomaly_process": "yes",
        "raw_payload": {
            "host": "agentic_os_flower",
            "metric": "cpu_percent",
            "value": 94.1,
            "threshold": 85.0,
            "duration_seconds": 120,
            "top_process": "yes",
            "cpu_cores": 2,
            "load_avg_1m": 1.96,
            "description": "Celery Flower monitoring UI CPU at 94.1% for 120s. Runaway 'yes' process consuming full core. Worker metrics dashboard unresponsive, operators unable to inspect task queue.",
        },
    },
    {
        "source": "watcher_brain",
        "event_type": "database.cache.memory_high",
        "resource_name": "agentic_os_postgres",
        "raw_criticality": "critical",
        "signal_value": 91.3,
        "signal_threshold": 80.0,
        "raw_payload": {
            "host": "agentic_os_postgres",
            "metric": "shared_buffers_used_percent",
            "value": 91.3,
            "threshold": 80.0,
            "buffer_hit_ratio": 87.2,
            "baseline_hit_ratio": 99.1,
            "shared_buffers_mb": 128,
            "cache_misses_per_sec": 342,
            "avg_query_latency_ms": 45.6,
            "description": "PostgreSQL shared buffer cache at 91.3% on primary DB container. Buffer hit ratio degraded from 99.1% to 87.2%. Increased disk I/O causing query latency spikes.",
        },
    },
]


class DemoTriggerResponse(BaseModel):
    triggered: int
    total: int


def _exec_watcher(cmd: dict) -> Optional[str]:
    """Fire-and-forget command via watcher /exec API. Returns error string or None."""
    try:
        data = json.dumps(cmd).encode()
        req = Request(WATCHER_EXEC_URL, data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=8)
        return None
    except URLError as e:
        return str(e)
    except Exception as e:
        return str(e)


_trigger_in_progress = False


async def _fire_events_background():
    """Submit 7 events at 10-second intervals, creating anomalies as needed."""
    global _trigger_in_progress
    from agentic_os.db.database import SessionLocal
    from agentic_os.api.routes.monitoring_events import (
        MonitoringEventSubmit,
        submit_monitoring_event,
    )
    from starlette.background import BackgroundTasks as _BgTasks

    triggered = 0
    for idx, evt_data in enumerate(DEMO_EVENTS):
        # Create anomaly if this event has one
        anomaly_cmd = ANOMALY_FOR_EVENT.get(idx)
        if anomaly_cmd:
            err = await asyncio.to_thread(_exec_watcher, anomaly_cmd)
            if err:
                logger.warning("[DEMO] Anomaly creation failed for event %d: %s", idx, err)
            else:
                await asyncio.sleep(2)

        db = SessionLocal()
        try:
            evt = MonitoringEventSubmit(**evt_data)
            bg = _BgTasks()
            resp = await submit_monitoring_event(evt, bg, db)
            if resp.qualified_as_incident:
                triggered += 1
            label = f"{evt.event_type.split('.')[-1]} on {evt.resource_name}"
            logger.info("[DEMO] %d/%d %s → qualified=%s wf=%s",
                        idx + 1, len(DEMO_EVENTS), label,
                        resp.qualified_as_incident, resp.incident_workflow_id)
        except Exception as e:
            logger.warning("[DEMO] Event %d failed: %s", idx, e)
        finally:
            db.close()

        if idx < len(DEMO_EVENTS) - 1:
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)

    logger.info("[DEMO] Batch complete: %d/%d incidents triggered", triggered, len(DEMO_EVENTS))

    # Wait for the pipeline to finish remediating before cleaning up
    # leftover anomaly processes. Too early and the pipeline finds
    # zombies instead of live processes to kill.
    await asyncio.sleep(180)
    logger.info("[DEMO] Running post-trigger cleanup")
    for cmd in CLEANUP_COMMANDS:
        try:
            await asyncio.to_thread(_exec_watcher, cmd)
        except Exception as e:
            logger.warning("[DEMO] Post-trigger cleanup failed for %s: %s", cmd["container"], e)

    _trigger_in_progress = False


@router.post("/demo/trigger-incidents", response_model=DemoTriggerResponse)
async def trigger_demo_incidents(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Fire 7 proven demo incidents through the full 7-agent pipeline.

    Creates anomalies and submits events at 10-second intervals in the
    background. Returns immediately so the UI stays responsive.
    """
    global _trigger_in_progress
    from agentic_os.services.demo_mode import DEMO_MODE
    if not DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    if _trigger_in_progress:
        raise HTTPException(status_code=409, detail="Demo incidents are already being triggered. Please wait.")

    _trigger_in_progress = True
    asyncio.create_task(_fire_events_background())

    return DemoTriggerResponse(triggered=0, total=len(DEMO_EVENTS))


# ── Cleanup loop ────────────────────────────────────────────────────────────
# Kills leftover `yes` processes and removes `dd` fill files every 10 minutes
# on safe containers. Runs only when DEMO_MODE=true.

_cleanup_task: Optional[asyncio.Task] = None


async def _cleanup_loop():
    """Kill stale anomaly processes every CLEANUP_INTERVAL_SECONDS."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        logger.info("[DEMO] Running anomaly cleanup on safe containers")
        for cmd in CLEANUP_COMMANDS:
            try:
                await asyncio.to_thread(_exec_watcher, cmd)
            except Exception as e:
                logger.warning("[DEMO] Cleanup failed for %s: %s", cmd["container"], e)


def start_cleanup_loop():
    """Start the cleanup background task. Call from app lifespan."""
    global _cleanup_task
    from agentic_os.services.demo_mode import DEMO_MODE
    if not DEMO_MODE:
        return
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("[DEMO] Anomaly cleanup loop started (every %ds)", CLEANUP_INTERVAL_SECONDS)


def stop_cleanup_loop():
    """Cancel the cleanup background task. Call from app shutdown."""
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()
        _cleanup_task = None
        logger.info("[DEMO] Anomaly cleanup loop stopped")
