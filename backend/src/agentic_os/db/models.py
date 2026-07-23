"""
SQLAlchemy ORM models for PostgreSQL persistence.
Uses JSONB for flexible, domain-specific context.
"""

from sqlalchemy import Column, String, Float, DateTime, Integer, BigInteger, Text, Boolean, JSON, Enum as SQLEnum, Index, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from agentic_os.core.models import WorkflowType, LifecycleState, Severity, EventType
from agentic_os.security.crypto import EncryptedString

Base = declarative_base()


class WorkflowStateModel(Base):
    """Mutable workflow state"""
    __tablename__ = "workflow_states"

    workflow_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_type = Column(SQLEnum(WorkflowType, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)

    # Incident enumeration — assigned automatically by DB trigger on INSERT
    # trigger: trg_workflow_human_id_insert (assign_workflow_human_id function)
    incident_number = Column(Integer, nullable=True, unique=True, index=True)       # 1, 2, 3, ...
    incident_number_str = Column(String(20), nullable=True, unique=True, index=True) # "INC0001", "INC0002", ...

    # Storm fields — is_storm_parent promoted from JSONB context flag to real column
    # storm_number/str assigned automatically by DB trigger on UPDATE when is_storm_parent flips TRUE
    # trigger: trg_workflow_human_id_update (assign_workflow_human_id function)
    is_storm_parent   = Column(Boolean,   nullable=False, default=False)
    storm_number      = Column(Integer,   nullable=True, unique=True)                # 1, 2, 3, ...
    storm_number_str  = Column(String(20), nullable=True, unique=True, index=True)   # "STRM0001", "STRM0002", ...
    # FK to parent storm incident (NULL = not part of a storm cluster)
    storm_id          = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id', ondelete='SET NULL'), nullable=True, index=True)
    storm_detected_at = Column(DateTime, nullable=True)  # when storm was first detected (set on parent row)

    lifecycle_state = Column(SQLEnum(LifecycleState, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)

    # Context: incident-specific, change-specific, problem-specific, or request-specific data
    context = Column(JSON, nullable=False, default={})

    # Typed context schema for incident workflows (Phase 10)
    # Stores IncidentWorkflowContext as JSON: {sentinel, cmdb, risk, proposal, governance, execution_results, verification}
    context_schema = Column(JSON, nullable=True, default=None)

    # Universal optional fields
    severity = Column(SQLEnum(Severity, values_callable=lambda x: [e.value for e in x]), nullable=True)
    title = Column(String(500), nullable=True)  # Incident/workflow title from alert or context
    risk_score = Column(Float, nullable=True)
    risk_level = Column(String(50), nullable=True)

    # AI-generated summary (async, cached)
    summary = Column(Text, nullable=True)            # Executive narrative (3-4 sentences)
    technical_summary = Column(Text, nullable=True)  # Technical digest (bullet points)
    summary_generated_at = Column(DateTime, nullable=True)

    # Governance
    governance_decision = Column(String(50), nullable=True)
    governance_reason = Column(String(500), nullable=True)
    approval_request_id = Column(String(100), nullable=True)

    # Remediation outcome — tracks how automation did, independently of lifecycle_state.
    # lifecycle_state reflects the overall incident (open, resolved, failed, etc.)
    # remediation_outcome reflects only what the automated steps achieved.
    # Values: succeeded / failed / aborted / skipped / pending
    remediation_outcome = Column(String(50), nullable=True)

    # Resolution source — what actually cleared the condition.
    # Values: automated_remediation / watcher_all_clear / manual
    resolution_source = Column(String(50), nullable=True)

    # Timestamp when watcher confirmed the triggering condition has cleared.
    all_clear_received_at = Column(DateTime, nullable=True)

    # Resolution tracking (v4.1.0 — incident lifecycle v2)
    # These columns are written when an operator or the system closes an incident.
    # resolution_category: WHY it was closed (manual_fix / wont_fix / self_healed / etc.)
    # resolution_notes:    operator's free-text explanation
    # resolved_by:         principal name/email (populated when auth lands in Phase B)
    # resolved_at:         explicit close timestamp (complements updated_at)
    resolution_category = Column(String(50), nullable=True)
    resolution_notes    = Column(Text,       nullable=True)
    resolved_by         = Column(String(200), nullable=True)
    resolved_at         = Column(DateTime,   nullable=True)

    # Audit trail (stored as JSON for flexibility)
    reasoning_trace = Column(JSON, default=list, nullable=False)
    execution_log = Column(JSON, default=list, nullable=False)

    # State transition history — [{state, timestamp, reason}, ...]
    state_history = Column(JSON, default=list, nullable=False)

    # Incremented when the same condition (resource + event_type + source) is
    # validated as recurring while this incident is still open — see the dedup
    # branch in api/routes/monitoring_events.py for where this gets bumped.
    duplicate_count = Column(Integer, default=0, nullable=False)

    # How many times the safety-net sweep (resume_stuck_approvals in
    # tasks/celery_app.py) has auto-re-fired resume_workflow_task for this
    # incident while it sat stuck in 'approved' with no progress. Past the
    # retry cap, the sweep escalates to awaiting_manual instead of retrying.
    resume_retry_count = Column(Integer, default=0, nullable=False)

    # Correlation
    correlation_id = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False)
    causation_id = Column(UUID(as_uuid=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Indices for common queries
    __table_args__ = (
        Index('idx_workflow_type_lifecycle', 'workflow_type', 'lifecycle_state'),
        Index('idx_correlation_id', 'correlation_id'),
        Index('idx_created_at', 'created_at'),
    )

    # Relationships
    events    = relationship("EventModel",        back_populates="workflow", cascade="all, delete-orphan")
    approvals = relationship("ApprovalModel",     back_populates="workflow", cascade="all, delete-orphan")
    notes     = relationship(
        "IncidentNoteModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
        # order_by intentionally omitted — IncidentNoteModel is declared below
        # WorkflowStateModel in this file, so a string eval would fail.
        # The /notes endpoint query sorts by created_at explicitly.
    )


class EventModel(Base):
    """Immutable event log (Event Sourcing)"""
    __tablename__ = "events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id'), nullable=False, index=True)
    workflow_type = Column(SQLEnum(WorkflowType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    event_type = Column(SQLEnum(EventType, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)

    source_agent = Column(String(100), nullable=False)

    # Event payload (domain-specific)
    payload = Column(JSON, nullable=False, default={})

    # Correlation for distributed tracing
    correlation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    causation_id = Column(UUID(as_uuid=True), nullable=True)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    workflow = relationship("WorkflowStateModel", back_populates="events")

    __table_args__ = (
        Index('idx_workflow_id_created', 'workflow_id', 'created_at'),
        Index('idx_correlation_id_timestamp', 'correlation_id', 'created_at'),
        Index('idx_event_type_timestamp', 'event_type', 'created_at'),
    )


class ApprovalModel(Base):
    """Approval requests and decisions (for CAB review, governance policies, etc.)"""
    __tablename__ = "approvals"

    approval_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id'), nullable=False, index=True)
    governance_policy_id = Column(UUID(as_uuid=True), ForeignKey('governance_policies.policy_id'), nullable=True, index=True)

    approval_type = Column(String(50), nullable=False)  # "governance", "cab", "change_advisory_board", etc.
    status = Column(String(20), nullable=False)  # "pending", "approved", "rejected"

    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)

    decided_by = Column(String(100), nullable=True)
    decision_notes = Column(String(1000), nullable=True)

    # Proposed remediation action (for governance approvals)
    proposed_action = Column(JSON, nullable=True, default={})  # {
    #   "tool": "restart_service",
    #   "target": "prod-api-01",
    #   "args": { "timeout": 30, "force": false },
    #   "blast_radius": 2,
    #   "estimated_mttr": 300
    # }

    # Incident context summary (for governance approvals)
    incident_summary = Column(JSON, nullable=True, default={})  # {
    #   "anomaly_type": "high_cpu",
    #   "severity": "high",
    #   "risk_score": 78
    # }

    # Additional metadata (for other approval types)
    extra_metadata = Column(JSON, default={}, nullable=False)

    # Relationships
    workflow = relationship("WorkflowStateModel", back_populates="approvals")

    __table_args__ = (
        Index('idx_workflow_approval_type', 'workflow_id', 'approval_type'),
        Index('idx_approval_status', 'status'),
        Index('idx_requested_at', 'requested_at'),
        Index('idx_governance_policy_id', 'governance_policy_id'),
    )


class IncidentNoteModel(Base):
    """
    Append-only work-log entries for incidents.

    Each row is immutable once created (no UPDATE expected).
    Used by operators to record manual actions, observations, and escalations
    while an incident is in the AWAITING_MANUAL state (or any other active state).

    note_type values:
      note       — general observation / comment
      action     — operator performed a specific action (e.g. restarted a pod manually)
      escalation — escalated to another team or on-call
      system     — automated platform entry (all-clear, retry triggered, etc.)
    """
    __tablename__ = "incident_notes"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_states.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author    = Column(String(200), nullable=False, default="operator")
    note_type = Column(String(20),  nullable=False, default="note")   # note|action|escalation|system
    body      = Column(Text,        nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship back to the parent workflow
    workflow = relationship("WorkflowStateModel", back_populates="notes")

    __table_args__ = (
        Index("idx_incident_notes_workflow", "workflow_id"),
        Index("idx_incident_notes_created",  "created_at"),
    )


class AgentExecutionModel(Base):
    """Track agent execution for debugging and monitoring"""
    __tablename__ = "agent_executions"

    execution_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id'), nullable=False, index=True)

    agent_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # "started", "completed", "failed"

    input_data = Column(JSON, nullable=False)  # State passed to agent
    output_data = Column(JSON, nullable=True)  # State returned from agent

    error_message = Column(String(1000), nullable=True)

    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    __table_args__ = (
        Index('idx_workflow_agent', 'workflow_id', 'agent_name'),
        Index('idx_agent_status', 'agent_name', 'status'),
    )


class GeneratedRunbookModel(Base):
    """
    AI-generated runbooks produced by RunbookGeneratorAgent.
    Distinct from operator-authored RunbookModel rows — these require human review
    before being promoted to the main runbooks table.
    """
    __tablename__ = "generated_runbooks"

    id                          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id                 = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id', ondelete='CASCADE'), nullable=False, index=True)
    anomaly_type                = Column(String(255), nullable=False, index=True)

    name                        = Column(String(500), nullable=False)
    description                 = Column(String(2000), nullable=True)
    diagnostics_steps           = Column(JSON, nullable=False, default=list)
    remediation_steps           = Column(JSON, nullable=False, default=list)
    rollback_steps              = Column(JSON, nullable=False, default=list)
    verification_steps          = Column(JSON, nullable=False, default=list)

    source_runbooks             = Column(JSON, nullable=True)
    generated_by                = Column(String(255), nullable=False, default='runbook_generator_agent')
    resource_type               = Column(String(100), nullable=True)
    environment                 = Column(String(100), nullable=True)

    validation_status           = Column(String(50), nullable=False, default='pending')
    validation_issues           = Column(JSON, nullable=True)
    validation_warnings         = Column(JSON, nullable=True)
    confidence_score            = Column(Float, nullable=False, default=0.0)

    approval_status             = Column(String(50), nullable=False, default='pending_review')
    approved_by                 = Column(String(255), nullable=True)
    approval_feedback           = Column(String(2000), nullable=True)
    approved_at                 = Column(DateTime, nullable=True)

    total_executions            = Column(Integer, nullable=False, default=0)
    successful_executions       = Column(Integer, nullable=False, default=0)
    failed_executions           = Column(Integer, nullable=False, default=0)
    success_rate                = Column(Float, nullable=True)
    last_executed_at            = Column(DateTime, nullable=True)

    estimated_blast_radius      = Column(Integer, nullable=True)
    estimated_duration_seconds  = Column(Integer, nullable=True)
    estimated_time_to_resolution = Column(Integer, nullable=True)
    actual_avg_duration_seconds = Column(Integer, nullable=True)
    actual_avg_ttm_seconds      = Column(Integer, nullable=True)

    is_active                   = Column(Boolean, nullable=False, default=True)
    created_at                  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at                  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_gen_runbooks_workflow',  'workflow_id'),
        Index('idx_gen_runbooks_anomaly',   'anomaly_type'),
        Index('idx_gen_runbooks_approval',  'approval_status'),
        Index('idx_gen_runbooks_created',   'created_at'),
    )


class RemediationOutcomeModel(Base):
    """
    Learning-pipeline records — tracks how each remediation attempt went.
    Fed into TuningAgent to improve risk weights and runbook selection.
    """
    __tablename__ = "remediation_outcomes"

    id                          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id                 = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id', ondelete='CASCADE'), nullable=False, index=True)

    applied_remediation_id      = Column(UUID(as_uuid=True), nullable=True)
    runbook_source              = Column(String(50), nullable=True)   # 'standard' | 'generated' | 'manual'

    incident_features           = Column(JSON, nullable=True)
    applied_steps               = Column(JSON, nullable=True)
    execution_duration_seconds  = Column(Integer, nullable=True)
    resolution_time_seconds     = Column(Integer, nullable=True)

    effectiveness_score         = Column(Float, nullable=True)
    remediation_successful      = Column(Boolean, nullable=True)
    incident_resolved           = Column(Boolean, nullable=True)
    side_effects                = Column(JSON, nullable=True)

    # Root-cause classification (Enhancement 2): tool_error | target_not_found |
    # precondition_unmet | timeout | permission_denied | partial_completion | unknown
    failure_category             = Column(String(30), nullable=True)

    system_stable               = Column(Boolean, nullable=True)
    performance_impact          = Column(String(50), nullable=True)
    resource_usage_change       = Column(JSON, nullable=True)

    feedback_provided           = Column(Boolean, nullable=False, default=False)
    feedback_score              = Column(Integer, nullable=True)
    feedback_notes              = Column(String(2000), nullable=True)
    feedback_from               = Column(String(255), nullable=True)

    created_at                  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at                  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_remediation_outcomes_workflow',  'workflow_id'),
        Index('idx_remediation_outcomes_score',     'effectiveness_score'),
        Index('idx_remediation_outcomes_feedback',  'feedback_provided'),
        Index('idx_remediation_outcomes_created',   'created_at'),
    )


class RunbookStepOutcomeModel(Base):
    """
    Per-step execution result for a single runbook run — previously computed by
    ToolRegistryAgent and discarded after the abort/continue decision, now persisted
    so Platform Intelligence can identify which specific step is brittle rather than
    only seeing the whole-runbook success rate.
    """
    __tablename__ = "runbook_step_outcomes"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id', ondelete='CASCADE'), nullable=False)
    runbook_id  = Column(UUID(as_uuid=True), ForeignKey('runbooks.id', ondelete='SET NULL'), nullable=True)

    step_index  = Column(Integer,      nullable=False)
    step_name   = Column(String(255),  nullable=True)
    step_type   = Column(String(50),   nullable=True)   # diagnostic | action | verification
    tool        = Column(String(200), nullable=True)
    status      = Column(String(20),  nullable=False)   # succeeded | failed | timed_out | skipped

    duration_seconds = Column(Float, nullable=True)   # not yet populated — step timing isn't instrumented
    error_message    = Column(Text,  nullable=True)
    # Root-cause classification (Enhancement 2): tool_error | target_not_found |
    # precondition_unmet | timeout | permission_denied | partial_completion | unknown
    failure_category = Column(String(30), nullable=True)

    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_runbook_step_outcomes_runbook_step', 'runbook_id', 'step_index'),
        Index('idx_runbook_step_outcomes_workflow',     'workflow_id'),
        Index('idx_runbook_step_outcomes_created',      'created_at'),
    )


# WorkflowDefinitionModel intentionally removed — the table was dead schema.
# WorkflowDefinitionLoader reads from YAML files (core/definitions.py), not the DB.
# The workflow_definitions table is dropped in migration v1_1_0_schema_foundations.sql.


class RunbookModel(Base):
    """Operator-authored remediation runbooks (highest confidence source)"""
    __tablename__ = "runbooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")

    # Matching criteria
    event_type = Column(String(100), nullable=False, index=True)
    service = Column(String(255), nullable=True)
    environment = Column(String(50), nullable=True)
    platform = Column(String(50), nullable=True, default="any")  # docker | linux | windows | kubernetes | any

    # Steps (ordered JSON arrays)
    diagnostics = Column(JSON, nullable=False, default=list)
    actions = Column(JSON, nullable=False, default=list)
    verification_steps = Column(JSON, nullable=False, default=list)

    # Metadata
    confidence = Column(Float, default=0.85)
    blast_radius = Column(Integer, default=1)
    enabled = Column(Boolean, default=True, nullable=False)
    # Origin: operator_authored | ai_generated
    source = Column(String(50), nullable=False, default='operator_authored')

    # Execution feedback — updated automatically after each remediation
    total_executions      = Column(Integer, default=0, nullable=False)
    successful_executions = Column(Integer, default=0, nullable=False)
    failed_executions     = Column(Integer, default=0, nullable=False)
    success_rate          = Column(Float, nullable=True)          # 0.0-1.0, null = no data
    recent_outcomes       = Column(JSON, default=list)            # last 10 booleans (newest last)
    confidence_trend      = Column(String(10), nullable=True)     # "up" | "down" | "stable" | "new"
    last_executed_at      = Column(DateTime, nullable=True)

    # Visual editor: preserve original unified steps so re-loading reconstructs graph exactly
    source_steps = Column(JSON, nullable=True)

    # LLM prompt used to generate this runbook (ai_generated only), for auditing/reproducibility
    generation_prompt = Column(Text, nullable=True)

    # Platform-seeded runbooks cannot be deleted; set True by seed pipeline on every startup
    is_seeded = Column(Boolean, default=False, nullable=False)

    # Draft/publish workflow — PUT writes only to draft_snapshot; the live columns
    # above (event_type..verification_steps, confidence, blast_radius, source_steps,
    # generation_prompt) are untouched until POST .../publish copies draft_snapshot
    # onto them. Execution-feedback columns above are never part of draft_snapshot.
    # `enabled` is deliberately NOT gated by this — it stays an instant kill-switch.
    status                  = Column(String(20), nullable=False, default='draft', server_default='draft')  # draft | published
    published_at            = Column(DateTime, nullable=True)
    has_unpublished_changes = Column(Boolean, nullable=False, default=False, server_default='false')
    draft_snapshot          = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_runbook_event_type', 'event_type'),
        Index('idx_runbook_enabled', 'enabled'),
        Index('idx_runbook_platform', 'platform'),
        Index('idx_runbook_status', 'status'),
    )


class RunbookVersionModel(Base):
    """Snapshot of a runbook's live fields taken each time it is published."""
    __tablename__ = "runbook_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id = Column(UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_by = Column(String(255), nullable=True)
    change_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_runbook_version_runbook_id', 'runbook_id'),
        UniqueConstraint('runbook_id', 'version', name='uq_runbook_version'),
    )


