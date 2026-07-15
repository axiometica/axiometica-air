"""
Data access layer (Repository pattern).
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from sqlalchemy.orm.attributes import flag_modified
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from agentic_os.core.models import EventEnvelope, WorkflowState, WorkflowType, EventType, LifecycleState
from agentic_os.db.models import (
    WorkflowStateModel, EventModel, ApprovalModel, AgentExecutionModel,
    RunbookModel, ApprovedActionModel, MonitoringEventModel, RiskWeightConfigModel,
    PolicyModel, GovernancePolicyModel, OptimizationRecommendationModel,
    EventConditionStateModel, RunbookStepOutcomeModel, PlatformIntelRunModel,
)


class WorkflowRepository:
    """Repository for workflow state operations"""
    model = WorkflowStateModel  # ORM model class

    def __init__(self, db: Session):
        self.db = db

    def save(self, workflow: WorkflowState) -> WorkflowState:
        """Save or update workflow state"""
        existing = self.db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow.workflow_id
        ).first()

        if existing:
            # Update existing
            existing.lifecycle_state = workflow.lifecycle_state
            existing.context = workflow.context
            # Phase 10: Typed context schema (use to_dict() for proper serialization)
            existing.context_schema = workflow.context_schema.to_dict() if workflow.context_schema and hasattr(workflow.context_schema, 'to_dict') else None
            existing.severity = workflow.severity
            existing.risk_score = workflow.risk_score
            existing.risk_level = workflow.risk_level
            existing.title = workflow.title
            existing.summary = workflow.summary
            existing.technical_summary = workflow.technical_summary
            existing.summary_generated_at = workflow.summary_generated_at
            existing.governance_decision = workflow.governance_decision
            existing.governance_reason = workflow.governance_reason
            existing.approval_request_id = workflow.approval_request_id
            existing.remediation_outcome = workflow.remediation_outcome
            existing.resolution_source = workflow.resolution_source
            existing.all_clear_received_at = workflow.all_clear_received_at
            existing.reasoning_trace = workflow.reasoning_trace
            existing.execution_log = workflow.execution_log
            existing.state_history = workflow.state_history
            existing.updated_at = datetime.utcnow()
            # Stamp resolved_at once, on first transition to a terminal state.
            # Never overwrite — preserves the actual resolution timestamp even if
            # subsequent saves (e.g. summary generation) touch the row later.
            _RESOLVED_STATES = {'resolved', 'deployed', 'rolled_back', 'closed'}
            if str(workflow.lifecycle_state) in _RESOLVED_STATES and existing.resolved_at is None:
                existing.resolved_at = datetime.utcnow()
        else:
            # Create new
            # Phase 10: Serialize context_schema if present (use to_dict() for proper serialization)
            context_schema_data = None
            if workflow.context_schema and hasattr(workflow.context_schema, 'to_dict'):
                context_schema_data = workflow.context_schema.to_dict()

            model = WorkflowStateModel(
                workflow_id=workflow.workflow_id,
                workflow_type=workflow.workflow_type,
                lifecycle_state=workflow.lifecycle_state,
                context=workflow.context,
                context_schema=context_schema_data,
                severity=workflow.severity,
                risk_score=workflow.risk_score,
                risk_level=workflow.risk_level,
                title=workflow.title,
                summary=workflow.summary,
                technical_summary=workflow.technical_summary,
                summary_generated_at=workflow.summary_generated_at,
                governance_decision=workflow.governance_decision,
                governance_reason=workflow.governance_reason,
                approval_request_id=workflow.approval_request_id,
                remediation_outcome=workflow.remediation_outcome,
                resolution_source=workflow.resolution_source,
                all_clear_received_at=workflow.all_clear_received_at,
                reasoning_trace=workflow.reasoning_trace,
                execution_log=workflow.execution_log,
                state_history=workflow.state_history,
                correlation_id=workflow.correlation_id,
                causation_id=workflow.causation_id,
            )
            self.db.add(model)

        self.db.commit()
        return workflow

    def get(self, workflow_id: UUID) -> Optional[WorkflowState]:
        """Get workflow by ID"""
        model = self.db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == workflow_id
        ).first()

        if not model:
            return None

        return self._model_to_state(model)

    def list_by_type(self, workflow_type: WorkflowType, limit: int = 100) -> List[WorkflowState]:
        """List workflows by type"""
        models = self.db.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_type == workflow_type
        ).order_by(
            desc(WorkflowStateModel.created_at)
        ).limit(limit).all()

        return [self._model_to_state(m) for m in models]

    def list_by_lifecycle(self, lifecycle_state: LifecycleState, limit: int = 100) -> List[WorkflowState]:
        """List workflows by lifecycle state"""
        models = self.db.query(WorkflowStateModel).filter(
            WorkflowStateModel.lifecycle_state == lifecycle_state
        ).order_by(
            desc(WorkflowStateModel.created_at)
        ).limit(limit).all()

        return [self._model_to_state(m) for m in models]

    def get_awaiting_approval(self, limit: int = 100) -> List[WorkflowState]:
        """Get workflows awaiting approval"""
        return self.list_by_lifecycle(LifecycleState.WAITING_APPROVAL, limit)

    def update_lifecycle_state(self, workflow_id: str, lifecycle_state: str) -> bool:
        """Directly update a workflow's lifecycle state (e.g. on rejection)."""
        from datetime import datetime
        updated = (
            self.db.query(WorkflowStateModel)
            .filter(WorkflowStateModel.workflow_id == workflow_id)
            .update({"lifecycle_state": lifecycle_state, "updated_at": datetime.utcnow()})
        )
        return updated > 0

    def increment_duplicate_count(self, workflow_id: str) -> Optional[int]:
        """Atomically bump duplicate_count by 1. Returns the new count, or None
        if the workflow_id doesn't exist."""
        from sqlalchemy import text as sql_text
        row = self.db.execute(
            sql_text("""
                UPDATE workflow_states
                SET duplicate_count = duplicate_count + 1, updated_at = now()
                WHERE workflow_id = :workflow_id
                RETURNING duplicate_count
            """),
            {"workflow_id": str(workflow_id)},
        ).fetchone()
        self.db.commit()
        return row[0] if row else None

    @staticmethod
    def _model_to_state(model: WorkflowStateModel) -> WorkflowState:
        """Convert database model to WorkflowState"""
        # Phase 10: Reconstruct typed context_schema if present
        context_schema = None
        if model.context_schema:
            try:
                from agentic_os.core.context_schema import IncidentWorkflowContext
                context_schema = IncidentWorkflowContext.from_dict(model.context_schema)
            except Exception:
                # If reconstruction fails, leave as None (will be reconstructed from untyped context on access)
                context_schema = None

        return WorkflowState(
            workflow_id=model.workflow_id,
            workflow_type=model.workflow_type,
            lifecycle_state=model.lifecycle_state,
            context=model.context,
            context_schema=context_schema,
            title=model.title,
            severity=model.severity,
            risk_score=model.risk_score,
            risk_level=model.risk_level,
            summary=model.summary,
            technical_summary=getattr(model, 'technical_summary', None),
            summary_generated_at=model.summary_generated_at,
            governance_decision=model.governance_decision,
            governance_reason=model.governance_reason,
            approval_request_id=model.approval_request_id,
            remediation_outcome=getattr(model, 'remediation_outcome', None),
            resolution_source=getattr(model, 'resolution_source', None),
            all_clear_received_at=getattr(model, 'all_clear_received_at', None),
            reasoning_trace=model.reasoning_trace,
            execution_log=model.execution_log,
            state_history=getattr(model, 'state_history', []) or [],
            correlation_id=model.correlation_id,
            causation_id=model.causation_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class EventRepository:
    """Repository for event sourcing operations"""

    def __init__(self, db: Session):
        self.db = db

    def append(self, event: EventEnvelope) -> EventEnvelope:
        """Append event to immutable log"""
        model = EventModel(
            event_id=event.event_id,
            workflow_id=event.workflow_id,
            workflow_type=event.workflow_type,
            event_type=event.event_type,
            source_agent=event.source_agent,
            payload=event.payload,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            created_at=event.timestamp,
        )
        self.db.add(model)
        self.db.commit()
        return event

    def get_by_workflow_id(self, workflow_id: UUID) -> List[EventEnvelope]:
        """Get all events for a workflow"""
        models = self.db.query(EventModel).filter(
            EventModel.workflow_id == workflow_id
        ).order_by(EventModel.created_at).all()

        return [self._model_to_envelope(m) for m in models]

    def get_by_correlation_id(self, correlation_id: UUID) -> List[EventEnvelope]:
        """Get all events in a correlation (distributed trace)"""
        models = self.db.query(EventModel).filter(
            EventModel.correlation_id == correlation_id
        ).order_by(EventModel.created_at).all()

        return [self._model_to_envelope(m) for m in models]

    def get_recent(self, limit: int = 100) -> List[EventEnvelope]:
        """Get recent events"""
        models = self.db.query(EventModel).order_by(
            desc(EventModel.created_at)
        ).limit(limit).all()

        return [self._model_to_envelope(m) for m in models]

    @staticmethod
    def _model_to_envelope(model: EventModel) -> EventEnvelope:
        """Convert database model to EventEnvelope"""
        return EventEnvelope(
            event_id=model.event_id,
            workflow_id=model.workflow_id,
            workflow_type=model.workflow_type,
            event_type=model.event_type,
            source_agent=model.source_agent,
            timestamp=model.created_at,
            correlation_id=model.correlation_id,
            causation_id=model.causation_id,
            payload=model.payload,
        )


class ApprovalRepository:
    """Repository for approval requests"""
    model = ApprovalModel  # ORM model class

    def __init__(self, db: Session):
        self.db = db

    def create_request(self, workflow_id: UUID, approval_type: str, notes: str = "") -> str:
        """Create approval request"""
        approval = ApprovalModel(
            workflow_id=workflow_id,
            approval_type=approval_type,
            status="pending",
            decision_notes=notes,
        )
        self.db.add(approval)
        self.db.commit()
        return str(approval.approval_id)

    def get_pending_for_type(self, approval_type: str, limit: int = 100) -> List[dict]:
        """Get pending approvals of a type"""
        models = self.db.query(ApprovalModel).filter(
            and_(
                ApprovalModel.approval_type == approval_type,
                ApprovalModel.status == "pending"
            )
        ).order_by(ApprovalModel.requested_at).limit(limit).all()

        return [self._model_to_dict(m) for m in models]

    def approve(self, approval_id: str, decided_by: str, notes: str = ""):
        """Approve a request"""
        approval = self.db.query(ApprovalModel).filter(
            ApprovalModel.approval_id == UUID(approval_id)
        ).first()

        if approval:
            approval.status = "approved"
            approval.decided_at = datetime.utcnow()
            approval.decided_by = decided_by
            approval.decision_notes = notes
            self.db.commit()

    def reject(self, approval_id: str, decided_by: str, notes: str = ""):
        """Reject a request"""
        approval = self.db.query(ApprovalModel).filter(
            ApprovalModel.approval_id == UUID(approval_id)
        ).first()

        if approval:
            approval.status = "rejected"
            approval.decided_at = datetime.utcnow()
            approval.decided_by = decided_by
            approval.decision_notes = notes
            self.db.commit()

    def decide(self, approval_id: UUID, decision: str, decided_by: str = "system", decision_notes: str = ""):
        """Record approval decision (approved or rejected)"""
        approval = self.db.query(ApprovalModel).filter(
            ApprovalModel.approval_id == approval_id
        ).first()

        if approval:
            approval.status = decision
            approval.decided_at = datetime.utcnow()
            approval.decided_by = decided_by
            approval.decision_notes = decision_notes
            self.db.commit()
            return approval
        return None

    @staticmethod
    def _model_to_dict(model: ApprovalModel) -> dict:
        """Convert model to dict"""
        return {
            "approval_id": str(model.approval_id),
            "workflow_id": str(model.workflow_id),
            "approval_type": model.approval_type,
            "status": model.status,
            "requested_at": model.requested_at.isoformat(),
            "decided_at": model.decided_at.isoformat() if model.decided_at else None,
            "decided_by": model.decided_by,
            "decision_notes": model.decision_notes,
        }


class RunbookRepository:
    """CRUD operations for runbooks"""

    # Editable via the UI / draft workflow. Excludes execution-feedback stats
    # (written only by the runbook_feedback service) and `enabled`, which stays
    # an instant kill-switch outside draft/publish.
    DRAFT_FIELDS = {
        "name", "description", "event_type", "service", "environment", "platform",
        "diagnostics", "actions", "verification_steps", "confidence", "blast_radius",
        "source", "source_steps", "generation_prompt",
    }
    JSON_FIELDS = {"source_steps", "diagnostics", "actions", "verification_steps"}

    def __init__(self, db: Session):
        self.db = db

    def list(self, event_type: Optional[str] = None, enabled_only: bool = True, platform: Optional[str] = None, limit: int = 100) -> List[RunbookModel]:
        q = self.db.query(RunbookModel)
        if enabled_only:
            q = q.filter(RunbookModel.enabled == True)
        if platform:
            q = q.filter(RunbookModel.platform == platform)
        rows = q.order_by(desc(RunbookModel.created_at)).limit(limit).all()
        if event_type:
            # Normalise the incoming event_type (resolves aliases like high_cpu →
            # infrastructure.compute.cpu_high) then apply wildcard-aware matching
            # so runbooks stored as "infrastructure.*" catch any infra event.
            try:
                from agentic_os.connectors.event_type_utils import (
                    normalize_event_type, event_type_matches
                )
                normalized = normalize_event_type(event_type)
                rows = [r for r in rows if event_type_matches(r.event_type, normalized)]
            except ImportError:
                # Fallback: plain exact match
                rows = [r for r in rows if r.event_type == event_type]
        return rows

    def get(self, runbook_id: UUID) -> Optional[RunbookModel]:
        return self.db.query(RunbookModel).filter(RunbookModel.id == runbook_id).first()

    def create(self, data: dict) -> RunbookModel:
        runbook = RunbookModel(**data)
        self.db.add(runbook)
        self.db.commit()
        self.db.refresh(runbook)
        # Seed draft_snapshot to mirror the as-created content so an immediate
        # publish() (which no-ops when draft_snapshot is empty) actually works —
        # a brand-new runbook would otherwise be unpublishable until its first edit.
        runbook.draft_snapshot = {field: getattr(runbook, field) for field in self.DRAFT_FIELDS}
        flag_modified(runbook, "draft_snapshot")
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def update(self, runbook_id: UUID, data: dict) -> Optional[RunbookModel]:
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        # JSON columns (source_steps, diagnostics, actions, verification_steps)
        # require explicit flag_modified when reassigned, because SQLAlchemy's
        # change-tracking can miss new-object assignments on JSON columns in
        # some driver/ORM version combinations.
        JSON_COLUMNS = {"source_steps", "diagnostics", "actions", "verification_steps"}
        for key, value in data.items():
            if hasattr(runbook, key):
                setattr(runbook, key, value)
                if key in JSON_COLUMNS:
                    flag_modified(runbook, key)
        runbook.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def save_draft(self, runbook_id: UUID, data: dict) -> Optional[RunbookModel]:
        """PUT semantics: merge `data` into draft_snapshot only — live columns
        (what _lookup_runbook actually reads) are untouched until publish()."""
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        base = dict(runbook.draft_snapshot) if runbook.draft_snapshot else {
            field: getattr(runbook, field) for field in self.DRAFT_FIELDS
        }
        for key, value in data.items():
            if key in self.DRAFT_FIELDS:
                base[key] = value
        runbook.draft_snapshot = base
        flag_modified(runbook, "draft_snapshot")
        runbook.has_unpublished_changes = True
        runbook.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def publish(self, runbook_id: UUID, created_by: Optional[str] = None, change_note: Optional[str] = None) -> Optional[RunbookModel]:
        """Copy draft_snapshot onto the live columns and record a version snapshot.

        No-op only when already published with nothing pending — a never-yet-
        published runbook (status='draft') must still publish on its first call
        even though has_unpublished_changes is False, since create() seeds
        draft_snapshot from the as-created content rather than leaving a real
        edit pending. draft_snapshot stays populated forever after that (mirrors
        live state), so its truthiness alone can't be the no-op signal."""
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        if runbook.status == "published" and not runbook.has_unpublished_changes:
            return runbook
        if not runbook.draft_snapshot:
            return runbook
        for key, value in runbook.draft_snapshot.items():
            if key in self.DRAFT_FIELDS:
                setattr(runbook, key, value)
                if key in self.JSON_FIELDS:
                    flag_modified(runbook, key)
        runbook.status = "published"
        runbook.published_at = datetime.utcnow()
        runbook.has_unpublished_changes = False
        runbook.updated_at = datetime.utcnow()
        self.db.flush()  # live columns visible before snapshotting the version row

        from agentic_os.db.models import RunbookVersionModel
        last = (
            self.db.query(RunbookVersionModel)
            .filter_by(runbook_id=runbook_id)
            .order_by(RunbookVersionModel.version.desc())
            .first()
        )
        next_version = (last.version + 1) if last else 1
        snapshot = {field: getattr(runbook, field) for field in self.DRAFT_FIELDS}
        self.db.add(RunbookVersionModel(
            runbook_id=runbook_id, version=next_version, snapshot=snapshot,
            created_by=created_by, change_note=change_note, created_at=datetime.utcnow(),
        ))
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def discard_draft(self, runbook_id: UUID) -> Optional[RunbookModel]:
        """Reset draft_snapshot to mirror current live state — discards pending edits."""
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        runbook.draft_snapshot = {field: getattr(runbook, field) for field in self.DRAFT_FIELDS}
        flag_modified(runbook, "draft_snapshot")
        runbook.has_unpublished_changes = False
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def set_enabled(self, runbook_id: UUID, enabled: bool) -> Optional[RunbookModel]:
        """Instant kill-switch — bypasses draft/publish entirely."""
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        runbook.enabled = enabled
        runbook.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def list_versions(self, runbook_id: UUID) -> list:
        from agentic_os.db.models import RunbookVersionModel
        return (
            self.db.query(RunbookVersionModel)
            .filter_by(runbook_id=runbook_id)
            .order_by(RunbookVersionModel.version.desc())
            .all()
        )

    def restore_version_to_draft(self, runbook_id: UUID, version: int) -> Optional[RunbookModel]:
        """Load a historical version into draft_snapshot for review — does NOT
        publish it directly (a frozen snapshot may reference since-changed
        taxonomy/actions, so it goes through the same review-then-publish path
        as any other draft edit)."""
        from agentic_os.db.models import RunbookVersionModel
        row = self.db.query(RunbookVersionModel).filter_by(runbook_id=runbook_id, version=version).first()
        if not row:
            return None
        runbook = self.get(runbook_id)
        if not runbook:
            return None
        runbook.draft_snapshot = dict(row.snapshot)
        flag_modified(runbook, "draft_snapshot")
        runbook.has_unpublished_changes = True
        self.db.commit()
        self.db.refresh(runbook)
        return runbook

    def delete(self, runbook_id: UUID) -> bool:
        runbook = self.get(runbook_id)
        if not runbook:
            return False
        self.db.delete(runbook)
        self.db.commit()
        return True

    @staticmethod
    def to_dict(r: RunbookModel) -> dict:
        # Transform verification_steps from database format to frontend format
        verification_steps = r.verification_steps or []
        transformed_verification = []
        for step in verification_steps:
            transformed_verification.append({
                "description": step.get("description", ""),
                "metric": step.get("metric", ""),
                "check": step.get("threshold_type", "less_than"),  # Database uses threshold_type, UI uses check
                "value": step.get("threshold", ""),  # Database uses threshold, UI uses value
            })

        return {
            "runbook_id": str(r.id),
            "name": r.name,
            "description": r.description or "",
            "event_type": r.event_type,
            "service": r.service,
            "environment": r.environment,
            "platform": getattr(r, "platform", "any") or "any",
            "diagnostics": r.diagnostics or [],
            "actions": r.actions or [],
            "verification_steps": transformed_verification,
            "confidence": float(r.confidence) if r.confidence else 0.85,
            "blast_radius": r.blast_radius or 1,
            "enabled": r.enabled,
            "source": getattr(r, 'source', 'operator_authored') or 'operator_authored',
            # Execution feedback stats
            "total_executions":      getattr(r, "total_executions", 0) or 0,
            "successful_executions": getattr(r, "successful_executions", 0) or 0,
            "failed_executions":     getattr(r, "failed_executions", 0) or 0,
            "success_rate":          getattr(r, "success_rate", None),
            "confidence_trend":      getattr(r, "confidence_trend", None),
            "last_executed_at":      r.last_executed_at.isoformat() if getattr(r, "last_executed_at", None) else None,
            "source_steps": getattr(r, "source_steps", None),
            "generation_prompt": getattr(r, "generation_prompt", None),
            "is_seeded": getattr(r, "is_seeded", False) or False,
            "status": getattr(r, "status", "published") or "published",
            "published_at": r.published_at.isoformat() if getattr(r, "published_at", None) else None,
            "has_unpublished_changes": bool(getattr(r, "has_unpublished_changes", False)),
            "draft_snapshot": getattr(r, "draft_snapshot", None),
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }


class RunbookStepOutcomeRepository:
    """Repository for per-step runbook execution outcomes (Platform Intelligence Enhancement 1)."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: dict) -> RunbookStepOutcomeModel:
        row = RunbookStepOutcomeModel(**data)
        self.db.add(row)
        return row

    def bulk_create(self, rows: list[dict]) -> int:
        """Create many step outcomes in one transaction. Caller commits."""
        for data in rows:
            self.db.add(RunbookStepOutcomeModel(**data))
        return len(rows)

    @staticmethod
    def to_dict(r: RunbookStepOutcomeModel) -> dict:
        return {
            "id":               str(r.id),
            "workflow_id":      str(r.workflow_id),
            "runbook_id":       str(r.runbook_id) if r.runbook_id else None,
            "step_index":       r.step_index,
            "step_name":        r.step_name,
            "step_type":        r.step_type,
            "tool":             r.tool,
            "status":           r.status,
            "duration_seconds": r.duration_seconds,
            "error_message":    r.error_message,
            "failure_category": getattr(r, "failure_category", None),
            "created_at":       r.created_at.isoformat(),
        }


class RemediationOutcomeRepository:
    """
    Repository for remediation_outcomes — defined alongside RemediationOutcomeModel
    but had no repository class until Enhancement 2. Note: nothing in the live
    incident pipeline currently writes to this table (verified — no call site
    constructs RemediationOutcomeModel outside of this file and its own class
    definition), so to_dict() exists for completeness/forward-compatibility but
    won't return real data until something starts writing rows here.
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: dict):
        from agentic_os.db.models import RemediationOutcomeModel
        row = RemediationOutcomeModel(**data)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    @staticmethod
    def to_dict(r) -> dict:
        return {
            "id":                     str(r.id),
            "workflow_id":            str(r.workflow_id),
            "runbook_source":         r.runbook_source,
            "remediation_successful": r.remediation_successful,
            "incident_resolved":      r.incident_resolved,
            "failure_category":       getattr(r, "failure_category", None),
            "feedback_provided":      r.feedback_provided,
            "feedback_score":         r.feedback_score,
            "created_at":             r.created_at.isoformat(),
        }


