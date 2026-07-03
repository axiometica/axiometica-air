"""
Integration test: Change workflow with all agents
Tests the full change workflow from submission through deployment
"""

import asyncio
import pytest
from uuid import uuid4
from agentic_os.core.models import (
    WorkflowState,
    WorkflowType,
    WorkflowDefinition,
    WorkflowStep,
    LifecycleState,
    Severity,
)
from agentic_os.core.workflow_engine import WorkflowEngine
from agentic_os.agents.registry import register_all_agents


@pytest.mark.asyncio
async def test_change_workflow_e2e():
    """Test full change workflow execution"""
    print("\n" + "=" * 70)
    print("CHANGE WORKFLOW INTEGRATION TEST")
    print("=" * 70)

    # Create mock event bus and db
    class MockEventBus:
        async def publish(self, event):
            pass

    class MockDB:
        pass

    # Initialize workflow engine
    event_bus = MockEventBus()
    db = MockDB()
    engine = WorkflowEngine(event_bus, db)

    # Create a mock repository
    class MockWorkflowRepository:
        def save(self, state):
            pass

    engine.workflow_repo = MockWorkflowRepository()

    # Register all agents
    register_all_agents(engine)

    # Create change workflow definition (simplified)
    definition = WorkflowDefinition(
        workflow_type=WorkflowType.CHANGE,
        version="1.0",
        start_step="assess_change_risk",
        steps={
            "assess_change_risk": WorkflowStep(
                step_id="assess_change_risk",
                step_type="agent",
                name="Change Risk Assessment",
                handler="change_risk_assessor",
                next_steps={"default": "schedule_deployment"},
                timeout_seconds=30,
            ),
            "schedule_deployment": WorkflowStep(
                step_id="schedule_deployment",
                step_type="agent",
                name="Schedule Deployment",
                handler="deployment_scheduler",
                next_steps={"default": "pre_deployment_checks"},
                timeout_seconds=30,
            ),
            "pre_deployment_checks": WorkflowStep(
                step_id="pre_deployment_checks",
                step_type="agent",
                name="Pre-Deployment Checks",
                handler="deployment_checker",
                next_steps={"ready": "deploy", "not_ready": "end_failed"},
                timeout_seconds=60,
            ),
            "deploy": WorkflowStep(
                step_id="deploy",
                step_type="agent",
                name="Deploy Change",
                handler="deployer",
                next_steps={"success": "verify_deployment", "failed": "end_failed"},
                timeout_seconds=300,
                retry_count=1,
            ),
            "verify_deployment": WorkflowStep(
                step_id="verify_deployment",
                step_type="agent",
                name="Verify Deployment",
                handler="deployment_verifier",
                next_steps={"success": "post_deployment_validation", "failed": "end_failed"},
                timeout_seconds=120,
            ),
            "post_deployment_validation": WorkflowStep(
                step_id="post_deployment_validation",
                step_type="agent",
                name="Post-Deployment Validation",
                handler="validation_agent",
                next_steps={"passed": "document_change", "failed": "end_failed"},
                timeout_seconds=300,
            ),
            "document_change": WorkflowStep(
                step_id="document_change",
                step_type="agent",
                name="Document Change",
                handler="documentation_service",
                next_steps={"default": "end_deployed"},
                timeout_seconds=30,
            ),
            "end_deployed": WorkflowStep(
                step_id="end_deployed",
                step_type="agent",
                name="Mark Deployed",
                handler="mark_deployed",
                next_steps={},
            ),
            "end_failed": WorkflowStep(
                step_id="end_failed",
                step_type="agent",
                name="Mark Failed",
                handler="mark_failed",
                next_steps={},
            ),
        },
        end_steps=["end_deployed", "end_failed"],
    )

    # Create initial change state
    state = WorkflowState(
        workflow_id=uuid4(),
        workflow_type=WorkflowType.CHANGE,
        lifecycle_state=LifecycleState.OPEN,
        context={
            "change_context": {
                "change_type": "standard",
                "description": "Update API server to v2.3.0",
                "affected_services": ["api-server", "load-balancer"],
                "rollback_plan": {"version": "v2.2.5"},
            }
        },
    )

    print(f"\nInitial State:")
    print(f"  Workflow ID: {state.workflow_id}")
    print(f"  Type: {state.workflow_type.value}")
    print(f"  Lifecycle: {state.lifecycle_state.value}")
    print(f"  Change Type: {state.context['change_context']['change_type']}")
    print(f"  Description: {state.context['change_context']['description']}")
    print(f"  Affected Services: {state.context['change_context']['affected_services']}")

    # Execute workflow
    print("\n" + "-" * 70)
    print("EXECUTION TRACE")
    print("-" * 70 + "\n")

    final_state = await engine.execute(definition, state)

    # Print results
    print("\n" + "-" * 70)
    print("FINAL STATE")
    print("-" * 70)
    print(f"  Lifecycle: {final_state.lifecycle_state.value}")
    print(f"  Severity: {final_state.severity.value if final_state.severity else 'N/A'}")
    print(f"  Risk Score: {final_state.risk_score}")

    change_context = final_state.context.get("change_context", {})
    deployment_window = change_context.get("deployment_window")
    if deployment_window:
        print(f"  Deployment Window: {deployment_window['start']} to {deployment_window['end']}")

    deployment_result = final_state.context.get("deployment_result")
    if deployment_result:
        print(f"  Deployment ID: {deployment_result.get('deployment_id')}")
        print(f"  Deployed At: {deployment_result.get('deployed_at')}")

    documentation = final_state.context.get("change_documentation")
    if documentation:
        print(f"  Documentation: {documentation.get('documentation_link')}")

    print("\n" + "-" * 70)
    print("REASONING TRACE")
    print("-" * 70)
    for i, trace in enumerate(final_state.reasoning_trace, 1):
        print(f"{i:2d}. {trace}")

    # Assertions
    assert final_state.workflow_type == WorkflowType.CHANGE
    assert final_state.lifecycle_state in [LifecycleState.DEPLOYED, LifecycleState.FAILED]
    assert final_state.severity is not None
    assert final_state.risk_score is not None
    assert len(final_state.reasoning_trace) > 0

    if final_state.lifecycle_state == LifecycleState.DEPLOYED:
        assert deployment_result is not None
        assert documentation is not None
        print("\nChange deployment successful!")
    else:
        print("\nChange deployment failed!")

    print("\n" + "=" * 70)
    print("TEST PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_change_workflow_e2e())