class ApprovedActionModel(Base):
    """
    Catalog of approved automation actions.

    Each action defines:
      - category: diagnostic | remediation_safe | remediation_intrusive
      - blast_radius: 1 (read-only) → 2 (moderate) → 3 (disruptive)
      - process_rules: JSON list of regex allow/deny rules evaluated in order.
        Only meaningful for actions that target processes (process_kill, force_restart).
        Each rule: { "pattern": "<regex>", "description": "...", "allow": bool, "priority": int }
        Evaluation: first matching rule wins.  If no rule matches → default_deny.
    """
    __tablename__ = "approved_actions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_name   = Column(String(100), nullable=False, unique=True, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category    = Column(String(50), nullable=False)   # diagnostic | remediation_safe | remediation_intrusive
    blast_radius       = Column(Integer, default=1, nullable=False)   # 1-3
    requires_approval  = Column(Boolean, default=False, nullable=False)
    enabled            = Column(Boolean, default=True,  nullable=False)

    # Actual command / invocation that runs when this action executes
    # e.g. "docker exec {container} kill -{signal} {process_name}"
    command     = Column(Text, nullable=True)

    # Per-environment command overrides — keyed by adapter_mode (or "any" for fallback)
    # e.g. {"docker": "docker exec {container} ...", "kubernetes": "kubectl exec {pod} -- ...", "any": "..."}
    # Resolution order: command_variants[adapter_mode] → command_variants["any"] → command
    command_variants = Column(JSON, nullable=True)

    # Schema of expected parameters for documentation / UI rendering
    parameters  = Column(JSON, default=list, nullable=False)

    # Process-targeting rules (null = not a process action)
    # [{ "pattern": "^yes$", "description": "test process", "allow": true, "priority": 10 }, ...]
    process_rules = Column(JSON, nullable=True)

    # Structured output-extraction rules, replacing hardcoded parsing in ToolRegistryAgent._parse_tool_output.
    # [{ "field": "http_code", "kind": "regex"|"jsonpath", "pattern": "...", "type": "boolean"|"integer"|"float"|"string" }, ...]
    output_fields = Column(JSON, default=list, nullable=False)

    # True for tools seeded from approved_actions_seed.py ("out of box"). Locks output_fields editing in the UI/API.
    is_builtin = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_action_category', 'category'),
        Index('idx_action_enabled',  'enabled'),
    )


