"""Terminal state agents for workflow completion"""

from agentic_os.agents.base import Agent
from agentic_os.core.models import WorkflowState, LifecycleState


class TerminalStateAgent(Agent):
    """Base class for terminal state handlers"""

    def __init__(self, name: str, lifecycle_state: LifecycleState):
        super().__init__(name)
        self.lifecycle_state = lifecycle_state

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Mark workflow as terminal"""
        state.transition_state(self.lifecycle_state, f'Terminal state reached: {self.lifecycle_state.value}')
        state = self._add_trace(state, f"✓ Workflow {self.lifecycle_state.value}")
        return state


# Incident terminal states
class MarkResolvedAgent(TerminalStateAgent):
    """Mark incident as resolved"""

    def __init__(self):
        super().__init__("mark_resolved", LifecycleState.RESOLVED)


class MarkFailedAgent(Agent):
    """
    Remediation failed — transition to AWAITING_MANUAL so a human can take over.

    Sets remediation_outcome="failed" (preserves "aborted" if already set) and
    lifecycle_state=AWAITING_MANUAL.  The legacy LifecycleState.FAILED value is
    intentionally NOT written here; it is reserved for internal pipeline errors
    (see base.py _standard_error_handler).
    """

    def __init__(self):
        super().__init__("mark_failed")

    async def run(self, state: WorkflowState) -> WorkflowState:
        state.lifecycle_state = LifecycleState.AWAITING_MANUAL
        if state.remediation_outcome != "aborted":
            state.remediation_outcome = "failed"
        state = self._add_trace(
            state,
            "⚠ Remediation failed — incident now AWAITING_MANUAL (human intervention required)"
        )
        return state


class MarkMonitoringAgent(TerminalStateAgent):
    """Mark incident as monitoring (no remedy applied)"""

    def __init__(self):
        super().__init__("mark_monitoring", LifecycleState.MONITORING)


# Change terminal states
class MarkDeployedAgent(TerminalStateAgent):
    """Mark change as deployed"""

    def __init__(self):
        super().__init__("mark_deployed", LifecycleState.DEPLOYED)


class MarkRolledBackAgent(TerminalStateAgent):
    """Mark change as rolled back"""

    def __init__(self):
        super().__init__("mark_rolled_back", LifecycleState.ROLLED_BACK)


class MarkRejectedAgent(Agent):
    """
    Approval rejected — transition to AWAITING_MANUAL so a human can decide next steps.

    Sets remediation_outcome="rejected" and lifecycle_state=AWAITING_MANUAL.
    The legacy LifecycleState.REJECTED value is intentionally NOT written here.
    """

    def __init__(self):
        super().__init__("mark_rejected")

    async def run(self, state: WorkflowState) -> WorkflowState:
        state.lifecycle_state = LifecycleState.AWAITING_MANUAL
        state.remediation_outcome = "rejected"
        state = self._add_trace(
            state,
            "⚠ Remediation rejected — incident now AWAITING_MANUAL (human intervention required)"
        )
        return state


class EscalationAgent(Agent):
    """Escalates issue to on-call or management"""

    def __init__(self):
        super().__init__("escalation_service")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Escalate to on-call team"""
        state = self._add_trace(
            state,
            f"⚠ Escalated to on-call team (correlation_id: {state.correlation_id})",
        )
        return state
