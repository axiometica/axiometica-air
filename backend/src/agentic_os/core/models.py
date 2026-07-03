"""
Core models for the Agentic OS platform.
Supports multi-ITSM workflows: Incident, Change, Problem, Request.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Literal, TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .context_schema import IncidentWorkflowContext


class WorkflowType(str, Enum):
    """Types of workflows supported"""
    INCIDENT = "incident"
    CHANGE = "change"
    PROBLEM = "problem"
    REQUEST = "request"


class LifecycleState(str, Enum):
    """Universal lifecycle states for any workflow.

    Incident lifecycle:
      open → in_progress → waiting_approval → executing
        executing + remediation_outcome=succeeded  → resolved → closed
        executing + remediation_outcome=failed     → awaiting_manual
        waiting_approval + approval=rejected       → awaiting_manual

      awaiting_manual:  human owns the incident.
        - operator resolves manually              → resolved (resolution_source=manual)
        - operator closes as won't-fix            → closed   (resolution_source=manual)
        - operator re-attempts                    → waiting_approval
        - watcher all-clear fires                 → resolved (resolution_source=watcher_all_clear)

      failed:  reserved for internal pipeline errors (agent bugs, not remediation failures).
      rejected: legacy — kept for backward compat; new incidents use awaiting_manual.

    Storm lifecycle:
      storm_hold: child incident is part of a correlated storm cluster; individual
                  remediation is suppressed until the storm parent receives CAB approval.
                  The storm parent itself is placed in waiting_approval immediately.
    """
    OPEN             = "open"
    IN_PROGRESS      = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED         = "approved"
    EXECUTING        = "executing"
    AWAITING_MANUAL  = "awaiting_manual"   # remediation failed or rejected — human owns it
    RESOLVED         = "resolved"
    CLOSED           = "closed"
    MONITORING       = "monitoring"
    DEPLOYED         = "deployed"
    ROLLED_BACK      = "rolled_back"
    # Storm states
    STORM_HOLD       = "storm_hold"    # child incident held by Storm Agent; awaits storm-level decision
    # Legacy states — kept for backward compat with existing DB rows
    REJECTED         = "rejected"
    FAILED           = "failed"


class Severity(str, Enum):
    """Incident severity levels (assessed by RiskAssessor)"""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EventCriticality(str, Enum):
    """Raw signal criticality (as detected by monitoring system)"""
    INFO = "info"          # Normal signal, monitoring only
    WARNING = "warning"    # Elevated signal, may need attention
    CRITICAL = "critical"  # High signal, likely needs action


class IncidentPriority(str, Enum):
    """Incident priority (P1-P5, derived from severity × business_criticality)"""
    P1 = "P1"  # Critical - immediate action required
    P2 = "P2"  # High - action within 1 hour
    P3 = "P3"  # Medium - action within 4 hours
    P4 = "P4"  # Low - action within 24 hours
    P5 = "P5"  # Informational - action within 5 days


class MonitoringEventStatus(str, Enum):
    """Status of a monitoring event through its lifecycle"""
    NEW = "new"           # Just received from monitoring system
    QUALIFIED = "qualified"  # Passed qualification threshold, incident opened
    DISMISSED = "dismissed"  # Below threshold, no action needed
    ESCALATED = "escalated"  # Manually escalated to incident despite low score


class EventType(str, Enum):
    """Event types for workflows and monitoring"""
    # Workflow lifecycle
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    # Monitoring events (raw signals from watcher/sentinel)
    MONITORING_EVENT_DETECTED = "monitoring.event_detected"
    MONITORING_EVENT_QUALIFIED = "monitoring.event_qualified"
    MONITORING_EVENT_DISMISSED = "monitoring.event_dismissed"

    # Incident events
    INCIDENT_SEVERITY_ASSESSED = "incident.severity_assessed"
    INCIDENT_RISK_ASSESSED = "incident.risk_assessed"
    INCIDENT_PROPOSAL_GENERATED = "incident.proposal_generated"
    INCIDENT_APPROVED = "incident.approved"
    INCIDENT_REMEDIATION_EXECUTED = "incident.remediation_executed"
    INCIDENT_RESOLVED = "incident.resolved"

    # Change events
    CHANGE_SUBMITTED = "change.submitted"
    CHANGE_RISK_ASSESSED = "change.risk_assessed"
    CHANGE_CAB_REVIEW_REQUESTED = "change.cab_review_requested"
    CHANGE_CAB_APPROVED = "change.cab_approved"
    CHANGE_CAB_REJECTED = "change.cab_rejected"
    CHANGE_DEPLOYMENT_SCHEDULED = "change.deployment_scheduled"
    CHANGE_DEPLOYED = "change.deployed"
    CHANGE_VERIFIED = "change.verified"
    CHANGE_ROLLED_BACK = "change.rolled_back"

    # Problem events
    PROBLEM_OPENED = "problem.opened"
    PROBLEM_RCA_IN_PROGRESS = "problem.rca_in_progress"
    PROBLEM_RCA_COMPLETED = "problem.rca_completed"
    PROBLEM_FIX_VERIFIED = "problem.fix_verified"
    PROBLEM_CLOSED = "problem.closed"

    # Request events
    REQUEST_SUBMITTED = "request.submitted"
    REQUEST_ASSIGNED = "request.assigned"
    REQUEST_IN_PROGRESS = "request.in_progress"
    REQUEST_FULFILLED = "request.fulfilled"
    REQUEST_REJECTED = "request.rejected"

    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"


@dataclass
class EventEnvelope:
    """Immutable event envelope for event sourcing"""
    workflow_id: UUID
    workflow_type: WorkflowType
    event_type: EventType
    source_agent: str
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: Optional[UUID] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "workflow_id": str(self.workflow_id),
            "workflow_type": self.workflow_type.value,
            "event_type": self.event_type.value,
            "source_agent": self.source_agent,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "payload": self.payload,
        }


@dataclass
class WorkflowState:
    """
    Generic workflow state for any ITSM process.
    Incident, Change, Problem, Request all use this model.
    Domain-specific data stored in 'context' field (JSONB).
    """
    # Workflow identity (required fields first, no defaults)
    workflow_type: WorkflowType

    # Workflow identity (fields with defaults)
    workflow_id: UUID = field(default_factory=uuid4)

    # Lifecycle
    lifecycle_state: LifecycleState = LifecycleState.OPEN
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # Context (domain-specific data)
    # For Incident: alert_payload, proposal, severity
    # For Change: change_type, cab_status, deployment_window, rollback_plan
    # For Problem: root_cause, permanent_fix, affected_incidents
    # For Request: fulfillment_steps, dependencies, sla
    context: dict[str, Any] = field(default_factory=dict)

    # NEW: Typed context for incident workflows (lazy-loaded from context dict)
    # For backward compatibility, context_schema is optional and synced with context
    context_schema: Optional[Any] = None  # Type would be IncidentWorkflowContext, using Any to avoid circular imports

    # Universal assessment fields (optional - not all processes use them)
    severity: Optional[Severity] = None
    title: Optional[str] = None  # Incident title from watcher alert
    risk_score: Optional[float] = None
    risk_level: Optional[str] = None

    # AI-generated summary
    summary: Optional[str] = None                   # Executive narrative (3-4 sentences)
    technical_summary: Optional[str] = None         # Technical digest (bullet points, LLM-only)
    summary_generated_at: Optional[datetime] = None

    # Governance & approval
    governance_decision: Optional[str] = None  # "approved", "rejected", "pending"
    governance_reason: Optional[str] = None
    approval_request_id: Optional[str] = None

    # Remediation outcome — independent of lifecycle_state.
    # Tracks how the automated remediation steps performed, not whether the condition cleared.
    # lifecycle_state: overall incident status (resolved, failed, etc.)
    # remediation_outcome: how automation did (succeeded/failed/aborted/skipped/pending)
    remediation_outcome: Optional[str] = None

    # Resolution source — what ultimately cleared the incident condition.
    # Values: automated_remediation / watcher_all_clear / manual
    resolution_source: Optional[str] = None

    # When the watcher confirmed the triggering condition has cleared (all-clear signal received).
    all_clear_received_at: Optional[datetime] = None

    # Audit trail
    reasoning_trace: list[str] = field(default_factory=list)
    execution_log: list[str] = field(default_factory=list)

    # State transition history — each entry: {state, timestamp, reason}
    # Populated by transition_state(); seeded with the initial OPEN state in __post_init__.
    state_history: list[dict] = field(default_factory=list)

    # Correlation for distributed tracing
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: Optional[UUID] = None

    def __post_init__(self):
        """
        Seed state_history with OPEN when a brand-new workflow is created.

        Only seeds when both conditions hold:
          1. state_history is empty (no history yet)
          2. lifecycle_state is OPEN (true initial state — not a DB reload of an
             in-progress/resolved record whose history predates the migration)
        """
        if not self.state_history and self.lifecycle_state == LifecycleState.OPEN:
            self.state_history = [{
                'state': self.lifecycle_state.value,
                'timestamp': self.created_at.isoformat(),
                'reason': 'Incident created',
            }]

    def transition_state(self, new_state: LifecycleState, reason: str = '') -> None:
        """
        Atomically record a state transition and update lifecycle_state.

        Appends {state, timestamp, reason} to state_history so the full
        lifecycle progression is preserved for the Timeline UI.
        """
        self.state_history.append({
            'state': new_state.value,
            'timestamp': datetime.utcnow().isoformat(),
            'reason': reason,
        })
        self.lifecycle_state = new_state
        self.updated_at = datetime.utcnow()

    def add_trace(self, message: str):
        """Add message to reasoning trace"""
        self.reasoning_trace.append(f"[{datetime.utcnow().isoformat()}] {message}")
        self.updated_at = datetime.utcnow()

    def add_log(self, message: str):
        """Add message to execution log"""
        self.execution_log.append(f"[{datetime.utcnow().isoformat()}] {message}")
        self.updated_at = datetime.utcnow()

    def get_context(self) -> "IncidentWorkflowContext":
        """
        Get typed context for incident workflows.
        If context_schema exists, return it (reconstructing from dict if from database).
        Otherwise, reconstruct from untyped context dict (backward compatibility).
        """
        from .context_schema import IncidentWorkflowContext

        # If context_schema exists, reconstruct from dict (comes from database as JSON)
        if self.context_schema is not None:
            if isinstance(self.context_schema, dict):
                return IncidentWorkflowContext.from_dict(self.context_schema)
            else:
                # Already an instance
                return self.context_schema

        # Lazy reconstruction from untyped dict for backward compatibility
        return IncidentWorkflowContext.from_dict(self.context)

    def set_context(self, context: "IncidentWorkflowContext") -> None:
        """
        Set typed context and sync to untyped dict for persistence.

        CRITICAL: Preserves keys that agents write directly onto state.context (not
        part of the typed schema) so they are not erased by subsequent set_context()
        calls from later agents.  Without this, ToolRegistryAgent sets
        decision_result="approved" and then immediately calls _set_typed_context(),
        which would wipe decision_result — causing _get_next_step() to return None
        and silently skip VerifierAgent, leaving the workflow stuck in 'executing'.
        """
        from .context_schema import IncidentWorkflowContext
        if not isinstance(context, IncidentWorkflowContext):
            raise TypeError(f"Expected IncidentWorkflowContext, got {type(context)}")
        self.context_schema = context

        new_context = context.to_dict()

        # Keys that agents write directly to state.context (not serialised by to_dict).
        # Preserve them so later set_context() calls don't silently discard them.
        PRESERVED_KEYS = {
            "alert_payload",        # written by monitoring_events / SentinelAgent
            "decision_result",      # written by PolicyBrokerAgent / ToolRegistryAgent
            "cmdb_context",         # written by LibrarianAgent (legacy path)
            "runbook_steps",        # written by MechanicAgent (legacy path)
            "runbook_execution_results",  # written by ToolRegistryAgent
            "risk_breakdown",       # written by RiskAssessor (legacy path)
            "incident_priority",    # written by PolicyBrokerAgent
            "last_error",           # written on exception paths
            "proposal",             # written by MechanicAgent — CRITICAL for runbook feedback
            "incident_update_requested",  # written by ToolRegistryAgent's incident_update step — read by VerifierAgent
            "runbook_graph",        # written by MechanicAgent / ToolRegistryAgent DB fallback — graph-walk source
        }
        for key in PRESERVED_KEYS:
            if key in self.context and key not in new_context:
                new_context[key] = self.context[key]

        self.context = new_context
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization"""
        # Serialize context_schema if present (use to_dict() for proper serialization)
        context_schema_dict = None
        if self.context_schema is not None:
            if hasattr(self.context_schema, 'to_dict'):
                context_schema_dict = self.context_schema.to_dict()

        return {
            "workflow_id": str(self.workflow_id),
            "workflow_type": self.workflow_type.value,
            "lifecycle_state": self.lifecycle_state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context": self.context,
            "context_schema": context_schema_dict,
            "severity": self.severity.value if self.severity else None,
            "title": self.title,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "summary": self.summary,
            "technical_summary": self.technical_summary,
            "summary_generated_at": self.summary_generated_at.isoformat() if self.summary_generated_at else None,
            "governance_decision": self.governance_decision,
            "governance_reason": self.governance_reason,
            "approval_request_id": self.approval_request_id,
            "reasoning_trace": self.reasoning_trace,
            "execution_log": self.execution_log,
            "state_history": self.state_history,
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id else None,
        }


