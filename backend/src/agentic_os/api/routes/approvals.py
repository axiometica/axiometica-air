"""Approval request and decision endpoints"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from uuid import UUID
from typing import Optional
import asyncio
import logging

from agentic_os.db.database import get_session
from agentic_os.db.repositories import ApprovalRepository, WorkflowRepository
from agentic_os.db.models import WorkflowStateModel
from agentic_os.core.models import EventEnvelope, EventType, WorkflowType
from agentic_os.bus.postgres_bus import PostgresEventBus

logger = logging.getLogger(__name__)

router = APIRouter()


class ApprovalDecision(BaseModel):
    """Approval decision submission"""
    decision: str  # approved, rejected, diagnostics_only
    notes: str = ""
    decided_by: str = "system"


class ApprovalResponse(BaseModel):
    """Approval request response"""
    approval_id: str
    workflow_id: str
    approval_type: str
    status: str
    requested_at: str
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    decision_notes: Optional[str] = None
    proposed_action: Optional[dict] = None
    incident_summary: Optional[dict] = None
    governance_policy_id: Optional[str] = None
    # Risk ranking — read from the linked incident, not stored on the approval row
    # itself, so the queue can be triaged worst-first instead of oldest-first.
    risk_score: Optional[float] = None
    severity: Optional[str] = None


@router.get("/approvals/pending", response_model=list[ApprovalResponse])
async def get_pending_approvals(
    approval_type: str = None,
    limit: int = 10,
    db: Session = Depends(get_session),
):
    """
    Get pending approval requests (CAB reviews, manual approvals).

    Ordered by the linked incident's risk_score descending (worst-impact first),
    then requested_at ascending so equally-severe incidents are still worked
    oldest-first rather than starving. Previously ordered by requested_at alone,
    which meant a trivial incident filed seconds ago could sit above a
    business-critical one that had been waiting much longer — pure arrival-order
    triage, with no notion of what actually matters most.
    """
    try:
        repo = ApprovalRepository(db)

        query = (
            db.query(repo.model, WorkflowStateModel.risk_score, WorkflowStateModel.severity)
            .outerjoin(WorkflowStateModel, repo.model.workflow_id == WorkflowStateModel.workflow_id)
            .filter(repo.model.status == "pending")
        )

        if approval_type:
            query = query.filter(repo.model.approval_type == approval_type)

        rows = (
            query
            .order_by(WorkflowStateModel.risk_score.desc().nullslast(), repo.model.requested_at.asc())
            .limit(limit)
            .all()
        )

        return [
            ApprovalResponse(
                approval_id=str(a.approval_id),
                workflow_id=str(a.workflow_id),
                approval_type=a.approval_type,
                status=a.status,
                requested_at=a.requested_at.isoformat(),
                decided_at=a.decided_at.isoformat() if a.decided_at else None,
                decided_by=a.decided_by,
                decision_notes=a.decision_notes,
                proposed_action=a.proposed_action,
                incident_summary=a.incident_summary,
                governance_policy_id=str(a.governance_policy_id) if a.governance_policy_id else None,
                risk_score=risk_score,
                severity=severity.value if severity else None,
            )
            for a, risk_score, severity in rows
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/approvals/history", response_model=list[ApprovalResponse])
async def get_approval_history(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    """Get decided approvals (approved, rejected, cancelled, diagnostics_only).

    Ordered by decided_at DESC so the most recently closed approvals appear first.
    Excludes pending approvals — use /approvals/pending for those.
    """
    try:
        repo = ApprovalRepository(db)
        query = db.query(repo.model).filter(repo.model.status != "pending")

        if status:
            query = query.filter(repo.model.status == status)

        approvals = (
            query
            .order_by(repo.model.decided_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        return [
            ApprovalResponse(
                approval_id=str(a.approval_id),
                workflow_id=str(a.workflow_id),
                approval_type=a.approval_type,
                status=a.status,
                requested_at=a.requested_at.isoformat(),
                decided_at=a.decided_at.isoformat() if a.decided_at else None,
                decided_by=a.decided_by,
                decision_notes=a.decision_notes,
                proposed_action=a.proposed_action,
                incident_summary=a.incident_summary,
                governance_policy_id=str(a.governance_policy_id) if a.governance_policy_id else None,
            )
            for a in approvals
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: str,
    db: Session = Depends(get_session),
):
    """Get approval request by ID"""
    try:
        repo = ApprovalRepository(db)
        approval = db.query(repo.model).filter(repo.model.approval_id == UUID(approval_id)).first()

        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")

        return ApprovalResponse(
            approval_id=str(approval.approval_id),
            workflow_id=str(approval.workflow_id),
            approval_type=approval.approval_type,
            status=approval.status,
            requested_at=approval.requested_at.isoformat(),
            decided_at=approval.decided_at.isoformat() if approval.decided_at else None,
            decided_by=approval.decided_by,
            decision_notes=approval.decision_notes,
            proposed_action=approval.proposed_action,
            incident_summary=approval.incident_summary,
            governance_policy_id=str(approval.governance_policy_id) if approval.governance_policy_id else None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid approval ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/by-workflow/{workflow_id}/decide", response_model=ApprovalResponse)
async def decide_by_workflow(
    workflow_id: str,
    decision: ApprovalDecision,
    db: Session = Depends(get_session),
):
    """Submit an approval decision by workflow_id — looks up the pending approval automatically.

    Also handles stuck workflows: if the workflow is still in waiting_approval but its approval
    was already decided (e.g. rejected via a different endpoint that forgot to update the lifecycle),
    this resets the approval to pending so the operator can re-decide.
    """
    try:
        repo = ApprovalRepository(db)
        workflow_uuid = UUID(workflow_id)

        approval = (
            db.query(repo.model)
            .filter(
                repo.model.workflow_id == workflow_uuid,
                repo.model.status == "pending",
            )
            .order_by(repo.model.requested_at.desc())
            .first()
        )

        if not approval:
            # ── Stuck-workflow recovery ───────────────────────────────────────
            # The approval may have been decided by another endpoint that didn't
            # update the workflow lifecycle. If the workflow is still in
            # waiting_approval, reset the latest approval to pending so the
            # operator can re-decide without having to create a new incident.
            latest_approval = (
                db.query(repo.model)
                .filter(repo.model.workflow_id == workflow_uuid)
                .order_by(repo.model.requested_at.desc())
                .first()
            )
            workflow_repo = WorkflowRepository(db)
            wf = workflow_repo.get(str(workflow_uuid))

            if latest_approval and wf and wf.lifecycle_state in ("waiting_approval",):
                logger.info(
                    f"[APPROVALS] Recovering stuck workflow {workflow_id}: "
                    f"resetting approval {latest_approval.approval_id} "
                    f"from '{latest_approval.status}' → 'pending' for re-decision"
                )
                latest_approval.status = "pending"
                latest_approval.decided_at = None
                latest_approval.decided_by = None
                latest_approval.decision_notes = None
                db.flush()
                approval = latest_approval
            else:
                raise HTTPException(status_code=404, detail="No pending approval found for this workflow")

        approval_uuid = approval.approval_id

        repo.decide(
            approval_id=approval_uuid,
            decision=decision.decision,
            decided_by=decision.decided_by,
            decision_notes=decision.notes,
        )

        updated = db.query(repo.model).filter(repo.model.approval_id == approval_uuid).first()

        event_type = EventType.APPROVAL_REJECTED if decision.decision == "rejected" else EventType.APPROVAL_GRANTED
        event = EventEnvelope(
            workflow_id=approval.workflow_id,
            workflow_type=WorkflowType.INCIDENT,
            event_type=event_type,
            source_agent="approval_api",
            payload={
                "approval_id": str(approval_uuid),
                "decision": decision.decision,
                "decided_by": decision.decided_by,
                "notes": decision.notes,
            },
        )

        try:
            event_bus = PostgresEventBus("postgresql://postgres:agentic_os@postgres:5432/agentic_os")
            await event_bus.publish(event)   # Fixed: was asyncio.run() which fails inside async def
            logger.info(f"Published {event_type} for workflow {workflow_id} (decision={decision.decision})")
        except Exception as publish_err:
            logger.warning(f"Failed to publish approval event: {publish_err}")

        if decision.decision in ("approved", "diagnostics_only"):
            try:
                from agentic_os.tasks.celery_app import resume_workflow_task
                # Pre-flight check: if the workflow already resolved (e.g. all-clear
                # arrived while the operator was looking at the approval), do not queue
                # remediation — executing on a self-healed system can cause harm.
                _wf_check = WorkflowRepository(db).get(str(approval.workflow_id))
                if _wf_check and _wf_check.lifecycle_state.value in ("resolved", "closed"):
                    logger.info(
                        f"[APPROVALS] Workflow {approval.workflow_id} already "
                        f"'{_wf_check.lifecycle_state.value}' — approval noted but "
                        f"remediation not queued (condition cleared before operator acted)"
                    )
                else:
                    # Synchronous state flip so the UI has something accurate to
                    # show the instant approval is granted — without this, the
                    # incident stays showing "Waiting Approval" until the Celery
                    # task is actually dequeued and the engine's own first step
                    # (transition_state(IN_PROGRESS)) runs, which can take anywhere
                    # from milliseconds to minutes under load. No race with that
                    # later write: this happens first, in the same request, before
                    # the task is even enqueued.
                    WorkflowRepository(db).update_lifecycle_state(str(approval.workflow_id), "approved")
                    db.commit()
                    resume_workflow_task.delay(
                        workflow_id=str(approval.workflow_id),
                        approval_id=str(approval_uuid),
                    )
            except Exception as resume_err:
                logger.warning(f"Failed to queue resumption task: {resume_err}")
        else:
            # Rejection: Update workflow lifecycle state to "rejected" (non-terminal)
            # This allows the operator to manually close with a specific outcome
            # The workflow becomes terminal only when explicitly closed via the close endpoint
            try:
                workflow_repo = WorkflowRepository(db)
                workflow_repo.update_lifecycle_state(str(approval.workflow_id), "rejected")
                db.commit()
                logger.info(f"Workflow {workflow_id} lifecycle set to rejected (non-terminal)")
            except Exception as state_err:
                logger.warning(f"Failed to update workflow lifecycle on rejection: {state_err}")

            # Auto system note on rejection
            try:
                from agentic_os.db.models import IncidentNoteModel
                from datetime import datetime as _dt

                decided_by  = decision.decided_by or "system"
                reason      = decision.notes.strip() if decision.notes else ""
                lines = [
                    f"✕ Remediation rejected by {decided_by}.",
                ]
                if reason:
                    lines.append(f"Reason: {reason}")
                else:
                    lines.append("No rejection reason provided.")
                lines.append(
                    "\nNo automated actions were taken. "
                    "Use 'Close' to record the final outcome."
                )

                note = IncidentNoteModel(
                    workflow_id=approval.workflow_id,
                    author="system",
                    note_type="system",
                    body="\n".join(lines),
                )
                db.add(note)
                db.commit()
                logger.info(f"System note written for rejection of workflow {workflow_id}")
            except Exception as _note_err:
                logger.warning(f"Failed to write rejection system note: {_note_err}")

        return ApprovalResponse(
            approval_id=str(updated.approval_id),
            workflow_id=str(updated.workflow_id),
            approval_type=updated.approval_type,
            status=updated.status,
            requested_at=updated.requested_at.isoformat(),
            decided_at=updated.decided_at.isoformat() if updated.decided_at else None,
            decided_by=updated.decided_by,
            decision_notes=updated.decision_notes,
            proposed_action=updated.proposed_action,
            incident_summary=updated.incident_summary,
            governance_policy_id=str(updated.governance_policy_id) if updated.governance_policy_id else None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workflow ID format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_request(
    approval_id: str,
    decision: ApprovalDecision,
    db: Session = Depends(get_session),
):
    """Approve or reject an approval request"""
    try:
        repo = ApprovalRepository(db)
        approval_uuid = UUID(approval_id)

        # Get approval
        approval = db.query(repo.model).filter(repo.model.approval_id == approval_uuid).first()
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")

        # Update approval
        repo.decide(
            approval_id=approval_uuid,
            decision=decision.decision,
            decided_by=decision.decided_by,
            decision_notes=decision.notes,
        )

        # Get updated approval
        updated = db.query(repo.model).filter(repo.model.approval_id == approval_uuid).first()

        # Publish approval decision event
        from agentic_os.bus.postgres_bus import PostgresEventBus

        # Map decision to event type
        if decision.decision == "approved":
            event_type = EventType.APPROVAL_GRANTED
        elif decision.decision == "diagnostics_only":
            # Partial approval - diagnostics only
            event_type = EventType.APPROVAL_GRANTED
        else:  # rejected
            event_type = EventType.APPROVAL_REJECTED

        event = EventEnvelope(
            workflow_id=approval.workflow_id,
            workflow_type=WorkflowType.INCIDENT,  # Fixed: was hardcoded CHANGE, breaks incident approvals
            event_type=event_type,
            source_agent="approval_api",
            payload={
                "approval_id": str(approval_uuid),
                "decision": decision.decision,
                "decided_by": decision.decided_by,
                "notes": decision.notes,
            },
        )

        # Publish approval decision event
        try:
            event_bus = PostgresEventBus("postgresql://postgres:agentic_os@postgres:5432/agentic_os")
            await event_bus.publish(event)   # Fixed: was asyncio.run() which fails inside async def
            logger.info(f"Published {event_type} event for workflow {approval.workflow_id}")
        except Exception as publish_err:
            logger.warning(f"Failed to publish approval event: {publish_err}")

        # Trigger workflow resumption for full approval OR diagnostics-only partial approval
        if decision.decision in ("approved", "diagnostics_only"):
            try:
                from agentic_os.tasks.celery_app import resume_workflow_task
                # Pre-flight check: if the workflow already resolved (e.g. all-clear
                # arrived while the operator was looking at the approval), do not queue
                # remediation — executing on a self-healed system can cause harm.
                _wf_check = WorkflowRepository(db).get(str(approval.workflow_id))
                if _wf_check and _wf_check.lifecycle_state.value in ("resolved", "closed"):
                    logger.info(
                        f"[APPROVALS] Workflow {approval.workflow_id} already "
                        f"'{_wf_check.lifecycle_state.value}' — approval noted but "
                        f"remediation not queued (condition cleared before operator acted)"
                    )
                else:
                    logger.info(
                        f"Queueing workflow resumption for {approval.workflow_id} "
                        f"(mode: {decision.decision})"
                    )
                    # Synchronous state flip so the UI has something accurate to
                    # show the instant approval is granted — see decide_by_workflow
                    # above for the full rationale. No race with the engine's own
                    # later transition_state(IN_PROGRESS): this write happens
                    # first, in the same request, before the task is even enqueued.
                    WorkflowRepository(db).update_lifecycle_state(str(approval.workflow_id), "approved")
                    db.commit()
                    resume_workflow_task.delay(
                        workflow_id=str(approval.workflow_id),
                        approval_id=str(approval_uuid)
                    )
            except Exception as resume_err:
                logger.warning(f"Failed to queue resumption task: {resume_err}")
        else:
            # Rejection: update workflow lifecycle so it doesn't get stuck in waiting_approval
            try:
                workflow_repo = WorkflowRepository(db)
                workflow_repo.update_lifecycle_state(str(approval.workflow_id), "rejected")
                db.commit()
                logger.info(f"Workflow {approval.workflow_id} lifecycle set to 'rejected'")
            except Exception as state_err:
                logger.warning(f"Failed to update workflow lifecycle on rejection: {state_err}")


        return ApprovalResponse(
            approval_id=str(updated.approval_id),
            workflow_id=str(updated.workflow_id),
            approval_type=updated.approval_type,
            status=updated.status,
            requested_at=updated.requested_at.isoformat(),
            decided_at=updated.decided_at.isoformat() if updated.decided_at else None,
            decided_by=updated.decided_by,
            decision_notes=updated.decision_notes,
            proposed_action=updated.proposed_action,
            incident_summary=updated.incident_summary,
            governance_policy_id=str(updated.governance_policy_id) if updated.governance_policy_id else None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid approval ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_request(
    approval_id: str,
    decision: ApprovalDecision,
    db: Session = Depends(get_session),
):
    """Reject an approval request"""
    try:
        repo = ApprovalRepository(db)
        approval_uuid = UUID(approval_id)

        # Get approval
        approval = db.query(repo.model).filter(repo.model.approval_id == approval_uuid).first()
        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")

        # Update approval with rejection
        repo.decide(
            approval_id=approval_uuid,
            decision="rejected",
            decided_by=decision.decided_by,
            decision_notes=decision.notes,
        )

        # Get updated approval
        updated = db.query(repo.model).filter(repo.model.approval_id == approval_uuid).first()

        # Publish rejection event
        event = EventEnvelope(
            workflow_id=approval.workflow_id,
            workflow_type=WorkflowType.INCIDENT,
            event_type=EventType.APPROVAL_REJECTED,
            source_agent="approval_api",
            payload={
                "approval_id": str(approval_uuid),
                "decision": "rejected",
                "decided_by": decision.decided_by,
                "notes": decision.notes,
            },
        )

        try:
            event_bus = PostgresEventBus("postgresql://postgres:agentic_os@postgres:5432/agentic_os")
            await event_bus.publish(event)   # Fixed: event was created but never published
            logger.info(f"Published APPROVAL_REJECTED for workflow {approval.workflow_id}")
        except Exception as publish_err:
            logger.warning(f"Failed to publish rejection event: {publish_err}")

        # Update workflow lifecycle state to "rejected" (non-terminal)
        # This allows the operator to manually close with a specific outcome
        # The workflow becomes terminal only when explicitly closed via the close endpoint
        try:
            workflow_repo = WorkflowRepository(db)
            workflow_repo.update_lifecycle_state(str(approval.workflow_id), "rejected")
            db.commit()
            logger.info(f"Workflow {approval.workflow_id} lifecycle set to rejected (non-terminal)")
        except Exception as state_err:
            logger.warning(f"Failed to update workflow lifecycle on rejection: {state_err}")

        return ApprovalResponse(
            approval_id=str(updated.approval_id),
            workflow_id=str(updated.workflow_id),
            approval_type=updated.approval_type,
            status=updated.status,
            requested_at=updated.requested_at.isoformat(),
            decided_at=updated.decided_at.isoformat() if updated.decided_at else None,
            decided_by=updated.decided_by,
            decision_notes=updated.decision_notes,
            proposed_action=updated.proposed_action,
            incident_summary=updated.incident_summary,
            governance_policy_id=str(updated.governance_policy_id) if updated.governance_policy_id else None,
        )

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid approval ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
