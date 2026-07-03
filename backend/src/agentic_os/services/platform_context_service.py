"""
Platform Context Service - Generates native incident summaries without LLM dependency.
Provides sophisticated context-based summaries for incidents using platform data.
"""

import logging
from typing import TYPE_CHECKING, Optional, Dict, Any
from agentic_os.db.models import WorkflowStateModel
from agentic_os.core.models import Severity, LifecycleState

if TYPE_CHECKING:
    from agentic_os.core.models import WorkflowState

logger = logging.getLogger(__name__)


class PlatformContextService:
    """Generates native incident summaries using platform context"""

    # Severity descriptions for context
    SEVERITY_DESCRIPTIONS = {
        Severity.CRITICAL: {
            "impact": "This is a critical issue requiring immediate action",
            "urgency": "urgent immediate attention",
            "consequences": "potential system-wide outage or data loss"
        },
        Severity.HIGH: {
            "impact": "This is a high-severity issue requiring prompt attention",
            "urgency": "priority action needed",
            "consequences": "significant service degradation or data risk"
        },
        Severity.MEDIUM: {
            "impact": "This is a medium-severity issue requiring attention",
            "urgency": "standard priority action",
            "consequences": "moderate service impact or partial functionality loss"
        },
        Severity.LOW: {
            "impact": "This is a low-severity issue requiring tracking",
            "urgency": "scheduled action",
            "consequences": "minimal service impact"
        },
        Severity.INFO: {
            "impact": "This is an informational event",
            "urgency": "monitoring only",
            "consequences": "no direct service impact"
        }
    }

    # Event type descriptions
    EVENT_DESCRIPTIONS = {
        "high_cpu": {
            "what": "CPU usage exceeded safe thresholds",
            "meaning": "The system is under heavy computational load, which could indicate performance degradation, runaway processes, or insufficient resource allocation",
            "implications": "May lead to slower response times, timeouts, or service unavailability if not addressed"
        },
        "disk_full": {
            "what": "Disk capacity reached critical levels",
            "meaning": "Storage space is nearly or completely exhausted, preventing new data writes and potentially causing application failures",
            "implications": "Can cause immediate service failures, data loss, or system crashes if disk space is not freed"
        },
        "service_down": {
            "what": "Service became unavailable",
            "meaning": "The service failed to respond or is completely offline, indicating either a crash, deployment failure, or infrastructure issue",
            "implications": "Users cannot access the service, resulting in complete service outage and business impact"
        },
        "database_error": {
            "what": "Database connectivity or operational failure occurred",
            "meaning": "The application cannot reach the database or the database is failing operations, blocking critical business functions",
            "implications": "Dependent applications and services are unable to persist or retrieve data, causing cascading failures"
        },
        "database_connection_failure": {
            "what": "Database connection pool exhausted or connections refused",
            "meaning": "The application cannot establish new database connections due to pool exhaustion or database unavailability",
            "implications": "All database-dependent operations are blocked, causing application hang or failure"
        },
        "network_issue": {
            "what": "Network latency or packet loss detected",
            "meaning": "Network connectivity is degraded, causing increased response times or communication failures",
            "implications": "Services may experience timeouts, retries, or failures if network issues persist"
        },
        "test_spike": {
            "what": "High-intensity testing traffic was detected",
            "meaning": "Load testing or stress testing was performed on the system, generating unusual traffic patterns",
            "implications": "This is typically expected during planned testing and should not indicate actual production issues"
        },
        "test_cpu_spike": {
            "what": "CPU spike detected during testing",
            "meaning": "Testing activity is consuming significant CPU resources to validate system performance",
            "implications": "This is part of planned testing and helps identify performance characteristics under load"
        }
    }

    # Risk score interpretation
    @staticmethod
    def interpret_risk_score(risk_score: float) -> str:
        """Convert numeric risk score to interpretation"""
        if risk_score >= 90:
            return "extremely critical"
        elif risk_score >= 75:
            return "very high"
        elif risk_score >= 60:
            return "high"
        elif risk_score >= 40:
            return "moderate"
        elif risk_score >= 20:
            return "low"
        else:
            return "minimal"

    @staticmethod
    def get_resolution_status(lifecycle_state, execution_result: Optional[Dict[str, Any]]) -> str:
        """Determine resolution status from lifecycle state"""
        # Compare as strings so both LifecycleState enum values and raw DB strings work
        state_val = lifecycle_state.value if hasattr(lifecycle_state, "value") else str(lifecycle_state)
        if state_val == LifecycleState.RESOLVED.value:
            if execution_result and execution_result.get("success"):
                return "was successfully resolved through automated remediation"
            else:
                return "was resolved through manual intervention"
        elif state_val == LifecycleState.FAILED.value:
            return "failed to resolve automatically and requires manual intervention"
        elif state_val == LifecycleState.EXECUTING.value:
            return "is currently being remediated"
        elif state_val in (LifecycleState.WAITING_APPROVAL.value, LifecycleState.APPROVED.value):
            return "is awaiting approval before remediation can proceed"
        elif state_val == LifecycleState.MONITORING.value:
            return "is under post-remediation monitoring"
        elif state_val in (LifecycleState.IN_PROGRESS.value, LifecycleState.OPEN.value):
            return "is currently under investigation"
        else:
            return "is pending triage"

    @staticmethod
    def build_progressive_summary(state: "WorkflowState") -> str:
        """
        Build a progressive narrative from whatever context is in WorkflowState.
        Called after each agent step so the frontend sees live updates while the
        workflow is still running.  Works with the in-memory WorkflowState —
        no database query required.

        The narrative grows as each agent adds its layer:
          Sentinel  → Detection line
          LibrarianAgent → environment
          RiskAssessor → severity / risk score
          MechanicAgent → runbook name
          PolicyBroker → governance decision
          ToolRegistry → execution results
          Verifier    → verification result
        """
        ctx = state.context or {}

        # ── Extract context layers ──────────────────────────────────────────
        sentinel_ctx   = ctx.get("sentinel") or {}
        alert_nested   = sentinel_ctx.get("alert_payload") or {}
        alert_payload  = ctx.get("alert_payload") or alert_nested

        cmdb_ctx       = ctx.get("cmdb") or {}
        risk_ctx       = ctx.get("risk") or {}
        proposal_ctx   = ctx.get("proposal") or {}
        governance_ctx = ctx.get("governance") or {}
        exec_results   = ctx.get("execution_results") or []
        verif_ctx      = ctx.get("verification") or {}

        # ── Core identifiers ────────────────────────────────────────────────
        event_type = (
            alert_payload.get("type")
            or sentinel_ctx.get("anomaly_type", "")
            or "unknown event"
        ).replace("_", " ")

        resource = (
            cmdb_ctx.get("resource_name")
            or alert_payload.get("resource_name", "")
            or "unknown resource"
        )
        environment  = cmdb_ctx.get("environment", "")
        env_str      = f" ({environment} environment)" if environment else ""

        severity_val = (
            (state.severity.value if state.severity else "")
            or alert_payload.get("severity", "")
        )
        risk_score = state.risk_score or risk_ctx.get("risk_score")

        parts: list[str] = []

        # 1. Detection (always present)
        parts.append(f"Detected {event_type} on {resource}{env_str}.")

        # 2. Severity + risk (populated after RiskAssessor)
        if severity_val:
            sev_upper = severity_val.upper()
            if risk_score is not None:
                risk_interp  = PlatformContextService.interpret_risk_score(float(risk_score))
                blast_radius = risk_ctx.get("blast_radius")
                blast_str    = f", blast radius {blast_radius}" if blast_radius else ""
                complexity   = risk_ctx.get("remediation_complexity", "")
                complex_str  = f", {complexity} remediation" if complexity else ""
                parts.append(
                    f"Severity: {sev_upper} — {risk_interp} risk"
                    f" (score {int(float(risk_score))}/100{blast_str}{complex_str})."
                )
            else:
                parts.append(f"Severity assessed as {sev_upper}.")

        # 3. Runbook selected (populated after MechanicAgent)
        runbook_name = proposal_ctx.get("runbook_name", "")
        if runbook_name:
            parts.append(f"Runbook selected: '{runbook_name}'.")

        # 4. Governance decision (populated after PolicyBrokerAgent)
        if governance_ctx:
            approval_required = governance_ctx.get("approval_required", False)
            decision_notes    = governance_ctx.get("decision_notes", "")
            gov_decision      = state.governance_decision or ""
            lc_val            = state.lifecycle_state.value if state.lifecycle_state else ""

            if approval_required:
                if gov_decision == "approved" or lc_val == "approved":
                    gov_str = "Manual approval granted."
                elif gov_decision == "rejected" or lc_val == "rejected":
                    gov_str = "Manual approval rejected — incident closed without remediation."
                else:
                    gov_str = "Awaiting manual approval before remediation can proceed."
            else:
                gov_str = "Auto-approved for automated remediation."

            if decision_notes:
                gov_str = gov_str.rstrip(".") + f" ({decision_notes})."

            parts.append(gov_str)

        # 5. Execution results (populated after ToolRegistryAgent)
        if exec_results:
            n_total   = len(exec_results)
            n_success = sum(
                1 for r in exec_results
                if str(r.get("status", "")).lower() in ("success", "ok", "completed", "passed")
            )
            tools  = ", ".join(r.get("tool", "?") for r in exec_results[:3])
            suffix = f" and {n_total - 3} more" if n_total > 3 else ""
            parts.append(
                f"{n_total} remediation action(s) executed ({tools}{suffix}): "
                f"{n_success}/{n_total} succeeded."
            )

        # 6. Verification (populated after VerifierAgent)
        if verif_ctx:
            overall       = verif_ctx.get("overall_success")
            verif_results = verif_ctx.get("verification_results", [])
            if overall:
                detail = ""
                if verif_results:
                    msgs = [v.get("message", "") for v in verif_results[:2] if v.get("message")]
                    detail = " ".join(msgs)
                parts.append(
                    f"Verification passed — {detail or 'system metrics confirm resolution'}."
                )
            elif overall is False:
                parts.append(
                    "Verification failed — system metrics do not confirm resolution; "
                    "manual intervention may be required."
                )

        # 7. Terminal / notable lifecycle note
        lc_val = state.lifecycle_state.value if state.lifecycle_state else ""
        terminal_msgs = {
            "resolved":         "Incident fully resolved.",
            "failed":           "Automated remediation failed; manual intervention required.",
            "monitoring":       "Resolved — post-remediation monitoring is active.",
            "closed":           "Incident closed.",
            "waiting_approval": "Awaiting manual approval.",
            "rejected":         "Approval rejected — incident closed without remediation.",
        }
        if lc_val in terminal_msgs:
            parts.append(terminal_msgs[lc_val])

        return " ".join(parts)

    @classmethod
    def generate_summary(
        cls,
        incident: WorkflowStateModel,
        max_length: Optional[int] = None
    ) -> str:
        """
        Generate a comprehensive platform context summary for an incident.

        Args:
            incident: WorkflowStateModel instance
            max_length: Optional maximum length for summary (None = no limit)

        Returns:
            Rich incident summary string
        """
        try:
            # Extract key data
            alert_payload = incident.context.get("alert_payload", {})
            event_type = alert_payload.get("type", "unknown_event").lower()
            resource_name = alert_payload.get("resource_name", "unknown_resource")
            severity = incident.severity or Severity.MEDIUM
            risk_score = incident.risk_score or 50
            description = alert_payload.get("description", "")

            # Get event-specific descriptions
            event_info = cls.EVENT_DESCRIPTIONS.get(event_type, {})
            what_happened = event_info.get("what", f"Event of type '{event_type}' occurred")
            what_it_means = event_info.get("meaning", f"This indicates an issue with {resource_name}")
            implications = event_info.get("implications", "")

            # Get severity context
            severity_desc = cls.SEVERITY_DESCRIPTIONS.get(severity, {})
            impact_statement = severity_desc.get("impact", "An issue occurred")

            # Get resolution status
            execution_result = incident.context.get("execution_result")
            resolution_status = cls.get_resolution_status(incident.lifecycle_state, execution_result)

            # Build comprehensive summary
            parts = []

            # Opening: What happened
            parts.append(f"{impact_statement}. {what_happened} on {resource_name}.")

            # Context: What it means
            parts.append(f" {what_it_means}.")

            # Add implications
            if implications:
                parts.append(f" {implications}")

            # Severity and risk context
            risk_interpretation = cls.interpret_risk_score(risk_score)
            severity_name = severity.value.upper() if hasattr(severity, 'value') else str(severity).upper()
            parts.append(f" The incident severity is {severity_name} with a {risk_interpretation} risk profile (score: {int(risk_score)}/100).")

            # Resolution status
            parts.append(f" The incident {resolution_status}.")

            # Add additional context if available
            if description and description != what_happened:
                parts.append(f" Additional context: {description}")

            # Check for specific remediation actions taken
            proposal = incident.context.get("proposal", {})
            if proposal.get("action"):
                action = proposal.get("action", "").replace("_", " ").upper()
                parts.append(f" Remediation action taken: {action}.")

            # Join all parts
            summary = "".join(parts).strip()

            # Truncate if needed
            if max_length and len(summary) > max_length:
                summary = summary[:max_length - 3] + "..."

            logger.debug(f"Generated platform context summary for {incident.workflow_id}")
            return summary

        except Exception as e:
            logger.error(f"Error generating platform context summary: {e}")
            # Fallback to minimal summary
            alert_payload = incident.context.get("alert_payload", {})
            event_type = alert_payload.get("type", "unknown")
            resource_name = alert_payload.get("resource_name", "unknown")
            severity = incident.severity or Severity.MEDIUM
            return f"{event_type.replace('_', ' ').upper()} on {resource_name} (Severity: {severity.value.upper()})"


def get_platform_context_service() -> PlatformContextService:
    """Get platform context service instance"""
    return PlatformContextService()