class MonitoringEventModel(Base):
    """
    Raw monitoring events from Watcher (eBPF kernel monitoring).

    These are raw signals detected by the monitoring system, separate from Incidents.
    Events go through qualification (lightweight risk check) to determine if they
    should trigger an incident workflow.

    Fields:
    - source: "watcher_brain", "sentinel_senses", "advanced_monitoring"
    - event_type: "high_syscall_intensity", "cpu_spike", "disk_full", etc.
    - raw_criticality: info, warning, critical (raw signal magnitude)
    - qualification_score: 0-100 (likelihood of needing action)
    - qualified_as_incident: whether this triggered an incident workflow
    - incident_workflow_id: FK to the incident workflow if opened
    - status: new, qualified, escalated, dismissed
    """
    __tablename__ = "monitoring_events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(100), nullable=False, index=True)  # "watcher_brain", "sentinel_senses"
    event_type = Column(String(100), nullable=False, index=True)  # "high_cpu", "disk_full", etc.
    resource_name = Column(String(255), nullable=False, index=True)  # e.g., "payment-service"

    # Raw signal from monitoring
    raw_criticality = Column(String(50), nullable=False)  # info, warning, critical
    signal_value = Column(Float, nullable=True)  # actual metric value
    signal_threshold = Column(Float, nullable=True)  # threshold that was exceeded
    anomaly_process = Column(String(255), nullable=True)  # process name if applicable

    # Full watcher payload for audit
    raw_payload = Column(JSON, nullable=False, default={})

    # Qualification results
    qualification_score = Column(Float, nullable=False)  # 0-100
    qualification_reason = Column(String(1000), nullable=True)  # human-readable reason
    qualification_factors = Column(JSON, nullable=True)  # factor breakdown for display
    confidence = Column(Float, nullable=True)  # 0-1 confidence of the qualification
    qualified_as_incident = Column(Boolean, default=False, nullable=False)

    # Link to incident workflow (if opened)
    incident_workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflow_states.workflow_id'), nullable=True, index=True)

    # Status tracking
    status = Column(String(50), nullable=False, default='new')  # new, qualified, escalated, dismissed

    # Timestamps
    detected_at = Column(DateTime, nullable=False)  # when the anomaly was detected
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_monitoring_event_type_timestamp', 'event_type', 'created_at'),
        Index('idx_monitoring_resource_timestamp', 'resource_name', 'created_at'),
        Index('idx_monitoring_status', 'status'),
        Index('idx_monitoring_incident_workflow', 'incident_workflow_id'),
    )


