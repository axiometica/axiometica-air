"""Change management agents"""

from datetime import datetime, timedelta
from typing import Dict
from agentic_os.agents.base import Agent
from agentic_os.core.models import WorkflowState, Severity


class ChangeRiskAssessorAgent(Agent):
    """Assesses change risk based on type and scope"""

    def __init__(self):
        super().__init__("change_risk_assessor")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Assess change risk"""
        change = state.context.get("change_context", {})
        change_type = change.get("change_type", "standard")

        # Risk scoring based on change type
        risk_scores = {
            "standard": 30,
            "normal": 50,
            "emergency": 80,
        }

        state.risk_score = risk_scores.get(change_type, 50)
        state.severity = Severity.HIGH if state.risk_score > 70 else Severity.MEDIUM

        reasoning = f"Change risk {state.risk_score} for {change_type} change"
        state = self._set_context(state, "risk_reasoning", reasoning)
        state = self._add_trace(state, f"✓ Change risk assessed: {state.risk_score}")
        return state


class DeploymentSchedulerAgent(Agent):
    """Schedules deployment window or waits for scheduled window"""

    def __init__(self):
        super().__init__("deployment_scheduler")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Schedule deployment or await deployment window"""
        change = state.context.get("change_context", {})

        # Check if there's an existing deployment window
        window = change.get("deployment_window")
        if not window:
            window = self._calculate_deployment_window()
            state = self._set_context(state, "change_context", {**change, "deployment_window": window})

        state = self._add_trace(
            state,
            f"✓ Deployment scheduled for {window['start']} UTC",
        )
        return state

    @staticmethod
    def _calculate_deployment_window() -> Dict:
        """Calculate next available deployment window (next day 2 AM UTC)"""
        now = datetime.utcnow()
        next_deployment = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if next_deployment <= now:
            next_deployment += timedelta(days=1)

        return {
            "start": next_deployment.isoformat(),
            "end": (next_deployment + timedelta(hours=4)).isoformat(),
            "duration_minutes": 240,
        }


class DeploymentCheckerAgent(Agent):
    """Performs pre-deployment health checks"""

    def __init__(self):
        super().__init__("deployment_checker")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Check system readiness for deployment"""
        change = state.context.get("change_context", {})
        affected_services = change.get("affected_services", [])

        # Simulate health checks
        all_healthy = await self._check_all_services(affected_services)

        if all_healthy:
            state = self._add_trace(state, f"✓ Pre-deployment checks passed")
            state.context["decision_result"] = "ready"
        else:
            state = self._add_trace(state, f"✗ Pre-deployment checks failed")
            state.context["decision_result"] = "not_ready"

        return state

    @staticmethod
    async def _check_all_services(services: list) -> bool:
        """Simulate health checks on all services"""
        # In production, would actually call health endpoints
        return True


class DeployerAgent(Agent):
    """Executes change deployment or rollback"""

    def __init__(self):
        super().__init__("deployer")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Deploy change or execute rollback"""
        change = state.context.get("change_context", {})
        action = state.context.get("deployment_action", "deploy")

        if action == "rollback":
            return await self._execute_rollback(state)
        else:
            return await self._execute_deployment(state)

    async def _execute_deployment(self, state: WorkflowState) -> WorkflowState:
        """Execute change deployment"""
        change = state.context.get("change_context", {})

        # Simulate deployment
        deployment_id = f"deploy-{datetime.utcnow().isoformat()}"
        result = {
            "deployment_id": deployment_id,
            "status": "success",
            "deployed_at": datetime.utcnow().isoformat(),
            "affected_services": change.get("affected_services", []),
        }

        state = self._set_context(state, "deployment_result", result)
        state = self._add_trace(state, f"✓ Deployment executed: {deployment_id}")
        state.context["decision_result"] = "success"
        return state

    async def _execute_rollback(self, state: WorkflowState) -> WorkflowState:
        """Execute change rollback"""
        change = state.context.get("change_context", {})
        rollback_plan = change.get("rollback_plan", {})

        # Simulate rollback
        rollback_id = f"rollback-{datetime.utcnow().isoformat()}"
        result = {
            "rollback_id": rollback_id,
            "status": "success",
            "rolled_back_at": datetime.utcnow().isoformat(),
            "previous_version": rollback_plan.get("version"),
        }

        state = self._set_context(state, "rollback_result", result)
        state = self._add_trace(state, f"✓ Rollback executed: {rollback_id}")
        state.context["decision_result"] = "success"
        return state


