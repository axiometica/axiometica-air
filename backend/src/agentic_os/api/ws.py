"""WebSocket support for real-time workflow status updates"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from uuid import UUID
import json
import asyncio
import logging

from agentic_os.db.database import SessionLocal
from agentic_os.db.repositories import WorkflowRepository, EventRepository

logger = logging.getLogger(__name__)


# ── Notification helpers (fire-and-forget, run off-thread) ──────────────────

def _notify_incident(
    incident_number: str,
    title: str,
    severity: str,
    lifecycle_state: str,
    risk_score: float | None = None,
):
    """Synchronous wrapper — called via asyncio.to_thread to avoid blocking."""
    try:
        from agentic_os.services.notifications import notify_incident_created
        notify_incident_created(incident_number, title, severity, lifecycle_state, risk_score)
    except Exception as exc:
        logger.warning("[Notify] incident notification failed: %s", exc)


def _notify_incident_resolved(
    incident_number: str,
    title: str,
    severity: str,
    lifecycle_state: str,
    risk_score: float | None = None,
    remediation_outcome: str | None = None,
):
    """Synchronous wrapper — called via asyncio.to_thread to avoid blocking."""
    try:
        from agentic_os.services.notifications import notify_incident_resolved
        notify_incident_resolved(incident_number, title, severity, lifecycle_state, risk_score, remediation_outcome)
    except Exception as exc:
        logger.warning("[Notify] resolved notification failed: %s", exc)


def _notify_storm(incident_number: str, title: str, child_count: int):
    """Synchronous wrapper — called via asyncio.to_thread to avoid blocking."""
    try:
        from agentic_os.services.notifications import notify_storm_detected
        notify_storm_detected(incident_number, title, child_count)
    except Exception as exc:
        logger.warning("[Notify] storm notification failed: %s", exc)


def _notify_approval(
    incident_number: str,
    title: str,
    severity: str,
    proposed_action: dict | None = None,
    risk_score: float | None = None,
):
    """Synchronous wrapper — called via asyncio.to_thread to avoid blocking."""
    try:
        from agentic_os.services.notifications import notify_approval_required
        notify_approval_required(incident_number, title, severity, proposed_action, risk_score)
    except Exception as exc:
        logger.warning("[Notify] approval notification failed: %s", exc)


class WorkflowConnectionManager:
    """Manages WebSocket connections for workflow updates"""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.polling_tasks: dict[str, asyncio.Task] = {}

    async def connect(self, workflow_id: str, websocket: WebSocket):
        """Accept WebSocket connection and start polling for updates"""
        await websocket.accept()

        if workflow_id not in self.active_connections:
            self.active_connections[workflow_id] = []

        self.active_connections[workflow_id].append(websocket)

        # Start polling for workflow updates
        if workflow_id not in self.polling_tasks:
            self.polling_tasks[workflow_id] = asyncio.create_task(
                self._poll_workflow(workflow_id)
            )

        logger.info(f"WebSocket connected for workflow {workflow_id}")

    def disconnect(self, workflow_id: str, websocket: WebSocket):
        """Disconnect WebSocket and cleanup"""
        if workflow_id in self.active_connections:
            self.active_connections[workflow_id].remove(websocket)

            # Stop polling if no more connections
            if not self.active_connections[workflow_id]:
                del self.active_connections[workflow_id]
                if workflow_id in self.polling_tasks:
                    self.polling_tasks[workflow_id].cancel()
                    del self.polling_tasks[workflow_id]

        logger.info(f"WebSocket disconnected for workflow {workflow_id}")

    async def broadcast(self, workflow_id: str, message: dict):
        """Broadcast message to all connected clients"""
        if workflow_id in self.active_connections:
            for connection in self.active_connections[workflow_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to {workflow_id}: {str(e)}")

    async def _poll_workflow(self, workflow_id: str):
        """Poll workflow status and broadcast updates"""
        db = SessionLocal()
        repo = WorkflowRepository(db)
        event_repo = EventRepository(db)
        last_event_count = 0

        try:
            while workflow_id in self.active_connections and self.active_connections[workflow_id]:
                # Get current workflow state
                state = repo.get(UUID(workflow_id))

                if state:
                    # Check if there are new events
                    events = event_repo.get_by_workflow_id(UUID(workflow_id))
                    new_events = len(events) > last_event_count

                    if new_events:
                        # Broadcast workflow update
                        message = {
                            "type": "workflow_update",
                            "workflow_id": str(state.workflow_id),
                            "lifecycle_state": state.lifecycle_state.value,
                            "severity": state.severity.value if state.severity else None,
                            "risk_score": state.risk_score,
                            "governance_decision": state.governance_decision,
                            "reasoning_trace_count": len(state.reasoning_trace),
                            "last_trace": state.reasoning_trace[-1] if state.reasoning_trace else None,
                        }

                        await self.broadcast(workflow_id, message)
                        last_event_count = len(events)

                        # Stop polling if workflow completed
                        if state.lifecycle_state.value in [
                            "resolved",
                            "failed",
                            "deployed",
                            "rolled_back",
                            "rejected",
                        ]:
                            await self.broadcast(
                                workflow_id,
                                {
                                    "type": "workflow_completed",
                                    "workflow_id": str(state.workflow_id),
                                    "final_state": state.lifecycle_state.value,
                                },
                            )
                            break

                # Poll every 500ms
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.debug(f"Polling cancelled for workflow {workflow_id}")
        except Exception as e:
            logger.error(f"Error polling workflow {workflow_id}: {str(e)}")
        finally:
            db.close()


# Global connection manager
manager = WorkflowConnectionManager()


async def websocket_endpoint(workflow_id: str, websocket: WebSocket):
    """WebSocket endpoint for workflow real-time updates"""
    await manager.connect(workflow_id, websocket)

    try:
        while True:
            # Keep connection alive, receive heartbeats
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(workflow_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket error for {workflow_id}: {str(e)}")
        manager.disconnect(workflow_id, websocket)


# ── Global event feed ────────────────────────────────────────────────────────

class GlobalConnectionManager:
    """
    Manages WebSocket connections for the /ws/events global feed.

    Polls workflow_states and approvals every second for any rows whose
    updated_at / requested_at moved past the last check timestamp, then
    broadcasts lightweight event messages to every connected browser tab.
    This replaces setInterval polling in the incident list, approval queue,
    and dashboard — giving sub-second latency with a single shared DB query.
    """

    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._poll_task: asyncio.Task | None = None
        # Tracks workflow_ids already notified as resolved so we don't double-fire
        # when the row gets a second updated_at bump in the same terminal state.
        self._notified_resolved: set[str] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        # Start the shared poll task only when the first client connects
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_changes())
        logger.info(f"[GlobalWS] client connected — total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"[GlobalWS] client disconnected — total: {len(self.active_connections)}")
        if not self.active_connections and self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def broadcast_all(self, message: dict):
        dead: set[WebSocket] = set()
        for ws in list(self.active_connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.active_connections -= dead

    async def _poll_changes(self):
        from datetime import datetime, timedelta
        from sqlalchemy import text

        db = SessionLocal()
        try:
            # Start 2 s in the past so any change that happened just before the
            # first client connected is caught on the very first iteration.
            last_check = datetime.utcnow() - timedelta(seconds=2)

            while self.active_connections:
                now = datetime.utcnow()
                try:
                    # ── Changed incidents ────────────────────────────────────
                    rows = db.execute(text("""
                        SELECT workflow_id, lifecycle_state, incident_number_str,
                               title, severity, risk_score, remediation_outcome,
                               duplicate_count, created_at, updated_at
                        FROM workflow_states
                        WHERE workflow_type = 'incident'
                          AND updated_at    > :last_check
                        ORDER BY updated_at DESC
                        LIMIT 30
                    """), {"last_check": last_check}).fetchall()

                    for row in rows:
                        ev_type = (
                            "incident_created"
                            if row.created_at and row.created_at > last_check
                            else "incident_updated"
                        )
                        await self.broadcast_all({
                            "type":                ev_type,
                            "workflow_id":          str(row.workflow_id),
                            "incident_number_str":  row.incident_number_str,
                            "lifecycle_state":      row.lifecycle_state,
                            "severity":             row.severity,
                            "risk_score":           float(row.risk_score) if row.risk_score else None,
                            "remediation_outcome":  row.remediation_outcome,
                            "duplicate_count":      row.duplicate_count,
                        })

                        # Outbound notification for new critical / high incidents
                        if ev_type == "incident_created":
                            sev = str(row.severity or "").lower()
                            if sev in ("critical", "high"):
                                asyncio.create_task(asyncio.to_thread(
                                    _notify_incident,
                                    str(row.incident_number_str or ""),
                                    str(row.title or "Untitled"),
                                    sev,
                                    str(row.lifecycle_state or ""),
                                    float(row.risk_score) if row.risk_score else None,
                                ))

                        # Notify when an incident reaches a terminal state (once per incident)
                        _TERMINAL_STATES = {"resolved", "deployed", "rolled_back", "rejected", "failed"}
                        wf_id_str = str(row.workflow_id)
                        if (
                            ev_type == "incident_updated"
                            and str(row.lifecycle_state or "") in _TERMINAL_STATES
                            and wf_id_str not in self._notified_resolved
                        ):
                            self._notified_resolved.add(wf_id_str)
                            asyncio.create_task(asyncio.to_thread(
                                _notify_incident_resolved,
                                str(row.incident_number_str or ""),
                                str(row.title or "Untitled"),
                                str(row.severity or "unknown"),
                                str(row.lifecycle_state or ""),
                                float(row.risk_score) if row.risk_score else None,
                                str(row.remediation_outcome or "") or None,
                            ))

                    # ── New pending approvals ────────────────────────────────
                    new_appr = db.execute(text("""
                        SELECT COUNT(*) AS cnt
                        FROM approvals
                        WHERE status       = 'pending'
                          AND requested_at > :last_check
                    """), {"last_check": last_check}).scalar() or 0

                    if new_appr:
                        await self.broadcast_all({
                            "type":      "approval_requested",
                            "new_count": int(new_appr),
                        })

                        # Outbound approval notifications — fetch incident details
                        appr_rows = db.execute(text("""
                            SELECT ws.incident_number_str,
                                   ws.title,
                                   COALESCE(CAST(ws.severity AS TEXT), 'unknown') AS severity,
                                   a.proposed_action,
                                   ws.risk_score
                            FROM approvals a
                            JOIN workflow_states ws ON ws.workflow_id = a.workflow_id
                            WHERE a.status       = 'pending'
                              AND a.requested_at > :last_check
                            LIMIT 5
                        """), {"last_check": last_check}).fetchall()
                        for ar in appr_rows:
                            # proposed_action may come back as str or dict depending on driver
                            pa = ar[3]
                            if isinstance(pa, str):
                                try:
                                    pa = json.loads(pa)
                                except Exception:
                                    pa = {}
                            elif pa is None:
                                pa = {}
                            asyncio.create_task(asyncio.to_thread(
                                _notify_approval,
                                str(ar[0] or ""),
                                str(ar[1] or "Untitled"),
                                str(ar[2] or "unknown"),
                                pa,
                                float(ar[4]) if ar[4] else None,
                            ))

                    # ── Approvals resolved (approved / rejected) ─────────────
                    resolved_appr = db.execute(text("""
                        SELECT COUNT(*) AS cnt
                        FROM approvals
                        WHERE status   IN ('approved', 'rejected')
                          AND decided_at > :last_check
                    """), {"last_check": last_check}).scalar() or 0

                    if resolved_appr:
                        await self.broadcast_all({
                            "type":           "approval_resolved",
                            "resolved_count": int(resolved_appr),
                        })

                    # ── New monitoring events (status = 'new') ───────────────
                    new_mon = db.execute(text("""
                        SELECT COUNT(*) AS cnt
                        FROM monitoring_events
                        WHERE status     = 'new'
                          AND created_at > :last_check
                    """), {"last_check": last_check}).scalar() or 0

                    if new_mon:
                        await self.broadcast_all({
                            "type":      "monitoring_event_new",
                            "new_count": int(new_mon),
                        })

                    # ── Storm parent state changes ────────────────────────────
                    storm_rows = db.execute(text("""
                        SELECT
                            ws.workflow_id,
                            ws.lifecycle_state,
                            ws.title,
                            ws.incident_number_str,
                            ws.created_at,
                            (SELECT COUNT(*)
                             FROM workflow_states c
                             WHERE c.storm_id = ws.workflow_id
                               AND c.workflow_id != ws.workflow_id) AS child_count
                        FROM workflow_states ws
                        WHERE ws.workflow_type = 'incident'
                          AND (ws.context ->> 'is_storm_parent')::boolean = true
                          AND ws.updated_at > :last_check
                    """), {"last_check": last_check}).fetchall()

                    for s_row in storm_rows:
                        await self.broadcast_all({
                            "type":            "storm_changed",
                            "workflow_id":     str(s_row[0]),
                            "lifecycle_state": s_row[1],
                        })

                        # Outbound notification only when storm is newly created
                        if s_row[4] and s_row[4] > last_check:
                            asyncio.create_task(asyncio.to_thread(
                                _notify_storm,
                                str(s_row[3] or ""),
                                str(s_row[2] or "Event Storm"),
                                int(s_row[5] or 0),
                            ))

                    last_check = now
                    db.commit()          # release any implicit transaction

                except Exception as poll_err:
                    logger.error(f"[GlobalWS] poll error: {poll_err}")
                    db.rollback()

                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.debug("[GlobalWS] poll task cancelled — no clients left")
        except Exception as e:
            logger.error(f"[GlobalWS] poll task crashed: {e}", exc_info=True)
        finally:
            db.close()


global_manager = GlobalConnectionManager()


async def global_events_endpoint(websocket: WebSocket):
    """WebSocket endpoint for the global incident / approval event feed."""
    await global_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        global_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"[GlobalWS] connection error: {e}")
        global_manager.disconnect(websocket)