class EventConditionStateModel(Base):
    """
    Tracks whether a monitoring condition is currently active for a (resource, event_type) pair.

    Used for backend-side deduplication: when the same event fires repeatedly
    (qualified or dismissed), only the first is stored; subsequent arrivals are
    dropped until the condition closes.

    A condition closes when:
      - A condition_cleared signal is received for the resource
      - The linked incident is resolved/closed in the UI or by automation
      - The safety TTL expires (open longer than ttl_hours → auto-closed)
    """
    __tablename__ = "event_condition_state"

    resource_name = Column(String(255), nullable=False, primary_key=True)
    event_type    = Column(String(100),  nullable=False, primary_key=True)
    status        = Column(String(20),   nullable=False, default='open')  # open | closed
    qualified     = Column(Boolean,      nullable=False, default=False)   # True = incident opened; False = dismissed
    last_event_id = Column(UUID(as_uuid=True), nullable=True)             # most recent event row
    opened_at     = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at     = Column(DateTime, nullable=True)
    updated_at    = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_ecs_resource', 'resource_name'),
        Index('idx_ecs_status',   'status'),
        Index('idx_ecs_opened',   'opened_at'),
    )


class TargetLockModel(Base):
    """
    Per-target remediation lease — prevents two incidents from running mutating
    steps against the same resource at once. Acquired by ToolRegistryAgent
    right before its step-execution loop; released in a finally block when
    the loop exits. TTL is a crash-safety valve, not the normal release path.
    """
    __tablename__ = "target_locks"

    target_id   = Column(String(255), primary_key=True)
    incident_id = Column(UUID(as_uuid=True), nullable=False)
    acquired_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at  = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_target_locks_expires_at', 'expires_at'),
        Index('idx_target_locks_incident_id', 'incident_id'),
    )


