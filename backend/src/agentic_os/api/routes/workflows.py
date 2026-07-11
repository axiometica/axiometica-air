"""Workflow submission and status endpoints"""

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

from agentic_os.db.database import get_session
from agentic_os.db.repositories import WorkflowRepository
from agentic_os.core.models import WorkflowState, WorkflowType, LifecycleState
from agentic_os.core.definitions import WorkflowDefinitionLoader
from agentic_os.tasks.celery_app import execute_workflow_task
from agentic_os.tasks.snow_sync import snow_push_incident_created, snow_push_incident_state
from agentic_os.services.enumeration_service import EnumerationService
from datetime import datetime

router = APIRouter()


# Request/Response models
class IncidentSubmit(BaseModel):
    """Submit a new incident"""
    severity: str
    type: str
    resource_name: str
    title: Optional[str] = None  # Event title from watcher
    description: Optional[str] = None
    anomaly_process: Optional[str] = None  # Process name for high_syscall_intensity alerts
    service_url: Optional[str] = None      # Full URL of the affected service (e.g. http://api:8080)
    service_port: Optional[int] = None     # Port, if URL not provided


class ChangeSubmit(BaseModel):
    """Submit a new change request"""
    change_type: str  # standard, normal, emergency
    description: str
    affected_services: list[str]
    rollback_plan: str


class CloseIncidentRequest(BaseModel):
    """Manually close an incident after diagnostics-only or manual investigation"""
    summary: str                             # What was found / root cause
    steps_taken: str                         # What the operator did
    outcome: str                             # resolved | self_resolved | wont_fix | escalated | no_action | monitoring
    resolution_category: Optional[str] = None  # manual_fix | wont_fix | self_healed | escalated | no_action_required
    # resolved_by is populated from auth context in Phase B; for now accept as optional input
    resolved_by: Optional[str] = None


class AddNoteRequest(BaseModel):
    """Add an operator note to an incident's work log"""
    body: str
    note_type: str = "note"   # note | action | escalation | system
    author: str = "operator"


class RetryRemediationRequest(BaseModel):
    """Request a fresh automated remediation attempt for an awaiting_manual incident"""
    reason: Optional[str] = None  # Why retrying (logged in trace)


class NoteResponse(BaseModel):
    """Single work-log entry"""
    id: str
    workflow_id: str
    author: str
    note_type: str
    body: str
    created_at: str


class WorkflowResponse(BaseModel):
    """Workflow status response"""
    workflow_id: str
    workflow_type: str
    lifecycle_state: str
    incident_number: Optional[int] = None  # Numeric incident ID (1, 2, 3, etc.)
    incident_number_str: Optional[str] = None  # Formatted incident number (INC0001, INC0002, etc.)
    title: Optional[str] = None  # Incident title from alert
    severity: Optional[str] = None
    risk_score: Optional[float] = None
    # Business criticality (tier_1/tier_2/tier_3) — a real CMDB-sourced field, distinct
    # from severity. Severity already factors this in as one of several weighted inputs,
    # so it drives triage ordering; this is exposed separately because managers want to
    # filter/group by "what does this affect" independently of the computed score.
    business_criticality: Optional[str] = None
    # CMDB ci_tier is stored as a raw int (1/2/3) in practice, not a string — confirmed
    # in production data (a strict Optional[str] here 400'd the entire list endpoint).
    ci_tier: Optional[int] = None
    summary: Optional[str] = None
    technical_summary: Optional[str] = None
    # Same condition (resource+event_type+source) validated as recurring while
    # this incident is still open — see monitoring_events.py's dedup branch
    duplicate_count: int = 0
    # Remediation & resolution tracking (decoupled from lifecycle_state)
    remediation_outcome: Optional[str] = None   # succeeded/failed/aborted/skipped/pending/rejected
    resolution_source: Optional[str] = None     # automated_remediation/watcher_all_clear/manual
    all_clear_received_at: Optional[str] = None # ISO timestamp when watcher confirmed condition cleared
    # Resolution detail (v4.1.0 — incident lifecycle v2)
    resolution_category: Optional[str] = None   # manual_fix/wont_fix/self_healed/escalated/no_action_required
    resolution_notes: Optional[str] = None      # operator free-text explanation
    resolved_by: Optional[str] = None           # principal who closed it (Phase B auth)
    resolved_at: Optional[str] = None           # ISO timestamp of explicit close
    context: dict
    context_schema: Optional[dict] = None  # Phase 10: Typed context schema (sentinel, cmdb, risk, governance, etc.)
    reasoning_trace: list[str]
    state_history: list[dict] = []  # Ordered lifecycle transition history [{state, timestamp, reason}]
    created_at: str
    updated_at: str
    # Storm Agent linkage (v1.0.0)
    storm_id: Optional[str] = None  # Set on child incidents that belong to a storm parent


