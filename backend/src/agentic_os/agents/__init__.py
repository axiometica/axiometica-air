"""Agents framework for workflow execution"""

from agentic_os.agents.base import Agent
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
from agentic_os.agents.registry import register_all_agents

__all__ = [
    "Agent",
    "register_all_agents",
    # Incident agents
    "SentinelAgent",
    "LibrarianAgent",
    "RiskAssessorAgent",
    "MechanicAgent",
    "PolicyBrokerAgent",
    "ToolRegistryAgent",
    "VerifierAgent",
    # Change agents
    "ChangeRiskAssessorAgent",
    "DeploymentSchedulerAgent",
    "DeploymentCheckerAgent",
    "DeployerAgent",
    "DeploymentVerifierAgent",
    "ValidationAgent",
    "DocumentationServiceAgent",
    # Terminal agents
    "MarkResolvedAgent",
    "MarkFailedAgent",
    "MarkMonitoringAgent",
    "MarkDeployedAgent",
    "MarkRolledBackAgent",
    "MarkRejectedAgent",
    "EscalationAgent",
]