class RiskWeightConfigModel(Base):
    """
    Configurable risk assessment weights and thresholds.

    Stores a JSON blob with weights for each risk factor:
    - ci_tier_weight: weight for CI tier (1, 2, 3)
    - business_criticality_weight: weight for service criticality
    - failover_available_weight: deduction if failover is available
    - is_spof_weight: additional weight if service is SPOF
    - avg_mttr_weight: weight for repair time
    - user_count_weight: weight for affected users
    - sla_percent_weight: weight for SLA impact
    - compliance_scope_weight: weight for regulatory impact (PCI, GDPR, HIPAA, SOC2)
    - event_criticality_weight: weight for raw signal magnitude
    - event_type_multipliers: map of event types to score multipliers
    - unknown_behavior: "neutral_default" or "exclude_from_calc"
    - qualification_threshold: score needed to qualify as incident (0-100)
    - confidence_threshold: % confidence needed to avoid forcing manual approval
    """
    __tablename__ = "risk_weight_configs"

    config_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_key = Column(String(100), nullable=False, unique=True, index=True)  # e.g., "default", "production"

    # Weights configuration as JSON
    weights = Column(JSON, nullable=False, default={})

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_config_key', 'config_key'),
    )


class LLMConfigModel(Base):
    """
    LLM provider configuration - persisted across restarts
    """
    __tablename__ = "llm_configs"

    config_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_key = Column(String(100), nullable=False, unique=True, index=True)  # e.g., "default"

    # Provider configuration
    provider = Column(String(50), nullable=False)  # "openai", "anthropic", "ollama"
    api_key = Column(EncryptedString(1500), nullable=True)  # Encrypted at rest; null for local providers
    model = Column(String(100), nullable=True)    # e.g., "gpt-3.5-turbo", "llama3"
    base_url = Column(String(500), nullable=True)  # For local providers (e.g. Ollama: http://localhost:11434)

    # Feature flags
    insights_enabled = Column(Boolean, nullable=False, default=True, server_default='true')

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_llm_config_key', 'config_key'),
    )


class PolicyModel(Base):
    """
    Incident response policies - define matching rules and approved actions.

    Policies control whether incidents auto-execute or require manual approval.
    Rules are matched against incident context to determine applicable actions.
    """
    __tablename__ = "policies"

    policy_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)

    # Matching rules (AND logic) - incident must match ALL to trigger policy
    rules = Column(JSON, nullable=False, default={})  # { "anomaly_type": [...], "service": [...], "environment": "prod", "min_severity": "high" }

    # Actions approved by policy (auto-remediation if confidence >= threshold)
    approved_actions = Column(JSON, nullable=False, default=list)  # ["restart_service", "scale_pods", "force_restart"]

    # Governance
    requires_manual_approval = Column(Boolean, default=False, nullable=False)  # If true, requires CAB/manual review even if matched
    approval_priority = Column(Integer, default=50, nullable=False)  # 1-100, lower = higher priority

    # Confidence gate — bypass manual approval once a runbook has proven reliable
    # Both fields must be set to activate the gate; either alone is ignored.
    confidence_gate_threshold = Column(Float, nullable=True)   # e.g. 0.90 → 90% runbook confidence required
    confidence_gate_min_runs  = Column(Integer, nullable=True) # e.g. 10  → must have ≥ N successful runs
    # Pin the gate to one specific runbook instead of whichever the event_type/
    # service/platform lookup cascade resolves at execution time. NULL keeps
    # the cascade-lookup behavior (auto-select best match).
    confidence_gate_runbook_id = Column(UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="SET NULL"), nullable=True)

    # Constraints (limits on what policy can do)
    constraints = Column(JSON, nullable=False, default={})  # { "max_blast_radius": 3, "max_restart_frequency": 2, "requires_post_monitoring": true }

    # Metadata
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Draft/publish workflow — see RunbookModel for the equivalent fields/rationale.
    # `enabled` above is deliberately NOT gated by this — it stays an instant kill-switch.
    status                  = Column(String(20), nullable=False, default='draft', server_default='draft')  # draft | published
    published_at            = Column(DateTime, nullable=True)
    has_unpublished_changes = Column(Boolean, nullable=False, default=False, server_default='false')
    draft_snapshot          = Column(JSON, nullable=True)

    __table_args__ = (
        Index('idx_policy_name', 'name'),
        Index('idx_policy_enabled', 'enabled'),
        Index('idx_policy_status', 'status'),
    )


class PolicyVersionModel(Base):
    """Snapshot of a policy's live fields taken each time it is published."""
    __tablename__ = "policy_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id = Column(UUID(as_uuid=True), ForeignKey("policies.policy_id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_by = Column(String(255), nullable=True)
    change_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_policy_version_policy_id', 'policy_id'),
        UniqueConstraint('policy_id', 'version', name='uq_policy_version'),
    )


