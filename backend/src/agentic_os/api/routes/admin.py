"""Admin utilities and system management endpoints."""

from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
import os
import logging

from agentic_os.db.database import SessionLocal, get_session

logger = logging.getLogger(__name__)
from agentic_os.db.models import WorkflowStateModel
from agentic_os.tasks.celery_app import generate_missing_summaries

router = APIRouter(tags=["admin"])


class AdminResponse:
    """Standard admin API response format."""
    pass


@router.get("/statistics")
async def get_statistics():
    """Get system statistics about incidents and workflows."""
    db = SessionLocal()
    try:
        # Count total incidents
        total_incidents = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_type == "incident"
        ).count()

        # Count total workflows
        total_workflows = db.query(WorkflowStateModel).count()

        # Count active incidents (not yet resolved)
        active_incidents = db.query(WorkflowStateModel).filter(
            (WorkflowStateModel.workflow_type == "incident") &
            (WorkflowStateModel.lifecycle_state.in_(["open", "in_progress", "waiting_approval", "executing"]))
        ).count()

        return {
            "total_incidents": total_incidents,
            "total_workflows": total_workflows,
            "active_incidents": active_incidents,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching statistics: {str(e)}")
    finally:
        db.close()


@router.get("/system-status")
async def get_system_status():
    """Check system health status."""
    db = SessionLocal()
    try:
        status = {
            "database_status": "unknown",
            "redis_status": "unknown",
            "timestamp": datetime.utcnow().isoformat()
        }

        # Check database
        try:
            result = db.execute(text("SELECT 1"))
            if result:
                status["database_status"] = "healthy"
        except Exception as e:
            status["database_status"] = f"unhealthy: {str(e)}"

        # Note: Redis status check would require redis client
        # For now, we'll mark it as not configured
        status["redis_status"] = "not_checked"

        return status
    finally:
        db.close()


@router.post("/platform/reset")
async def platform_reset():
    """
    Full operational data reset.

    Wipes ALL transient/operational data and resets enumeration sequences.
    Configuration (settings, policies, runbooks, connectors, users) is preserved —
    but runbooks' execution-feedback counters (total_executions, success_rate,
    confidence, etc.) are NOT configuration; they're 1:1 tied to the incidents
    being wiped here, so they're reset to the fresh-runbook baseline too. Without
    this, a runbook can end up claiming more executions than incidents exist
    anywhere in the system.

    Deletion order respects FK constraints:
      1. monitoring_events       — FK → workflow_states (nullable), whole table
      2. agent_executions        — FK → workflow_states (no cascade), whole table
      3. approvals               — FK → workflow_states, whole table
      4. events                  — FK → workflow_states, whole table
      5. incident_notes          — FK → workflow_states (CASCADE), whole table
      6. snow_incident_map       — no FK, whole table
      7. snow_sync_logs          — no FK, whole table
      8. optimization_recommendations — no FK, whole table (Platform Intel)
      9. workflow_states         — incidents + changes, now safe
     10. runbooks                — execution-feedback fields reset to fresh baseline
     11. Reset incident_seq → 1
     12. Reset watcher in-memory state

    WARNING: This is irreversible.
    """
    from agentic_os.db.models import (
        MonitoringEventModel, ApprovalModel, EventModel,
        AgentExecutionModel, SNowIncidentMapModel, SNowSyncLogModel,
        OptimizationRecommendationModel, RunbookModel,
    )

    db = SessionLocal()
    counts: dict = {}
    try:
        # ── 1. monitoring_events (whole table) ───────────────────────────────
        counts["monitoring_events"] = db.query(MonitoringEventModel).delete(
            synchronize_session=False
        )

        # ── 2. agent_executions (FK to workflow_states, no CASCADE) ──────────
        counts["agent_executions"] = db.query(AgentExecutionModel).delete(
            synchronize_session=False
        )

        # ── 3. approvals (whole table — covers incidents + changes) ──────────
        counts["approvals"] = db.query(ApprovalModel).delete(
            synchronize_session=False
        )

        # ── 4. events / event-sourcing log (whole table) ─────────────────────
        counts["events"] = db.query(EventModel).delete(
            synchronize_session=False
        )

        # ── 5. incident_notes (FK → workflow_states with CASCADE, but explicit
        #        is safer and avoids partial-delete surprises) ─────────────────
        from agentic_os.db.models import IncidentNoteModel
        counts["incident_notes"] = db.query(IncidentNoteModel).delete(
            synchronize_session=False
        )

        # ── 6. ServiceNow incident map (whole table) ─────────────────────────
        counts["snow_incident_map"] = db.query(SNowIncidentMapModel).delete(
            synchronize_session=False
        )

        # ── 7. ServiceNow sync logs (whole table) ────────────────────────────
        counts["snow_sync_logs"] = db.query(SNowSyncLogModel).delete(
            synchronize_session=False
        )

        # ── 8. Platform Intel recommendations (whole table) ──────────────────
        counts["recommendations"] = db.query(OptimizationRecommendationModel).delete(
            synchronize_session=False
        )

        # ── 9. workflow_states — incidents and changes ────────────────────────
        counts["workflows"] = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_type.in_(["incident", "change"])
        ).delete(synchronize_session=False)

        # ── 10. Runbook execution-feedback fields — 1:1 tied to the incidents
        #         just deleted above, not configuration to preserve ──────────
        counts["runbook_stats_reset"] = db.query(RunbookModel).update({
            RunbookModel.total_executions: 0,
            RunbookModel.successful_executions: 0,
            RunbookModel.failed_executions: 0,
            RunbookModel.success_rate: None,
            RunbookModel.recent_outcomes: [],
            RunbookModel.confidence: 0.85,
            RunbookModel.confidence_trend: None,
            RunbookModel.last_executed_at: None,
        }, synchronize_session=False)

        db.commit()

        # ── 11. Reset incident enumeration sequence ───────────────────────────
        db.execute(text("ALTER SEQUENCE incident_seq RESTART WITH 1"))
        db.commit()

        # ── 12. Reset in-memory state on all approved watchers ───────────────
        watcher_reset = False
        import httpx as _httpx
        try:
            from agentic_os.db.models import WatcherRegistrationModel as _WRM
            _w_rows = db.query(_WRM).filter_by(registration_status="approved").all()
            _w_urls = [
                (getattr(r, "kill_api_url", "") or f"http://{r.watcher_name}:8080").rstrip("/")
                for r in _w_rows
            ] or ["http://watcher_brain:8080"]
            async with _httpx.AsyncClient(timeout=5.0) as _client:
                for _wurl in _w_urls:
                    try:
                        _resp = await _client.post(f"{_wurl}/reset")
                        if _resp.status_code == 200:
                            watcher_reset = True
                    except Exception:
                        pass  # Non-fatal — each watcher reconciles within ~60 s
        except Exception:
            pass

        return {
            "success": True,
            "deleted": counts,
            "watcher_reset": watcher_reset,
            "message": (
                f"Platform reset complete. "
                f"Deleted {counts['workflows']} workflows "
                f"({counts['monitoring_events']} monitoring events, "
                f"{counts['approvals']} approvals, "
                f"{counts['recommendations']} recommendations). "
                f"Reset execution stats on {counts['runbook_stats_reset']} runbooks. "
                f"Incident enumeration reset to INC0001."
            ),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error during platform reset: {str(e)}"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error during platform reset: {str(e)}"
        )
    finally:
        db.close()


