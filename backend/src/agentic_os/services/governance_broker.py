"""
Governance Broker - Evaluates governance policies and gates remediation actions.

Responsibilities:
- Evaluate governance policies against proposed remediation actions
- Create approval requests when policies require approval
- Block auto-remediation until approval is received
- Provide decision status for workflow engine
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy.orm import Session
import logging

from agentic_os.db.repositories import GovernancePolicyRepository, ApprovalRepository
from agentic_os.db.models import GovernancePolicyModel, ApprovalModel

logger = logging.getLogger(__name__)


class GovernanceBroker:
    """Evaluates governance policies and manages approval workflow"""

    def __init__(self, db: Session):
        self.db = db
        self.policy_repo = GovernancePolicyRepository(db)
        self.approval_repo = ApprovalRepository(db)

    def evaluate_policies(
        self,
        workflow_id: UUID,
        proposed_action: str,
        blast_radius: int = 1,
        risk_score: float = 0,
        severity: str = "low",
        environment: str = "dev",
        service_name: str = None
    ) -> Optional[GovernancePolicyModel]:
        """
        Check if any governance policies match the proposed action.

        Returns: First matching policy, or None if no policies match
        """
        policies = self.policy_repo.list_all(enabled_only=True)

        for policy in policies:
            if self._policy_matches(policy, proposed_action, blast_radius, risk_score, severity, environment, service_name):
                logger.info(f"Governance policy '{policy.name}' matched for workflow {workflow_id}, action: {proposed_action}")
                return policy

        return None

    def _policy_matches(
        self,
        policy: GovernancePolicyModel,
        proposed_action: str,
        blast_radius: int,
        risk_score: float,
        severity: str,
        environment: str,
        service_name: str
    ) -> bool:
        """Check if a proposed action matches policy conditions"""

        # Check if action requires approval (wildcard "*" means all actions)
        if "*" not in policy.actions_requiring_approval and proposed_action not in policy.actions_requiring_approval:
            return False

        # Check conditions (AND logic - all must match)
        conditions = policy.conditions or {}

        # Environment condition
        if "environment" in conditions and conditions["environment"] != environment:
            return False

        # Service condition
        if "service_name" in conditions and conditions["service_name"] != service_name:
            return False

        # Risk score condition
        if "min_risk_score" in conditions and risk_score < conditions["min_risk_score"]:
            return False

        # Severity condition
        if "min_severity" in conditions:
            severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
            required_level = severity_order.get(conditions["min_severity"], 0)
            actual_level = severity_order.get(severity, 0)
            if actual_level < required_level:
                return False

        return True

    def create_approval_request(
        self,
        workflow_id: UUID,
        policy_id: UUID,
        proposed_action: Dict[str, Any],
        incident_summary: Dict[str, Any]
    ) -> ApprovalModel:
        """
        Create an approval request for a governance policy gate.

        Args:
            workflow_id: The incident workflow
            policy_id: The governance policy that triggered
            proposed_action: { "tool", "target", "args", "blast_radius", "estimated_mttr" }
            incident_summary: { "anomaly_type", "severity", "risk_score" }

        Returns: Created ApprovalModel
        """
        approval = ApprovalModel(
            workflow_id=workflow_id,
            governance_policy_id=policy_id,
            approval_type="governance",
            status="pending",
            proposed_action=proposed_action,
            incident_summary=incident_summary
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)

        logger.info(f"Created governance approval request {approval.approval_id} for workflow {workflow_id}")
        return approval

    def is_approval_pending(self, workflow_id: UUID) -> bool:
        """Check if there's a pending governance approval for this workflow"""
        pending = self.db.query(ApprovalModel).filter(
            ApprovalModel.workflow_id == workflow_id,
            ApprovalModel.approval_type == "governance",
            ApprovalModel.status == "pending"
        ).first()
        return pending is not None

    def get_pending_approval(self, workflow_id: UUID) -> Optional[ApprovalModel]:
        """Get the pending governance approval for this workflow (if any)"""
        return self.db.query(ApprovalModel).filter(
            ApprovalModel.workflow_id == workflow_id,
            ApprovalModel.approval_type == "governance",
            ApprovalModel.status == "pending"
        ).first()

    def get_approval_decision(self, workflow_id: UUID) -> Optional[str]:
        """
        Get the decision status of governance approval.

        Returns: "approved", "rejected", or None (if no pending or decided approval)
        """
        approval = self.db.query(ApprovalModel).filter(
            ApprovalModel.workflow_id == workflow_id,
            ApprovalModel.approval_type == "governance"
        ).order_by(ApprovalModel.requested_at.desc()).first()

        if approval:
            if approval.status in ("approved", "diagnostics_only"):
                return approval.status
            elif approval.status == "rejected":
                return "rejected"

        return None