class GovernancePolicyModel(Base):
    """
    Governance policies - control when remediation actions require approval.

    These are organization-level rules that gate auto-remediation based on:
    - Environment (prod, staging, dev)
    - Service name
    - Risk score
    - Severity level

    When a remediation proposal matches a governance policy condition,
    an approval request is created and the action is blocked until approved.
    """
    __tablename__ = "governance_policies"

    policy_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    description = Column(String(500), nullable=True)

    # When to trigger approval requirement
    conditions = Column(JSON, nullable=False, default={})  # {
    #   "environment": "prod",  # optional: 'prod', 'staging', 'dev'
    #   "service_name": "database",  # optional: service name or null for all
    #   "min_risk_score": 75,  # optional: minimum risk score
    #   "min_severity": "high"  # optional: 'critical', 'high', 'medium', 'low'
    # }

    # Which actions require approval
    actions_requiring_approval = Column(JSON, nullable=False, default=list)  # ["restart_service", "scale_pods", "*"] (* means all)

    # Who can approve
    approval_groups = Column(JSON, nullable=False, default=list)  # ["dba-team", "on-call", "prod-leads"]

    # Metadata
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_governance_policy_enabled', 'enabled'),
        Index('idx_governance_policy_name', 'name'),
    )


class PlatformSettingModel(Base):
    """
    Key-value store for platform-wide runtime configuration.
    Covers watcher thresholds and other operator-tunable parameters.
    """
    __tablename__ = "platform_settings"

    key = Column(String(128), primary_key=True)          # e.g. "watcher.cpu_threshold"
    value = Column(Text, nullable=False)                  # always stored as string
    value_type = Column(String(16), nullable=False, default="str")  # int | float | bool | str
    category = Column(String(64), nullable=False, index=True)       # "watcher", "celery", …
    label = Column(String(128), nullable=False, default="")
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_platform_settings_category', 'category'),
    )


class WatcherRegistrationModel(Base):
    """
    Self-registration record written by each watcher on startup.
    Enables the UI to discover available watchers and show live status.

    Identity:
      watcher_id   — immutable UUID primary key; assigned by the platform on first
                     registration and persisted by the watcher in
                     .state/watcher_identity.json.  Survives renames.
      watcher_name — human-readable, unique, used for display and Docker DNS routing.
                     Can be changed; the watcher_id remains stable.

    Registration lifecycle:
      pending  → watcher has registered but is awaiting operator approval
      approved → operator has approved; watcher may submit monitoring events
      rejected → operator has rejected; watcher will exit on next heartbeat
    """
    __tablename__ = "watcher_registrations"

    watcher_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watcher_name = Column(String(100), nullable=False, unique=True, index=True)
    display_name = Column(String(200), nullable=False, default="")
    host = Column(String(255), nullable=False, default="")
    poll_interval = Column(Integer, nullable=False, default=20)
    sentinel_container = Column(String(100), nullable=True)

    # Registration approval workflow
    registration_status = Column(String(20), nullable=False, default="pending")
    nginx_url = Column(String(500), nullable=False, default="")
    kill_api_url = Column(String(500), nullable=False, default="")
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(100), nullable=False, default="")

    # Environment & adapter
    environment      = Column(String(50),  nullable=False, default="unknown")
    adapter_mode     = Column(String(20),  nullable=False, default="docker")
    watcher_version  = Column(String(50),  nullable=True)
    targets          = Column(JSON,        nullable=True)   # JSON list of managed targets
    metrics_history  = Column(JSON,        nullable=True)   # rolling list of {ts,cpu,mem,disk,alerts}

    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WatcherExternalCheckModel(Base):
    """
    Persistent configuration for external connectivity checks (ping, HTTP, TCP, DNS, TLS)
    associated with a specific watcher instance.

    When the watcher polls the platform API it fetches these rows and replaces its
    in-memory self.external_checks list, making the UI the single source of truth.
    """
    __tablename__ = "watcher_external_checks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watcher_name = Column(String(100), nullable=False, index=True)

    # Check descriptor
    check_type = Column(String(20), nullable=False)    # ping | http | https | tcp | dns | tls
    target = Column(String(500), nullable=False)       # IP, hostname, or full URL
    name = Column(String(200), nullable=False, default="")

    # Type-specific parameters
    port = Column(Integer, nullable=True)
    expected_status = Column(Integer, nullable=False, default=200)
    timeout_ms = Column(Integer, nullable=False, default=5000)
    latency_threshold_ms = Column(Integer, nullable=False, default=0)
    tls_expiry_warning_days = Column(Integer, nullable=False, default=30)

    enabled = Column(Boolean, nullable=False, default=True)
    container_name = Column(String(200), nullable=False, default="")
    service_name   = Column(String(100), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_watcher_ext_checks_watcher', 'watcher_name'),
        Index('idx_watcher_ext_checks_enabled', 'watcher_name', 'enabled'),
    )


class LogMonitorConfigModel(Base):
    """
    Persistent configuration for log file monitoring per watcher.

    Stores patterns to match in log files, which trigger custom events that
    runbooks can then handle. Each monitor tails a specific log file, matches
    lines against a regex pattern, and emits a custom event_type.

    The watcher fetches these rows on startup and via live-push (kill-api),
    making the UI the single source of truth.
    """
    __tablename__ = "log_monitor_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watcher_name = Column(String(100), nullable=False, index=True)

    # Monitor descriptor
    name = Column(String(200), nullable=False)              # Display name, unique per watcher
    source = Column(String(20), nullable=False, default="file")     # "file" or "docker"
    file = Column(String(500), nullable=False, default="")  # Log file path (file mode)
    container = Column(String(200), nullable=False, default="")     # Container name (docker mode)
    pattern = Column(String(1000), nullable=False)          # Regex pattern to match
    event_type = Column(String(100), nullable=False)        # Event type to emit (e.g., "log_error_detected")

    # Configuration
    interval_sec = Column(Integer, nullable=False, default=5)  # Poll interval in seconds
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_log_monitor_watcher', 'watcher_name'),
        Index('idx_log_monitor_watcher_enabled', 'watcher_name', 'enabled'),
        Index('idx_log_monitor_watcher_name', 'watcher_name', 'name'),  # For uniqueness check
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Connector Hub models
# ══════════════════════════════════════════════════════════════════════════════