@router.post("/incidents/delete-all")
async def delete_all_incidents():
    """Deprecated — use POST /admin/platform/reset instead."""
    return await platform_reset()


@router.post("/database/vacuum")
async def vacuum_database():
    """Optimize database (PostgreSQL VACUUM ANALYZE)."""
    from agentic_os.db.database import engine
    try:
        # VACUUM cannot run inside a transaction — use a raw connection with autocommit
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text("VACUUM ANALYZE"))
        return {
            "success": True,
            "message": "Database optimized successfully",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error optimizing database: {str(e)}"
        )


@router.get("/health-detailed")
async def health_detailed():
    """Get detailed health information for all system components."""
    db = SessionLocal()
    try:
        health = {
            "status": "unknown",
            "components": {},
            "timestamp": datetime.utcnow().isoformat()
        }

        # Check database
        try:
            result = db.execute(text("SELECT 1"))
            health["components"]["database"] = {"status": "healthy", "message": "Connected"}
        except Exception as e:
            health["components"]["database"] = {"status": "unhealthy", "message": str(e)}

        # API is always healthy if we got this far
        health["components"]["api"] = {"status": "healthy", "message": "API operational"}

        # Determine overall status
        all_healthy = all(
            comp.get("status") == "healthy"
            for comp in health["components"].values()
        )
        health["status"] = "healthy" if all_healthy else "degraded"

        return health
    finally:
        db.close()


