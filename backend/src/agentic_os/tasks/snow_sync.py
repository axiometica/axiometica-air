"""
ServiceNow incident auto-sync Celery tasks.

Two tasks:
  snow_push_incident_created   — fired when a platform incident is created
  snow_push_incident_state     — fired when a platform incident changes lifecycle state

Both run on the "workflows" queue (celery_worker) — they are incident event handlers,
not maintenance jobs, and must never be delayed by cleanup or backup tasks.
"""

import asyncio
import logging
from typing import Optional

from agentic_os.tasks.celery_app import app

logger = logging.getLogger(__name__)

# Per-worker persistent event loop. Reused across tasks in the same ForkPoolWorker
# to avoid fd-number collisions that can occur when closing a fresh loop each task.
# asyncio.run() is not used here because it calls asyncio.set_event_loop(None) on
# exit, which corrupts the thread-local loop that billiard uses for result pipe I/O.
_WORKER_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _WORKER_LOOP
    if _WORKER_LOOP is None or _WORKER_LOOP.is_closed():
        _WORKER_LOOP = asyncio.new_event_loop()
    return _WORKER_LOOP


@app.task(bind=True, queue="workflows", max_retries=3, default_retry_delay=30)
def snow_push_incident_created(self, workflow_id: str):
    """
    Auto-create a ServiceNow incident when a platform incident is opened.
    Retries up to 3 times with 30 s delay on transient failures.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.connectors.servicenow.incident_push import IncidentPush

    logger.info(f"[SNOW] Auto-push created: workflow={workflow_id}")
    db = SessionLocal()
    try:
        result = _get_loop().run_until_complete(
            IncidentPush.auto_push_if_configured(
                db_session=db,
                workflow_id=workflow_id,
                trigger_event="created",
                new_lifecycle_state="open",
            )
        )
        logger.info(f"[SNOW] Created result: {result}")
        return result
    except Exception as exc:
        logger.error(f"[SNOW] Push-created failed for {workflow_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, queue="workflows", max_retries=3, default_retry_delay=30)
def snow_push_incident_state(self, workflow_id: str, new_lifecycle_state: str):
    """
    Auto-update a ServiceNow incident when the platform incident lifecycle state changes.
    Retries up to 3 times with 30 s delay on transient failures.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.connectors.servicenow.incident_push import IncidentPush

    logger.info(f"[SNOW] Auto-push state-change: workflow={workflow_id} → {new_lifecycle_state}")
    db = SessionLocal()
    try:
        result = _get_loop().run_until_complete(
            IncidentPush.auto_push_if_configured(
                db_session=db,
                workflow_id=workflow_id,
                trigger_event="state_changed",
                new_lifecycle_state=new_lifecycle_state,
            )
        )
        logger.info(f"[SNOW] State-change result: {result}")
        return result
    except Exception as exc:
        logger.error(f"[SNOW] Push-state failed for {workflow_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, queue="workflows", max_retries=2, default_retry_delay=60)
def snow_cmdb_sync(self):
    """
    Run a full ServiceNow CMDB sync in the background.
    Enqueued by POST /api/connectors/servicenow/sync so the HTTP request
    returns immediately instead of blocking until the sync finishes.
    """
    from agentic_os.db.database import SessionLocal
    from agentic_os.connectors.servicenow.cmdb_sync import CMDBSync
    from agentic_os.api.routes.connectors import _get_config, _require_creds

    logger.info("[SNOW] CMDB sync task started")
    db = SessionLocal()
    try:
        cfg   = _get_config(db, "servicenow")
        creds = _require_creds(cfg)
    finally:
        db.close()

    db2  = SessionLocal()
    sync = CMDBSync(creds["base_url"], creds["username"], creds["password"])
    try:
        result = _get_loop().run_until_complete(sync.sync_all(db2))
        logger.info("[SNOW] CMDB sync complete: %s", result)
        return result
    except Exception as exc:
        logger.error("[SNOW] CMDB sync failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db2.close()