class ConnectorConfigModel(Base):
    """
    Stores connection settings for each registered external connector
    (e.g. ServiceNow, Splunk).  Credentials are stored as plain JSON here;
    wrap with vault/secrets-manager in production.
    """
    __tablename__ = "connector_configs"

    id                = Column(String(50),  primary_key=True)   # "servicenow", "splunk"
    display_name      = Column(String(100), nullable=False)
    enabled           = Column(Boolean,     nullable=False, default=False)
    # Flexible config bag: {base_url, username, password, sync_interval, ...}
    config_json       = Column(JSON,        nullable=False, default=dict)
    sync_interval_min = Column(Integer,     nullable=False, default=0)  # 0 = manual only
    last_sync_at      = Column(DateTime,    nullable=True)
    last_sync_status  = Column(String(20),  nullable=True)   # ok | partial | error | never
    created_at        = Column(DateTime,    default=datetime.utcnow, nullable=False)
    updated_at        = Column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SNowCIModel(Base):
    """
    Local cache of ServiceNow CMDB configuration items.
    Records are replaced on each sync (upsert by sys_id).
    """
    __tablename__ = "snow_ci_cache"

    sys_id      = Column(String(32),  primary_key=True)   # ServiceNow sys_id (32 hex chars)
    ci_class    = Column(String(100), nullable=False, index=True)  # cmdb_ci_service | cmdb_ci_server | …
    name        = Column(String(255), nullable=False, index=True)
    # All raw field values returned by SN Table API (flattened display values)
    fields_json = Column(JSON,        nullable=False, default=dict)
    synced_at   = Column(DateTime,    nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_snow_ci_class_name', 'ci_class', 'name'),
    )


class SNowSyncLogModel(Base):
    """
    Audit log of every CMDB sync operation.
    """
    __tablename__ = "snow_sync_logs"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id   = Column(String(50),  nullable=False, index=True)
    started_at     = Column(DateTime,    nullable=False)
    finished_at    = Column(DateTime,    nullable=True)
    records_pulled = Column(Integer,     nullable=False, default=0)
    status         = Column(String(20),  nullable=False)   # ok | partial | error
    error_message  = Column(Text,        nullable=True)

    __table_args__ = (
        Index('idx_snow_sync_log_connector', 'connector_id', 'started_at'),
    )


class SNowIncidentMapModel(Base):
    """
    Maps platform workflow IDs to ServiceNow incident sys_ids.
    Enables idempotent push and bi-directional status lookups.
    """
    __tablename__ = "snow_incident_map"

    platform_workflow_id = Column(String(100), primary_key=True)
    snow_sys_id          = Column(String(32),  nullable=True, index=True)
    snow_number          = Column(String(20),  nullable=True)   # "INC0012345"
    last_pushed_at       = Column(DateTime,    nullable=True)
    push_status          = Column(String(20),  nullable=True)   # created | updated | error

    __table_args__ = (
        Index('idx_snow_inc_map_sys_id', 'snow_sys_id'),
    )


