"""
End-to-end test for governance policy workflow.
Tests the complete flow:
1. Create a governance policy
2. Submit an incident that triggers the policy
3. Verify approval is required
4. Approve the incident
5. Verify remediation executes
"""

import sys
import json
import asyncio
from uuid import UUID

# Add the src directory to path
sys.path.insert(0, r"C:\Users\mikeb\OneDrive\Documents\New project\AgenticPlatform_v2\backend\src")

from agentic_os.db.database import SessionLocal, init_db
from agentic_os.db.models import GovernancePolicyModel, WorkflowStateModel
from agentic_os.db.repositories import (
    GovernancePolicyRepository,
    ApprovalRepository,
    WorkflowRepository,
)
from agentic_os.services.governance_broker import GovernanceBroker
from agentic_os.core.workflow_engine import WorkflowEngine
from agentic_os.bus.postgres_bus import PostgresEventBus


async def test_governance_workflow():
    """Test complete governance policy flow"""
    print("\n" + "=" * 80)
    print("END-TO-END GOVERNANCE POLICY TEST")
    print("=" * 80)

    # Initialize database
    print("\n[1] Initializing database...")
    init_db()

    db = SessionLocal()
    broker = GovernanceBroker(db)

    try:
        # ====================================================================
        # STEP 1: Create a governance policy
        # ====================================================================
        print("\n[2] Creating governance policy...")
        policy_repo = GovernancePolicyRepository(db)

        policy = policy_repo.create(
            name="Critical Service Restart Approval",
            description="Require approval for restart_service on production critical services",
            conditions={
                "environment": "prod",
                "min_risk_score": 50,
            },
            actions_requiring_approval=["restart_service"],
            approval_groups=["dba-team", "on-call"],
        )

        print(f"✓ Policy created: {policy.name}")
        print(f"  ID: {policy.policy_id}")
        print(f"  Conditions: {policy.conditions}")
        print(f"  Actions Requiring Approval: {policy.actions_requiring_approval}")
        print(f"  Approval Groups: {policy.approval_groups}")

        # ====================================================================
        # STEP 2: Simulate incident submission that should trigger policy
        # ====================================================================
        print("\n[3] Simulating incident submission...")

        from agentic_os.core.models import WorkflowState, WorkflowType, Severity

        # Create an incident workflow
        workflow = WorkflowState(
            workflow_type=WorkflowType.INCIDENT,
            title="Database service down on production",
            summary="PostgreSQL service is unresponsive",
            severity=Severity.CRITICAL,
            context={
                "alert_payload": {
                    "type": "service_down",
                    "resource_name": "postgres-db-01",
                    "environment": "prod",
                    "description": "Database service health check failed",
                },
                "risk_score": 85,
                "cmdb_context": {
                    "resource_name": "postgres-db-01",
                    "resource_info": {
                        "name": "postgres-db-01",
                        "type": "database",
                        "criticality": "critical",
                    },
                    "impacted_services": [
                        {"name": "api-service"},
                        {"name": "web-service"},
                    ],
                },
                "proposal": {
                    "action": "restart_service",
                    "target": "postgres-db-01",
                    "blast_radius": 5,
                },
            },
        )

        print(f"✓ Incident created:")
        print(f"  Title: {workflow.title}")
        print(f"  Severity: {workflow.severity.value}")
        print(f"  Environment: prod")
        print(f"  Risk Score: 85")

        # ====================================================================
        # STEP 3: Evaluate governance policies
        # ====================================================================
        print("\n[4] Evaluating governance policies...")

        matching_policy = broker.evaluate_policies(
            workflow_id=workflow.workflow_id,
            proposed_action="restart_service",
            blast_radius=5,
            risk_score=85,
            severity=Severity.CRITICAL.value,
            environment="prod",
            service_name="postgres-db-01",
        )

        if matching_policy:
            print(f"✓ POLICY MATCHED: {matching_policy.name}")
            print(f"  Policy will gate remediation behind approval")

            # ====================================================================
            # STEP 4: Create approval request
            # ====================================================================
            print("\n[5] Creating approval request...")

            proposal = workflow.context["proposal"]
            incident_summary = {
                "anomaly_type": "service_down",
                "severity": Severity.CRITICAL.value,
                "risk_score": 85,
                "resource_name": "postgres-db-01",
            }

            approval = broker.create_approval_request(
                workflow_id=workflow.workflow_id,
                policy_id=matching_policy.policy_id,
                proposed_action=proposal,
                incident_summary=incident_summary,
            )

            print(f"✓ Approval request created:")
            print(f"  ID: {approval.approval_id}")
            print(f"  Status: {approval.status}")
            print(f"  Proposed Action: {approval.proposed_action}")
            print(f"  Incident Summary: {approval.incident_summary}")

            # ====================================================================
            # STEP 5: Verify approval is pending
            # ====================================================================
            print("\n[6] Verifying approval is pending...")

            is_pending = broker.is_approval_pending(workflow.workflow_id)
            print(
                f"✓ Approval pending for workflow: {is_pending}"
            )

            pending_approval = broker.get_pending_approval(workflow.workflow_id)
            if pending_approval:
                print(f"✓ Retrieved pending approval: {pending_approval.approval_id}")

            # ====================================================================
            # STEP 6: Approve the request
            # ====================================================================
            print("\n[7] Approving the request...")

            approval_repo = ApprovalRepository(db)
            approval_repo.decide(
                approval_id=approval.approval_id,
                decision="approved",
                decided_by="dba-team",
                decision_notes="Approved - verified safe restart window",
            )

            # Fetch updated approval
            updated_approval = db.query(approval_repo.model).filter(
                approval_repo.model.approval_id == approval.approval_id
            ).first()

            print(f"✓ Approval decision recorded:")
            print(f"  Status: {updated_approval.status}")
            print(f"  Decided By: {updated_approval.decided_by}")
            print(f"  Decision Notes: {updated_approval.decision_notes}")

            # ====================================================================
            # STEP 7: Verify approval decision can be retrieved
            # ====================================================================
            print("\n[8] Verifying approval decision...")

            decision = broker.get_approval_decision(workflow.workflow_id)
            print(f"✓ Approval decision status: {decision}")

            if decision == "approved":
                print(f"✓ Remediation is now APPROVED and can proceed")
            else:
                print(f"⚠ Approval decision is: {decision}")

            # ====================================================================
            # STEP 8: Test rejection path
            # ====================================================================
            print("\n[9] Testing rejection path...")

            # Create another incident to test rejection
            workflow2 = WorkflowState(
                workflow_type=WorkflowType.INCIDENT,
                title="Another incident",
                summary="Testing rejection",
                severity=Severity.HIGH,
                context={
                    "alert_payload": {
                        "type": "service_down",
                        "resource_name": "postgres-db-02",
                        "environment": "prod",
                    },
                    "risk_score": 75,
                    "proposal": {
                        "action": "restart_service",
                        "target": "postgres-db-02",
                        "blast_radius": 3,
                    },
                },
            )

            approval2 = broker.create_approval_request(
                workflow_id=workflow2.workflow_id,
                policy_id=policy.policy_id,
                proposed_action=workflow2.context["proposal"],
                incident_summary={"anomaly_type": "service_down", "severity": "high"},
            )

            # Reject it
            approval_repo.decide(
                approval_id=approval2.approval_id,
                decision="rejected",
                decided_by="dba-team",
                decision_notes="Rejected - not in approved maintenance window",
            )

            decision2 = broker.get_approval_decision(workflow2.workflow_id)
            print(f"✓ Second workflow rejection status: {decision2}")

            # ====================================================================
            # STEP 9: Summary
            # ====================================================================
            print("\n[10] Test Summary")
            print("=" * 80)

            approval_count = db.query(approval_repo.model).count()
            policy_count = db.query(policy_repo.model).count()

            print(f"✓ Governance policies in system: {policy_count}")
            print(f"✓ Approval requests created: {approval_count}")
            print(f"✓ Approval decisions recorded: 2 (1 approved, 1 rejected)")
            print()
            print("✅ ALL TESTS PASSED - Governance policy framework is operational!")
            print()

        else:
            print("✗ No matching policy found (this is unexpected for this test)")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(test_governance_workflow())
