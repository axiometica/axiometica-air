"""Example tests demonstrating agent usage in workflows"""

import asyncio
from uuid import uuid4
from datetime import datetime

from agentic_os.core.models import (
    WorkflowState,
    WorkflowType,
    Severity,
    LifecycleState,
)
from agentic_os.agents.incident_agents import (
    SentinelAgent,
    LibrarianAgent,
    RiskAssessorAgent,
    MechanicAgent,
    PolicyBrokerAgent,
    ToolRegistryAgent,
    VerifierAgent,
)
from agentic_os.agents.change_agents import (
    ChangeRiskAssessorAgent,
    DeploymentSchedulerAgent,
    DeploymentCheckerAgent,
    DeployerAgent,
    ValidationAgent,
)


async def test_incident_workflow():
    """Test incident workflow agents"""
    print("\n[TEST] Incident Workflow\n")

    # Create initial state with alert
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

    print(f"Initial state: {state.workflow_id}")
    print(f"Lifecycle: {state.lifecycle_state.value}\n")

    # Run through agents
    agents = [
        SentinelAgent(),
        LibrarianAgent(),
        RiskAssessorAgent(),
        MechanicAgent(),
        PolicyBrokerAgent(),
        ToolRegistryAgent(),
        VerifierAgent(),
    ]

    for agent in agents:
        state = await agent.run(state)
        print(f"[{agent.name}] {state.reasoning_trace[-1] if state.reasoning_trace else ''}")

    print(f"\nFinal state:")
    print(f"  Severity: {state.severity.value}")
    print(f"  Risk Score: {state.risk_score}")
    print(f"  Decision: {state.context.get('decision_result')}")
    print(f"  Trace entries: {len(state.reasoning_trace)}")


async def test_change_workflow():
    """Test change workflow agents"""
    print("\n[TEST] Change Workflow\n")

    # Create initial state with change request
    state = WorkflowState(
        workflow_id=uuid4(),
        workflow_type=WorkflowType.CHANGE,
        lifecycle_state=LifecycleState.OPEN,
        context={
            "change_context": {
                "change_type": "standard",
                "affected_services": ["api-server", "database"],
                "rollback_plan": {"version": "v1.2.3"},
            }
        },
    )

    print(f"Initial state: {state.workflow_id}")
    print(f"Lifecycle: {state.lifecycle_state.value}\n")

    # Run through agents (abbreviated for demo)
    agents = [
        ChangeRiskAssessorAgent(),
        DeploymentSchedulerAgent(),
        DeploymentCheckerAgent(),
        DeployerAgent(),
        ValidationAgent(),
    ]

    for agent in agents:
        state = await agent.run(state)
        print(f"[{agent.name}] {state.reasoning_trace[-1] if state.reasoning_trace else ''}")

    print(f"\nFinal state:")
    print(f"  Severity: {state.severity.value}")
    print(f"  Risk Score: {state.risk_score}")
    print(f"  Deployment window: {state.context.get('change_context', {}).get('deployment_window', {}).get('start')}")
    print(f"  Deployment ID: {state.context.get('deployment_result', {}).get('deployment_id')}")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("AGENT FRAMEWORK TEST SUITE")
    print("=" * 60)

    await test_incident_workflow()
    await test_change_workflow()

    print("\n" + "=" * 60)
    print("All tests completed successfully")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
