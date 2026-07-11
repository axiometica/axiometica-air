"""
Monitoring Events API Routes

Endpoints for monitoring event submission, qualification, and management.
Events are raw signals from watcher/sentinel. They go through qualification
to determine if they should trigger incident workflows.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
from datetime import datetime

from agentic_os.db.database import get_session
from agentic_os.db.models import WatcherRegistrationModel
from agentic_os.db.repositories import MonitoringEventRepository, WorkflowRepository, EventConditionStateRepository
from agentic_os.core.models import WorkflowState, WorkflowType, LifecycleState
from agentic_os.services.event_qualification import get_qualification_service
from agentic_os.services.enumeration_service import EnumerationService
from agentic_os.tasks.celery_app import execute_workflow_task
from agentic_os.tasks.snow_sync import snow_push_incident_created, snow_push_incident_state

router = APIRouter()


# ========== Pydantic Models ==========

class MonitoringEventSubmit(BaseModel):
    """Submit a raw monitoring event"""
    source: str  # "watcher_brain", "sentinel_senses"
    event_type: str  # "high_cpu", "disk_full", etc.
    resource_name: str  # resource affected
    raw_criticality: str  # "info", "warning", "critical"
    signal_value: Optional[float] = None
    signal_threshold: Optional[float] = None
    anomaly_process: Optional[str] = None  # process involved
    raw_payload: dict = {}  # full event details


class MonitoringEventResponse(BaseModel):
    """Monitoring event response"""
    event_id: str
    source: str
    event_type: str
    resource_name: str
    raw_criticality: str
    qualification_score: float
    qualified_as_incident: bool
    incident_workflow_id: Optional[str]
    status: str
    detected_at: str
    created_at: str
    updated_at: str

    # Qualification details
    qualification_reason: Optional[str] = None
    qualification_factors: Optional[dict] = None
    confidence: Optional[float] = None

    # Signal details (metric values from the watcher)
    signal_value: Optional[float] = None
    signal_threshold: Optional[float] = None
    anomaly_process: Optional[str] = None

    # Payload details (human-readable content from the watcher)
    payload_title: Optional[str] = None
    payload_description: Optional[str] = None

    # Full raw event payload
    raw_payload: dict = {}


class QualificationResult(BaseModel):
    """Qualification scoring result"""
    qualified: bool
    score: float
    confidence: float
    reason: str
    factors: dict
    ci_info: dict
    unknown_fields: list


# ========== Helpers ==========

def _get_pipeline_hold_seconds() -> int:
    """
    Read storm.pipeline_hold_seconds from platform_settings (env-var fallback).

    When > 0, newly-created incident pipelines are delayed by this many seconds,
    giving storm detection time to fire and set storm_hold before the pipeline
    even starts.  The pre-pipeline storm guard in execute_workflow_task will then
    exit immediately if the incident was adopted into a storm during the hold.

    Default: 0 (no delay — current behaviour).
    Recommended: 30–120 for noisy environments where false individual remediations
    are a concern.  Setting to 120 guarantees storm detection always wins.
    """
    import os as _os
    fallback = int(_os.getenv("STORM_PIPELINE_HOLD_SECONDS", "0"))
    try:
        from agentic_os.db.database import SessionLocal as _SL
        from sqlalchemy import text as _sql_text
        _db = _SL()
        try:
            row = _db.execute(_sql_text("""
                SELECT value FROM platform_settings
                WHERE category = 'storm' AND key = 'storm.pipeline_hold_seconds'
                LIMIT 1
            """)).fetchone()
            if row:
                return int(row[0])
        finally:
            _db.close()
    except Exception:
        pass
    return fallback


def _build_storm_check_task():
    """
    Build a storm-detection background task function.

    Called after EVERY qualified monitoring event — both newly-created incidents
    and dedup'd events that matched existing incidents.  This ensures the storm
    detector fires even when all events in a burst dedup to pre-existing incidents
    (e.g., after a storm is released and children revert to 'open').

    Returns a callable suitable for FastAPI BackgroundTasks.add_task().
    """
    import logging as _log

    def _check():
        _logger = _log.getLogger(__name__)
        try:
            import time as _time
            from agentic_os.db.database import SessionLocal as _SL
            from agentic_os.services.storm_detection import get_storm_detection_service
            from agentic_os.tasks.celery_app import execute_storm_analysis_task

            _db2 = _SL()
            try:
                svc = get_storm_detection_service()
                candidate = svc.detect(_db2)
                if candidate:
                    _logger.info(
                        f"[STORM DETECT] Candidate: "
                        f"{len(candidate.incident_ids)} incidents, "
                        f"resources={candidate.resource_names}"
                    )

                    # ── Deduplication via time-bucketed task ID ────────────────
                    # When a burst of incidents arrives (e.g. 13 disk_full events
                    # in 8 seconds), storm detection fires once per incident,
                    # queueing up to 13 identical tasks.  With the advisory lock
                    # they all serialise, but the Celery queue fills with wasted
                    # work.  Using a 20-second time-bucket task_id means Celery
                    # will deduplicate them: if a task with the same ID is already
                    # in the queue (PENDING), the new apply_async REPLACES its
                    # kwargs with the latest (larger) incident list.  The running
                    # task (if any) still completes; the replacement runs after it
                    # and the advisory-lock merge path absorbs any remaining
                    # unassigned incidents into the existing storm.
                    _bucket   = int(_time.time() // 20)   # 20-second window
                    _task_id  = f"storm-burst-{_bucket}"

                    execute_storm_analysis_task.apply_async(
                        kwargs={
                            "incident_ids":   candidate.incident_ids,
                            "resource_names": candidate.resource_names,
                            "event_types":    candidate.event_types,
                        },
                        countdown=5,    # allow in-flight DB tx to commit first
                        task_id=_task_id,
                    )
                    _logger.debug(
                        f"[STORM DETECT] Task queued: {_task_id} "
                        f"({len(candidate.incident_ids)} incidents)"
                    )
            finally:
                _db2.close()
        except Exception as _storm_err:
            _log.getLogger(__name__).warning(
                f"[STORM DETECT] Check failed (non-fatal): {_storm_err}"
            )

    return _check


# ========== Endpoints ==========

async def _handle_condition_cleared(
    event: "MonitoringEventSubmit",
    db: Session,
) -> "MonitoringEventResponse":
    """
    Handle a condition_cleared signal from the watcher.

    Finds all open (non-resolved) incidents for this resource that match the
    original event type and closes them with resolution_source=watcher_all_clear.
    The remediation_outcome is preserved (aborted/failed stays as-is).
    """
    import logging
    from sqlalchemy import text as sql_text
    bg_logger = logging.getLogger(__name__)

    original_event_type = (event.raw_payload or {}).get("original_event_type")

    # Find open incidents for this resource (optionally filtered by original event type).
    # Only skip truly terminal states (resolved, closed).
    # awaiting_manual IS included — if the condition clears while a human is deciding
    # what to do, the incident auto-resolves and the operator is notified.
    # BUG FIX: 'rejected' was previously excluded, meaning rejected-remediation incidents
    # never auto-resolved even when the triggering condition cleared on its own.
    open_rows = db.execute(sql_text("""
        SELECT workflow_id, lifecycle_state, incident_number_str
        FROM workflow_states
        WHERE workflow_type = 'incident'
          AND lifecycle_state NOT IN ('resolved', 'closed')
          AND context -> 'alert_payload' ->> 'resource_name' = :resource_name
          AND (
              :original_event_type IS NULL
              OR context -> 'alert_payload' ->> 'type' = :original_event_type
          )
        ORDER BY created_at DESC
    """), {
        "resource_name": event.resource_name,
        "original_event_type": original_event_type,
    }).fetchall()

    closed_count = 0
    now = datetime.utcnow()
    for row in open_rows:
        workflow_id, prev_state, inc_num = row

        # Resolve the workflow
        db.execute(sql_text("""
            UPDATE workflow_states
            SET lifecycle_state        = 'resolved',
                resolution_source      = 'watcher_all_clear',
                all_clear_received_at  = :now,
                updated_at             = :now,
                resolved_at            = COALESCE(resolved_at, :now)
            WHERE workflow_id = :workflow_id
        """), {"workflow_id": workflow_id, "now": now})

        # If the workflow was waiting for human approval, cancel the pending approval
        # record so it disappears from the queue and cannot be acted on after the
        # fact (acting on a stale approval would queue remediation on a self-healed
        # system, which could cause harm).
        if prev_state == "waiting_approval":
            cancelled = db.execute(sql_text("""
                UPDATE approvals
                SET status         = 'cancelled',
                    decided_at     = :now,
                    decided_by     = 'system',
                    decision_notes = 'Auto-cancelled — watcher confirmed condition cleared before operator decision'
                WHERE workflow_id = :workflow_id
                  AND status      = 'pending'
            """), {"workflow_id": workflow_id, "now": now})
            if cancelled.rowcount:
                bg_logger.info(
                    f"[ALL CLEAR] Cancelled pending approval for {inc_num or workflow_id} "
                    f"(condition cleared before operator acted)"
                )

        bg_logger.info(
            f"[ALL CLEAR] Closed {inc_num or workflow_id} "
            f"(was {prev_state}) — watcher confirmed condition cleared"
        )
        closed_count += 1

        # System note — written into the same transaction as the UPDATE above
        try:
            from agentic_os.db.models import IncidentNoteModel
            from uuid import UUID as _UUID

            note_lines = [
                "✓ Incident auto-resolved — watcher confirmed condition cleared",
                "",
                "The monitoring system observed that the triggering condition has "
                "self-healed. No manual intervention was required.",
            ]
            if prev_state == "waiting_approval":
                note_lines += [
                    "",
                    "The pending CAB approval was automatically cancelled — "
                    "executing remediation on an already-healed system could cause harm.",
                ]
            note_lines += [
                "",
                f"Resolution source: watcher all-clear signal",
                f"Resolved at: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            ]
            db.add(IncidentNoteModel(
                workflow_id=_UUID(str(workflow_id)),
                author="system",
                note_type="system",
                body="\n".join(note_lines),
            ))
        except Exception as _note_err:
            bg_logger.warning(
                f"[ALL CLEAR] Failed to write system note for {inc_num or workflow_id}: {_note_err}"
            )

    db.commit()

    # Close all open condition-state rows for this resource so the next alert
    # fires a fresh event instead of being deduplicated against the old one.
    cond_repo = EventConditionStateRepository(db)
    closed_conditions = cond_repo.close_for_resource(event.resource_name)
    if closed_conditions:
        bg_logger.info(
            "[ALL CLEAR] Closed %d condition-state row(s) for %s",
            closed_conditions, event.resource_name,
        )

    # Push resolved state to ServiceNow for each closed incident
    for row in open_rows:
        workflow_id = row[0]
        snow_push_incident_state.delay(str(workflow_id), "resolved")

    # Create a dismissed monitoring event for audit trail.
    # condition_cleared is a definitive recovery observation — not a probabilistic
    # qualification.  Set confidence=100 and a clear reason so the UI can distinguish
    # it from low-confidence alert events (score is left at 0 because there is no
    # qualification score concept for an all-clear signal).
    all_clear_reason = f"Condition cleared — {closed_count} incident(s) closed"
    event_repo = MonitoringEventRepository(db)
    monitoring_event = event_repo.create(
        source=event.source,
        event_type=event.event_type,
        resource_name=event.resource_name,
        raw_criticality=event.raw_criticality,
        qualification_score=0.0,
        qualification_reason=all_clear_reason,
        confidence=100.0,
        raw_payload=event.raw_payload or {},
        signal_value=None,
        signal_threshold=None,
        anomaly_process=None,
        detected_at=now,
    )
    event_repo.dismiss_event(monitoring_event.event_id)

    bg_logger.info(
        f"[ALL CLEAR] {event.resource_name} ({original_event_type or 'any'}): "
        f"closed {closed_count} incident(s)"
    )

    return MonitoringEventResponse(
        event_id=str(monitoring_event.event_id),
        source=monitoring_event.source,
        event_type=monitoring_event.event_type,
        resource_name=monitoring_event.resource_name,
        raw_criticality=monitoring_event.raw_criticality,
        qualification_score=0.0,
        qualified_as_incident=False,
        incident_workflow_id=None,
        status="dismissed",
        detected_at=monitoring_event.detected_at.isoformat(),
        created_at=monitoring_event.created_at.isoformat(),
        updated_at=monitoring_event.updated_at.isoformat(),
        qualification_reason=all_clear_reason,
        confidence=100.0,
    )


def _record_validated_duplicate(
    db: Session,
    logger,
    event: "MonitoringEventSubmit",
    original_source: str,
    original_event_type: str,
    incident_workflow_id,
) -> None:
    """
    Bump duplicate_count + leave a visible system note when `event` is
    confirmed to be an exact repeat (same source AND same event_type) of the
    condition already linked to `incident_workflow_id` — and that incident
    hasn't reached a state where a fresh incident should open instead.

    Called from both dedup gates: the early EventConditionState short-circuit
    (the one that actually intercepts most real-world recurrences, since that
    condition stays open for up to 24h) and the later resource-level dedup in
    the qualification branch (which matches on resource_name alone, so the
    original event there could be a different event_type — multi-symptom
    incidents are intentionally linked but aren't literal duplicates).
    """
    if original_source != event.source or original_event_type != event.event_type or not incident_workflow_id:
        return
    try:
        from sqlalchemy import text as sql_text
        wf_row = db.execute(sql_text(
            "SELECT lifecycle_state FROM workflow_states WHERE workflow_id = :wf_id"
        ), {"wf_id": str(incident_workflow_id)}).fetchone()
        if not wf_row or wf_row[0] in ("failed", "resolved", "closed", "rejected"):
            return
        new_count = WorkflowRepository(db).increment_duplicate_count(incident_workflow_id)
        if new_count:
            from agentic_os.db.models import IncidentNoteModel
            db.add(IncidentNoteModel(
                workflow_id=incident_workflow_id,
                author="system",
                note_type="system",
                body=(
                    f"⚠ Recurred again — {event.event_type} on {event.resource_name} "
                    f"detected again while this incident is '{wf_row[0]}' "
                    f"(occurrence #{new_count + 1})."
                ),
            ))
            db.commit()
            logger.info(
                f"[DEDUP] Validated duplicate (occurrence #{new_count + 1}) for "
                f"incident {incident_workflow_id} ({event.event_type} on {event.resource_name})"
            )
            # Append to the event log too — the incident detail page's live-update
            # poller (_poll_workflow in api/ws.py) only triggers on new rows in
            # `events`, not on arbitrary workflow_states changes, so without this
            # the badge stays stale for anyone with that page open when a
            # recurrence lands.
            try:
                from uuid import UUID as _UUID
                from agentic_os.core.models import EventEnvelope, EventType
                from agentic_os.db.repositories import EventRepository
                EventRepository(db).append(EventEnvelope(
                    workflow_id=_UUID(str(incident_workflow_id)),
                    workflow_type=WorkflowType.INCIDENT,
                    event_type=EventType.MONITORING_EVENT_DETECTED,
                    source_agent="dedup_validator",
                    payload={
                        "duplicate_count": new_count,
                        "event_type": event.event_type,
                        "resource_name": event.resource_name,
                        "occurrence": new_count + 1,
                    },
                ))
            except Exception as event_err:
                logger.warning(f"[DEDUP] Failed to append duplicate event: {event_err}")
    except Exception as dup_err:
        logger.warning(f"[DEDUP] Failed to record validated duplicate: {dup_err}")


@router.post("/monitoring-events", response_model=MonitoringEventResponse, status_code=201)
async def submit_monitoring_event(
    event: MonitoringEventSubmit,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """
    Submit a raw monitoring event for qualification.

    Special case: event_type='condition_cleared' bypasses qualification and
    closes any open incidents for the resource (all-clear from watcher).

    The event goes through EventQualificationService which:
    1. Fetches CMDB data for the resource
    2. Scores the event based on criticality, resource importance, etc.
    3. Compares against configurable threshold
    4. If qualified: automatically creates incident workflow
    5. Returns event + qualification details

    Args:
        event: Monitoring event data
        db: Database session

    Returns:
        MonitoringEventResponse with qualification result and incident status
    """
    # ── Watcher registration gate ─────────────────────────────────────────────
    # Reject events from watchers that are not in 'approved' state.
    # This enforces Disable and Invalidate operations — previously the event
    # submission route had no status check, so disable/invalidate had no effect
    # on event flow (the watcher kept submitting and events kept being created).
    _watcher_row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == event.source
    ).first()
    if _watcher_row is not None:
        _reg_status = getattr(_watcher_row, "registration_status", "approved") or "approved"
        if _reg_status != "approved":
            logger.info(
                f"[EVENTS] Suppressed event from '{event.source}' "
                f"(registration_status={_reg_status})"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Watcher '{event.source}' is not approved (status: {_reg_status}). "
                       f"Re-approve in Admin → Monitoring Setup to resume event ingestion.",
            )

    # All-clear signal — short-circuit before qualification
    if event.event_type == "condition_cleared":
        return await _handle_condition_cleared(event, db)

    try:
        import logging
        logger = logging.getLogger(__name__)

        # ── CONDITION-STATE DEDUPLICATION ────────────────────────────────────
        # If (resource_name, event_type) is already open we have already recorded
        # this condition.  Return the original event idempotently — no new row,
        # no new incident.  The condition stays open until condition_cleared or
        # incident resolution signals that the problem is gone.
        cond_repo = EventConditionStateRepository(db)
        open_condition = cond_repo.is_open(event.resource_name, event.event_type)
        if open_condition and open_condition.last_event_id:
            existing = db.query(__import__('agentic_os.db.models', fromlist=['MonitoringEventModel']).MonitoringEventModel).filter_by(
                event_id=open_condition.last_event_id
            ).first()
            if existing:
                logger.info(
                    "[DEDUP] Dropping duplicate %s on %s — condition already open (first seen: %s)",
                    event.event_type, event.resource_name,
                    open_condition.opened_at.isoformat(),
                )
                if existing.incident_workflow_id:
                    _record_validated_duplicate(
                        db, logger, event, existing.source, existing.event_type,
                        existing.incident_workflow_id,
                    )
                return MonitoringEventResponse(
                    event_id=str(existing.event_id),
                    source=existing.source,
                    event_type=existing.event_type,
                    resource_name=existing.resource_name,
                    raw_criticality=existing.raw_criticality,
                    qualification_score=existing.qualification_score,
                    qualified_as_incident=existing.qualified_as_incident,
                    incident_workflow_id=str(existing.incident_workflow_id) if existing.incident_workflow_id else None,
                    status=existing.status,
                    detected_at=existing.detected_at.isoformat(),
                    created_at=existing.created_at.isoformat(),
                    updated_at=existing.updated_at.isoformat(),
                    qualification_reason=existing.qualification_reason,
                    confidence=existing.confidence,
                )
        # ─────────────────────────────────────────────────────────────────────

        # Get qualification service
        qualifier = get_qualification_service()

        # Run qualification scoring
        qual_result = qualifier.qualify_event(
            event_type=event.event_type,
            resource_name=event.resource_name,
            raw_criticality=event.raw_criticality,
            signal_value=event.signal_value,
            signal_threshold=event.signal_threshold,
        )

        # Create MonitoringEvent record
        event_repo = MonitoringEventRepository(db)
        monitoring_event = event_repo.create(
            source=event.source,
            event_type=event.event_type,
            resource_name=event.resource_name,
            raw_criticality=event.raw_criticality,
            qualification_score=qual_result["score"],
            qualification_reason=qual_result.get("reason", ""),
            qualification_factors=qual_result.get("factors"),
            confidence=qual_result.get("confidence"),
            raw_payload=event.raw_payload,
            signal_value=event.signal_value,
            signal_threshold=event.signal_threshold,
            anomaly_process=event.anomaly_process,
            detected_at=datetime.utcnow(),
        )

        # Mark condition as open so subsequent duplicate events are dropped.
        # qualified flag is set after the scoring block below, so we open with
        # qualified=False first and then re-set it once we know the outcome.
        cond_repo.open_condition(event.resource_name, event.event_type, monitoring_event.event_id, qualified=False)

        incident_workflow_id = None

        # If qualified: create incident workflow
        if qual_result["qualified"]:
            try:
                import logging
                from datetime import timedelta
                from sqlalchemy import text as sql_text
                logger = logging.getLogger(__name__)

                # ── DEDUPLICATION CHECK ──────────────────────────────────────────
                # Rules:
                #   1. Active incident (non-terminal state) → always suppress
                #   2. failed / rejected within cooldown → suppress
                #      (prevents rapid retry loops on broken/denied workflows)
                #   3. resolved / closed → NEVER suppress
                #      Remediation success means the condition was fixed.  If the
                #      same signal fires again it is a new occurrence and must open
                #      a fresh incident immediately.
                #
                # Cooldown only applies to failed + rejected.  Window = 5 min
                # (tight enough to stop retry spam, loose enough to let a genuine
                # second failure through quickly).
                # ────────────────────────────────────────────────────────────────
                COOLDOWN_MINUTES = 5
                cooldown_cutoff = datetime.utcnow() - timedelta(minutes=COOLDOWN_MINUTES)

                dedup_row = db.execute(sql_text("""
                    SELECT workflow_id, lifecycle_state
                    FROM workflow_states
                    WHERE workflow_type = 'incident'
                      AND context -> 'alert_payload' ->> 'resource_name' = :resource_name
                      AND (
                          -- Any in-flight incident for this resource suppresses a new one.
                          -- We match on resource_name only (not event_type) so that multiple
                          -- anomaly types from the same container (e.g. high_cpu + high_syscall
                          -- both caused by the same 'yes' process) are linked to the same
                          -- incident rather than opening duplicates.
                          lifecycle_state NOT IN ('failed','resolved','closed','rejected')
                          OR
                          -- failed/rejected recently: back-off to prevent spam
                          (lifecycle_state IN ('failed','rejected')
                           AND created_at > :cooldown_cutoff)
                          -- resolved / closed: NOT included → re-trigger creates new incident
                      )
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {
                    "resource_name":   event.resource_name,
                    "cooldown_cutoff": cooldown_cutoff,
                }).fetchone()

                if dedup_row:
                    existing_id, existing_state = dedup_row[0], dedup_row[1]
                    logger.info(
                        f"[DEDUP] Suppressed {event.event_type} on {event.resource_name} "
                        f"— existing incident {existing_id} state='{existing_state}'"
                    )
                    # Link monitoring event to existing incident for audit trail
                    try:
                        event_repo.qualify_event(monitoring_event.event_id, existing_id)
                    except Exception:
                        pass  # ignore if already linked
                    incident_workflow_id = str(existing_id)

                    # ── Validated duplicate detection ──────────────────────────
                    # Only bump duplicate_count when this is confirmed to be an
                    # exact repeat of the same condition (same source AND same
                    # event_type) that originally opened this incident — not just
                    # any event sharing the resource. A different anomaly type on
                    # the same container still links above (intentional), but
                    # isn't a literal repeat of the same problem, so it doesn't
                    # count as a duplicate.
                    original_row = db.execute(sql_text("""
                        SELECT source, event_type
                        FROM monitoring_events
                        WHERE incident_workflow_id = :workflow_id
                        ORDER BY created_at ASC
                        LIMIT 1
                    """), {"workflow_id": existing_id}).fetchone()
                    if original_row:
                        _record_validated_duplicate(
                            db, logger, event, original_row[0], original_row[1], existing_id,
                        )

                    # ── Storm detection for dedup'd events ─────────────────────
                    # Even though this event matched an existing incident, the
                    # correlated cluster may now have grown to storm threshold.
                    # Fire the same storm check so rapidly-arriving dedup'd events
                    # (e.g., after a storm release) are also caught.
                    background_tasks.add_task(_build_storm_check_task())

                else:
                    # ── No active/recent incident — create a new one ──────────
                    title = (event.raw_payload.get("title") if event.raw_payload else None)
                    if not title:
                        type_display = event.event_type.replace("_", " ").title()
                        title = f"{type_display} on {event.resource_name}"

                    # Map raw_criticality → incident severity (never default to "high")
                    _severity_map = {"info": "low", "warning": "medium", "critical": "high"}
                    incident_severity = _severity_map.get(event.raw_criticality, "medium")

                    # Use payload description if available, otherwise fall back to qual reason
                    event_description = (
                        (event.raw_payload.get("description") if event.raw_payload else None)
                        or qual_result.get("reason", "")
                    )

                    initial_summary = (
                        f"{event.event_type} on {event.resource_name} "
                        f"(Severity: {incident_severity})"
                    )

                    # Merge enriched fields from raw_payload (container, port,
                    # process_name, failure_reason set by watcher discovery).
                    # Base fields take precedence; raw_payload fills in extras.
                    _raw = event.raw_payload or {}
                    _extra = {k: v for k, v in _raw.items()
                              if k not in ("severity", "type", "resource_name",
                                           "description", "anomaly_process")}

                    # Tag externally-sourced events so PolicyBrokerAgent can gate
                    # auto-remediation. Internal sources are never tagged.
                    # Dynamically include all registered watcher names so any
                    # watcher instance (watcher_brain, watcher_test, etc.) is
                    # treated as internal regardless of its name.
                    _base_internal = {"sentinel_senses", "manual"}
                    try:
                        from agentic_os.db.models import WatcherRegistrationModel as _WRM
                        _watcher_names = {
                            r.watcher_name
                            for r in db.query(_WRM.watcher_name).all()
                        }
                    except Exception:
                        _watcher_names = {"watcher_brain"}
                    _internal_sources = _base_internal | _watcher_names
                    _source_connector = (
                        event.source
                        if event.source not in _internal_sources
                        else None
                    )

                    # For connector events the source name (e.g. "dynatrace") is
                    # not a watcher container.  Resolve the correct watcher via
                    # the CI's watcher_source_id (UUID) — set during discovery by
                    # whichever watcher last saw the container.  Falls back to the
                    # first registered watcher, then to "watcher_brain".
                    if _source_connector:
                        try:
                            from agentic_os.db.models import WatcherRegistrationModel as _WRM
                            from agentic_os.services.cmdb import get_cmdb as _get_cmdb
                            import uuid as _uuid_mod
                            _watcher_source_id = _get_cmdb().get_ci_watcher_id(event.resource_name)
                            _wreg = None
                            if _watcher_source_id:
                                try:
                                    _wreg = db.query(_WRM).filter_by(
                                        watcher_id=_uuid_mod.UUID(_watcher_source_id)
                                    ).first()
                                except Exception:
                                    pass
                            if _wreg is None:
                                _wreg = db.query(_WRM).first()
                            _execution_watcher = _wreg.watcher_name if _wreg else "watcher_brain"
                        except Exception:
                            _execution_watcher = "watcher_brain"
                    else:
                        _execution_watcher = event.source

                    # ── Storm eligibility resolution ───────────────────────────
                    # For external connector events, read allow_storm_detection
                    # from the connector config (default: True).  If False, the
                    # storm detector will skip this incident — prevents bulk syncs
                    # from a historical or batch-mode connector from triggering
                    # false storms (all events inserted at once share the same
                    # created_at even though they may span hours of real time).
                    #
                    # We also capture the original alert time (monitoring_event.
                    # detected_at) so the storm detector can anchor the time
                    # window on the REAL event time rather than the DB insertion
                    # time.  A batch sync will then correctly show events spread
                    # across hours, not clustered in a 300-second window.
                    _storm_eligible_tag: dict = {}
                    _source_alert_time_tag: dict = {}

                    if _source_connector:
                        try:
                            from agentic_os.db.models import ConnectorConfigModel as _CCM
                            _conn_cfg = db.query(_CCM).filter_by(id=_source_connector).first()
                            _allow_storm = bool(
                                (_conn_cfg.config_json or {}).get("allow_storm_detection", True)
                            ) if _conn_cfg else True
                            if not _allow_storm:
                                _storm_eligible_tag = {"storm_eligible": False}
                        except Exception as _sce:
                            logger.debug(f"Could not read storm eligibility for {_source_connector}: {_sce}")

                    # Embed the original alert time when available.
                    # monitoring_event.detected_at is set by the watcher/connector
                    # to the time the anomaly was actually detected, which for
                    # real-time connectors equals now(), and for historical syncs
                    # equals the original alert timestamp.
                    if monitoring_event.detected_at:
                        _source_alert_time_tag = {
                            "source_alert_time": monitoring_event.detected_at.isoformat()
                        }

                    incident_state = WorkflowState(
                        workflow_type=WorkflowType.INCIDENT,
                        lifecycle_state=LifecycleState.OPEN,
                        title=title,
                        summary=initial_summary,
                        context={
                            "alert_payload": {
                                "severity": incident_severity,
                                "type": event.event_type,
                                "resource_name": event.resource_name,
                                "description": event_description,
                                "anomaly_process": event.anomaly_process,
                                "monitoring_event_id": str(monitoring_event.event_id),
                                "qualification_score": qual_result["score"],
                                "qualification_confidence": qual_result["confidence"],
                                # Which watcher to use for tool execution.
                                # Connector events use a real registered watcher
                                # (watcher_brain) — the connector name is not routable.
                                "watcher_name": _execution_watcher,
                                # External connector tag (None for internal events)
                                **({"source_connector": _source_connector} if _source_connector else {}),
                                # Storm eligibility (False = excluded from storm detection)
                                **_storm_eligible_tag,
                                # Original alert time for accurate storm window filtering
                                **_source_alert_time_tag,
                                **_extra,
                            }
                        }
                    )

                    workflow_repo = WorkflowRepository(db)
                    workflow_repo.save(incident_state)
                    incident_workflow_id = str(incident_state.workflow_id)

                    # INC number is assigned automatically by DB trigger on INSERT.
                    # Read it back to confirm and log it.
                    try:
                        incident_number_str = EnumerationService.generate_incident_number(
                            db, incident_workflow_id
                        )
                        logger.info(f"Incident {incident_number_str} created for watcher event {incident_workflow_id}")
                    except Exception as enum_err:
                        logger.warning(f"Could not read incident number: {enum_err}")

                    # Link event to new incident
                    event_repo.qualify_event(monitoring_event.event_id, incident_state.workflow_id)

                    # Upgrade condition state to qualified=True so the 24h TTL applies
                    # (not the 15-min dismissed TTL)
                    cond_repo.open_condition(event.resource_name, event.event_type, monitoring_event.event_id, qualified=True)

                    # ── Queue incident pipeline (with optional storm-detection hold) ──
                    # storm.pipeline_hold_seconds (platform setting, default 0) delays
                    # the pipeline Celery task so storm detection has time to adopt
                    # the incident before any enrichment or remediation begins.
                    # When > 0, the pre-pipeline guard in execute_workflow_task exits
                    # immediately if storm_hold has been set during the hold window.
                    _hold_secs = _get_pipeline_hold_seconds()
                    if _hold_secs > 0:
                        execute_workflow_task.apply_async(
                            kwargs={
                                "workflow_id":   incident_workflow_id,
                                "workflow_type": WorkflowType.INCIDENT.value,
                            },
                            countdown=_hold_secs,
                        )
                        logger.info(
                            f"[STORM HOLD] Pipeline for {incident_workflow_id[:8]} "
                            f"delayed {_hold_secs}s for storm detection window"
                        )
                    else:
                        execute_workflow_task.delay(
                            workflow_id=incident_workflow_id,
                            workflow_type=WorkflowType.INCIDENT.value,
                        )

                    # ── Storm detection (fire-and-forget background task) ──────
                    # After creating a new incident, check whether it forms part
                    # of a correlated storm.  We use a FastAPI background task
                    # so it does NOT block the HTTP response.  The actual heavy
                    # lifting (Neo4j + LLM) is delegated to a Celery task.
                    background_tasks.add_task(_build_storm_check_task())

                    # Auto-push to ServiceNow if configured (fire-and-forget)
                    snow_push_incident_created.delay(incident_workflow_id)

                    # Queue LLM summary generation
                    _event_type   = event.event_type
                    _resource_name = event.resource_name
                    _severity      = qual_result.get("severity", "high")
                    _description   = qual_result.get("reason", "")
                    _workflow_id   = incident_state.workflow_id

                    def generate_watcher_summary_sync():
                        """Generate placeholder summary for watcher-created incidents"""
                        import logging
                        from sqlalchemy import update
                        from agentic_os.db.models import WorkflowStateModel
                        from agentic_os.db.database import SessionLocal

                        bg_logger = logging.getLogger(__name__)
                        bg_logger.info(f"[SUMMARY] Watcher background task started for {_workflow_id}")

                        try:
                            summary = None

                            # Always use the deterministic platform-context summary here — never
                            # an LLM call. It's a placeholder shown only until the Celery ENRICH
                            # task lands a real LLM-generated executive summary at workflow
                            # completion, which always runs and always overwrites this value. An
                            # LLM call here would be pure wasted spend: guaranteed to be replaced
                            # before the incident's lifecycle ends.
                            try:
                                from agentic_os.services.platform_context_service import get_platform_context_service
                                platform_service = get_platform_context_service()
                                plat_db = SessionLocal()
                                try:
                                    incident_model = plat_db.query(WorkflowStateModel).filter(
                                        WorkflowStateModel.workflow_id == _workflow_id
                                    ).first()
                                    if incident_model:
                                        summary = platform_service.generate_summary(incident_model)
                                finally:
                                    plat_db.close()
                            except Exception as plat_err:
                                bg_logger.error(f"[SUMMARY] Platform context failed: {plat_err}")
                                summary = f"{_event_type.replace('_', ' ').title()} on {_resource_name}"

                            if summary:
                                update_db = SessionLocal()
                                try:
                                    update_db.execute(
                                        update(WorkflowStateModel).where(
                                            WorkflowStateModel.workflow_id == _workflow_id
                                        ).values(
                                            summary=summary,
                                            summary_generated_at=datetime.utcnow()
                                        )
                                    )
                                    update_db.commit()
                                    bg_logger.info(f"[SUMMARY] Saved for {_workflow_id}: {summary[:80]}")
                                except Exception as db_err:
                                    bg_logger.error(f"[SUMMARY] Save failed: {db_err}")
                                finally:
                                    update_db.close()
                        except Exception as e:
                            bg_logger.error(f"[SUMMARY] Generation failed: {e}", exc_info=True)

                    background_tasks.add_task(generate_watcher_summary_sync)

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to create/dedup incident workflow: {e}", exc_info=True)
        else:
            # Event doesn't qualify - dismiss it
            event_repo.dismiss_event(monitoring_event.event_id)
            # Leave condition as qualified=False (dismissed TTL applies — 15 min)

        return MonitoringEventResponse(
            event_id=str(monitoring_event.event_id),
            source=monitoring_event.source,
            event_type=monitoring_event.event_type,
            resource_name=monitoring_event.resource_name,
            raw_criticality=monitoring_event.raw_criticality,
            qualification_score=monitoring_event.qualification_score,
            qualified_as_incident=qual_result["qualified"],
            incident_workflow_id=incident_workflow_id,
            status=monitoring_event.status,
            detected_at=monitoring_event.detected_at.isoformat(),
            created_at=monitoring_event.created_at.isoformat(),
            updated_at=monitoring_event.updated_at.isoformat(),
            qualification_reason=qual_result["reason"],
            qualification_factors=qual_result.get("factors"),
            confidence=qual_result["confidence"],
            signal_value=monitoring_event.signal_value,
            signal_threshold=monitoring_event.signal_threshold,
            anomaly_process=monitoring_event.anomaly_process,
            payload_title=(event.raw_payload or {}).get("title"),
            payload_description=(event.raw_payload or {}).get("description"),
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to submit monitoring event: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/monitoring-events/{event_id}", response_model=MonitoringEventResponse)
async def get_monitoring_event(
    event_id: str,
    db: Session = Depends(get_session),
):
    """Get a specific monitoring event by ID"""
    try:
        repo = MonitoringEventRepository(db)
        event = repo.get(UUID(event_id))

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        return MonitoringEventResponse(
            event_id=str(event.event_id),
            source=event.source,
            event_type=event.event_type,
            resource_name=event.resource_name,
            raw_criticality=event.raw_criticality,
            qualification_score=event.qualification_score,
            qualified_as_incident=event.qualified_as_incident,
            incident_workflow_id=str(event.incident_workflow_id) if event.incident_workflow_id else None,
            status=event.status,
            detected_at=event.detected_at.isoformat(),
            created_at=event.created_at.isoformat(),
            updated_at=event.updated_at.isoformat(),
            qualification_reason=event.qualification_reason or "",
            qualification_factors=event.qualification_factors,
            confidence=event.confidence,
            signal_value=event.signal_value,
            signal_threshold=event.signal_threshold,
            anomaly_process=event.anomaly_process,
            payload_title=(event.raw_payload or {}).get("title"),
            payload_description=(event.raw_payload or {}).get("description"),
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monitoring-events", response_model=list[MonitoringEventResponse])
async def list_monitoring_events(
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    workflow_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    """
    List monitoring events with optional filtering.

    Args:
        status: Filter by status (new, qualified, dismissed, escalated)
        event_type: Filter by event type
        workflow_id: Filter by linked incident workflow ID
        limit: Max events to return
        offset: Pagination offset
    """
    try:
        repo = MonitoringEventRepository(db)
        # Push workflow_id filter to DB so LIMIT applies after filtering
        wf_uuid = UUID(workflow_id) if workflow_id else None
        events = repo.list_recent(limit=limit, status=status, incident_workflow_id=wf_uuid)

        # Filter by event_type if provided (in-memory; rare use-case)
        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return [
            MonitoringEventResponse(
                event_id=str(e.event_id),
                source=e.source,
                event_type=e.event_type,
                resource_name=e.resource_name,
                raw_criticality=e.raw_criticality,
                qualification_score=e.qualification_score,
                qualified_as_incident=e.qualified_as_incident,
                incident_workflow_id=str(e.incident_workflow_id) if e.incident_workflow_id else None,
                status=e.status,
                detected_at=e.detected_at.isoformat(),
                created_at=e.created_at.isoformat(),
                updated_at=e.updated_at.isoformat(),
                qualification_reason=e.qualification_reason or "",
                qualification_factors=e.qualification_factors,
                confidence=e.confidence,
                signal_value=e.signal_value,
                signal_threshold=e.signal_threshold,
                anomaly_process=e.anomaly_process,
                payload_title=(e.raw_payload or {}).get("title"),
                payload_description=(e.raw_payload or {}).get("description"),
                raw_payload=e.raw_payload or {},
            )
            for e in events
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitoring-events/{event_id}/escalate", response_model=MonitoringEventResponse)
async def escalate_monitoring_event(
    event_id: str,
    db: Session = Depends(get_session),
):
    """
    Manually escalate a dismissed event to incident.

    Args:
        event_id: Event ID to escalate
    """
    try:
        repo = MonitoringEventRepository(db)
        event = repo.get(UUID(event_id))

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        # If already has incident, just mark as escalated
        if event.incident_workflow_id:
            repo.escalate_event(event.event_id, event.incident_workflow_id)
        else:
            # Create new incident for escalated event
            incident_state = WorkflowState(
                workflow_type=WorkflowType.INCIDENT,
                lifecycle_state=LifecycleState.OPEN,
                context={
                    "alert_payload": {
                        "severity": "high",
                        "type": event.event_type,
                        "resource_name": event.resource_name,
                        "description": f"Manually escalated monitoring event: {event.event_type}",
                        "monitoring_event_id": str(event.event_id),
                        "qualification_score": event.qualification_score,
                        "escalated": True,
                    }
                }
            )

            workflow_repo = WorkflowRepository(db)
            workflow_repo.save(incident_state)

            # Link and mark as escalated
            repo.escalate_event(event.event_id, incident_state.workflow_id)

            # Queue incident (with optional storm-detection hold)
            _hold_secs_esc = _get_pipeline_hold_seconds()
            if _hold_secs_esc > 0:
                execute_workflow_task.apply_async(
                    kwargs={
                        "workflow_id":   str(incident_state.workflow_id),
                        "workflow_type": WorkflowType.INCIDENT.value,
                    },
                    countdown=_hold_secs_esc,
                )
            else:
                execute_workflow_task.delay(
                    workflow_id=str(incident_state.workflow_id),
                    workflow_type=WorkflowType.INCIDENT.value,
                )

        # Refresh to get updated event
        event = repo.get(UUID(event_id))

        return MonitoringEventResponse(
            event_id=str(event.event_id),
            source=event.source,
            event_type=event.event_type,
            resource_name=event.resource_name,
            raw_criticality=event.raw_criticality,
            qualification_score=event.qualification_score,
            qualified_as_incident=event.qualified_as_incident,
            incident_workflow_id=str(event.incident_workflow_id) if event.incident_workflow_id else None,
            status=event.status,
            detected_at=event.detected_at.isoformat(),
            created_at=event.created_at.isoformat(),
            updated_at=event.updated_at.isoformat(),
            qualification_reason="Manually escalated",
            confidence=None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitoring-events/{event_id}/dismiss", response_model=MonitoringEventResponse)
async def dismiss_monitoring_event(
    event_id: str,
    db: Session = Depends(get_session),
):
    """
    Manually dismiss a monitoring event (no incident will be created).

    Args:
        event_id: Event ID to dismiss
    """
    try:
        repo = MonitoringEventRepository(db)
        event = repo.dismiss_event(UUID(event_id))

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        return MonitoringEventResponse(
            event_id=str(event.event_id),
            source=event.source,
            event_type=event.event_type,
            resource_name=event.resource_name,
            raw_criticality=event.raw_criticality,
            qualification_score=event.qualification_score,
            qualified_as_incident=event.qualified_as_incident,
            incident_workflow_id=str(event.incident_workflow_id) if event.incident_workflow_id else None,
            status=event.status,
            detected_at=event.detected_at.isoformat(),
            created_at=event.created_at.isoformat(),
            updated_at=event.updated_at.isoformat(),
            qualification_reason="Manually dismissed",
            confidence=None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
