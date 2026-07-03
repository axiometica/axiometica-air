"""
Regression tests for incident workflow agents with typed context.
Tests that agent pipeline still works correctly after refactoring.
"""

import pytest
from datetime import datetime
from uuid import uuid4
from agentic_os.core.models import WorkflowState, WorkflowType, LifecycleState, Severity
from agentic_os.core.context_schema import (
    IncidentWorkflowContext,
    SentinelContext,
    AlertPayload,
    CMDBContext,
    ResourceInfo,
    RiskContext,
    RiskBreakdown,
)


@pytest.fixture
def sample_incident_workflow():
    """Create a sample incident workflow for testing."""
    workflow = WorkflowState(
        workflow_type=WorkflowType.INCIDENT,
        workflow_id=uuid4(),
        lifecycle_state=LifecycleState.OPEN,
        context={
            "alert_payload": {
                "type": "high_syscall_intensity",
                "message": "Excessive syscalls detected",
                "severity": "high",
                "resource_name": "yes-service",
                "anomaly_process": "yes",
            }
        },
    )
    return workflow


class TestContextBuildup:
    """Test that context is built up correctly by agents."""

    def test_sentinel_layer(self, sample_incident_workflow):
        """Test SentinelAgent adds sentinel context layer."""
        workflow = sample_incident_workflow

        # Create sentinel context
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Excessive syscalls detected",
            severity="high",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="syscall_spike",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp=datetime.utcnow().isoformat(),
            confidence=0.95,
        )

        # Create context with sentinel
        ctx = IncidentWorkflowContext(sentinel=sentinel)

        # Simulate SentinelAgent setting typed context
        workflow.set_context(ctx)

        # Verify context persists and can be retrieved
        assert workflow.context is not None
        assert workflow.context["sentinel"]["anomaly_type"] == "high_syscall_intensity"

        # Verify can reconstruct from dict
        ctx_restored = workflow.get_context()
        assert ctx_restored.sentinel.anomaly_type == "high_syscall_intensity"
        assert ctx_restored.sentinel.alert_payload.anomaly_process == "yes"

    def test_librarian_layer(self, sample_incident_workflow):
        """Test LibrarianAgent adds cmdb context layer."""
        workflow = sample_incident_workflow

        # Start with sentinel
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Test",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="test",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp=datetime.utcnow().isoformat(),
            confidence=0.95,
        )
        ctx = IncidentWorkflowContext(sentinel=sentinel)

        # Add CMDB layer (LibrarianAgent)
        resource = ResourceInfo(
            name="yes-service",
            type="pod",
            status="operational",
            owner="platform-team",
            environment="prod",
            criticality="critical",
        )
        cmdb = CMDBContext(
            resource_name="yes-service",
            resource_info=resource,
            environment="prod",  # Extracted from resource_info
            dependencies=[{"name": "db", "type": "database"}],
            impacted_services=[{"name": "api", "criticality": "critical"}],
        )
        ctx.cmdb = cmdb

        # Persist
        workflow.set_context(ctx)

        # Verify both layers
        ctx_restored = workflow.get_context()
        assert ctx_restored.sentinel is not None
        assert ctx_restored.cmdb is not None
        assert ctx_restored.cmdb.environment == "prod"
        assert ctx_restored.get_environment() == "prod"

    def test_risk_layer(self, sample_incident_workflow):
        """Test RiskAssessor adds risk context layer."""
        workflow = sample_incident_workflow

        # Build up context through sentinel and librarian
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Test",
            anomaly_process="yes",
        )
        sentinel = SentinelContext(
            detected_anomaly="test",
            anomaly_type="high_syscall_intensity",
            alert_payload=alert,
            timestamp=datetime.utcnow().isoformat(),
            confidence=0.95,
        )
        resource = ResourceInfo(
            name="yes-service",
            type="pod",
            status="operational",
            owner="platform-team",
            environment="prod",
        )
        cmdb = CMDBContext(
            resource_name="yes-service",
            resource_info=resource,
            environment="prod",
        )

        # Add risk layer (RiskAssessor)
        breakdown = RiskBreakdown(
            severity_score=8.0,
            resource_criticality_score=7.5,
            dependency_impact_score=6.0,
            business_impact_score=7.5,
        )
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )

        ctx = IncidentWorkflowContext(sentinel=sentinel, cmdb=cmdb, risk=risk)
        workflow.set_context(ctx)
        workflow.risk_score = 7.5  # RiskAssessor also sets state.risk_score

        # Verify all three layers
        ctx_restored = workflow.get_context()
        assert ctx_restored.sentinel is not None
        assert ctx_restored.cmdb is not None
        assert ctx_restored.risk is not None
        assert ctx_restored.risk.risk_score == 7.5
        assert workflow.risk_score == 7.5

    def test_context_roundtrip_preserves_all_layers(self, sample_incident_workflow):
        """Test that full context survives serialization roundtrip."""
        workflow = sample_incident_workflow

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
            name="yes-service",
            type="pod",
            status="operational",
            owner="team",
            environment="prod",
        )
        cmdb = CMDBContext(
            resource_name="yes-service",
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

        ctx = IncidentWorkflowContext(
            sentinel=sentinel,
            cmdb=cmdb,
            risk=risk,
            execution_results=[{"tool": "test", "status": "success"}],
            reasoning_trace=["Step 1", "Step 2"],
        )

        # Set in workflow
        workflow.set_context(ctx)

        # Simulate database persistence/loading
        context_dict = workflow.context
        assert isinstance(context_dict, dict)

        # Create new workflow and load from dict
        workflow2 = WorkflowState(
            workflow_type=WorkflowType.INCIDENT,
            context=context_dict,
        )

        # Verify all layers reconstructed correctly
        ctx2 = workflow2.get_context()
        assert ctx2.sentinel.anomaly_type == "high_syscall_intensity"
        assert ctx2.cmdb.environment == "prod"
        assert ctx2.risk.risk_score == 7.5
        assert len(ctx2.execution_results) == 1
        assert len(ctx2.reasoning_trace) == 2


class TestEnvironmentMatching:
    """Test that environment field is correctly propagated for policy matching."""

    def test_environment_available_for_policy_matching(self):
        """Test environment is accessible via get_environment() for policy matching."""
        resource = ResourceInfo(
            name="svc",
            type="pod",
            status="operational",
            owner="team",
            environment="prod",
        )
        cmdb = CMDBContext(
            resource_name="svc",
            resource_info=resource,
            environment="prod",
        )
        ctx = IncidentWorkflowContext(cmdb=cmdb)

        # PolicyBrokerAgent will call this to get environment for policy matching
        environment = ctx.get_environment()
        assert environment == "prod"

    def test_environment_fallback_to_dev(self):
        """Test environment defaults to 'dev' when CMDB not available."""
        ctx = IncidentWorkflowContext()
        assert ctx.get_environment() == "dev"

    def test_environment_extraction_from_nested_resource_info(self):
        """Test that environment is correctly extracted from nested resource_info."""
        # Simulate what LibrarianAgent does
        raw_resource_info = {
            "name": "api-service",
            "type": "container",
            "status": "healthy",
            "owner": "backend-team",
            "environment": "staging",
        }

        # Extract environment (LibrarianAgent pattern)
        environment = raw_resource_info.get("environment", "dev")

        # Create structured ResourceInfo
        resource = ResourceInfo(
            name=raw_resource_info["name"],
            type=raw_resource_info["type"],
            status=raw_resource_info["status"],
            owner=raw_resource_info["owner"],
            environment=environment,
        )

        # Create CMDBContext with extracted environment
        cmdb = CMDBContext(
            resource_name=raw_resource_info["name"],
            resource_info=resource,
            environment=environment,
        )

        ctx = IncidentWorkflowContext(cmdb=cmdb)
        assert ctx.get_environment() == "staging"


class TestProcessNamePropagation:
    """Test that process name is correctly propagated for kill actions."""

    def test_anomaly_process_available_for_kill_actions(self):
        """Test process name is accessible for process_kill tool."""
        alert = AlertPayload(
            type="high_syscall_intensity",
            message="Process causing syscalls",
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

        # ToolRegistryAgent will call this to get process name
        process_name = ctx.get_anomaly_process()
        assert process_name == "yes"

    def test_process_name_in_proposal_main_args(self):
        """Test that process name is in proposal.main_args for execution."""
        # Simulate MechanicAgent creating proposal with resolved process name
        from agentic_os.core.context_schema import RunbookStep, Proposal

        diag_step = RunbookStep(
            order=1,
            type="diagnostic",
            name="Check Process",
            description="Verify process",
            tool="process_info",
            args_json={"process_name": "yes"},
        )
        rem_step = RunbookStep(
            order=1,
            type="remediation",
            name="Kill Process",
            description="Kill process",
            tool="process_kill",
            args_json={"process_name": "yes", "signal": "SIGKILL"},
        )
        proposal = Proposal(
            runbook_id="rb-123",
            runbook_name="Kill Process",
            diagnostics_steps=[diag_step],
            remediation_steps=[rem_step],
            confidence=0.94,
            blast_radius=1,
            approval_required=True,
            main_args={"process_name": "yes", "signal": "SIGKILL"},
        )

        ctx = IncidentWorkflowContext(proposal=proposal)

        # Verify process name is in main_args for execution
        assert ctx.proposal.main_args["process_name"] == "yes"


class TestBlastRadiusAccuracy:
    """Test that blast radius is read from correct location."""

    def test_blast_radius_from_risk_context(self):
        """Test blast radius is available from risk context for governance checks."""
        breakdown = RiskBreakdown(7.0, 7.0, 7.0, 7.0)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=2,
            remediation_complexity="moderate",
        )
        ctx = IncidentWorkflowContext(risk=risk)

        # PolicyBrokerAgent will check this for governance constraints
        blast_radius = ctx.get_blast_radius()
        assert blast_radius == 2

    def test_blast_radius_comparison_with_limit(self):
        """Test blast radius can be compared with governance limit."""
        breakdown = RiskBreakdown(7.0, 7.0, 7.0, 7.0)
        risk = RiskContext(
            risk_score=7.5,
            risk_breakdown=breakdown,
            blast_radius=3,
            remediation_complexity="complex",
        )
        governance_limit = 2  # Policy says max blast radius 2

        ctx = IncidentWorkflowContext(risk=risk)

        # Check if blast radius exceeds limit
        actual_radius = ctx.get_blast_radius()
        exceeds_limit = actual_radius > governance_limit

        assert exceeds_limit is True
        assert actual_radius == 3


class TestWorkflowStateIntegration:
    """Test WorkflowState integration with typed context."""

    def test_workflow_state_context_schema_field(self):
        """Test WorkflowState has context_schema field."""
        workflow = WorkflowState(workflow_type=WorkflowType.INCIDENT)
        assert hasattr(workflow, "context_schema")
        assert workflow.context_schema is None

    def test_workflow_state_set_get_typed_context(self):
        """Test WorkflowState get_context and set_context methods."""
        workflow = WorkflowState(workflow_type=WorkflowType.INCIDENT)

        alert = AlertPayload(type="test", message="test", anomaly_process="test")
        sentinel = SentinelContext(
            detected_anomaly="test",
            anomaly_type="test",
            alert_payload=alert,
            timestamp="2026-05-12T10:00:00Z",
            confidence=1.0,
        )
        ctx = IncidentWorkflowContext(sentinel=sentinel)

        # Set typed context
        workflow.set_context(ctx)

        # Verify both typed and untyped fields are populated
        assert workflow.context_schema is not None
        assert workflow.context is not None
        assert workflow.context["sentinel"] is not None

        # Verify can retrieve typed context
        ctx_retrieved = workflow.get_context()
        assert ctx_retrieved.sentinel.anomaly_type == "test"

    def test_workflow_state_backward_compat_untyped_dict(self):
        """Test WorkflowState still works with untyped context dict."""
        workflow = WorkflowState(
            workflow_type=WorkflowType.INCIDENT,
            context={
                "alert_payload": {
                    "type": "test",
                    "message": "test alert",
                }
            },
        )

        # Verify can still access via untyped dict
        alert = workflow.context.get("alert_payload")
        assert alert["type"] == "test"

        # Verify can still set via untyped dict
        workflow.context["custom_field"] = "custom_value"
        assert workflow.context["custom_field"] == "custom_value"