class WorkflowListResponse(BaseModel):
    """Paginated list of workflows with metadata"""
    workflows: list[WorkflowResponse]
    total_count: int
    limit: int
    offset: int
    has_more: bool


# Endpoints
@router.post("/workflows/incident", response_model=WorkflowResponse)
async def submit_incident(
    incident: IncidentSubmit,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """
    Submit a new incident for automated triage and remediation.

    - **severity**: critical, high, medium, low, info
    - **type**: high_cpu, disk_full, service_down, etc.
    - **resource_name**: affected resource (e.g., api-server)
    """
    try:
        # Create workflow state
        alert_payload = {
            "severity": incident.severity,
            "type": incident.type,
            "resource_name": incident.resource_name,
            "description": incident.description or "",
            "title": incident.title or f"{incident.type} on {incident.resource_name}",
        }
        if incident.anomaly_process:
            alert_payload["anomaly_process"] = incident.anomaly_process
        if incident.service_url:
            alert_payload["service_url"] = incident.service_url.strip()
        if incident.service_port:
            alert_payload["service_port"] = str(incident.service_port)

        # Generate initial summary from alert data
        initial_summary = f"{incident.type} on {incident.resource_name} (Severity: {incident.severity})"
        if incident.description:
            initial_summary += f" - {incident.description[:100]}"

        state = WorkflowState(
            workflow_type=WorkflowType.INCIDENT,
            lifecycle_state=LifecycleState.OPEN,
            title=alert_payload.get("title"),
            summary=initial_summary,
            context={
                "alert_payload": alert_payload
            },
        )

        # Save to database
        repo = WorkflowRepository(db)
        repo.save(state)

        # Read back the INC number assigned by DB trigger (trg_workflow_human_id_insert).
        # EnumerationService.generate_incident_number() now reads the trigger-assigned
        # value first and falls back to manual nextval() only on pre-migration deployments.
        incident_number = None
        incident_number_str = None
        if state.workflow_type == WorkflowType.INCIDENT:
            try:
                incident_number_str = EnumerationService.generate_incident_number(db, str(state.workflow_id))
                if incident_number_str and incident_number_str.startswith("INC"):
                    try:
                        incident_number = int(incident_number_str[3:])
                    except ValueError:
                        incident_number = None
            except Exception as e:
                print(f"Warning: Could not read incident number: {e}")
                incident_number_str = None
                incident_number = None

        # Queue async summary generation as background task
        def generate_summary_sync():
            """Synchronous wrapper for summary generation (runs in thread pool)"""
            import logging
            from sqlalchemy import update
            from agentic_os.db.models import WorkflowStateModel
            from agentic_os.db.database import SessionLocal

            logger_local = logging.getLogger(__name__)
            logger_local.info(f"[SUMMARY] Background task started for workflow {state.workflow_id}")

            try:
                summary = None

                # Always use the deterministic platform-context summary here — never an LLM
                # call. This is a placeholder shown only until the Celery ENRICH task lands a
                # real LLM-generated executive summary at workflow completion, which always
                # runs and always overwrites this value (see celery_app.py's enrich task).
                # An LLM call here would be pure wasted spend: its result is guaranteed to be
                # replaced before the incident's lifecycle ends.
                logger_local.info(f"[SUMMARY] Generating placeholder summary for {state.workflow_id}")
                try:
                    from agentic_os.services.platform_context_service import get_platform_context_service
                    platform_service = get_platform_context_service()
                    # Query the freshly-saved DB record (alert_payload is already stored)
                    query_db = SessionLocal()
                    try:
                        incident_model = query_db.query(WorkflowStateModel).filter(
                            WorkflowStateModel.workflow_id == str(state.workflow_id)
                        ).first()
                        if incident_model:
                            summary = platform_service.generate_summary(incident_model)
                            logger_local.info(
                                f"[SUMMARY] Platform context summary generated for "
                                f"{state.workflow_id}: {summary[:80]}..."
                            )
                        else:
                            raise ValueError("No DB record found")
                    finally:
                        query_db.close()
                except Exception as plat_err:
                    logger_local.error(
                        f"[SUMMARY] Platform context fallback failed: {plat_err}", exc_info=True
                    )
                    alert_payload = state.context.get("alert_payload", {})
                    event_type = alert_payload.get("type", "Unknown")
                    resource = alert_payload.get("resource_name", "Unknown")
                    summary = f"{event_type.replace('_', ' ').title()} on {resource}"

                # Update database with generated summary
                if summary:
                    logger_local.info(f"[SUMMARY] Saving summary to database for {state.workflow_id}")
                    update_db = SessionLocal()
                    try:
                        update_db.execute(
                            update(WorkflowStateModel).where(
                                WorkflowStateModel.workflow_id == state.workflow_id
                            ).values(
                                summary=summary,
                                summary_generated_at=datetime.utcnow()
                            )
                        )
                        update_db.commit()
                        logger_local.info(f"[SUMMARY] Summary saved successfully for {state.workflow_id}")
                    except Exception as db_err:
                        logger_local.error(f"[SUMMARY] Failed to save summary: {db_err}", exc_info=True)
                    finally:
                        update_db.close()
                else:
                    logger_local.warning(f"[SUMMARY] No summary generated for {state.workflow_id}")
            except Exception as e:
                logger_local.error(f"[SUMMARY] Background summary generation failed: {e}", exc_info=True)

        # Add to background tasks (runs in thread pool)
        background_tasks.add_task(generate_summary_sync)

        # Queue for execution
        print(f"DEBUG: Queueing workflow {state.workflow_id}")
        print(f"DEBUG: execute_workflow_task = {execute_workflow_task}")
        result = execute_workflow_task.delay(
            workflow_id=str(state.workflow_id),
            workflow_type=WorkflowType.INCIDENT.value,
        )
        print(f"DEBUG: Task queued with ID {result.id}")

        # Auto-push to ServiceNow if configured (fire-and-forget)
        snow_push_incident_created.delay(str(state.workflow_id))

        # Serialize context_schema if present (use to_dict() for proper serialization)
        context_schema_dict = None
        if state.context_schema and hasattr(state.context_schema, 'to_dict'):
            context_schema_dict = state.context_schema.to_dict()

        return WorkflowResponse(
            workflow_id=str(state.workflow_id),
            workflow_type=state.workflow_type.value,
            lifecycle_state=state.lifecycle_state.value,
            incident_number=incident_number,
            incident_number_str=incident_number_str,
            title=state.title,
            severity=state.severity.value if state.severity else None,
            risk_score=state.risk_score,
            summary=state.summary,
            technical_summary=getattr(state, 'technical_summary', None),
            duplicate_count=getattr(state, 'duplicate_count', 0) or 0,
            context=state.context,
            context_schema=context_schema_dict,
            reasoning_trace=state.reasoning_trace,
            state_history=state.state_history,
            created_at=state.created_at.isoformat(),
            updated_at=state.updated_at.isoformat(),
            storm_id=str(state.storm_id) if getattr(state, 'storm_id', None) else None,
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workflows/change", response_model=WorkflowResponse)
async def submit_change(
    change: ChangeSubmit,
    request: Request,
    db: Session = Depends(get_session),
):
    """
    Submit a new change request for CAB review and deployment.

    - **change_type**: standard (low risk), normal (medium risk), emergency (high risk)
    - **affected_services**: list of services impacted by change
    - **rollback_plan**: instructions for rollback if deployment fails
    """
    try:
        # Create workflow state
        state = WorkflowState(
            workflow_type=WorkflowType.CHANGE,
            lifecycle_state=LifecycleState.OPEN,
            context={
                "change_context": {
                    "change_type": change.change_type,
                    "description": change.description,
                    "affected_services": change.affected_services,
                    "rollback_plan": {"instructions": change.rollback_plan},
                }
            },
        )

        # Save to database
        repo = WorkflowRepository(db)
        repo.save(state)

        # Queue for execution
        execute_workflow_task.delay(
            workflow_id=str(state.workflow_id),
            workflow_type=WorkflowType.CHANGE.value,
        )

        # Serialize context_schema if present (use to_dict() for proper serialization)
        context_schema_dict = None
        if state.context_schema and hasattr(state.context_schema, 'to_dict'):
            context_schema_dict = state.context_schema.to_dict()

        return WorkflowResponse(
            workflow_id=str(state.workflow_id),
            workflow_type=state.workflow_type.value,
            lifecycle_state=state.lifecycle_state.value,
            title=state.title,
            severity=state.severity.value if state.severity else None,
            risk_score=state.risk_score,
            summary=state.summary,
            technical_summary=getattr(state, 'technical_summary', None),
            duplicate_count=getattr(state, 'duplicate_count', 0) or 0,
            remediation_outcome=getattr(state, 'remediation_outcome', None),
            resolution_source=getattr(state, 'resolution_source', None),
            all_clear_received_at=getattr(state, 'all_clear_received_at', None).isoformat() if getattr(state, 'all_clear_received_at', None) else None,
            context=state.context,
            context_schema=context_schema_dict,
            reasoning_trace=state.reasoning_trace,
            state_history=state.state_history,
            created_at=state.created_at.isoformat(),
            updated_at=state.updated_at.isoformat(),
            storm_id=str(state.storm_id) if getattr(state, 'storm_id', None) else None,
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workflows/{workflow_id}/close", response_model=WorkflowResponse)
async def close_incident(
    workflow_id: str,
    body: CloseIncidentRequest,
    db: Session = Depends(get_session),
):
    """
    Manually close an incident.

    Intended for diagnostics-only incidents where a human has investigated and taken
    action outside the automated workflow. Requires a close summary and steps taken.

    - **outcome**: resolved | self_resolved | monitoring | escalated | no_action
    """
    from agentic_os.db.models import WorkflowStateModel

    # Only truly-final states block a manual close.  awaiting_manual, monitoring, failed,
    # rejected, and every in-flight state are all closeable by an operator.
    TERMINAL = {"resolved", "closed"}

    # outcome → final lifecycle state
    OUTCOME_TO_LIFECYCLE = {
        "resolved":      LifecycleState.RESOLVED,
        "self_resolved": LifecycleState.RESOLVED,
        "wont_fix":      LifecycleState.CLOSED,
        "escalated":     LifecycleState.CLOSED,
        "no_action":     LifecycleState.CLOSED,
        "monitoring":    LifecycleState.MONITORING,
    }
    # outcome → remediation_outcome (describes what was done, not the lifecycle)
    OUTCOME_TO_REMEDIATION = {
        "resolved":      "manual_fix",
        "self_resolved": "self_healed",
        "wont_fix":      "wont_fix",
        "escalated":     "escalated",
        "no_action":     "no_action_required",
        "monitoring":    "monitoring_ongoing",
    }
    # outcome → default resolution_category (overridden by body.resolution_category if supplied)
    OUTCOME_TO_CATEGORY = {
        "resolved":      "manual_fix",
        "self_resolved": "self_healed",
        "wont_fix":      "wont_fix",
        "escalated":     "escalated",
        "no_action":     "no_action_required",
        "monitoring":    "monitoring_ongoing",
    }

    if body.outcome not in OUTCOME_TO_LIFECYCLE:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome '{body.outcome}'. Must be one of: "
                   f"{', '.join(sorted(OUTCOME_TO_LIFECYCLE))}",
        )

    if not body.summary.strip():
        raise HTTPException(status_code=400, detail="Close summary is required")
    if not body.steps_taken.strip():
        raise HTTPException(status_code=400, detail="Steps taken is required")

    try:
        model = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow_id
        ).first()
        if not model:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if model.lifecycle_state in TERMINAL:
            raise HTTPException(status_code=400, detail=f"Workflow is already in terminal state '{model.lifecycle_state}'")

        new_lifecycle = OUTCOME_TO_LIFECYCLE[body.outcome]
        close_ts = datetime.utcnow().isoformat()

        # Merge close notes into existing context
        ctx = dict(model.context or {})
        ctx["close_notes"] = {
            "summary":     body.summary,
            "steps_taken": body.steps_taken,
            "outcome":     body.outcome,
            "closed_at":   close_ts,
        }

        # Append to reasoning trace
        trace = list(model.reasoning_trace or [])
        trace.append(
            f"[MANUAL CLOSE — {close_ts}]\n"
            f"  Outcome: {body.outcome}\n"
            f"  Summary: {body.summary}\n"
            f"  Steps Taken: {body.steps_taken}"
        )

        # Append the manual close to state_history
        history = list(getattr(model, 'state_history', None) or [])
        history.append({
            'state': new_lifecycle.value,
            'timestamp': close_ts,
            'reason': f'Manual close — outcome: {body.outcome}',
        })

        close_dt = datetime.utcnow()
        resolved_cat = body.resolution_category or OUTCOME_TO_CATEGORY[body.outcome]

        # Capture state before mutation so the close note can describe what happened
        prev_lifecycle   = model.lifecycle_state
        prev_remediation = model.remediation_outcome

        model.lifecycle_state       = new_lifecycle.value
        model.remediation_outcome   = OUTCOME_TO_REMEDIATION[body.outcome]
        model.resolution_source     = "manual"
        model.resolution_category   = resolved_cat
        model.resolution_notes      = body.steps_taken   # operator's detailed notes
        model.resolved_by           = body.resolved_by   # None until Phase B auth
        model.resolved_at           = close_dt
        model.context               = ctx
        model.reasoning_trace       = trace
        model.state_history         = history
        model.updated_at            = close_dt
        db.commit()
        db.refresh(model)

        # ── Close event condition state ──────────────────────────────────────
        # When an incident is resolved/closed, the underlying condition is gone.
        # Clear the dedup state so the next alert on this resource fires fresh.
        if new_lifecycle in (LifecycleState.RESOLVED, LifecycleState.CLOSED):
            try:
                from agentic_os.db.repositories import EventConditionStateRepository
                _cond_repo = EventConditionStateRepository(db)
                _closed = _cond_repo.close_for_workflow(workflow_id, db)
                if _closed:
                    import logging as _log
                    _log.getLogger(__name__).info(
                        "[CLOSE] Cleared %d condition-state row(s) for workflow %s",
                        _closed, workflow_id,
                    )
            except Exception as _cond_err:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "[CLOSE] Failed to clear condition state for %s: %s", workflow_id, _cond_err
                )

        # ── Storm parent child cascade ───────────────────────────────────────
        # Always attempt to resolve any children held in storm_hold under this
        # incident.  We do NOT check context.is_storm_parent because the
        # pipeline (resume_workflow_task) may have overwritten the context,
        # stripping storm metadata.  The storm_id column is set once and never
        # touched by the pipeline, so it is the only reliable signal.
        # For non-storm incidents storm_id is NULL → WHERE matches 0 rows → no-op.
        _storm_children_resolved = 0
        if new_lifecycle in (LifecycleState.RESOLVED, LifecycleState.CLOSED):
            try:
                from sqlalchemy import text as sql_text
                _child_note = (
                    f"Resolved when storm parent "
                    f"{getattr(model, 'incident_number_str', None) or workflow_id} "
                    f"was manually closed."
                )
                _storm_children_resolved = db.execute(sql_text("""
                    UPDATE workflow_states
                    SET lifecycle_state   = 'resolved',
                        resolution_source = 'manual',
                        resolution_notes  = :note,
                        updated_at        = :now,
                        resolved_at       = COALESCE(resolved_at, :now)
                    WHERE storm_id::text = :parent_id
                      AND workflow_id::text != :parent_id
                      AND lifecycle_state NOT IN ('resolved', 'closed')
                """), {
                    "parent_id": str(workflow_id),
                    "note":      _child_note,
                    "now":       close_dt,
                }).rowcount
                if _storm_children_resolved:
                    db.commit()
                    import logging as _log
                    _log.getLogger(__name__).info(
                        "[CLOSE] Storm child cascade: resolved %d child(ren) for parent %s",
                        _storm_children_resolved, workflow_id,
                    )
            except Exception as _cascade_err:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "[CLOSE] Storm child cascade failed for parent %s: %s",
                    workflow_id, _cascade_err,
                )

        # ── System close note ────────────────────────────────────────────────
        # Written after the main commit so a note-write failure never rolls back
        # the close itself.
        try:
            import logging as _log
            from agentic_os.db.models import IncidentNoteModel

            _close_logger = _log.getLogger(__name__)

            OUTCOME_LABEL = {
                "resolved":      "Resolved",
                "self_resolved": "Self-Resolved",
                "wont_fix":      "Won't Fix",
                "escalated":     "Escalated",
                "no_action":     "No Action Required",
                "monitoring":    "Placed Under Monitoring",
            }
            outcome_label = OUTCOME_LABEL.get(body.outcome, body.outcome.replace("_", " ").title())
            closed_by     = (body.resolved_by or "operator").strip()

            lines = [f"✓ Incident closed — {outcome_label}", ""]
            lines += ["Summary", body.summary.strip(), ""]
            lines += ["Steps taken", body.steps_taken.strip()]

            # Show prior automation context when the workflow went through automation
            # before the manual close (prev_remediation is the pre-close DB value).
            if prev_remediation and prev_remediation not in OUTCOME_TO_REMEDIATION.values():
                auto_label_map = {
                    "succeeded": "Automated remediation completed successfully",
                    "failed":    "Automated remediation failed before manual close",
                    "aborted":   "Automated remediation was aborted",
                    "skipped":   "Automated remediation was skipped",
                    "pending":   "Automated remediation was pending",
                }
                auto_label = auto_label_map.get(prev_remediation, f"Automation: {prev_remediation}")
                lines += ["", f"Prior automation: {auto_label}"]

            lines += [
                "",
                f"Resolution: {resolved_cat.replace('_', ' ').title()}",
                f"Closed by: {closed_by}",
                f"Closed at: {close_ts}",
            ]

            # Mention child cascade if this was a storm parent
            if _storm_children_resolved > 0:
                lines += [
                    "",
                    f"Storm cascade: {_storm_children_resolved} child incident(s) also resolved.",
                ]

            close_note = IncidentNoteModel(
                workflow_id=UUID(workflow_id),
                author="system",
                note_type="system",
                body="\n".join(lines),
            )
            db.add(close_note)
            db.commit()
            _close_logger.info("System close note written for workflow %s", workflow_id)
        except Exception as _note_err:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Failed to write system close note for %s: %s", workflow_id, _note_err
            )

        # Auto-update ServiceNow incident if configured (fire-and-forget)
        snow_push_incident_state.delay(str(model.workflow_id), new_lifecycle.value)

        acr = getattr(model, 'all_clear_received_at', None)
        rat = getattr(model, 'resolved_at', None)
        return WorkflowResponse(
            workflow_id=str(model.workflow_id),
            workflow_type=model.workflow_type,
            lifecycle_state=model.lifecycle_state,
            incident_number=getattr(model, 'incident_number', None),
            incident_number_str=getattr(model, 'incident_number_str', None),
            title=model.title,
            severity=model.severity if isinstance(model.severity, str) else (model.severity.value if model.severity else None),
            risk_score=model.risk_score,
            summary=model.summary,
            technical_summary=getattr(model, 'technical_summary', None),
            duplicate_count=getattr(model, 'duplicate_count', 0) or 0,
            remediation_outcome=model.remediation_outcome,
            resolution_source=model.resolution_source,
            all_clear_received_at=acr.isoformat() if acr else None,
            resolution_category=getattr(model, 'resolution_category', None),
            resolution_notes=getattr(model, 'resolution_notes', None),
            resolved_by=getattr(model, 'resolved_by', None),
            resolved_at=rat.isoformat() if rat else None,
            context=model.context,
            reasoning_trace=model.reasoning_trace,
            state_history=getattr(model, 'state_history', None) or [],
            created_at=model.created_at.isoformat(),
            updated_at=model.updated_at.isoformat(),
            storm_id=str(model.storm_id) if getattr(model, 'storm_id', None) else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_session),
):
    """Get workflow status by ID"""
    try:
        repo = WorkflowRepository(db)
        state = repo.get(UUID(workflow_id))

        if not state:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Serialize context_schema if present (use to_dict() for proper serialization)
        context_schema_dict = None
        if state.context_schema and hasattr(state.context_schema, 'to_dict'):
            context_schema_dict = state.context_schema.to_dict()

        # Read incident number and storm number from DB (assigned by triggers).
        incident_number_str = EnumerationService.get_incident_number_str(db, workflow_id) or None
        storm_number_str    = EnumerationService.get_storm_number_str(db, workflow_id)    or None
        incident_number = None
        if incident_number_str and incident_number_str.startswith('INC'):
            try:
                incident_number = int(incident_number_str[3:])
            except ValueError:
                pass

        return WorkflowResponse(
            workflow_id=str(state.workflow_id),
            workflow_type=state.workflow_type.value,
            lifecycle_state=state.lifecycle_state.value,
            incident_number=incident_number,
            incident_number_str=incident_number_str,
            title=state.title,
            severity=state.severity.value if state.severity else None,
            risk_score=state.risk_score,
            summary=state.summary,
            technical_summary=getattr(state, 'technical_summary', None),
            duplicate_count=getattr(state, 'duplicate_count', 0) or 0,
            remediation_outcome=getattr(state, 'remediation_outcome', None),
            resolution_source=getattr(state, 'resolution_source', None),
            all_clear_received_at=getattr(state, 'all_clear_received_at', None).isoformat() if getattr(state, 'all_clear_received_at', None) else None,
            context=state.context,
            context_schema=context_schema_dict,
            reasoning_trace=state.reasoning_trace,
            state_history=state.state_history,
            created_at=state.created_at.isoformat(),
            updated_at=state.updated_at.isoformat(),
            storm_id=str(getattr(state, 'storm_id', None)) if getattr(state, 'storm_id', None) else None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    workflow_type: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    severity: Optional[str] = None,
    service: Optional[str] = None,
    business_criticality: Optional[str] = None,
    q: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    """
    List workflows with pagination, filtering, and sorting.

    Query Parameters:
    - workflow_type: Filter by type ("incident" or "change")
    - lifecycle_state: Filter by state (e.g., "open", "in_progress", "resolved").
                       Use "active" to return all non-terminal states in one query.
    - severity: Filter by severity ("critical", "high", "medium", "low")
    - service: Filter by service/CI name (partial match)
    - business_criticality: Filter by CMDB business criticality tier (e.g. "tier_1") —
                       distinct from severity; lets managers slice by what's affected
                       regardless of how each incident's overall score shook out.
    - q: Free-text search — partial, case-insensitive match across title, summary,
                       and incident number (e.g. "INC0017" or "nginx")
    - sort_by: Sort field (created_at, severity, risk_score, title, updated_at)
    - sort_order: Sort direction (asc, desc)
    - limit: Page size (default 10, max 100)
    - offset: Pagination offset (default 0)

    Returns: Paginated list with total_count and has_more flag
    """
    try:
        from agentic_os.db.models import WorkflowStateModel

        # Limit page size
        limit = min(limit, 100)
        offset = max(offset, 0)

        # Build query
        query = db.query(WorkflowStateModel)

        # Apply filters
        if workflow_type:
            query = query.filter(WorkflowStateModel.workflow_type == workflow_type)

        if lifecycle_state:
            # "active" is a convenience alias that returns everything that isn't
            # a terminal state (resolved / failed / rejected / closed).
            _ACTIVE_STATES = [
                'open', 'in_progress',
                'waiting_approval', 'approved', 'executing',
                'awaiting_manual', 'storm_hold',
            ]
            if lifecycle_state == 'active':
                query = query.filter(WorkflowStateModel.lifecycle_state.in_(_ACTIVE_STATES))
            else:
                query = query.filter(WorkflowStateModel.lifecycle_state == lifecycle_state)

        if severity:
            query = query.filter(WorkflowStateModel.severity == severity)

        if service:
            # Partial match on service field (case-insensitive)
            query = query.filter(WorkflowStateModel.title.ilike(f"%{service}%"))

        if business_criticality:
            # CMDB-sourced field, set early during enrichment — present even if risk
            # assessment later bails out due to incomplete CMDB context, unlike the
            # copy that lands in risk_breakdown.ci_info.
            #
            # Two issues confirmed live before landing on this form:
            #  1. .astext doesn't exist after chaining two [] indexes off a plain JSON
            #     column ("Neither 'BinaryExpression' object nor 'Comparator' object
            #     has an attribute 'astext'").
            #  2. WorkflowStateModel.context.op("#>>")([...]) binds the Python list as
            #     a JSON array literal ('["a", "b"]'), not a Postgres text[] literal
            #     ('{a,b}') - psycopg2.errors.InvalidTextRepresentation.
            # Chaining single-key -> / ->> operators avoids needing any array literal.
            query = query.filter(
                WorkflowStateModel.context.op("->")("cmdb_context").op("->>")("business_criticality")
                == business_criticality
            )

        if q:
            from sqlalchemy import or_
            _term = f"%{q}%"
            query = query.filter(
                or_(
                    WorkflowStateModel.title.ilike(_term),
                    WorkflowStateModel.summary.ilike(_term),
                    WorkflowStateModel.incident_number_str.ilike(_term),
                )
            )

        # Get total count before pagination
        total_count = query.count()

        # Apply sorting
        sort_field_map = {
            "created_at": WorkflowStateModel.created_at,
            "updated_at": WorkflowStateModel.updated_at,
            "severity": WorkflowStateModel.severity,
            "risk_score": WorkflowStateModel.risk_score,
            "title": WorkflowStateModel.title,
        }

        sort_field = sort_field_map.get(sort_by, WorkflowStateModel.created_at)
        if sort_order.lower() == "asc":
            query = query.order_by(sort_field.asc())
        else:
            query = query.order_by(sort_field.desc())

        # Apply pagination
        workflows = query.offset(offset).limit(limit).all()

        # Convert to response
        workflow_list = []
        for w in workflows:
            # Get incident numbers (both numeric and formatted string)
            incident_number = w.incident_number if hasattr(w, 'incident_number') else None
            incident_number_str = w.incident_number_str if hasattr(w, 'incident_number_str') else None

            # Serialize context_schema if present
            context_schema_dict = None
            if hasattr(w, 'context_schema') and w.context_schema and hasattr(w.context_schema, 'to_dict'):
                context_schema_dict = w.context_schema.to_dict()

            acr = getattr(w, 'all_clear_received_at', None)
            cmdb_ctx = (w.context or {}).get("cmdb_context") or {}
            workflow_list.append(
                WorkflowResponse(
                    workflow_id=str(w.workflow_id),
                    workflow_type=w.workflow_type,
                    lifecycle_state=w.lifecycle_state,
                    incident_number=incident_number,
                    incident_number_str=incident_number_str,
                    title=w.title,
                    severity=w.severity if isinstance(w.severity, str) else (w.severity.value if w.severity else None),
                    risk_score=w.risk_score,
                    business_criticality=cmdb_ctx.get("business_criticality"),
                    ci_tier=cmdb_ctx.get("ci_tier"),
                    summary=w.summary,
                    technical_summary=getattr(w, 'technical_summary', None),
                    duplicate_count=getattr(w, 'duplicate_count', 0) or 0,
                    remediation_outcome=getattr(w, 'remediation_outcome', None),
                    resolution_source=getattr(w, 'resolution_source', None),
                    all_clear_received_at=acr.isoformat() if acr else None,
                    context=w.context,
                    context_schema=context_schema_dict,
                    reasoning_trace=w.reasoning_trace,
                    state_history=getattr(w, 'state_history', None) or [],
                    created_at=w.created_at.isoformat(),
                    updated_at=w.updated_at.isoformat(),
                    storm_id=str(w.storm_id) if getattr(w, 'storm_id', None) else None,
                )
            )

        return WorkflowListResponse(
            workflows=workflow_list,
            total_count=total_count,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total_count,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter value: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Incident work-log: notes ────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/notes", response_model=NoteResponse, status_code=201)
async def add_incident_note(
    workflow_id: str,
    body: AddNoteRequest,
    db: Session = Depends(get_session),
):
    """
    Append an operator note to an incident's work log.

    Accepted note_type values: note | action | escalation | system

    Notes are immutable once created (append-only audit trail).
    """
    from agentic_os.db.models import WorkflowStateModel, IncidentNoteModel

    VALID_TYPES = {"note", "action", "escalation", "system"}
    if body.note_type not in VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid note_type '{body.note_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}",
        )
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Note body cannot be empty")

    try:
        # Verify workflow exists
        model = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow_id
        ).first()
        if not model:
            raise HTTPException(status_code=404, detail="Workflow not found")

        note = IncidentNoteModel(
            workflow_id=UUID(workflow_id),
            author=body.author or "operator",
            note_type=body.note_type,
            body=body.body.strip(),
        )
        db.add(note)
        db.commit()
        db.refresh(note)

        return NoteResponse(
            id=str(note.id),
            workflow_id=str(note.workflow_id),
            author=note.author,
            note_type=note.note_type,
            body=note.body,
            created_at=note.created_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows/{workflow_id}/notes", response_model=list[NoteResponse])
async def list_incident_notes(
    workflow_id: str,
    db: Session = Depends(get_session),
):
    """
    Retrieve all work-log entries for an incident, oldest first.
    """
    from agentic_os.db.models import WorkflowStateModel, IncidentNoteModel

    try:
        # Verify workflow exists
        model = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow_id
        ).first()
        if not model:
            raise HTTPException(status_code=404, detail="Workflow not found")

        notes = (
            db.query(IncidentNoteModel)
            .filter(IncidentNoteModel.workflow_id == UUID(workflow_id))
            .order_by(IncidentNoteModel.created_at.asc())
            .all()
        )

        return [
            NoteResponse(
                id=str(n.id),
                workflow_id=str(n.workflow_id),
                author=n.author,
                note_type=n.note_type,
                body=n.body,
                created_at=n.created_at.isoformat(),
            )
            for n in notes
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Incident retry: re-queue automated remediation ──────────────────────────

@router.post("/workflows/{workflow_id}/retry", response_model=WorkflowResponse)
async def retry_incident_remediation(
    workflow_id: str,
    body: RetryRemediationRequest,
    db: Session = Depends(get_session),
):
    """
    Re-queue automated remediation for an incident that is currently AWAITING_MANUAL.

    This resets the incident back to OPEN and queues a fresh workflow execution.
    The previous reasoning trace is preserved; a new trace entry is appended.

    Only valid for incidents in the ``awaiting_manual`` lifecycle state.
    """
    from agentic_os.db.models import WorkflowStateModel, IncidentNoteModel

    try:
        model = db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow_id
        ).first()
        if not model:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if model.lifecycle_state != LifecycleState.AWAITING_MANUAL.value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Retry is only valid for incidents in 'awaiting_manual' state. "
                    f"Current state: '{model.lifecycle_state}'"
                ),
            )

        retry_ts = datetime.utcnow().isoformat()
        reason_str = body.reason or "Operator requested re-attempt"

        # Append trace entry
        trace = list(model.reasoning_trace or [])
        trace.append(
            f"[RETRY — {retry_ts}]\n"
            f"  Requested by: operator\n"
            f"  Reason: {reason_str}"
        )

        # State history
        history = list(getattr(model, 'state_history', None) or [])
        history.append({
            "state": LifecycleState.OPEN.value,
            "timestamp": retry_ts,
            "reason": f"Retry requested: {reason_str}",
        })

        # Reset state for a clean run
        model.lifecycle_state     = LifecycleState.OPEN.value
        model.remediation_outcome = None
        model.reasoning_trace     = trace
        model.state_history       = history
        model.updated_at          = datetime.utcnow()
        db.commit()
        db.refresh(model)

        # Log a system note in the work log
        system_note = IncidentNoteModel(
            workflow_id=UUID(workflow_id),
            author="system",
            note_type="system",
            body=f"Automated remediation re-queued by operator. Reason: {reason_str}",
        )
        db.add(system_note)
        db.commit()

        # Re-queue workflow execution
        execute_workflow_task.delay(
            workflow_id=workflow_id,
            workflow_type="incident",
        )

        acr = getattr(model, 'all_clear_received_at', None)
        rat = getattr(model, 'resolved_at', None)
        return WorkflowResponse(
            workflow_id=str(model.workflow_id),
            workflow_type=model.workflow_type,
            lifecycle_state=model.lifecycle_state,
            incident_number=getattr(model, 'incident_number', None),
            incident_number_str=getattr(model, 'incident_number_str', None),
            title=model.title,
            severity=model.severity if isinstance(model.severity, str) else (model.severity.value if model.severity else None),
            risk_score=model.risk_score,
            summary=model.summary,
            technical_summary=getattr(model, 'technical_summary', None),
            duplicate_count=getattr(model, 'duplicate_count', 0) or 0,
            remediation_outcome=model.remediation_outcome,
            resolution_source=model.resolution_source,
            all_clear_received_at=acr.isoformat() if acr else None,
            resolution_category=getattr(model, 'resolution_category', None),
            resolution_notes=getattr(model, 'resolution_notes', None),
            resolved_by=getattr(model, 'resolved_by', None),
            resolved_at=rat.isoformat() if rat else None,
            context=model.context,
            reasoning_trace=model.reasoning_trace,
            state_history=getattr(model, 'state_history', None) or [],
            created_at=model.created_at.isoformat(),
            updated_at=model.updated_at.isoformat(),
            storm_id=str(model.storm_id) if getattr(model, 'storm_id', None) else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
