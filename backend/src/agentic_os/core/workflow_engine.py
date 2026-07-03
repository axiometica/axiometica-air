"""
Workflow orchestration engine.
Executes workflow steps in sequence, handles branching, timeouts, and state persistence.
"""

import asyncio
import logging
from typing import Callable, Dict, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from agentic_os.core.models import (
    WorkflowState, WorkflowDefinition, WorkflowStep, EventEnvelope,
    EventType, LifecycleState
)
from agentic_os.bus.postgres_bus import PostgresEventBus
from agentic_os.db.repositories import WorkflowRepository, EventRepository

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """
    Orchestrates workflow execution.
    Loads definitions, executes steps, handles branching/timeout, persists state.
    """

    def __init__(self, event_bus: PostgresEventBus, db: Session):
        self.bus = event_bus
        self.db = db
        self.workflow_repo = WorkflowRepository(db)
        self.event_repo = EventRepository(db)
        self.agent_handlers: Dict[str, Callable] = {}

    def register_agent(self, agent_name: str, handler: Callable):
        """
        Register an agent handler.

        Args:
            agent_name: Name of agent (e.g., "sentinel", "librarian")
            handler: Async callable that takes WorkflowState and returns modified WorkflowState
        """
        self.agent_handlers[agent_name] = handler
        print(f"✓ Registered agent: {agent_name}")

    async def execute(
        self,
        definition: WorkflowDefinition,
        state: WorkflowState,
        start_step: Optional[str] = None,
    ) -> WorkflowState:
        """
        Execute workflow according to definition.

        Args:
            definition: Workflow definition (with steps)
            state: Initial workflow state
            start_step: Override the first step (used when resuming after approval).
                        Defaults to definition.start_step.

        Returns:
            Final workflow state
        """
        if start_step:
            state.add_trace(
                f"Workflow engine resuming {definition.workflow_type.value} from step: {start_step}"
            )
        else:
            state.add_trace(f"Workflow engine starting execution of {definition.workflow_type.value}")
        state.transition_state(LifecycleState.IN_PROGRESS, 'Workflow engine started execution')

        current_step_id = start_step or definition.start_step

        while current_step_id:
            # Get step definition
            step = definition.steps.get(current_step_id)
            if not step:
                state.add_trace(f"✗ Step not found: {current_step_id}")
                state.transition_state(LifecycleState.FAILED, f'Step not found: {current_step_id}')
                break

            # Execute step
            state.add_trace(f"Executing step: {step.name} ({current_step_id})")

            try:
                state = await self._execute_step(step, state, definition)
            except Exception as e:
                state.add_trace(f"✗ Step failed: {e}")
                state.transition_state(LifecycleState.FAILED, f'Step {step.step_id} failed: {e}')
                current_step_id = step.fallback_step
                continue

            # Determine next step
            current_step_id = self._get_next_step(step, state)

            # Check if we've reached an end state — execute the terminal step first
            if current_step_id in definition.end_steps:
                terminal_step = definition.steps.get(current_step_id)
                if terminal_step:
                    try:
                        state = await self._execute_step(terminal_step, state, definition)
                    except Exception as e:
                        state.add_trace(f"✗ Terminal step {current_step_id} failed: {e}")
                break

            # Update progressive summary so the frontend sees live updates
            if step.step_type == "agent":
                try:
                    from agentic_os.services.platform_context_service import PlatformContextService
                    state.summary = PlatformContextService.build_progressive_summary(state)
                    logger.debug(
                        f"Progressive summary after {step.step_id}: "
                        f"{(state.summary or '')[:120]}"
                    )
                except Exception as _summary_err:
                    logger.warning(
                        f"Progressive summary failed for step {step.step_id}: {_summary_err}"
                    )

            # Persist state after each step
            self.workflow_repo.save(state)

        # Fallback lifecycle state (in case terminal agent failed to set it)
        if current_step_id in definition.end_steps and state.lifecycle_state == LifecycleState.IN_PROGRESS:
            if "resolved" in current_step_id:
                state.transition_state(LifecycleState.RESOLVED, 'Workflow reached resolved end step')
            elif "monitoring" in current_step_id:
                state.transition_state(LifecycleState.MONITORING, 'Workflow reached monitoring end step')
            elif "failed" in current_step_id or "rejected" in current_step_id or "denied" in current_step_id:
                state.transition_state(LifecycleState.FAILED, 'Workflow reached failed/rejected end step')
            else:
                state.transition_state(LifecycleState.RESOLVED, 'Workflow reached terminal end step')

        state.add_trace(f"Workflow completed with state: {state.lifecycle_state.value}")
        self.workflow_repo.save(state)

        return state

    async def _execute_step(self, step: WorkflowStep, state: WorkflowState, definition: WorkflowDefinition) -> WorkflowState:
        """Execute a single step"""

        if step.step_type == "agent":
            return await self._execute_agent_step(step, state)

        elif step.step_type == "human_approval":
            return await self._execute_approval_step(step, state)

        elif step.step_type == "decision":
            return await self._execute_decision_step(step, state)

        elif step.step_type == "external_call":
            return await self._execute_external_step(step, state)

        elif step.step_type == "parallel":
            return await self._execute_parallel_step(step, state, definition)

        else:
            raise ValueError(f"Unknown step type: {step.step_type}")

    async def _execute_agent_step(self, step: WorkflowStep, state: WorkflowState) -> WorkflowState:
        """
        Execute agent handler with enforced timeout.

        ISSUE E FIX: Implements per-step timeout enforcement with:
        - Required timeout specification
        - Clear timeout error messages
        - Comprehensive logging
        - State preservation on timeout
        """
        import logging
        import time
        logger = logging.getLogger(__name__)
        agent_name = step.handler

        if agent_name not in self.agent_handlers:
            raise ValueError(f"Agent not registered: {agent_name}")

        handler = self.agent_handlers[agent_name]

        # ISSUE E FIX: Enforce timeout (required, no fallback to no-timeout)
        timeout_seconds = step.timeout_seconds or 300  # Default: 5 minutes if not specified

        logger.info(f"Starting agent: {agent_name} (timeout: {timeout_seconds}s)")
        start_time = time.time()

        # Execute with timeout
        try:
            state = await asyncio.wait_for(
                handler(state),
                timeout=timeout_seconds
            )
            elapsed = time.time() - start_time
            logger.info(f"✓ Agent {agent_name} completed in {elapsed:.2f}s")

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            error_msg = (
                f"Agent '{agent_name}' exceeded timeout of {timeout_seconds}s "
                f"(actual: {elapsed:.2f}s)"
            )
            state.add_trace(f"✗ TIMEOUT: {error_msg}")
            logger.critical(f"✗ {error_msg}")

            # Store timeout error in context for debugging
            state.context["last_error"] = {
                "agent": agent_name,
                "error_type": "TimeoutError",
                "timeout_seconds": timeout_seconds,
                "actual_seconds": elapsed,
                "message": error_msg,
                "timestamp": __import__('datetime').datetime.utcnow().isoformat()
            }

            raise TimeoutError(error_msg)

        except Exception as e:
            error_type = e.__class__.__name__
            error_msg = str(e)
            state.add_trace(f"✗ AGENT ERROR: {agent_name}: {error_msg} ({error_type})")
            logger.error(
                f"✗ Agent {agent_name} failed: {error_msg} ({error_type})",
                exc_info=True
            )
            raise

        # Publish event (ISSUE F FIX: log failures, don't silently ignore)
        try:
            event = EventEnvelope(
                workflow_id=state.workflow_id,
                workflow_type=state.workflow_type,
                event_type=self._get_event_type(state.workflow_type.value, step.step_id),
                source_agent=agent_name,
                payload={"step": step.step_id},
                correlation_id=state.correlation_id,
                causation_id=state.causation_id,
            )
            await self.bus.publish(event)
        except Exception as e:
            logger.error(f"CRITICAL: Failed to publish step completion event: {e}", exc_info=True)
            # Don't fail workflow on event publish error, but log it prominently
            state.add_trace(f"⚠️ Could not publish status update: {str(e)}")

        return state

    async def _execute_approval_step(self, step: WorkflowStep, state: WorkflowState) -> WorkflowState:
        """
        Wait for human approval (pause workflow).
        Returns when approval is received via callback.

        ISSUE C FIX: Includes race condition prevention:
        - Checks current state before proceeding after timeout
        - Prevents duplicate execution if approval arrives late
        """
        import logging
        logger = logging.getLogger(__name__)

        state.transition_state(LifecycleState.WAITING_APPROVAL, f'Awaiting approval: {step.name}')
        state.add_trace(f"Awaiting approval: {step.name}")

        # Create approval request
        approval_event_type = "approval.requested"

        event = EventEnvelope(
            workflow_id=state.workflow_id,
            workflow_type=state.workflow_type,
            event_type=EventType.APPROVAL_REQUESTED,
            source_agent="workflow_engine",
            payload={"step": step.step_id, "approval_type": step.handler or "generic"},
            correlation_id=state.correlation_id,
        )
        await self.bus.publish(event)

        # Wait for approval decision
        def is_approval_for_this_workflow(event: EventEnvelope) -> bool:
            return (
                event.workflow_id == state.workflow_id and
                event.event_type in [EventType.APPROVAL_GRANTED, EventType.APPROVAL_REJECTED]
            )

        approval_event = await self.bus.wait_for_event(
            "approval.*",
            predicate=is_approval_for_this_workflow,
            timeout_seconds=step.timeout_seconds
        )

        if not approval_event:
            # ISSUE C FIX: Timeout occurred
            state.add_trace(f"✗ Approval timeout for {step.name}")
            state.transition_state(LifecycleState.FAILED, f'Approval timeout: {step.name}')
            logger.warning(f"Approval timeout for workflow {state.workflow_id} after {step.timeout_seconds}s")
            raise TimeoutError(f"Approval timeout: {step.name}")

        # ISSUE C FIX: Verify current state matches expectation
        # Re-fetch state from database to catch any concurrent modifications
        current_db_state = self.workflow_repo.get(state.workflow_id)
        if current_db_state and current_db_state.lifecycle_state != LifecycleState.WAITING_APPROVAL:
            # Another process may have already handled this approval or failed the workflow
            logger.warning(
                f"Approval arrived late for {state.workflow_id}: "
                f"current state is {current_db_state.lifecycle_state.value}, expected WAITING_APPROVAL"
            )
            state.add_trace(
                f"⚠️ Late approval: Workflow is already in {current_db_state.lifecycle_state.value} state"
            )
            # Return current state instead of processing late approval
            return current_db_state

        # Check approval decision
        if approval_event.event_type == EventType.APPROVAL_GRANTED:
            state.add_trace(f"✓ Approval granted for {step.name}")
            state.governance_decision = "approved"
            state.transition_state(LifecycleState.APPROVED, f'Approval granted for {step.name}')
            logger.info(f"Approval granted for workflow {state.workflow_id}")
        else:
            state.add_trace(f"✗ Approval rejected for {step.name}")
            state.governance_decision = "rejected"
            state.transition_state(LifecycleState.REJECTED, f'Approval rejected for {step.name}')
            logger.info(f"Approval rejected for workflow {state.workflow_id}")
            raise Exception(f"Approval rejected: {step.name}")

        return state

    async def _execute_decision_step(self, step: WorkflowStep, state: WorkflowState) -> WorkflowState:
        """
        Execute decision step (branching).
        Context must contain "decision_result" key.
        """
        decision = state.context.get("decision_result")

        if not decision:
            state.add_trace(f"⚠ No decision_result in context for step {step.step_id}")
            decision = "default"

        state.add_trace(f"Decision: {decision}")
        return state

    async def _execute_external_step(self, step: WorkflowStep, state: WorkflowState) -> WorkflowState:
        """Execute call to external service"""
        state.add_trace(f"Calling external service: {step.handler}")
        # Placeholder for external service calls (e.g., SNOW, Slack, etc.)
        return state

    async def _execute_parallel_step(self, step: WorkflowStep, state: WorkflowState, definition: WorkflowDefinition) -> WorkflowState:
        """Execute multiple steps in parallel"""
        # Placeholder for parallel execution
        state.add_trace(f"Executing parallel steps: {step.handler}")
        return state

    def _get_next_step(self, step: WorkflowStep, state: WorkflowState) -> Optional[str]:
        """
        Determine the next step based on decision result or default.

        Args:
            step: Current step
            state: Workflow state

        Returns:
            Next step ID, or None if no next step
        """
        # Check for decision result
        decision = state.context.get("decision_result")

        if decision and decision in step.next_steps:
            return step.next_steps[decision]

        # Fall back to default
        return step.next_steps.get("default")

    @staticmethod
    def _get_event_type(workflow_type: str, step_id: str) -> EventType:
        """Map step to event type"""
        event_map = {
            # Incident events
            ("incident", "triage"): EventType.INCIDENT_SEVERITY_ASSESSED,
            ("incident", "assess_risk"): EventType.INCIDENT_RISK_ASSESSED,
            ("incident", "propose"): EventType.INCIDENT_PROPOSAL_GENERATED,
            ("incident", "broker"): EventType.INCIDENT_APPROVED,
            ("incident", "execute"): EventType.INCIDENT_REMEDIATION_EXECUTED,

            # Change events
            ("change", "assess_risk"): EventType.CHANGE_RISK_ASSESSED,
            ("change", "cab_review"): EventType.CHANGE_CAB_REVIEW_REQUESTED,
            ("change", "deploy"): EventType.CHANGE_DEPLOYED,
            ("change", "verify"): EventType.CHANGE_VERIFIED,
        }

        key = (workflow_type, step_id)
        return event_map.get(key, EventType.WORKFLOW_STARTED)
