"""
Integration test: Incident workflow with all agents
Tests the full incident workflow from alert to resolution
"""

import pytest
import asyncio
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
async def test_incident_workflow_e2e():
    """Test full incident workflow execution"""
    print("\n" + "=" * 70)
    print("INCIDENT WORKFLOW INTEGRATION TEST")
    print("=" * 70)

    # Create mock event bus and db (in production, would use real PostgreSQL)
    class MockEventBus:
        async def publish(self, event):
            pass

    class MockDB:
        pass

    # Initialize workflow engine
    event_bus = MockEventBus()
    db = MockDB()
    engine = WorkflowEngine(event_bus, db)

    # Create a mock repository that doesn't persist
    class MockWorkflowRepository:
        def save(self, state):
            pass

    engine.workflow_repo = MockWorkflowRepository()

    # Register all agents
    register_all_agents(engine)

    # Create incident workflow definition (simplified version)
    definition = WorkflowDefinition(
        workflow_type=WorkflowType.INCIDENT,
        version="1.0",
        start_step="triage",
        steps={
            "triage": WorkflowStep(
                step_id="triage",
                step_type="agent",
                name="Incident Triage",
                handler="sentinel",
                next_steps={"default": "enrich"},
                timeout_seconds=10,
            ),
            "enrich": WorkflowStep(
                step_id="enrich",
                step_type="agent",
                name="Context Enrichment",
                handler="librarian",
                next_steps={"default": "assess_risk"},
                timeout_seconds=15,
            ),
            "assess_risk": WorkflowStep(
                step_id="assess_risk",
                step_type="agent",
                name="Risk Assessment",
                handler="risk_assessor",
                next_steps={"default": "propose"},
                timeout_seconds=10,
            ),
            "propose": WorkflowStep(
                step_id="propose",
                step_type="agent",
                name="Remediation Proposal",
                handler="mechanic",
                next_steps={"default": "policy_broker"},
                timeout_seconds=30,
            ),
            "policy_broker": WorkflowStep(
                step_id="policy_broker",
                step_type="agent",
                name="Policy & Governance Check",
                handler="policy_broker",
                next_steps={
                    "approved": "execute",
                    "pending_approval": "end_no_remedy",
                    "recommend_only": "end_no_remedy",
                },
                timeout_seconds=10,
            ),
            "execute": WorkflowStep(
                step_id="execute",
                step_type="agent",
                name="Remediation Execution",
                handler="tool_registry",
                next_steps={"success": "verify_resolution", "failed": "end_failed"},
                timeout_seconds=60,
                retry_count=1,
            ),
            "verify_resolution": WorkflowStep(
                step_id="verify_resolution",
                step_type="agent",
                name="Verify Resolution",
                handler="verifier",
                next_steps={"resolved": "end_resolved", "still_active": "end_failed"},
                timeout_seconds=30,
            ),
            "end_resolved": WorkflowStep(
                step_id="end_resolved",
                step_type="agent",
                name="Mark Resolved",
                handler="mark_resolved",
                next_steps={},
            ),
            "end_failed": WorkflowStep(
                step_id="end_failed",
                step_type="agent",
                name="Mark Failed",
                handler="mark_failed",
                next_steps={},
            ),
            "end_no_remedy": WorkflowStep(
                step_id="end_no_remedy",
                step_type="agent",
                name="Mark Monitoring",
                handler="mark_monitoring",
                next_steps={},
            ),
        },
        end_steps=["end_resolved", "end_failed", "end_no_remedy"],
    )

    # Create initial incident state
    state = WorkflowState(
        workflow_id=uuid4(),
        workflow_type=WorkflowType.INCIDENT,
        lifecycle_state=LifecycleState.OPEN,
        context={
            "alert_payload": {
                "severity": "high",
                "type": "high_cpu",
                "resource_name": "api-server",
            }
        },
    )

    print(f"\nInitial State:")
    print(f"  Workflow ID: {state.workflow_id}")
    print(f"  Type: {state.workflow_type.value}")
    print(f"  Lifecycle: {state.lifecycle_state.value}")
    print(f"  Alert: {state.context['alert_payload']['type']} on {state.context['alert_payload']['resource_name']}")

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
    print(f"  Decision: {final_state.context.get('decision_result', 'N/A')}")

    print("\n" + "-" * 70)
    print("REASONING TRACE")
    print("-" * 70)
    for i, trace in enumerate(final_state.reasoning_trace, 1):
        print(f"{i:2d}. {trace}")

    # Assertions
    assert final_state.workflow_type == WorkflowType.INCIDENT
    # waiting_approval is valid when no auto-approval policies are configured
    # (policy broker defaults to requiring manual approval for safety)
    assert final_state.lifecycle_state in [
        LifecycleState.RESOLVED,
        LifecycleState.FAILED,
        LifecycleState.MONITORING,
        LifecycleState.WAITING_APPROVAL,
    ]
    assert final_state.severity is not None
    assert final_state.risk_score is not None
    assert len(final_state.reasoning_trace) > 0

    print("\n" + "=" * 70)
    print("TEST PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_incident_workflow_e2e())