class DeploymentVerifierAgent(Agent):
    """Verifies deployment or rollback success"""

    def __init__(self):
        super().__init__("deployment_verifier")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Verify deployment or rollback"""
        if "deployment_result" in state.context:
            return await self._verify_deployment(state)
        elif "rollback_result" in state.context:
            return await self._verify_rollback(state)
        else:
            state = self._add_trace(state, "✗ No deployment or rollback to verify")
            state.context["decision_result"] = "failed"
            return state

    async def _verify_deployment(self, state: WorkflowState) -> WorkflowState:
        """Verify deployment success"""
        deployment = state.context.get("deployment_result", {})
        deployment_id = deployment.get("deployment_id")

        # Simulate verification
        is_healthy = await self._check_deployment_health(deployment_id)

        if is_healthy:
            state = self._add_trace(state, f"✓ Deployment verified: {deployment_id}")
            state.context["decision_result"] = "success"
        else:
            state = self._add_trace(state, f"✗ Deployment verification failed")
            state.context["decision_result"] = "failed"

        return state

    async def _verify_rollback(self, state: WorkflowState) -> WorkflowState:
        """Verify rollback success"""
        rollback = state.context.get("rollback_result", {})
        rollback_id = rollback.get("rollback_id")

        # Simulate verification
        is_healthy = await self._check_deployment_health(rollback_id)

        if is_healthy:
            state = self._add_trace(state, f"✓ Rollback verified: {rollback_id}")
            state.context["decision_result"] = "success"
        else:
            state = self._add_trace(state, f"✗ Rollback verification failed")
            state.context["decision_result"] = "failed"

        return state

    @staticmethod
    async def _check_deployment_health(deployment_id: str) -> bool:
        """Simulate health check on deployed services"""
        # In production, would call health endpoints and smoke tests
        return True


class ValidationAgent(Agent):
    """Performs post-deployment validation and smoke tests"""

    def __init__(self):
        super().__init__("validation_agent")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Run post-deployment validation"""
        change = state.context.get("change_context", {})
        affected_services = change.get("affected_services", [])

        # Simulate smoke tests
        tests_passed = await self._run_smoke_tests(affected_services)

        if tests_passed:
            state = self._add_trace(state, f"✓ Post-deployment validation passed")
            state.context["decision_result"] = "passed"
        else:
            state = self._add_trace(state, f"✗ Validation failed")
            state.context["decision_result"] = "failed"

        return state

    @staticmethod
    async def _run_smoke_tests(services: list) -> bool:
        """Simulate smoke test execution"""
        # In production, would run actual test suite
        return True


class DocumentationServiceAgent(Agent):
    """Documents change execution and creates records"""

    def __init__(self):
        super().__init__("documentation_service")

    async def run(self, state: WorkflowState) -> WorkflowState:
        """Document change completion"""
        change = state.context.get("change_context", {})
        deployment = state.context.get("deployment_result", {})

        documentation = {
            "change_id": state.workflow_id,
            "change_type": change.get("change_type"),
            "deployed_at": deployment.get("deployed_at"),
            "deployed_by": "automation",
            "affected_services": deployment.get("affected_services", []),
            "documentation_link": f"https://wiki.example.com/changes/{state.workflow_id}",
        }

        state = self._set_context(state, "change_documentation", documentation)
        state = self._add_trace(
            state,
            f"✓ Change documented: {documentation['documentation_link']}",
        )
        return state