class ApprovedActionRepository:
    """CRUD for the approved_actions catalog."""

    def __init__(self, db: Session):
        self.db = db

    def list(self, category: str = None, enabled_only: bool = False) -> List[ApprovedActionModel]:
        q = self.db.query(ApprovedActionModel)
        if category:
            q = q.filter(ApprovedActionModel.category == category)
        if enabled_only:
            q = q.filter(ApprovedActionModel.enabled == True)
        return q.order_by(ApprovedActionModel.category, ApprovedActionModel.name).all()

    def get(self, action_id: UUID) -> Optional[ApprovedActionModel]:
        return self.db.query(ApprovedActionModel).filter(
            ApprovedActionModel.id == action_id
        ).first()

    def get_by_tool_name(self, tool_name: str) -> Optional[ApprovedActionModel]:
        return self.db.query(ApprovedActionModel).filter(
            ApprovedActionModel.tool_name == tool_name,
            ApprovedActionModel.enabled == True,
        ).first()

    def create(self, data: dict) -> ApprovedActionModel:
        action = ApprovedActionModel(**data)
        self.db.add(action)
        self.db.commit()
        self.db.refresh(action)
        return action

    def update(self, action_id: UUID, data: dict) -> Optional[ApprovedActionModel]:
        action = self.get(action_id)
        if not action:
            return None
        for key, value in data.items():
            if hasattr(action, key):
                setattr(action, key, value)
        from datetime import datetime
        action.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(action)
        return action

    def delete(self, action_id: UUID) -> bool:
        action = self.get(action_id)
        if not action:
            return False
        self.db.delete(action)
        self.db.commit()
        return True

    def seed_defaults(self) -> int:
        """
        Upsert the approved-actions catalog from approved_actions_seed.APPROVED_ACTIONS.

        Changed from "skip if any exist" to a full upsert so that:
          • New deployments → INSERT all actions.
          • Existing deployments → UPDATE command_variants and any other changed
            fields so Week-1 fixes (command_variants, bug fixes) propagate to the
            running database without requiring a manual migration.

        Execution statistics and created_at are never overwritten.
        Returns the number of rows inserted or updated.
        """
        from datetime import datetime
        from agentic_os.db.approved_actions_seed import APPROVED_ACTIONS

        now = datetime.utcnow()
        inserted = 0
        updated  = 0

        for item in APPROVED_ACTIONS:
            tool_name = item.get("tool_name")
            existing  = self.db.query(ApprovedActionModel).filter_by(
                tool_name=tool_name
            ).first()

            if existing is None:
                self.db.add(ApprovedActionModel(**item, is_builtin=True))
                inserted += 1
            else:
                changed = False

                def _set(attr, val):
                    nonlocal changed
                    if getattr(existing, attr, None) != val:
                        setattr(existing, attr, val)
                        changed = True

                _set("name",             item.get("name"))
                _set("description",      item.get("description"))
                _set("command",          item.get("command"))
                _set("command_variants", item.get("command_variants"))
                _set("category",         item.get("category"))
                _set("blast_radius",     item.get("blast_radius"))
                _set("requires_approval",item.get("requires_approval"))
                _set("parameters",       item.get("parameters"))
                _set("output_fields",    item.get("output_fields", []))
                _set("is_builtin",       True)
                # process_rules is intentionally not overwritten —
                # operators may have customised allow/deny rules in the UI.

                if changed:
                    existing.updated_at = now
                    updated += 1

        self.db.commit()
        return inserted + updated

    @staticmethod
    def to_dict(a: ApprovedActionModel) -> dict:
        return {
            "action_id":         str(a.id),
            "tool_name":         a.tool_name,
            "name":              a.name,
            "description":       a.description or "",
            "command":           a.command or "",
            "command_variants":  a.command_variants or {},
            "category":          a.category,
            "blast_radius":      a.blast_radius,
            "requires_approval": a.requires_approval,
            "enabled":           a.enabled,
            "parameters":        a.parameters or [],
            "process_rules":     a.process_rules,  # None if not a process action
            "output_fields":     a.output_fields or [],
            "is_builtin":        bool(a.is_builtin),
            "created_at":        a.created_at.isoformat(),
            "updated_at":        a.updated_at.isoformat(),
        }


