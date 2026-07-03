"""Agent registry for workflow engine initialization"""

from agentic_os.core.workflow_engine import WorkflowEngine
from agentic_os.agents.incident_agents import (
    SentinelAgent,
    LibrarianAgent,
    RiskAssessorAgent,
    MechanicAgent,
    PolicyBrokerAgent,
    ToolRegistryAgent,
    VerifierAgent,
)
from agentic_os.agents.runbook_generator import RunbookGeneratorAgent
from agentic_os.agents.change_agents import (
    ChangeRiskAssessorAgent,
    DeploymentSchedulerAgent,
    DeploymentCheckerAgent,
    DeployerAgent,
    DeploymentVerifierAgent,
    ValidationAgent,
    DocumentationServiceAgent,
)
from agentic_os.agents.terminal_agents import (
    MarkResolvedAgent,
    MarkFailedAgent,
    MarkMonitoringAgent,
    MarkDeployedAgent,
    MarkRolledBackAgent,
    MarkRejectedAgent,
    EscalationAgent,
)


def register_all_agents(engine: WorkflowEngine) -> None:
    """Register all incident and change agents with the workflow engine"""

    # Incident agents
    engine.register_agent("sentinel", SentinelAgent().run)
    engine.register_agent("librarian", LibrarianAgent().run)
    engine.register_agent("risk_assessor", RiskAssessorAgent().run)
    engine.register_agent("mechanic", MechanicAgent().run)
    engine.register_agent("runbook_generator", RunbookGeneratorAgent().run)
    engine.register_agent("policy_broker", PolicyBrokerAgent().run)
    engine.register_agent("tool_registry", ToolRegistryAgent().run)
    engine.register_agent("verifier", VerifierAgent().run)

    # Change agents
    engine.register_agent("change_risk_assessor", ChangeRiskAssessorAgent().run)
    engine.register_agent("deployment_scheduler", DeploymentSchedulerAgent().run)
    engine.register_agent("deployment_checker", DeploymentCheckerAgent().run)
    engine.register_agent("deployer", DeployerAgent().run)
    engine.register_agent("deployment_verifier", DeploymentVerifierAgent().run)
    engine.register_agent("validation_agent", ValidationAgent().run)
    engine.register_agent("documentation_service", DocumentationServiceAgent().run)

    # Terminal state handlers
    engine.register_agent("mark_resolved", MarkResolvedAgent().run)
    engine.register_agent("mark_failed", MarkFailedAgent().run)
    engine.register_agent("mark_monitoring", MarkMonitoringAgent().run)
    engine.register_agent("mark_deployed", MarkDeployedAgent().run)
    engine.register_agent("mark_rolled_back", MarkRolledBackAgent().run)
    engine.register_agent("mark_rejected", MarkRejectedAgent().run)

    # Escalation and special handlers (use placeholder agents)
    engine.register_agent("escalation_service", EscalationAgent().run)

    print("✓ All agents registered with workflow engine")
