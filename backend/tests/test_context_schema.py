"""
Unit tests for context schema dataclasses and conversion methods.
Tests schema validation, object creation, and dict/from_dict roundtrip.
"""

import pytest
from datetime import datetime
from agentic_os.core.context_schema import (
    LifecycleState,
    AlertPayload,
    SentinelContext,
    ResourceInfo,
    CMDBContext,
    RiskBreakdown,
    RiskContext,
    RunbookStep,
    Proposal,
    GovernanceContext,
    VerificationResult,
    VerificationContext,
    IncidentWorkflowContext,
)


class TestAlertPayload:
    """Test AlertPayload dataclass."""

    def test_alert_payload_creation(self):
        """Test creating AlertPayload with all fields."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Excessive syscalls detected",
            severity="critical",
            anomaly_process="yes",
        )
        assert alert.type == "high_syscall_intensity"
        assert alert.message == "Excessive syscalls detected"
        assert alert.severity == "critical"
        assert alert.anomaly_process == "yes"

    def test_alert_payload_with_none_values(self):
        """Test AlertPayload with optional fields as None."""
        alert = AlertPayload(type="test", message="test message")
        assert alert.severity is None
        assert alert.anomaly_process is None


class TestSentinelContext:
    """Test SentinelContext dataclass."""

    def test_sentinel_context_creation(self):
        """Test creating SentinelContext."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Test anomaly",
            severity="high",
            anomaly_process="yes",
        )
        ctx = SentinelContext(
            detected_anomaly="syscall_spike",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=0.95,
        )
        assert ctx.anomaly_type == "high_syscall_intensity"
        assert ctx.alert_payload.anomaly_process == "yes"
        assert ctx.confidence == 0.95


class TestResourceInfo:
    """Test ResourceInfo dataclass."""

    def test_resource_info_creation(self):
        """Test creating ResourceInfo with all fields."""
        resource = ResourceInfo(
            name="service-1",
            type="pod",
            status="operational",
            owner="team-a",
            environment="prod",
            criticality="critical",
        )
        assert resource.name == "service-1"
        assert resource.environment == "prod"
        assert resource.criticality == "critical"

    def test_resource_info_without_criticality(self):
        """Test ResourceInfo without optional criticality."""
        resource = ResourceInfo(
            name="service-1",
            type="pod",
            status="operational",
            owner="team-a",
            environment="dev",
        )
        assert resource.criticality is None
        assert resource.environment == "dev"


class TestCMDBContext:
    """Test CMDBContext dataclass."""

    def test_cmdb_context_creation(self):
        """Test creating CMDBContext with ResourceInfo."""
        resource = ResourceInfo(
            name="service-1",
            type="pod",
            status="operational",
            owner="team-a",
            environment="prod",
        )
        cmdb = CMDBContext(
            resource_name="service-1",
            resource_info=resource,
            environment="prod",
            dependencies=[{"name": "db-1", "type": "database"}],
            impacted_services=[{"name": "api-1", "criticality": "critical"}],
        )
        assert cmdb.resource_name == "service-1"
        assert cmdb.environment == "prod"
        assert len(cmdb.dependencies) == 1
        assert len(cmdb.impacted_services) == 1

    def test_cmdb_context_environment_extracted(self):
        """Test that environment can be extracted from resource_info."""
        resource = ResourceInfo(
            name="service-1",
            type="pod",
            status="operational",
            owner="team-a",
            environment="staging",
        )
        cmdb = CMDBContext(
            resource_name="service-1",
            resource_info=resource,
            environment=resource.environment,  # Explicitly set from resource_info
        )
        assert cmdb.environment == "staging"


class TestRiskContext:
    """Test RiskContext dataclass."""

    def test_risk_context_creation(self):
        """Test creating RiskContext with breakdown."""
        breakdown = RiskBreakdown(
            severity_score=8.0,
            resource_criticality_score=7.0,
            dependency_impact_score=6.0,
            business_impact_score=7.5,
        )
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )
        assert risk.risk_score == 7.5
        assert risk.blast_radius == 2
        assert risk.risk_breakdown.severity_score == 8.0