class OptimizationRecommendationModel(Base):
    """
    AI-generated tuning recommendations produced by TuningAgent (Platform Intelligence).

    Each row is one concrete, actionable recommendation such as:
      "Raise qualification_threshold from 50 → 58 to reduce false positives"

    Admins accept or reject; accepted recommendations are applied to RiskWeightConfig
    and marked applied=True.

    category values:
      threshold       — qualification_threshold / confidence_threshold
      factor_weight   — factors.<name>.weight
      event_multiplier — event_type_multipliers.<type>
      missing_data    — factors.<name>.missing_data policy
      general         — narrative/informational (no direct config change)
    """
    __tablename__ = "optimization_recommendations"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category        = Column(String(50),  nullable=False, index=True)
    parameter       = Column(String(200), nullable=False)          # dotted path, e.g. "qualification_threshold"
    current_value   = Column(JSON,        nullable=True)           # value before change (null for general)
    suggested_value = Column(JSON,        nullable=True)           # proposed new value (null for general)

    title           = Column(String(255), nullable=False)
    rationale       = Column(Text,        nullable=False)
    impact          = Column(String(500), nullable=True)           # "Reduces false negatives by ~15%"
    confidence      = Column(Float,       nullable=False, default=0.7)  # 0-1
    priority        = Column(String(10),  nullable=False, default='medium')  # high | medium | low

    # Supporting evidence snapshot
    evidence        = Column(JSON,        nullable=True)  # {incidents_analysed, period_days, metric_name, metric_value, ...}

    # Lifecycle: pending → accepted / rejected
    status          = Column(String(20),  nullable=False, default='pending', index=True)
    reviewed_by     = Column(String(200), nullable=True)
    review_reason   = Column(Text,        nullable=True)
    reviewed_at     = Column(DateTime,    nullable=True)

    # Set True when config has been written
    applied         = Column(Boolean,     nullable=False, default=False)
    applied_at      = Column(DateTime,    nullable=True)

    # Auto-apply trust gating (mirrors the runbook confidence-gate pattern):
    # a parameter earns auto_apply_eligible after 3 consecutive accepted+applied+
    # verified-improved cycles. status="auto_applied" rows skip manual review.
    auto_apply_eligible        = Column(Boolean,  nullable=False, default=False)
    auto_apply_threshold_met_at = Column(DateTime, nullable=True)

    # Post-application verification close-out — did the targeted metric actually
    # improve N days after this recommendation was applied?
    outcome_verified_at = Column(DateTime, nullable=True)
    outcome_improved    = Column(Boolean,  nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at      = Column(DateTime, nullable=True)   # stale after 30 days

    __table_args__ = (
        Index('idx_rec_status_created',  'status',   'created_at'),
        Index('idx_rec_category_status', 'category', 'status'),
    )


class PlatformIntelRunModel(Base):
    """
    One row per TuningAgent analysis pass (scheduled, manual, or force refresh).

    Persists what was previously discarded the moment run_analysis() returned:
    which path produced the recommendations, the LLM's raw response (for an
    audit/debug view), and a JSONB snapshot of computed KPIs — the only way to
    plot a KPI trend over time rather than just the current instantaneous value.
    """
    __tablename__ = "platform_intel_runs"

    id                          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at                 = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    period_days                 = Column(Integer, nullable=False)
    trigger                    = Column(String(20), nullable=False)   # scheduled | manual | force_refresh
    source                     = Column(String(30), nullable=False)   # llm | rules | healthy | suppressed | insufficient_data
    incidents_analysed          = Column(Integer, nullable=False, default=0)
    recommendations_generated   = Column(Integer, nullable=False, default=0)
    recommendations_skipped     = Column(Integer, nullable=False, default=0)
    llm_raw_response            = Column(Text, nullable=True)
    kpis                        = Column(JSONB, nullable=False, default={})


class PrincipalModel(Base):
    """
    Human users and automation accounts.
    Human accounts: hashed_pw set, api_key_hash NULL.
    Automation accounts: api_key_hash set, hashed_pw NULL.
    """
    __tablename__ = "principals"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name           = Column(String(100), nullable=False)
    email          = Column(String(200), unique=True, nullable=True)
    role           = Column(String(20),  nullable=False)   # admin|operator|viewer|automation
    hashed_pw      = Column(String(200), nullable=True)    # bcrypt hash; NULL for automation
    api_key_hash   = Column(String(64),  unique=True, nullable=True)  # SHA-256 of raw key
    api_key_prefix = Column(String(16),  nullable=True)    # "ak_" + first chars shown in UI
    enabled        = Column(Boolean,     nullable=False, default=True)
    created_at     = Column(DateTime,    nullable=False, default=datetime.utcnow)
    last_seen_at   = Column(DateTime,    nullable=True)
    created_by_id  = Column(UUID(as_uuid=True), ForeignKey("principals.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("idx_principals_role",    "role"),
        Index("idx_principals_email",   "email"),
        Index("idx_principals_api_key", "api_key_hash"),
    )


class EventTypeTaxonomyModel(Base):
    """
    Canonical event-type taxonomy — the controlled vocabulary for all runbook triggers.

    Each row is one canonical event type in the form domain.resource.symptom
    (e.g., 'infrastructure.compute.cpu_high').

    Fields:
      code        : primary key — the canonical type string used everywhere
      label       : short UI label ('High CPU Utilization')
      description : one-sentence explanation of what the event means
      category    : top-level domain ('infrastructure', 'container', ...)
      aliases     : JSON list of legacy/external strings that map to this code
                    (used by the normalizer for backward compatibility)
      is_system   : True = shipped with the platform, cannot be deleted via API
      enabled     : False = hidden from dropdowns (soft-disable without delete)
      default_severity : info/warning/critical — base severity for watcher-sourced
                    events of this type (null for most rows; only watcher-native
                    types have one). Connector-sourced events never consult this —
                    each connector does its own tool-specific severity translation.
    """
    __tablename__ = "event_type_taxonomy"

    code             = Column(String(150), primary_key=True)
    label            = Column(String(200), nullable=False)
    description      = Column(Text,        nullable=True)
    category         = Column(String(50),  nullable=False, index=True)   # top-level domain
    aliases          = Column(JSON,        nullable=False, default=list)  # ["high_cpu", ...]
    is_system        = Column(Boolean,     nullable=False, default=True)
    enabled          = Column(Boolean,     nullable=False, default=True)
    default_severity = Column(String(20),  nullable=True)   # info | warning | critical | null
    created_at       = Column(DateTime,    nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_taxonomy_category",         "category"),
        Index("idx_taxonomy_enabled_category", "enabled", "category"),
    )


class PrincipalAuditLogModel(Base):
    """
    Immutable audit trail for principal management events.
    No foreign-key constraints so records survive principal deletion.
    """
    __tablename__ = "principal_audit_log"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    ts          = Column(DateTime,   nullable=False, default=datetime.utcnow)
    actor_id    = Column(UUID(as_uuid=True), nullable=True)   # who performed the action
    actor_name  = Column(String(100), nullable=True)
    action      = Column(String(50),  nullable=False)          # login|password_changed|role_changed|…
    target_id   = Column(UUID(as_uuid=True), nullable=True)   # who was affected
    target_name = Column(String(100), nullable=True)
    detail      = Column(Text, nullable=True)                  # free-text context

    __table_args__ = (
        Index("idx_audit_ts",     "ts"),
        Index("idx_audit_actor",  "actor_id"),
        Index("idx_audit_target", "target_id"),
    )


class NotificationTeamModel(Base):
    """
    Standalone notification routing target — a named team with whichever
    channels it has configured (any combination, all optional). Looked up by
    name via the `team` arg on the notify/alert_escalate/alert_update/send_alert
    runbook actions; falls back to the global PagerDuty/Slack/SMTP defaults
    when no team is given or the named team isn't found/enabled.
    """
    __tablename__ = "notification_teams"

    team_id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name                  = Column(String(100), nullable=False, unique=True, index=True)

    pagerduty_routing_key = Column(EncryptedString(500), nullable=True)
    slack_channel         = Column(String(100), nullable=True)
    email_recipients      = Column(Text, nullable=True)   # comma-separated addresses
    webhook_url           = Column(String(500), nullable=True)
    webhook_secret        = Column(EncryptedString(500), nullable=True)

    enabled               = Column(Boolean, nullable=False, default=True, server_default='true')

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_notification_team_name', 'name'),
    )


class SyntheticMonitorModel(Base):
    """
    Synthetic transaction monitor — stores a generated Python script that is
    replayed on a schedule by the watcher to validate end-to-end user journeys.

    credentials_enc stores a Fernet-encrypted JSON blob of key/value pairs
    that are injected as environment variables when the script is executed.
    """
    __tablename__ = "synthetic_monitors"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name            = Column(String(200), nullable=False, unique=True, index=True)
    har_filename    = Column(String(500), nullable=True)
    script          = Column(Text, nullable=True)
    pages_json      = Column(Text, nullable=True)   # JSON-serialized ParsedPage[] — lets the edit UI
                                                      # re-show/re-edit page assertions without the HAR file
    credentials_enc = Column(Text, nullable=True)   # Fernet-encrypted JSON {"KEY": "VALUE", ...}
    schedule_mins   = Column(Integer, nullable=False, default=15)
    enabled         = Column(Boolean, nullable=False, default=True, server_default='true')
    last_run_at     = Column(DateTime, nullable=True)
    last_status     = Column(String(20), nullable=True)   # pass | fail | error
    last_output     = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_synthetic_monitors_enabled', 'enabled'),
    )