@router.post("/summaries/generate-missing")
async def generate_missing_incident_summaries(
    limit: int = Query(None, description="Maximum number of incidents to process"),
    regenerate_short: bool = Query(True, description="Also regenerate summaries shorter than 120 chars (old simple-fallback format)"),
):
    """
    Generate platform context summaries for incidents that don't have summaries,
    or whose summaries are too short (old simple-fallback format).

    This endpoint triggers a background task that will:
    1. Find incidents without summaries (or with short fallback summaries)
    2. Generate rich platform context summaries using incident data
    3. Store the summaries in the database

    Returns the task ID for monitoring progress.
    """
    try:
        # Trigger background task
        task = generate_missing_summaries.delay(limit=limit, regenerate_short=regenerate_short)

        return {
            "success": True,
            "task_id": task.id,
            "message": "Summary generation task started in background",
            "status": "processing",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error starting summary generation task: {str(e)}"
        )


@router.get("/summaries/generation-status/{task_id}")
async def get_summary_generation_status(task_id: str):
    """
    Get the status of a summary generation task.

    Returns:
    - state: Task state (PENDING, STARTED, SUCCESS, FAILURE)
    - result: Task result (stats or error message)
    """
    try:
        from agentic_os.tasks.celery_app import app

        task = app.AsyncResult(task_id)

        return {
            "task_id": task_id,
            "state": task.state,
            "result": task.result,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving task status: {str(e)}"
        )


# ── Backup endpoints ──────────────────────────────────────────────────────────

