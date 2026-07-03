"""Base agent class for workflow execution"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING
from agentic_os.core.models import WorkflowState

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agentic_os.core.context_schema import IncidentWorkflowContext


class Agent(ABC):
    """Abstract base class for all agents in the workflow engine"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute agent logic and return modified state.

        Args:
            state: Current workflow state

        Returns:
            Modified workflow state with new context, decision_result, or reasoning
        """
        pass

    def _extract_context(self, state: WorkflowState, key: str, default=None):
        """Extract value from workflow context dict (untyped)"""
        return state.context.get(key, default)

    def _set_context(self, state: WorkflowState, key: str, value) -> WorkflowState:
        """Update workflow context dict and return modified state (untyped)"""
        state.context[key] = value
        return state

    def _add_trace(self, state: WorkflowState, message: str) -> WorkflowState:
        """Add trace message and return modified state"""
        state.add_trace(message)
        return state

    # Typed context helper methods (Phase 10: Context Schema Refactoring)

    def _get_typed_context(self, state: WorkflowState) -> Optional["IncidentWorkflowContext"]:
        """
        Get typed IncidentWorkflowContext from workflow state.
        Works for incident workflows only.
        """
        try:
            ctx = state.get_context()
            return ctx
        except Exception as e:
            # Broad catch: from_dict can raise KeyError on malformed nested data.
            # Returning None causes the caller to fall back to a fresh context —
            # that's acceptable but we log a WARNING so it appears in diagnostics.
            logger.warning(f"[{self.name}] _get_typed_context failed ({type(e).__name__}: {e}) — using fresh context")
            return None

    def _set_typed_context(self, state: WorkflowState, context: "IncidentWorkflowContext") -> WorkflowState:
        """
        Set typed IncidentWorkflowContext in workflow state.
        Syncs to untyped context dict for backward compatibility and persistence.
        """
        try:
            # logger.debug(f"[{self.name}] _set_typed_context: Saving context, cmdb.platform={context.cmdb.platform if context.cmdb else 'N/A'}")
            state.set_context(context)
        except (ImportError, TypeError) as e:
            # Fallback for workflows without typed context support
            # logger.warning(f"[{self.name}] _set_typed_context failed: {e}, using fallback")
            if hasattr(context, 'to_dict'):
                state.context = context.to_dict()
        return state

    # ========================================================================
    # Context Validation (Issue A: Missing Context Validation Fix)
    # ========================================================================

    def _validate_context_layer(self, state: WorkflowState, layer_name: str, required_fields: list = None) -> bool:
        """
        Validate that a context layer exists and has required fields.

        Args:
            state: Workflow state
            layer_name: Name of context layer (e.g., 'sentinel', 'cmdb', 'risk')
            required_fields: List of required fields in the layer (optional)

        Returns:
            True if validation passed, False otherwise
        """
        import logging
        logger = logging.getLogger(__name__)

        ctx = state.get_context() if hasattr(state, 'get_context') else None

        if not ctx:
            logger.error(f"{self.name}: Context not available")
            return False

        # Check if layer exists
        layer = getattr(ctx, layer_name, None)
        if layer is None:
            logger.error(f"{self.name}: Required context layer '{layer_name}' is missing. "
                        f"Previous agent must have failed.")
            return False

        # Check required fields if specified
        if required_fields:
            for field in required_fields:
                if not hasattr(layer, field):
                    logger.error(f"{self.name}: Context layer '{layer_name}' missing required field '{field}'")
                    return False
                if getattr(layer, field) is None:
                    logger.error(f"{self.name}: Context layer '{layer_name}' field '{field}' is None")
                    return False

        return True

    def _handle_missing_context(self, state: WorkflowState, layer_name: str, message: str = "") -> WorkflowState:
        """
        Handle missing required context layer gracefully.
        Marks workflow as failed with clear error message.

        Args:
            state: Workflow state
            layer_name: Name of missing context layer
            message: Additional context about what was expected

        Returns:
            Modified state with FAILED status
        """
        import logging
        from agentic_os.core.models import LifecycleState

        logger = logging.getLogger(__name__)

        error_msg = f"Missing required context layer '{layer_name}' from previous agent"
        if message:
            error_msg += f": {message}"

        logger.error(f"{self.name}: {error_msg}")
        state.add_trace(f"ERROR: {error_msg}")

        # Store error details for debugging
        state.context["last_error"] = {
            "agent": self.name,
            "error_type": "MissingContextLayer",
            "missing_layer": layer_name,
            "message": error_msg
        }

        state.lifecycle_state = LifecycleState.FAILED
        return state

    def _standard_error_handler(self, state: WorkflowState, error: Exception, context_msg: str = "") -> WorkflowState:
        """
        Standardized error handling across all agents.
        Ensures errors are logged, traced, and context is preserved.

        Args:
            state: Workflow state
            error: The exception that occurred
            context_msg: Additional context about what was being done

        Returns:
            Modified state with error details
        """
        import logging
        import traceback
        from agentic_os.core.models import LifecycleState

        logger = logging.getLogger(__name__)

        error_type = error.__class__.__name__
        error_msg = str(error)

        # Build full error message
        if context_msg:
            full_message = f"{context_msg}: {error_msg} ({error_type})"
        else:
            full_message = f"{error_msg} ({error_type})"

        # Log with full stack trace
        logger.exception(f"{self.name} failed: {full_message}")

        # Add to trace (visible to user)
        state.add_trace(f"ERROR: {self.name}: {full_message}")

        # Store detailed error in context (for downstream analysis)
        state.context["last_error"] = {
            "agent": self.name,
            "error_type": error_type,
            "message": error_msg,
            "context": context_msg,
            "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
            "stack_trace": traceback.format_exc()
        }

        state.lifecycle_state = LifecycleState.FAILED
        return state