class TestProposal:
    """Test Proposal dataclass."""

    def test_proposal_creation(self):
        """Test creating Proposal with runbook steps."""
        diag_step = RunbookStep(
            order=1,
            type="diagnostic",
            name="Analyze Syscalls",
            description="Check syscall rate",
            tool="syscall_profiler",
            args_json={"process_name": "yes", "timeframe_seconds": 10},
        )
        rem_step = RunbookStep(
            order=1,
            type="remediation",
            name="Kill Process",
            description="Terminate anomalous process",
            tool="process_kill",
            args_json={"process_name": "yes", "signal": "SIGKILL"},
        )
        proposal = Proposal(
            runbook_id="rb-123",
            runbook_name="High Syscall Intensity - Process Termination",
            diagnostics_steps=[diag_step],
            remediation_steps=[rem_step],
            confidence=0.94,
            blast_radius=1,
            approval_required=True,
            main_args={"process_name": "yes", "signal": "SIGKILL"},
        )
        assert proposal.runbook_name == "High Syscall Intensity - Process Termination"
        assert len(proposal.diagnostics_steps) == 1
        assert proposal.main_args["process_name"] == "yes"


class TestGovernanceContext:
    """Test GovernanceContext dataclass."""

    def test_governance_context_creation(self):
        """Test creating GovernanceContext."""
        governance = GovernanceContext(
            matching_policies=[{"name": "prod-diagnostics-only"}],
            approval_required=True,
            approval_priority=10,
            allowed_actions=["process_kill"],
            blast_radius_limit=2,
            requires_post_monitoring=True,
            decision_notes="Approved for diagnostics phase only",
        )
        assert governance.approval_required is True
        assert governance.approval_priority == 10
        assert len(governance.allowed_actions) == 1


class TestVerificationContext:
    """Test VerificationContext dataclass."""

    def test_verification_context_creation(self):
        """Test creating VerificationContext with results."""
        result = VerificationResult(
            step_name="Verify Process Termination",
            status="passed",
            metric="process_exists",
            actual_value=0.0,
            threshold=0.0,
            message="Process no longer running",
        )
        verification = VerificationContext(
            verification_results=[result],
            overall_success=True,
            remediation_effective=True,
            issues_resolved=True,
        )
        assert verification.overall_success is True
        assert len(verification.verification_results) == 1