@dataclass
class WorkflowStep:
    """Definition of a step in a workflow"""
    step_id: str
    step_type: Literal["agent", "human_approval", "external_call", "decision", "parallel"]
    name: str
    handler: Optional[str] = None  # Agent name or function reference
    next_steps: dict[str, str] = field(default_factory=dict)  # Conditional routing
    timeout_seconds: Optional[int] = None
    retry_count: int = 0
    fallback_step: Optional[str] = None


@dataclass
class WorkflowDefinition:
    """Definition for a workflow (Incident, Change, Problem, Request)"""
    workflow_type: WorkflowType
    version: str
    start_step: str
    steps: dict[str, WorkflowStep] = field(default_factory=dict)
    end_steps: list[str] = field(default_factory=list)

    def add_step(self, step: WorkflowStep, is_start: bool = False, is_end: bool = False):
        """Add step to workflow definition"""
        self.steps[step.step_id] = step
        if is_start:
            self.start_step = step.step_id
        if is_end:
            self.end_steps.append(step.step_id)


# Domain-specific context schemas

@dataclass
class IncidentContext:
    """Incident-specific context"""
    alert_payload: dict[str, Any]
    proposal: Optional[dict[str, Any]] = None
    proposal_source: str = "deterministic"
    proposal_confidence: Optional[float] = None
    execution_mode: str = "dry_run"
    action_outcomes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ChangeContext:
    """Change-specific context"""
    change_type: Literal["standard", "normal", "emergency"] = "standard"
    description: str = ""
    cab_approval_status: Optional[str] = None  # "pending", "approved", "rejected"
    cab_approval_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    deployment_window_start: Optional[datetime] = None
    deployment_window_end: Optional[datetime] = None
    rollback_plan: str = ""
    affected_services: list[str] = field(default_factory=list)
    deployment_steps: list[str] = field(default_factory=list)
    deployment_outcome: Optional[str] = None
    verification_steps: list[str] = field(default_factory=list)


@dataclass
class ProblemContext:
    """Problem-specific context"""
    problem_title: str
    problem_description: str
    root_cause: Optional[str] = None
    permanent_fix_plan: Optional[str] = None
    workaround: Optional[str] = None
    affected_services: list[str] = field(default_factory=list)
    affected_incidents: list[UUID] = field(default_factory=list)
    fix_verified: bool = False


@dataclass
class RequestContext:
    """Service Request-specific context"""
    request_type: str  # e.g., "access_request", "software_install"
    requested_by: str
    assigned_to: Optional[str] = None
    fulfillment_steps: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    sla_due_date: Optional[datetime] = None
