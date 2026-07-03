"""
Typed context schemas for incident workflows.

This module defines dataclasses for each layer of context that agents build up
incrementally. Each agent reads the output of previous agents and adds its own
typed data to the context.

Context Flow:
  Sentinel → Librarian → RiskAssessor → Mechanic → PolicyBroker → ToolRegistry → Verifier
  └─ adds sentinel
              └─ adds cmdb
                          └─ adds risk
                                      └─ adds proposal
                                                   └─ adds governance
                                                                 └─ updates execution_results
                                                                            └─ adds verification
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class LifecycleState(str, Enum):
    """Workflow lifecycle states."""
    SUBMITTED = "submitted"
    TRIAGING = "triaging"
    DIAGNOSTICS = "diagnostics"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFICATION = "verification"
    RESOLVED = "resolved"
    FAILED = "failed"
    REJECTED = "rejected"


# ============================================================================
# SENTINEL CONTEXT (from anomaly detection)
# ============================================================================

@dataclass
class AlertPayload:
    """Alert/anomaly details from Sentinel monitoring."""
    type: str
    message: str
    severity: Optional[str] = None
    anomaly_process: Optional[str] = None  # Process name for kill actions


@dataclass
class SentinelContext:
    """Context added by SentinelAgent - anomaly detection results."""
    detected_anomaly: str
    anomaly_type: str  # high_cpu, high_syscall_intensity, disk_full, etc.
    alert_payload: AlertPayload
    timestamp: str
    confidence: float


# ============================================================================
# CMDB CONTEXT (from library/enrichment service)
# ============================================================================

@dataclass
class ResourceInfo:
    """Resource information from CMDB."""
    name: str
    type: str
    status: str  # operational, degraded, down
    owner: str
    environment: str  # prod, staging, dev
    criticality: Optional[str] = None
    platform: str = "any"  # docker | linux | windows | kubernetes | any


@dataclass
class CMDBContext:
    """Context added by LibrarianAgent - CMDB enrichment."""
    resource_name: str
    resource_info: ResourceInfo
    environment: str  # Top-level for easy policy matching (extracted from resource_info)
    platform: str = "any"  # Top-level for runbook selection (derived from resource type)
    dependencies: List[Dict[str, Any]] = field(default_factory=list)
    impacted_services: List[Dict[str, Any]] = field(default_factory=list)
    cmdb_context: Optional[Dict[str, Any]] = None  # Raw Neo4j data if available


# ============================================================================
# RISK CONTEXT (from risk assessment)
# ============================================================================

@dataclass
class RiskBreakdown:
    """Detailed risk score breakdown."""
    severity_score: float
    resource_criticality_score: float
    dependency_impact_score: float
    business_impact_score: float


@dataclass
class RiskContext:
    """Context added by RiskAssessor - risk evaluation."""
    risk_score: float  # 0-10 scale
    risk_breakdown: RiskBreakdown
    blast_radius: int  # 1=single pod, 2=multiple pods, 3=service, 4=service group
    remediation_complexity: str  # simple, moderate, complex


# ============================================================================
# PROPOSAL CONTEXT (from remediation proposal)
# ============================================================================

@dataclass
class RunbookStep:
    """Single step in a runbook (diagnostic, remediation, or verification)."""
    order: int
    type: str  # diagnostic, remediation, verification
    name: str
    description: str
    tool: str  # process_kill, kubectl_scale, etc. (empty for verification steps)
    args_json: Dict[str, Any]  # Tool arguments (matches database field name)
    # Verification-specific fields (metric-based checks)
    metric: Optional[str] = None  # "container_status", "cpu_percent", etc.
    check: Optional[str] = None   # "equals", "less_than", "greater_than", etc.
    value: Optional[Any] = None   # Expected value for the metric check


@dataclass
class Proposal:
    """Context added by MechanicAgent - remediation proposal."""
    runbook_id: str
    runbook_name: str
    diagnostics_steps: List[RunbookStep]
    remediation_steps: List[RunbookStep]
    confidence: float
    blast_radius: int
    approval_required: bool
    verification_steps: List[RunbookStep] = field(default_factory=list)  # Validation steps after remediation
    main_args: Dict[str, Any] = field(default_factory=dict)  # Flattened, resolved args
    # Origin of this proposal — used for UI display and runbook promotion
    # Values: "runbook_library" | "cmdb_playbook" | "fallback_escalation" | "llm_generated"
    source: str = "runbook_library"
    target: str = ""  # The resource/container/pod/VM name to act on


# ============================================================================
# GOVERNANCE CONTEXT (from policy broker)
# ============================================================================

@dataclass
class GovernanceContext:
    """Context added by PolicyBrokerAgent - governance decision."""
    matching_policies: List[Dict[str, Any]]  # Policies that matched this incident
    approval_required: bool
    approval_priority: int  # 1-100, lower = higher priority
    allowed_actions: List[str]  # Actions permitted by governance
    blast_radius_limit: Optional[int] = None  # Max allowed blast radius
    requires_post_monitoring: bool = False
    decision_notes: str = ""


# ============================================================================
# VERIFICATION CONTEXT (from verifier)
# ============================================================================

@dataclass
class VerificationResult:
    """Single verification step result."""
    step_name: str
    status: str  # passed, failed, warning
    metric: str
    actual_value: float
    threshold: float
    message: str


@dataclass
class VerificationContext:
    """Context added by VerifierAgent - verification results."""
    verification_results: List[VerificationResult]
    overall_success: bool
    remediation_effective: bool
    issues_resolved: bool


# ============================================================================
# COMPLETE WORKFLOW CONTEXT
# ============================================================================

@dataclass
class IncidentWorkflowContext:
    """
    Complete typed context for incident workflows.
    Built up incrementally as agents execute in sequence.
    """

    # From Sentinel
    sentinel: Optional[SentinelContext] = None

    # From Librarian
    cmdb: Optional[CMDBContext] = None

    # From RiskAssessor
    risk: Optional[RiskContext] = None

    # From Mechanic
    proposal: Optional[Proposal] = None

    # From PolicyBroker
    governance: Optional[GovernanceContext] = None

    # From ToolRegistry/execution
    execution_results: List[Dict[str, Any]] = field(default_factory=list)

    # From Verifier
    verification: Optional[VerificationContext] = None

    # Metadata
    reasoning_trace: List[str] = field(default_factory=list)

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def get_environment(self) -> str:
        """
        Get environment from CMDB context, fallback to 'dev'.
        Used for policy matching.
        """
        if self.cmdb and hasattr(self.cmdb, 'environment') and self.cmdb.environment:
            return self.cmdb.environment
        return "dev"

    def get_anomaly_process(self) -> Optional[str]:
        """Get process name from sentinel alert payload."""
        if self.sentinel and self.sentinel.alert_payload:
            return self.sentinel.alert_payload.anomaly_process
        return None

    def get_risk_score(self) -> Optional[float]:
        """Get risk score from risk assessment."""
        return self.risk.risk_score if self.risk else None

    def get_blast_radius(self) -> Optional[int]:
        """Get blast radius from risk assessment."""
        return self.risk.blast_radius if self.risk else None

    def get_platform(self) -> str:
        """Get target platform from CMDB context, fallback to 'any'."""
        if self.cmdb and self.cmdb.platform:
            return self.cmdb.platform
        return "any"

    def add_trace(self, message: str) -> None:
        """Add a message to the reasoning trace."""
        self.reasoning_trace.append(message)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict for persistence to database.
        Handles nested dataclass conversion.
        """
        result = {}

        if self.sentinel:
            result["sentinel"] = {
                "detected_anomaly": self.sentinel.detected_anomaly,
                "anomaly_type": self.sentinel.anomaly_type,
                "alert_payload": {
                    "type": self.sentinel.alert_payload.type,
                    "message": self.sentinel.alert_payload.message,
                    "severity": self.sentinel.alert_payload.severity,
                    "anomaly_process": self.sentinel.alert_payload.anomaly_process,
                },
                "timestamp": self.sentinel.timestamp,
                "confidence": self.sentinel.confidence,
            }

        if self.cmdb:
            result["cmdb"] = {
                "resource_name": self.cmdb.resource_name,
                "resource_info": {
                    "name": self.cmdb.resource_info.name,
                    "type": self.cmdb.resource_info.type,
                    "status": self.cmdb.resource_info.status,
                    "owner": self.cmdb.resource_info.owner,
                    "environment": self.cmdb.resource_info.environment,
                    "criticality": self.cmdb.resource_info.criticality,
                },
                "environment": self.cmdb.environment,
                "platform": self.cmdb.platform,  # CRITICAL: Include platform for runbook selection
                "dependencies": self.cmdb.dependencies,
                "impacted_services": self.cmdb.impacted_services,
                "cmdb_context": self.cmdb.cmdb_context,
            }

        if self.risk:
            result["risk"] = {
                "risk_score": self.risk.risk_score,
                "risk_breakdown": {
                    "severity_score": self.risk.risk_breakdown.severity_score,
                    "resource_criticality_score": self.risk.risk_breakdown.resource_criticality_score,
                    "dependency_impact_score": self.risk.risk_breakdown.dependency_impact_score,
                    "business_impact_score": self.risk.risk_breakdown.business_impact_score,
                },
                "blast_radius": self.risk.blast_radius,
                "remediation_complexity": self.risk.remediation_complexity,
            }

        if self.proposal:
            result["proposal"] = {
                "runbook_id": self.proposal.runbook_id,
                "runbook_name": self.proposal.runbook_name,
                "diagnostics_steps": [
                    {
                        "order": step.order,
                        "type": step.type,
                        "name": step.name,
                        "description": step.description,
                        "tool": step.tool,
                        "args_json": step.args_json,
                    }
                    for step in self.proposal.diagnostics_steps
                ],
                "remediation_steps": [
                    {
                        "order": step.order,
                        "type": step.type,
                        "name": step.name,
                        "description": step.description,
                        "tool": step.tool,
                        "args_json": step.args_json,
                    }
                    for step in self.proposal.remediation_steps
                ],
                "verification_steps": [
                    {
                        "order": step.order,
                        "type": step.type,
                        "name": step.name,
                        "description": step.description,
                        "tool": step.tool,
                        "args_json": step.args_json,
                        "metric": step.metric,      # NEW: metric-based check
                        "check": step.check,        # NEW: comparison operator
                        "value": step.value,        # NEW: expected value
                    }
                    for step in self.proposal.verification_steps
                ],
                "confidence": self.proposal.confidence,
                "blast_radius": self.proposal.blast_radius,
                "approval_required": self.proposal.approval_required,
                "main_args": self.proposal.main_args,
                "source": self.proposal.source,
                "target": self.proposal.target,
            }

        if self.governance:
            result["governance"] = {
                "matching_policies": self.governance.matching_policies,
                "approval_required": self.governance.approval_required,
                "approval_priority": self.governance.approval_priority,
                "allowed_actions": self.governance.allowed_actions,
                "blast_radius_limit": self.governance.blast_radius_limit,
                "requires_post_monitoring": self.governance.requires_post_monitoring,
                "decision_notes": self.governance.decision_notes,
            }

        if self.verification:
            result["verification"] = {
                "verification_results": [
                    {
                        "step_name": vr.step_name,
                        "status": vr.status,
                        "metric": vr.metric,
                        "actual_value": vr.actual_value,
                        "threshold": vr.threshold,
                        "message": vr.message,
                    }
                    for vr in self.verification.verification_results
                ],
                "overall_success": self.verification.overall_success,
                "remediation_effective": self.verification.remediation_effective,
                "issues_resolved": self.verification.issues_resolved,
            }

        result["execution_results"] = self.execution_results
        result["reasoning_trace"] = self.reasoning_trace

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IncidentWorkflowContext":
        """
        Reconstruct typed context from persistence dict.
        Handles nested dataclass reconstruction.
        """
        sentinel = None
        if data.get("sentinel"):
            s = data["sentinel"]
            sentinel = SentinelContext(
                detected_anomaly=s.get("detected_anomaly", ""),
                anomaly_type=s.get("anomaly_type", ""),
                alert_payload=AlertPayload(
                    type=s["alert_payload"].get("type", ""),
                    message=s["alert_payload"].get("message", ""),
                    severity=s["alert_payload"].get("severity"),
                    anomaly_process=s["alert_payload"].get("anomaly_process"),
                ),
                timestamp=s.get("timestamp", ""),
                confidence=s.get("confidence", 0.0),
            )

        cmdb = None
        if data.get("cmdb"):
            c = data["cmdb"]
            cmdb = CMDBContext(
                resource_name=c.get("resource_name", ""),
                resource_info=ResourceInfo(
                    name=c["resource_info"].get("name", ""),
                    type=c["resource_info"].get("type", ""),
                    status=c["resource_info"].get("status", ""),
                    owner=c["resource_info"].get("owner", ""),
                    environment=c["resource_info"].get("environment", ""),
                    criticality=c["resource_info"].get("criticality"),
                ),
                environment=c.get("environment", "dev"),
                platform=c.get("platform", "any"),  # CRITICAL: Reconstruct platform for runbook selection
                dependencies=c.get("dependencies", []),
                impacted_services=c.get("impacted_services", []),
                cmdb_context=c.get("cmdb_context"),
            )

        risk = None
        if data.get("risk"):
            r = data["risk"]
            risk = RiskContext(
                risk_score=r.get("risk_score", 0.0),
                risk_breakdown=RiskBreakdown(
                    severity_score=r["risk_breakdown"].get("severity_score", 0.0),
                    resource_criticality_score=r["risk_breakdown"].get("resource_criticality_score", 0.0),
                    dependency_impact_score=r["risk_breakdown"].get("dependency_impact_score", 0.0),
                    business_impact_score=r["risk_breakdown"].get("business_impact_score", 0.0),
                ),
                blast_radius=r.get("blast_radius", 1),
                remediation_complexity=r.get("remediation_complexity", "moderate"),
            )

        proposal = None
        if data.get("proposal"):
            p = data["proposal"]
            proposal = Proposal(
                runbook_id=p.get("runbook_id", ""),
                runbook_name=p.get("runbook_name", ""),
                diagnostics_steps=[
                    RunbookStep(
                        order=step.get("order", 0),
                        type=step.get("type", ""),
                        name=step.get("name", ""),
                        description=step.get("description", ""),
                        tool=step.get("tool", ""),
                        args_json=step.get("args_json", {}),
                    )
                    for step in p.get("diagnostics_steps", [])
                ],
                remediation_steps=[
                    RunbookStep(
                        order=step.get("order", 0),
                        type=step.get("type", ""),
                        name=step.get("name", ""),
                        description=step.get("description", ""),
                        tool=step.get("tool", ""),
                        args_json=step.get("args_json", {}),
                    )
                    for step in p.get("remediation_steps", [])
                ],
                confidence=p.get("confidence", 0.0),
                blast_radius=p.get("blast_radius", 1),
                approval_required=p.get("approval_required", False),
                main_args=p.get("main_args", {}),
                source=p.get("source", "runbook_library"),
            )

        governance = None
        if data.get("governance"):
            g = data["governance"]
            governance = GovernanceContext(
                matching_policies=g.get("matching_policies", []),
                approval_required=g.get("approval_required", False),
                approval_priority=g.get("approval_priority", 50),
                allowed_actions=g.get("allowed_actions", []),
                blast_radius_limit=g.get("blast_radius_limit"),
                requires_post_monitoring=g.get("requires_post_monitoring", False),
                decision_notes=g.get("decision_notes", ""),
            )

        verification = None
        if data.get("verification"):
            v = data["verification"]
            verification = VerificationContext(
                verification_results=[
                    VerificationResult(
                        step_name=vr.get("step_name", ""),
                        status=vr.get("status", ""),
                        metric=vr.get("metric", ""),
                        actual_value=vr.get("actual_value", 0.0),
                        threshold=vr.get("threshold", 0.0),
                        message=vr.get("message", ""),
                    )
                    for vr in v.get("verification_results", [])
                ],
                overall_success=v.get("overall_success", False),
                remediation_effective=v.get("remediation_effective", False),
                issues_resolved=v.get("issues_resolved", False),
            )

        return cls(
            sentinel=sentinel,
            cmdb=cmdb,
            risk=risk,
            proposal=proposal,
            governance=governance,
            execution_results=data.get("execution_results", []),
            verification=verification,
            reasoning_trace=data.get("reasoning_trace", []),
        )