class TestIncidentWorkflowContext:
    """Test IncidentWorkflowContext dataclass."""

    def test_empty_context(self):
        """Test creating empty IncidentWorkflowContext."""
        ctx = IncidentWorkflowContext()
        assert ctx.sentinel is None
        assert ctx.cmdb is None
        assert ctx.risk is None
        assert ctx.proposal is None
        assert ctx.governance is None
        assert len(ctx.execution_results) == 0
        assert len(ctx.reasoning_trace) == 0

    def test_context_with_sentinel(self):
        """Test context with sentinel layer populated."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Excessive syscalls",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="syscall_spike",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=0.95,
        )
        ctx = IncidentWorkflowContext(sentinel=sentinel)
        assert ctx.sentinel is not None
        assert ctx.sentinel.anomaly_type == "high_syscall_intensity"

    def test_get_environment_default(self):
        """Test get_environment with default fallback."""
        ctx = IncidentWorkflowContext()
        assert ctx.get_environment() == "dev"

    def test_get_environment_from_cmdb(self):
        """Test get_environment extracts from CMDB context."""
        resource = ResourceInfo(
            name="svc", type="pod", status="op", owner="team", environment="prod"
        )
        cmdb = CMDBContext(resource_name="svc", resource_info=resource, environment="prod")
        ctx = IncidentWorkflowContext(cmdb=cmdb)
        assert ctx.get_environment() == "prod"

    def test_get_anomaly_process(self):
        """Test get_anomaly_process from sentinel."""
        alert = AlertPayload(
            type="test",
            message="test",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="test",
            anomaly_type="test",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=1.0,
        )
        ctx = IncidentWorkflowContext(sentinel=sentinel)
        assert ctx.get_anomaly_process() == "yes"

    def test_get_anomaly_process_none(self):
        """Test get_anomaly_process returns None when not set."""
        ctx = IncidentWorkflowContext()
        assert ctx.get_anomaly_process() is None

    def test_get_risk_score(self):
        """Test get_risk_score from risk context."""
        breakdown = RiskBreakdown(7.0, 7.0, 7.0, 7.0)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )
        ctx = IncidentWorkflowContext(risk=risk)
        assert ctx.get_risk_score() == 7.5

    def test_get_blast_radius(self):
        """Test get_blast_radius from risk context."""
        breakdown = RiskBreakdown(7.0, 7.0, 7.0, 7.0)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=3,
            remediation_complexity="complex",
        )
        ctx = IncidentWorkflowContext(risk=risk)
        assert ctx.get_blast_radius() == 3


class TestContextConversion:
    """Test to_dict and from_dict conversion methods."""

    def test_full_context_to_dict(self):
        """Test converting full context to dict."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Excessive syscalls",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="syscall_spike",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=0.95,
        )
        resource = ResourceInfo(
            name="svc", type="pod", status="op", owner="team", environment="prod"
        )
        cmdb = CMDBContext(resource_name="svc", resource_info=resource, environment="prod")
        breakdown = RiskBreakdown(8.0, 7.0, 6.0, 7.5)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )

        ctx = IncidentWorkflowContext(
            sentinel=sentinel,
            cmdb=cmdb,
            risk=risk,
            reasoning_trace=["Step 1", "Step 2"],
        )

        ctx_dict = ctx.to_dict()

        assert ctx_dict["sentinel"]["anomaly_type"] == "high_syscall_intensity"
        assert ctx_dict["cmdb"]["environment"] == "prod"
        assert ctx_dict["risk"]["risk_score"] == 7.5
        assert len(ctx_dict["reasoning_trace"]) == 2

    def test_context_roundtrip_sentinel_only(self):
        """Test converting context with only sentinel, roundtrip."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Test",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="test",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=0.95,
        )
        ctx = IncidentWorkflowContext(sentinel=sentinel)

        # Convert to dict and back
        ctx_dict = ctx.to_dict()
        ctx_restored = IncidentWorkflowContext.from_dict(ctx_dict)

        # Verify restored context matches original
        assert ctx_restored.sentinel is not None
        assert ctx_restored.sentinel.anomaly_type == ctx.sentinel.anomaly_type
        assert ctx_restored.sentinel.alert_payload.anomaly_process == "yes"

    def test_context_roundtrip_full(self):
        """Test converting full context, roundtrip."""
        # Build full context
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Excessive syscalls",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="syscall_spike",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=0.95,
        )
        resource = ResourceInfo(
            name="svc", type="pod", status="operational", owner="team-a", environment="prod"
        )
        cmdb = CMDBContext(
            resource_name="svc",
            resource_info=resource,
            environment="prod",
            dependencies=[{"name": "db"}],
        )
        breakdown = RiskBreakdown(8.0, 7.0, 6.0, 7.5)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )
        governance = GovernanceContext(
            matching_policies=[{"name": "prod-policy"}],
            approval_required=True,
            approval_priority=10,
            allowed_actions=["process_kill"],
        )

        ctx = IncidentWorkflowContext(
            sentinel=sentinel,
            cmdb=cmdb,
            risk=risk,
            governance=governance,
            execution_results=[{"tool": "process_kill", "status": "success"}],
            reasoning_trace=["Detected", "Assessed", "Approved"],
        )

        # Roundtrip
        ctx_dict = ctx.to_dict()
        ctx_restored = IncidentWorkflowContext.from_dict(ctx_dict)

        # Verify all layers match
        assert ctx_restored.sentinel.anomaly_type == "high_syscall_intensity"
        assert ctx_restored.cmdb.environment == "prod"
        assert ctx_restored.risk.risk_score == 7.5
        assert ctx_restored.governance.approval_required is True
        assert len(ctx_restored.execution_results) == 1
        assert len(ctx_restored.reasoning_trace) == 3

    def test_context_roundtrip_with_none_values(self):
        """Test roundtrip preserves None optional values."""
        resource = ResourceInfo(
            name="svc",
            type="pod",
            status="operational",
            owner="team",
            environment="dev",
            criticality=None,
        )
        cmdb = CMDBContext(resource_name="svc", resource_info=resource, environment="dev")

        ctx = IncidentWorkflowContext(cmdb=cmdb)

        ctx_dict = ctx.to_dict()
        ctx_restored = IncidentWorkflowContext.from_dict(ctx_dict)

        assert ctx_restored.cmdb.resource_info.criticality is None
        assert ctx_restored.sentinel is None
        assert ctx_restored.risk is None


class TestLifecycleStateEnum:
    """Test LifecycleState enum."""

    def test_lifecycle_state_values(self):
        """Test LifecycleState enum has expected values."""
        assert LifecycleState.SUBMITTED.value == "submitted"
        assert LifecycleState.TRIAGING.value == "triaging"
        assert LifecycleState.DIAGNOSTICS.value == "diagnostics"
        assert LifecycleState.WAITING_APPROVAL.value == "waiting_approval"
        assert LifecycleState.RESOLVED.value == "resolved"
        assert LifecycleState.FAILED.value == "failed"