@router.post("/backup/run")
async def trigger_backup():
    """Dispatch a full platform backup as a background Celery task.

    Backs up: PostgreSQL, Neo4j CMDB, and Watcher configuration.
    Files are written to the ./backups/ directory (bind-mounted into the
    celery_worker container at /app/backups/).

    Returns immediately with the Celery task ID; poll /admin/backup/status
    for progress.
    """
    db = SessionLocal()
    try:
        from agentic_os.db.models import PlatformSettingModel
        from agentic_os.tasks.backup import run_backup_task

        # Read retention_days from settings (default 7 if not yet saved)
        retention_row = db.get(PlatformSettingModel, "general.backup_retention_days")
        retention_days = int(retention_row.value) if retention_row else 7

        # Guard: refuse to start a new backup if one is already running.
        status_row = db.get(PlatformSettingModel, "general.last_backup_status")
        if status_row and status_row.value == "in_progress":
            raise HTTPException(
                status_code=409,
                detail="A backup is already in progress. Wait for it to complete."
            )

        task = run_backup_task.delay(retention_days=retention_days)

        return {
            "task_id": task.id,
            "status": "queued",
            "retention_days": retention_days,
            "message": "Backup queued. Check /api/admin/backup/status for progress.",
            "timestamp": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue backup: {str(e)}")
    finally:
        db.close()


@router.get("/backup/status")
async def get_backup_status():
    """Return the result of the most recently completed backup."""
    db = SessionLocal()
    try:
        from agentic_os.db.models import PlatformSettingModel

        keys = [
            "general.last_backup_at",
            "general.last_backup_status",
            "general.last_backup_message",
            "general.backup_retention_days",
        ]
        rows = {r.key: r.value for r in db.query(PlatformSettingModel).filter(
            PlatformSettingModel.key.in_(keys)
        ).all()}

        return {
            "last_backup_at":      rows.get("general.last_backup_at"),
            "last_backup_status":  rows.get("general.last_backup_status", "never"),
            "last_backup_message": rows.get("general.last_backup_message", ""),
            "retention_days":      int(rows.get("general.backup_retention_days", "7")),
            "timestamp":           datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching backup status: {str(e)}")
    finally:
        db.close()


@router.get("/workers")
async def get_worker_health(db: Session = Depends(get_session)):
    """
    Celery worker health: online workers, active tasks, queue depths, stuck incidents.
    Uses Celery inspect (2.5s timeout) + direct Redis llen for queue depths.
    """
    from agentic_os.tasks.celery_app import app as celery_app
    import redis as _redis

    result = {
        "workers": [],
        "queue_depths": {"workflows": 0, "default": 0, "approvals": 0},
        "stuck_incidents": [],
        "celery_reachable": False,
        "timestamp": datetime.utcnow().isoformat(),
    }

    # ── 1. Worker status via Celery broadcast inspect ────────────────────────
    try:
        inspector = celery_app.control.inspect(timeout=2.5)
        active = inspector.active() or {}
        stats  = inspector.stats()  or {}

        seen = set(list(active.keys()) + list(stats.keys()))
        for name in seen:
            tasks      = active.get(name, [])
            wstats     = stats.get(name, {})
            # Queues the worker is consuming
            queues = [
                q.get("name", "")
                for q in wstats.get("consumer", {}).get("queues", [])
            ]
            total_processed = sum(wstats.get("total", {}).values()) if isinstance(wstats.get("total"), dict) else 0
            result["workers"].append({
                "name":             name,
                "short_name":       name.split("@")[0],
                "status":           "online",
                "active_tasks":     len(tasks),
                "active_task_names": [t.get("name", "").split(".")[-1] for t in tasks],
                "queues":           queues,
                "processed":        total_processed,
            })
        result["celery_reachable"] = True
    except Exception as e:
        logger.warning(f"Celery inspect failed (workers may be down): {e}")

    # ── 2. Queue depths via Redis llen ───────────────────────────────────────
    try:
        redis_url = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
        rc = _redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        result["queue_depths"] = {
            "workflows": int(rc.llen("workflows")),
            "default":   int(rc.llen("default")),
            "approvals": int(rc.llen("approvals")),
        }
    except Exception as e:
        logger.warning(f"Redis queue depth check failed: {e}")

    # ── 3. Stuck incidents (active processing state for > 15 min) ───────────
    try:
        threshold = datetime.utcnow() - timedelta(minutes=15)
        rows = db.execute(text("""
            SELECT workflow_id, incident_number_str, lifecycle_state, updated_at
            FROM workflow_states
            WHERE lifecycle_state::text = ANY(:states)
              AND updated_at < :threshold
              AND (is_storm_parent IS NULL OR is_storm_parent = FALSE)
            ORDER BY updated_at ASC
            LIMIT 10
        """), {
            # Active, pipeline-owned states — not yet resolved and not already
            # sitting with a human (awaiting_manual) or deliberately held (storm_hold).
            "states": ["open", "in_progress", "waiting_approval", "approved", "executing"],
            "threshold": threshold,
        }).fetchall()

        result["stuck_incidents"] = [
            {
                "workflow_id":    str(r.workflow_id),
                "incident_number": r.incident_number_str or "—",
                "state":          r.lifecycle_state,
                "stuck_minutes":  int((datetime.utcnow() - r.updated_at).total_seconds() / 60),
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Stuck incident check failed: {e}")

    return result