class MonitoringEventRepository:
    """Repository for monitoring events (raw watcher signals)"""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        source: str,
        event_type: str,
        resource_name: str,
        raw_criticality: str,
        qualification_score: float,
        raw_payload: dict,
        signal_value: Optional[float] = None,
        signal_threshold: Optional[float] = None,
        anomaly_process: Optional[str] = None,
        detected_at: Optional[datetime] = None,
        qualification_reason: Optional[str] = None,
        confidence: Optional[float] = None,
        qualification_factors: Optional[dict] = None,
    ) -> MonitoringEventModel:
        """Create a new monitoring event"""
        event = MonitoringEventModel(
            source=source,
            event_type=event_type,
            resource_name=resource_name,
            raw_criticality=raw_criticality,
            qualification_score=qualification_score,
            qualification_reason=qualification_reason,
            qualification_factors=qualification_factors,
            confidence=confidence,
            raw_payload=raw_payload,
            signal_value=signal_value,
            signal_threshold=signal_threshold,
            anomaly_process=anomaly_process,
            detected_at=detected_at or datetime.utcnow(),
            status="new",
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get(self, event_id: UUID) -> Optional[MonitoringEventModel]:
        """Get event by ID"""
        return self.db.query(MonitoringEventModel).filter(
            MonitoringEventModel.event_id == event_id
        ).first()

    def list_recent(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        incident_workflow_id: Optional[UUID] = None,
    ) -> list:
        """List recent monitoring events, optionally filtered by status and/or workflow"""
        query = self.db.query(MonitoringEventModel)
        if status:
            query = query.filter(MonitoringEventModel.status == status)
        if incident_workflow_id is not None:
            query = query.filter(MonitoringEventModel.incident_workflow_id == incident_workflow_id)
        return query.order_by(desc(MonitoringEventModel.created_at)).limit(limit).all()

    def list_by_resource(self, resource_name: str, limit: int = 50) -> list:
        """List events for a specific resource"""
        return self.db.query(MonitoringEventModel).filter(
            MonitoringEventModel.resource_name == resource_name
        ).order_by(desc(MonitoringEventModel.created_at)).limit(limit).all()

    def qualify_event(self, event_id: UUID, incident_workflow_id: UUID) -> MonitoringEventModel:
        """Mark event as qualified and link to incident workflow"""
        event = self.get(event_id)
        if event:
            event.status = "qualified"
            event.qualified_as_incident = True
            event.incident_workflow_id = incident_workflow_id
            event.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(event)
        return event

    def dismiss_event(self, event_id: UUID) -> MonitoringEventModel:
        """Mark event as dismissed (below threshold)"""
        event = self.get(event_id)
        if event:
            event.status = "dismissed"
            event.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(event)
        return event

    def escalate_event(self, event_id: UUID, incident_workflow_id: UUID) -> MonitoringEventModel:
        """Manually escalate event to incident"""
        event = self.get(event_id)
        if event:
            event.status = "escalated"
            event.qualified_as_incident = True
            event.incident_workflow_id = incident_workflow_id
            event.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(event)
        return event

    @staticmethod
    def to_dict(e: MonitoringEventModel) -> dict:
        return {
            "event_id": str(e.event_id),
            "source": e.source,
            "event_type": e.event_type,
            "resource_name": e.resource_name,
            "raw_criticality": e.raw_criticality,
            "signal_value": e.signal_value,
            "signal_threshold": e.signal_threshold,
            "anomaly_process": e.anomaly_process,
            "qualification_score": e.qualification_score,
            "qualification_factors": e.qualification_factors,
            "qualified_as_incident": e.qualified_as_incident,
            "incident_workflow_id": str(e.incident_workflow_id) if e.incident_workflow_id else None,
            "status": e.status,
            "detected_at": e.detected_at.isoformat(),
            "created_at": e.created_at.isoformat(),
            "updated_at": e.updated_at.isoformat(),
        }


class RiskWeightConfigRepository:
    """Repository for risk weight configuration"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, config_key: str) -> Optional[RiskWeightConfigModel]:
        """Get config by key"""
        return self.db.query(RiskWeightConfigModel).filter(
            RiskWeightConfigModel.config_key == config_key
        ).first()

    def create_or_update(self, config_key: str, weights: dict) -> RiskWeightConfigModel:
        """Create or update risk weight config"""
        config = self.get_by_key(config_key)
        if config:
            config.weights = weights
            config.updated_at = datetime.utcnow()
        else:
            config = RiskWeightConfigModel(config_key=config_key, weights=weights)
            self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def list_all(self) -> list:
        """List all configs"""
        return self.db.query(RiskWeightConfigModel).order_by(
            RiskWeightConfigModel.created_at
        ).all()

    def delete(self, config_key: str) -> bool:
        """Delete config by key"""
        config = self.get_by_key(config_key)
        if config:
            self.db.delete(config)
            self.db.commit()
            return True
        return False

    @staticmethod
    def to_dict(c: RiskWeightConfigModel) -> dict:
        return {
            "config_id": str(c.config_id),
            "config_key": c.config_key,
            "weights": c.weights,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }


class PolicyRepository:
    """Repository for incident response policies"""

    # Editable via the UI / draft workflow. Excludes `enabled`, which stays an
    # instant kill-switch outside draft/publish.
    DRAFT_FIELDS = {
        "name", "rules", "approved_actions", "requires_manual_approval", "approval_priority",
        "constraints", "description", "confidence_gate_threshold", "confidence_gate_min_runs",
        "confidence_gate_runbook_id",
    }
    JSON_FIELDS = {"rules", "approved_actions", "constraints"}

    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, rules: dict, approved_actions: list,
               requires_manual_approval: bool = False, approval_priority: int = 50,
               constraints: dict = None, description: str = None,
               confidence_gate_threshold: float = None,
               confidence_gate_min_runs: int = None,
               confidence_gate_runbook_id: UUID = None) -> PolicyModel:
        """Create new policy"""
        policy = PolicyModel(
            name=name,
            rules=rules or {},
            approved_actions=approved_actions or [],
            requires_manual_approval=requires_manual_approval,
            approval_priority=approval_priority,
            constraints=constraints or {},
            description=description,
            confidence_gate_threshold=confidence_gate_threshold,
            confidence_gate_min_runs=confidence_gate_min_runs,
            confidence_gate_runbook_id=confidence_gate_runbook_id,
        )
        self.db.add(policy)
        self.db.commit()
        self.db.refresh(policy)
        # Seed draft_snapshot to mirror the as-created content — see
        # RunbookRepository.create for why publish() would otherwise no-op.
        policy.draft_snapshot = self._snapshot(policy)
        flag_modified(policy, "draft_snapshot")
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def get(self, policy_id: UUID) -> Optional[PolicyModel]:
        """Get policy by ID"""
        return self.db.query(PolicyModel).filter(
            PolicyModel.policy_id == policy_id
        ).first()

    def list_all(self, enabled_only: bool = False, published_only: bool = False) -> list:
        """List all policies"""
        query = self.db.query(PolicyModel)
        if enabled_only:
            query = query.filter(PolicyModel.enabled == True)
        if published_only:
            query = query.filter(PolicyModel.status == "published")
        return query.order_by(PolicyModel.created_at.desc()).all()

    def update(self, policy_id: UUID, **kwargs) -> Optional[PolicyModel]:
        """Update policy"""
        policy = self.get(policy_id)
        if policy:
            for key, value in kwargs.items():
                if hasattr(policy, key):
                    setattr(policy, key, value)
            policy.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(policy)
        return policy

    @classmethod
    def _snapshot(cls, policy: PolicyModel) -> dict:
        """Draft-field snapshot — confidence_gate_runbook_id (a UUID column) is
        stringified since draft_snapshot/version snapshots are plain JSON."""
        snap = {field: getattr(policy, field) for field in cls.DRAFT_FIELDS}
        if snap.get("confidence_gate_runbook_id") is not None:
            snap["confidence_gate_runbook_id"] = str(snap["confidence_gate_runbook_id"])
        return snap

    def save_draft(self, policy_id: UUID, data: dict) -> Optional[PolicyModel]:
        """PUT semantics: merge `data` into draft_snapshot only — live columns
        (what PolicyBrokerAgent actually reads) are untouched until publish()."""
        policy = self.get(policy_id)
        if not policy:
            return None
        base = dict(policy.draft_snapshot) if policy.draft_snapshot else self._snapshot(policy)
        for key, value in data.items():
            if key in self.DRAFT_FIELDS:
                base[key] = str(value) if key == "confidence_gate_runbook_id" and value is not None else value
        policy.draft_snapshot = base
        flag_modified(policy, "draft_snapshot")
        policy.has_unpublished_changes = True
        policy.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def publish(self, policy_id: UUID, created_by: Optional[str] = None, change_note: Optional[str] = None) -> Optional[PolicyModel]:
        """Copy draft_snapshot onto the live columns and record a version snapshot.
        No-op only when already published with nothing pending — see
        RunbookRepository.publish for the full rationale."""
        policy = self.get(policy_id)
        if not policy:
            return None
        if policy.status == "published" and not policy.has_unpublished_changes:
            return policy
        if not policy.draft_snapshot:
            return policy
        for key, value in policy.draft_snapshot.items():
            if key not in self.DRAFT_FIELDS:
                continue
            if key == "confidence_gate_runbook_id":
                value = UUID(value) if value else None
            setattr(policy, key, value)
            if key in self.JSON_FIELDS:
                flag_modified(policy, key)
        policy.status = "published"
        policy.published_at = datetime.utcnow()
        policy.has_unpublished_changes = False
        policy.updated_at = datetime.utcnow()
        self.db.flush()

        from agentic_os.db.models import PolicyVersionModel
        last = (
            self.db.query(PolicyVersionModel)
            .filter_by(policy_id=policy_id)
            .order_by(PolicyVersionModel.version.desc())
            .first()
        )
        next_version = (last.version + 1) if last else 1
        self.db.add(PolicyVersionModel(
            policy_id=policy_id, version=next_version, snapshot=self._snapshot(policy),
            created_by=created_by, change_note=change_note, created_at=datetime.utcnow(),
        ))
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def discard_draft(self, policy_id: UUID) -> Optional[PolicyModel]:
        """Reset draft_snapshot to mirror current live state — discards pending edits."""
        policy = self.get(policy_id)
        if not policy:
            return None
        policy.draft_snapshot = self._snapshot(policy)
        flag_modified(policy, "draft_snapshot")
        policy.has_unpublished_changes = False
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def set_enabled(self, policy_id: UUID, enabled: bool) -> Optional[PolicyModel]:
        """Instant kill-switch — bypasses draft/publish entirely."""
        policy = self.get(policy_id)
        if not policy:
            return None
        policy.enabled = enabled
        policy.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def list_versions(self, policy_id: UUID) -> list:
        from agentic_os.db.models import PolicyVersionModel
        return (
            self.db.query(PolicyVersionModel)
            .filter_by(policy_id=policy_id)
            .order_by(PolicyVersionModel.version.desc())
            .all()
        )

    def restore_version_to_draft(self, policy_id: UUID, version: int) -> Optional[PolicyModel]:
        """Load a historical version into draft_snapshot for review — does NOT
        publish it directly, same rationale as RunbookRepository.restore_version_to_draft."""
        from agentic_os.db.models import PolicyVersionModel
        row = self.db.query(PolicyVersionModel).filter_by(policy_id=policy_id, version=version).first()
        if not row:
            return None
        policy = self.get(policy_id)
        if not policy:
            return None
        policy.draft_snapshot = dict(row.snapshot)
        flag_modified(policy, "draft_snapshot")
        policy.has_unpublished_changes = True
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def delete(self, policy_id: UUID) -> bool:
        """Delete policy"""
        policy = self.get(policy_id)
        if policy:
            self.db.delete(policy)
            self.db.commit()
            return True
        return False

    @staticmethod
    def to_dict(p: PolicyModel) -> dict:
        return {
            "policy_id": str(p.policy_id),
            "name": p.name,
            "rules": p.rules,
            "approved_actions": p.approved_actions,
            "requires_manual_approval": p.requires_manual_approval,
            "approval_priority": p.approval_priority,
            "constraints": p.constraints,
            "enabled": p.enabled,
            "description": p.description,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
            "confidence_gate_threshold": p.confidence_gate_threshold,
            "confidence_gate_min_runs": p.confidence_gate_min_runs,
            "confidence_gate_runbook_id": str(p.confidence_gate_runbook_id) if p.confidence_gate_runbook_id else None,
            "status": getattr(p, "status", "published") or "published",
            "published_at": p.published_at.isoformat() if getattr(p, "published_at", None) else None,
            "has_unpublished_changes": bool(getattr(p, "has_unpublished_changes", False)),
            "draft_snapshot": getattr(p, "draft_snapshot", None),
        }


class GovernancePolicyRepository:
    """Repository for governance policy operations"""
    model = GovernancePolicyModel

    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, conditions: dict, actions_requiring_approval: list,
               approval_groups: list, description: str = None) -> GovernancePolicyModel:
        """Create a new governance policy"""
        policy = GovernancePolicyModel(
            name=name,
            conditions=conditions or {},
            actions_requiring_approval=actions_requiring_approval or [],
            approval_groups=approval_groups or [],
            description=description
        )
        self.db.add(policy)
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def get(self, policy_id: UUID) -> Optional[GovernancePolicyModel]:
        """Get governance policy by ID"""
        return self.db.query(GovernancePolicyModel).filter(
            GovernancePolicyModel.policy_id == policy_id
        ).first()

    def list_all(self, enabled_only: bool = True) -> list:
        """List all governance policies"""
        query = self.db.query(GovernancePolicyModel)
        if enabled_only:
            query = query.filter(GovernancePolicyModel.enabled == True)
        return query.order_by(GovernancePolicyModel.created_at.desc()).all()

    def update(self, policy_id: UUID, **kwargs) -> Optional[GovernancePolicyModel]:
        """Update governance policy"""
        policy = self.get(policy_id)
        if policy:
            for key, value in kwargs.items():
                if hasattr(policy, key):
                    setattr(policy, key, value)
            policy.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(policy)
        return policy

    def delete(self, policy_id: UUID) -> bool:
        """Delete governance policy"""
        policy = self.get(policy_id)
        if policy:
            self.db.delete(policy)
            self.db.commit()
            return True
        return False

    @staticmethod
    def to_dict(p: GovernancePolicyModel) -> dict:
        return {
            "policy_id": str(p.policy_id),
            "name": p.name,
            "description": p.description,
            "conditions": p.conditions,
            "actions_requiring_approval": p.actions_requiring_approval,
            "approval_groups": p.approval_groups,
            "enabled": p.enabled,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }


class OptimizationRecommendationRepository:
    """Repository for Platform Intelligence optimization recommendations."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: dict) -> OptimizationRecommendationModel:
        rec = OptimizationRecommendationModel(**data)
        self.db.add(rec)
        self.db.commit()
        self.db.refresh(rec)
        return rec

    def get(self, rec_id: UUID) -> Optional[OptimizationRecommendationModel]:
        return self.db.query(OptimizationRecommendationModel).filter(
            OptimizationRecommendationModel.id == rec_id
        ).first()

    def list_all(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[OptimizationRecommendationModel]:
        q = self.db.query(OptimizationRecommendationModel)
        if status:
            q = q.filter(OptimizationRecommendationModel.status == status)
        return (
            q.order_by(desc(OptimizationRecommendationModel.created_at))
             .offset(offset)
             .limit(limit)
             .all()
        )

    def count_pending(self) -> int:
        return self.db.query(OptimizationRecommendationModel).filter(
            OptimizationRecommendationModel.status == 'pending'
        ).count()

    def accept(self, rec_id: UUID, reviewed_by: str = "admin") -> Optional[OptimizationRecommendationModel]:
        rec = self.get(rec_id)
        if rec and rec.status == 'pending':
            rec.status = 'accepted'
            rec.reviewed_by = reviewed_by
            rec.reviewed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(rec)
        return rec

    def reject(self, rec_id: UUID, reviewed_by: str = "admin", reason: str = "") -> Optional[OptimizationRecommendationModel]:
        rec = self.get(rec_id)
        if rec and rec.status == 'pending':
            rec.status = 'rejected'
            rec.reviewed_by = reviewed_by
            rec.review_reason = reason
            rec.reviewed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(rec)
        return rec

    def mark_applied(self, rec_id: UUID) -> Optional[OptimizationRecommendationModel]:
        rec = self.get(rec_id)
        if rec and rec.status == 'accepted':
            rec.applied = True
            rec.applied_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(rec)
        return rec

    def clear_pending(self) -> int:
        """
        Delete all pending (unreviewed) recommendations.
        Called at the start of each analysis run so each run produces a fresh set
        rather than accumulating or deduplicating against stale entries.
        Accepted / rejected records are preserved for audit history.
        """
        rows = self.db.query(OptimizationRecommendationModel).filter(
            OptimizationRecommendationModel.status == 'pending',
        ).all()
        for r in rows:
            self.db.delete(r)
        if rows:
            self.db.commit()
        return len(rows)

    def expire_stale(self) -> int:
        """Expire pending recommendations older than 30 days."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=30)
        rows = self.db.query(OptimizationRecommendationModel).filter(
            OptimizationRecommendationModel.status == 'pending',
            OptimizationRecommendationModel.created_at < cutoff,
        ).all()
        for r in rows:
            r.status = 'expired'
        if rows:
            self.db.commit()
        return len(rows)

    @staticmethod
    def to_dict(r: OptimizationRecommendationModel) -> dict:
        return {
            "id":              str(r.id),
            "category":        r.category,
            "parameter":       r.parameter,
            "current_value":   r.current_value,
            "suggested_value": r.suggested_value,
            "title":           r.title,
            "rationale":       r.rationale,
            "impact":          r.impact,
            "confidence":      r.confidence,
            "priority":        r.priority,
            "evidence":        r.evidence,
            "status":          r.status,
            "reviewed_by":     r.reviewed_by,
            "review_reason":   r.review_reason,
            "reviewed_at":     r.reviewed_at.isoformat() if r.reviewed_at else None,
            "applied":         r.applied,
            "applied_at":      r.applied_at.isoformat() if r.applied_at else None,
            "auto_apply_eligible":         getattr(r, "auto_apply_eligible", False) or False,
            "auto_apply_threshold_met_at": r.auto_apply_threshold_met_at.isoformat() if getattr(r, "auto_apply_threshold_met_at", None) else None,
            "outcome_verified_at":         r.outcome_verified_at.isoformat() if getattr(r, "outcome_verified_at", None) else None,
            "outcome_improved":            getattr(r, "outcome_improved", None),
            "created_at":      r.created_at.isoformat(),
            "expires_at":      r.expires_at.isoformat() if r.expires_at else None,
        }


class PlatformIntelRunRepository:
    """Repository for Platform Intelligence analysis run history / KPI snapshots."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: dict) -> PlatformIntelRunModel:
        run = PlatformIntelRunModel(**data)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def list_recent(self, limit: int = 20, offset: int = 0) -> List[PlatformIntelRunModel]:
        return (
            self.db.query(PlatformIntelRunModel)
            .order_by(desc(PlatformIntelRunModel.created_at))
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count(self) -> int:
        return self.db.query(PlatformIntelRunModel).count()

    def kpi_series(self, since: datetime) -> List[PlatformIntelRunModel]:
        """Runs since `since`, oldest first — the shape a trend chart wants."""
        return (
            self.db.query(PlatformIntelRunModel)
            .filter(PlatformIntelRunModel.created_at >= since)
            .order_by(PlatformIntelRunModel.created_at.asc())
            .all()
        )

    @staticmethod
    def to_dict(r: PlatformIntelRunModel) -> dict:
        return {
            "id":                         str(r.id),
            "created_at":                 r.created_at.isoformat(),
            "period_days":                r.period_days,
            "trigger":                    r.trigger,
            "source":                     r.source,
            "incidents_analysed":         r.incidents_analysed,
            "recommendations_generated":  r.recommendations_generated,
            "recommendations_skipped":    r.recommendations_skipped,
            "llm_raw_response":           r.llm_raw_response,
            "kpis":                       r.kpis or {},
        }


class EventConditionStateRepository:
    """
    Manages open/closed state per (resource_name, event_type) condition.

    This is the single source of truth for backend-side event deduplication.
    All callers (monitoring_events route, workflow close, condition_cleared handler)
    go through here so the logic lives in one place.
    """

    # Open conditions linked to an active incident: 24h safety TTL.
    TTL_HOURS = 24
    # Dismissed conditions (no incident created): short TTL so CMDB/config changes
    # take effect quickly without requiring a manual DB fix.
    DISMISSED_TTL_MINUTES = 15

    def __init__(self, db: Session):
        self.db = db

    def get(self, resource_name: str, event_type: str) -> Optional[EventConditionStateModel]:
        return self.db.query(EventConditionStateModel).filter(
            EventConditionStateModel.resource_name == resource_name,
            EventConditionStateModel.event_type    == event_type,
        ).first()

    def is_open(self, resource_name: str, event_type: str) -> Optional[EventConditionStateModel]:
        """
        Return the open condition row if this (resource, event_type) is currently active,
        or None if it is closed / doesn't exist.

        TTL enforcement:
          - Dismissed conditions (qualified=False): 15-minute TTL so that CMDB or
            scoring-config changes take effect on the next watcher cycle without
            requiring a manual DB fix.
          - Qualified conditions (qualified=True, incident created): 24-hour safety
            TTL guards against the edge-case where the incident never closes.
        """
        row = self.get(resource_name, event_type)
        if row is None or row.status != 'open':
            return None
        now = datetime.utcnow()
        age_seconds = (now - row.opened_at).total_seconds()
        # Dismissed → short TTL
        if not row.qualified and age_seconds > self.DISMISSED_TTL_MINUTES * 60:
            row.status     = 'closed'
            row.closed_at  = now
            row.updated_at = now
            self.db.commit()
            return None
        # Qualified → 24h safety TTL
        if row.qualified and age_seconds > self.TTL_HOURS * 3600:
            row.status     = 'closed'
            row.closed_at  = now
            row.updated_at = now
            self.db.commit()
            return None
        return row

    def open_condition(self, resource_name: str, event_type: str, event_id, qualified: bool = False) -> EventConditionStateModel:
        """
        Mark (resource, event_type) as open, storing the event_id that opened it.

        `qualified=True` means an incident was created; `False` means the event was
        dismissed.  This drives the TTL policy in is_open().
        Uses UPSERT semantics: if a row exists it is reset to open.
        """
        now = datetime.utcnow()
        row = self.get(resource_name, event_type)
        if row is None:
            row = EventConditionStateModel(
                resource_name = resource_name,
                event_type    = event_type,
                status        = 'open',
                qualified     = qualified,
                last_event_id = event_id,
                opened_at     = now,
                updated_at    = now,
            )
            self.db.add(row)
        else:
            row.status        = 'open'
            row.qualified     = qualified
            row.last_event_id = event_id
            row.opened_at     = now
            row.closed_at     = None
            row.updated_at    = now
        self.db.commit()
        return row

    def close_for_resource(self, resource_name: str) -> int:
        """
        Close ALL open conditions for a resource (called on condition_cleared or incident close).
        Returns the number of rows closed.
        """
        now  = datetime.utcnow()
        rows = self.db.query(EventConditionStateModel).filter(
            EventConditionStateModel.resource_name == resource_name,
            EventConditionStateModel.status        == 'open',
        ).all()
        for row in rows:
            row.status    = 'closed'
            row.closed_at = now
            row.updated_at = now
        if rows:
            self.db.commit()
        return len(rows)

    def close_for_workflow(self, workflow_id: str, db) -> int:
        """
        Close the condition linked to a specific incident workflow.

        Looks up the resource_name from workflow_states.context and then calls
        close_for_resource.  Safe to call when there is no linked condition.
        """
        from sqlalchemy import text as sql_text
        row = db.execute(sql_text("""
            SELECT context -> 'alert_payload' ->> 'resource_name' AS resource_name
            FROM workflow_states
            WHERE workflow_id = :wf_id
        """), {"wf_id": str(workflow_id)}).fetchone()
        if row and row[0]:
            return self.close_for_resource(row[0])
        return 0


class DistributedLockRepository:
    """
    Per-target remediation lease backing target_locks. Postgres is the system
    of record (not Redis), so acquire is a single atomic INSERT ... ON CONFLICT
    DO NOTHING — no read-then-write race window, no SELECT FOR UPDATE needed.
    """

    def __init__(self, db: Session):
        self.db = db

    def acquire(self, target_id: str, incident_id: UUID, ttl_seconds: int) -> bool:
        """True if this call acquired the lease; False if another incident
        already holds an unexpired lease on this target_id."""
        from sqlalchemy import text as sql_text
        now = datetime.utcnow()
        result = self.db.execute(
            sql_text("""
                INSERT INTO target_locks (target_id, incident_id, acquired_at, expires_at)
                VALUES (:target_id, :incident_id, :acquired_at, :expires_at)
                ON CONFLICT (target_id) DO NOTHING
            """),
            {"target_id": target_id, "incident_id": str(incident_id),
             "acquired_at": now, "expires_at": now + timedelta(seconds=ttl_seconds)},
        )
        acquired = result.rowcount > 0
        self.db.commit()
        return acquired

    def renew(self, target_id: str, incident_id: UUID, ttl_seconds: int) -> bool:
        """
        Extend the lease's expiry — called periodically by the holder while still
        actively executing, so a long-running-but-legitimate remediation doesn't
        lose its lock to the TTL sweep mid-flight. Scoped to (target_id,
        incident_id): if this incident no longer holds the row (already reclaimed
        by the sweep, possibly re-acquired by a different incident since), this
        is a no-op that returns False rather than touching a lease it doesn't own.
        """
        from sqlalchemy import text as sql_text
        result = self.db.execute(
            sql_text("""
                UPDATE target_locks
                SET expires_at = :expires_at
                WHERE target_id = :target_id AND incident_id = :incident_id
            """),
            {"target_id": target_id, "incident_id": str(incident_id),
             "expires_at": datetime.utcnow() + timedelta(seconds=ttl_seconds)},
        )
        renewed = result.rowcount > 0
        self.db.commit()
        return renewed

    def release(self, target_id: str, incident_id: UUID) -> None:
        """Scoped to (target_id, incident_id) so an incident can never release
        a lease it doesn't currently hold (e.g. one reclaimed by TTL expiry)."""
        from sqlalchemy import text as sql_text
        self.db.execute(
            sql_text("DELETE FROM target_locks WHERE target_id = :target_id AND incident_id = :incident_id"),
            {"target_id": target_id, "incident_id": str(incident_id)},
        )
        self.db.commit()

    def delete_expired(self) -> int:
        """Delete all leases past expires_at. Returns rows deleted."""
        from sqlalchemy import text as sql_text
        result = self.db.execute(
            sql_text("DELETE FROM target_locks WHERE expires_at < :now"),
            {"now": datetime.utcnow()},
        )
        deleted = result.rowcount
        self.db.commit()
        return deleted


class SyntheticMonitorRepository:
    """CRUD for synthetic transaction monitors."""

    def __init__(self, db: Session):
        self.db = db

    def _row_to_dict(self, row) -> dict:
        from agentic_os.db.models import SyntheticMonitorModel  # avoid circular
        return {
            "id":              str(row.id),
            "name":            row.name,
            "har_filename":    row.har_filename,
            "script":          row.script,
            "pages_json":      row.pages_json,
            "credentials_enc": row.credentials_enc,
            "schedule_mins":   row.schedule_mins,
            "enabled":         row.enabled,
            "last_run_at":     row.last_run_at.isoformat() if row.last_run_at else None,
            "last_status":     row.last_status,
            "last_output":     row.last_output,
            "created_at":      row.created_at.isoformat() if row.created_at else None,
            "updated_at":      row.updated_at.isoformat() if row.updated_at else None,
        }

    def list_all(self) -> list[dict]:
        from agentic_os.db.models import SyntheticMonitorModel
        rows = self.db.query(SyntheticMonitorModel).order_by(SyntheticMonitorModel.created_at).all()
        return [self._row_to_dict(r) for r in rows]

    def list_enabled(self) -> list[dict]:
        from agentic_os.db.models import SyntheticMonitorModel
        rows = (
            self.db.query(SyntheticMonitorModel)
            .filter(SyntheticMonitorModel.enabled == True)
            .order_by(SyntheticMonitorModel.created_at)
            .all()
        )
        return [self._row_to_dict(r) for r in rows]

    def get(self, monitor_id: str) -> Optional[dict]:
        from agentic_os.db.models import SyntheticMonitorModel
        row = self.db.query(SyntheticMonitorModel).filter(
            SyntheticMonitorModel.id == monitor_id
        ).first()
        return self._row_to_dict(row) if row else None

    def create(self, data: dict) -> dict:
        from agentic_os.db.models import SyntheticMonitorModel
        import uuid as _uuid
        row = SyntheticMonitorModel(
            id=_uuid.uuid4(),
            name=data["name"],
            har_filename=data.get("har_filename"),
            script=data.get("script"),
            pages_json=data.get("pages_json"),
            credentials_enc=data.get("credentials_enc"),
            schedule_mins=data.get("schedule_mins", 15),
            enabled=data.get("enabled", True),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return self._row_to_dict(row)

    def update(self, monitor_id: str, data: dict) -> Optional[dict]:
        from agentic_os.db.models import SyntheticMonitorModel
        row = self.db.query(SyntheticMonitorModel).filter(
            SyntheticMonitorModel.id == monitor_id
        ).first()
        if not row:
            return None
        for field in ("name", "har_filename", "script", "pages_json", "credentials_enc", "schedule_mins", "enabled"):
            if field in data:
                setattr(row, field, data[field])
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._row_to_dict(row)

    def update_last_run(self, monitor_id: str, status: str, output: str) -> None:
        from agentic_os.db.models import SyntheticMonitorModel
        row = self.db.query(SyntheticMonitorModel).filter(
            SyntheticMonitorModel.id == monitor_id
        ).first()
        if row:
            row.last_run_at = datetime.utcnow()
            row.last_status = status
            row.last_output = output[-4000:] if output else None  # cap at 4KB
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def delete(self, monitor_id: str) -> bool:
        from agentic_os.db.models import SyntheticMonitorModel
        row = self.db.query(SyntheticMonitorModel).filter(
            SyntheticMonitorModel.id == monitor_id
        ).first()
        if not row:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
