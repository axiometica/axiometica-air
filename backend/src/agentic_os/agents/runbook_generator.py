"""
RunbookGenerator Agent - Auto-generate remediation runbooks for novel incident types.

This agent analyzes novel incidents (ones without exact runbook matches) and uses LLM
to generate appropriate remediation steps. The generated runbooks are validated,
saved for future use, and require human approval before first application.

Part of Phase 3 of AI Improvements Roadmap.
"""

import json
import logging
from typing import Optional, Dict, List, Any, Tuple
from uuid import uuid4
import asyncio
from datetime import datetime

from agentic_os.agents.base import Agent
from agentic_os.core.models import WorkflowState, LifecycleState
from agentic_os.core.context_schema import (
    IncidentWorkflowContext,
    Proposal,
    RunbookStep,
)

logger = logging.getLogger(__name__)


class RunbookGeneratorAgent(Agent):
    """
    Generates remediation runbooks for novel incident types.

    Execution Flow:
    1. Check if exact runbook exists for incident type
    2. If not, find similar historical runbooks
    3. Use LLM to generate candidate remediation steps
    4. Validate generated runbook against constraints
    5. Save and mark as requiring human approval

    Prerequisite: MechanicAgent has failed to find matching runbook
    """

    LLM_MAX_TOKENS = 2000
    LLM_TEMPERATURE = 0.7

    _SYSTEM_PROMPT = (
        "You are an expert SRE engineer generating incident remediation runbooks. "
        "Respond with ONLY valid JSON — no markdown fences, no extra text."
    )

    # Similarity threshold for finding related runbooks
    SIMILARITY_THRESHOLD = 0.5
    MAX_SIMILAR_RUNBOOKS = 5

    # Confidence thresholds
    CONFIDENCE_THRESHOLD_FOR_AUTO_USE = 0.85
    CONFIDENCE_THRESHOLD_FOR_REVIEW = 0.65

    def __init__(self):
        """Initialize RunbookGenerator agent."""
        super().__init__("runbook_generator")
        self.version = "1.0.0"

        # Runbook storage (in production: query database)
        self.generated_runbooks = {}

        # Seed catalog with known reference runbooks so the LLM has examples to learn from.
        # In production this would be loaded from the runbooks database table.
        self.runbook_catalog = {
            "high_cpu": {
                "id": "rb-high-cpu-001",
                "name": "High CPU Usage Remediation",
                "anomaly_type": "high_cpu",
                "resource_type": "pod",
                "success_rate": 87,
                "diagnostics_steps": [
                    {"order": 1, "name": "Collect CPU metrics", "tool": "get_metrics",
                     "args": {"metric": "cpu_percent", "window": "5m"}, "description": "Sample CPU over 5 minutes"},
                    {"order": 2, "name": "Identify top processes", "tool": "collect_logs",
                     "args": {"source": "top", "lines": 20}, "description": "Find which processes are consuming CPU"},
                ],
                "remediation_steps": [
                    {"order": 1, "name": "Scale out deployment", "tool": "scale_pods",
                     "args": {"replicas": "+2"}, "description": "Add 2 more pod replicas to distribute load"},
                    {"order": 2, "name": "Verify CPU drops", "tool": "get_metrics",
                     "args": {"metric": "cpu_percent", "window": "2m"}, "description": "Confirm CPU normalised"},
                ],
                "rollback_steps": [
                    {"order": 1, "name": "Scale back", "tool": "scale_pods", "args": {"replicas": "-2"}},
                ],
                "verification_steps": [
                    {"order": 1, "name": "CPU check", "tool": "get_metrics",
                     "args": {"metric": "cpu_percent"}, "expected_result": "cpu < 70%"},
                ],
                "estimated_blast_radius": 1,
                "estimated_duration_seconds": 180,
                "requires_approval": False,
                "main_args": {},
            },
            "high_syscall_intensity": {
                "id": "rb-syscall-001",
                "name": "High Syscall Intensity - Process Termination",
                "anomaly_type": "high_syscall_intensity",
                "resource_type": "pod",
                "success_rate": 92,
                "diagnostics_steps": [
                    {"order": 1, "name": "Identify offending process", "tool": "get_metrics",
                     "args": {"metric": "syscall_rate"},
                     "description": "Find which process is generating syscalls — outputs top_process for step 2"},
                ],
                "remediation_steps": [
                    # process_name_from_step: 1 means: take the "top_process" output from
                    # diagnostic step 1 (get_metrics) and use it as process_name here.
                    # Falls back to anomaly_process from the alert if step 1 has no output.
                    {"order": 1, "name": "Terminate offending process", "tool": "process_kill",
                     "args": {"process_name_from_step": 1, "signal": "SIGKILL"},
                     "description": "Kill the process identified by step 1"},
                ],
                "rollback_steps": [],
                "verification_steps": [
                    {"order": 1, "name": "Syscall rate check", "tool": "get_metrics",
                     "args": {"metric": "syscall_rate"}, "expected_result": "syscall_rate < 1000/s"},
                ],
                "estimated_blast_radius": 1,
                "estimated_duration_seconds": 60,
                "requires_approval": True,
                "main_args": {"signal": "SIGKILL"},
            },
            "disk_full": {
                "id": "rb-disk-001",
                "name": "Disk Full - Log Rotation and Cleanup",
                "anomaly_type": "disk_full",
                "resource_type": "node",
                "success_rate": 95,
                "diagnostics_steps": [
                    {"order": 1, "name": "Check disk usage", "tool": "get_metrics",
                     "args": {"metric": "disk_usage_percent"}, "description": "Identify which mount is full"},
                    {"order": 2, "name": "Find large files", "tool": "collect_logs",
                     "args": {"source": "du -sh /*", "lines": 30}, "description": "Find large directories"},
                ],
                "remediation_steps": [
                    {"order": 1, "name": "Rotate logs", "tool": "run_script",
                     "args": {"script": "logrotate -f /etc/logrotate.conf"}, "description": "Force log rotation"},
                    {"order": 2, "name": "Clean tmp", "tool": "run_script",
                     "args": {"script": "find /tmp -mtime +7 -delete"}, "description": "Remove files older than 7 days"},
                ],
                "rollback_steps": [],
                "verification_steps": [
                    {"order": 1, "name": "Disk check", "tool": "get_metrics",
                     "args": {"metric": "disk_usage_percent"}, "expected_result": "disk < 80%"},
                ],
                "estimated_blast_radius": 1,
                "estimated_duration_seconds": 120,
                "requires_approval": False,
                "main_args": {},
            },
        }

    def _get_provider(self):
        """Return the platform LLMProvider — reads DB config on every call so provider
        changes take effect immediately without restarting the worker."""
        from agentic_os.services.summary_service import get_summary_service
        return get_summary_service().provider

    async def run(self, workflow_state: WorkflowState) -> WorkflowState:
        """
        Main execution method required by Agent base class.

        Args:
            workflow_state: Current workflow state with context

        Returns:
            Updated workflow state with generated runbook if applicable
        """
        return await self.execute(workflow_state)

    async def execute(self, workflow_state: WorkflowState) -> WorkflowState:
        """
        Implementation of runbook generation logic.

        Args:
            workflow_state: Current workflow state with context

        Returns:
            Updated workflow state with generated runbook if applicable
        """
        # Get typed context — fail fast if context reconstruction fails
        try:
            ctx = workflow_state.get_context()
        except Exception as e:
            logger.error(f"[RunbookGenerator] Failed to get typed context: {e}", exc_info=True)
            workflow_state.add_trace(f"RunbookGenerator: Could not load context ({type(e).__name__}), skipping")
            return workflow_state

        logger.info(f"[RunbookGenerator] Context loaded ({type(ctx).__name__})")

        # Check prerequisites — log to both context trace and workflow state trace
        if not ctx.sentinel:
            msg = "RunbookGenerator: No sentinel context available, skipping runbook generation"
            ctx.reasoning_trace.append(msg)
            workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")
            workflow_state.set_context(ctx)
            return workflow_state

        if not ctx.cmdb:
            msg = "RunbookGenerator: No CMDB context available, skipping runbook generation"
            ctx.reasoning_trace.append(msg)
            workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")
            workflow_state.set_context(ctx)
            return workflow_state

        if ctx.proposal and hasattr(ctx.proposal, 'runbook_id') and ctx.proposal.runbook_id:
            # Check whether MechanicAgent found a real runbook or just a fallback escalation.
            # Fallback proposals get runbook_id="fallback-escalate" and no remediation steps.
            is_fallback = (
                ctx.proposal.runbook_id == "fallback-escalate"
                or not ctx.proposal.remediation_steps
            )

            if not is_fallback:
                # Real runbook was matched — RunbookGenerator not needed
                msg = (
                    f"Real runbook matched by MechanicAgent "
                    f"('{ctx.proposal.runbook_name}'), skipping AI generation"
                )
                ctx.reasoning_trace.append(f"RunbookGenerator: {msg}")
                workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")
                workflow_state.set_context(ctx)
                return workflow_state

            # Fallback escalation — MechanicAgent found nothing useful, try AI generation
            workflow_state.add_trace(
                f"[RUNBOOK GENERATOR] MechanicAgent only produced a fallback escalation for "
                f"'{ctx.sentinel.anomaly_type}' — attempting AI runbook generation"
            )

        # Novel incident — no runbook found by MechanicAgent
        anomaly_type = ctx.sentinel.anomaly_type
        workflow_state.add_trace(
            f"[RUNBOOK GENERATOR] Novel incident type '{anomaly_type}' — no existing runbook found, "
            f"checking for similar runbooks to use as LLM generation reference"
        )
        ctx.reasoning_trace.append(f"RunbookGenerator: Evaluating novel incident type '{anomaly_type}'")

        # Step 1: Check for exact match
        existing_runbook = self._load_runbook(anomaly_type)
        if existing_runbook:
            ctx.reasoning_trace.append(f"RunbookGenerator: Found existing runbook, using it")
            ctx.proposal = self._create_proposal_from_runbook(existing_runbook)
            workflow_state.set_context(ctx)
            return workflow_state

        ctx.reasoning_trace.append(f"RunbookGenerator: Novel incident type '{anomaly_type}', generating runbook")

        # Step 2: Find similar runbooks as reference
        similar_runbooks = self._find_similar_runbooks(anomaly_type, ctx)

        if not similar_runbooks:
            msg = "No similar runbooks found in catalog — AI generation requires reference runbooks. Skipping."
            ctx.reasoning_trace.append(f"RunbookGenerator: {msg}")
            workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")
            workflow_state.set_context(ctx)
            return workflow_state

        msg = f"Found {len(similar_runbooks)} reference runbook(s) — generating AI runbook via LLM"
        ctx.reasoning_trace.append(f"RunbookGenerator: {msg}")
        workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")

        # Step 3: Generate runbook using LLM
        try:
            generated_runbook = await self._generate_runbook_with_llm(
                ctx.sentinel,
                ctx.cmdb,
                ctx.risk if ctx.risk else None,
                similar_runbooks
            )
        except Exception as e:
            msg = f"LLM runbook generation failed: {str(e)}"
            ctx.reasoning_trace.append(f"RunbookGenerator: {msg}")
            workflow_state.add_trace(f"[RUNBOOK GENERATOR] {msg}")
            logger.error(f"Runbook generation failed: {e}", exc_info=True)
            workflow_state.set_context(ctx)
            return workflow_state

        # Step 4: Validate generated runbook
        validation_result = self._validate_runbook(
            generated_runbook,
            ctx.sentinel,
            ctx.cmdb,
            ctx.risk if ctx.risk else None
        )

        if not validation_result['valid']:
            ctx.reasoning_trace.append(
                f"RunbookGenerator: Validation failed: {', '.join(validation_result['issues'])}"
            )
            logger.warning(f"Generated runbook failed validation: {validation_result['issues']}")
            workflow_state.set_context(ctx)
            return workflow_state

        ctx.reasoning_trace.append(
            f"RunbookGenerator: Generated runbook validated successfully "
            f"(warnings: {len(validation_result.get('warnings', []))})"
        )

        # Step 5: Save generated runbook
        runbook_id = await self._save_generated_runbook(
            anomaly_type,
            generated_runbook,
            similar_runbooks,
            validation_result
        )

        # Step 6: Create proposal from generated runbook
        ctx.proposal = Proposal(
            runbook_id=str(runbook_id),
            runbook_name=generated_runbook.get('name', anomaly_type),
            diagnostics_steps=[
                RunbookStep(
                    order=i,
                    type="diagnostic",
                    name=step.get('name', f"Diagnostic Step {i+1}"),
                    description=step.get('description', ''),
                    tool=step.get('tool', 'echo'),
                    args_json=step.get('args', {}),
                )
                for i, step in enumerate(generated_runbook.get('diagnostics_steps', []))
            ],
            remediation_steps=[
                RunbookStep(
                    order=i,
                    type="remediation",
                    name=step.get('name', f"Remediation Step {i+1}"),
                    description=step.get('description', ''),
                    tool=step.get('tool', 'echo'),
                    args_json=step.get('args', {}),
                )
                for i, step in enumerate(generated_runbook.get('remediation_steps', []))
            ],
            confidence=validation_result.get('confidence_score', 0.45),
            blast_radius=generated_runbook.get('estimated_blast_radius', 2),
            approval_required=True,  # Generated runbooks always require approval
            main_args=generated_runbook.get('main_args', {}),
            source="llm_generated",  # AI-generated — distinct from ops-authored library entries
        )

        # Mark as auto-generated (always requires human review)
        gen_msg = (
            f"AI-generated runbook saved (ID: {runbook_id}, "
            f"confidence: {validation_result.get('confidence_score', 0.45):.1%}). "
            f"Human review required before this runbook can be used for auto-remediation."
        )
        ctx.reasoning_trace.append(f"RunbookGenerator: {gen_msg}")
        workflow_state.add_trace(f"[RUNBOOK GENERATOR] {gen_msg}")

        workflow_state.set_context(ctx)
        return workflow_state

    def _load_runbook(self, anomaly_type: str) -> Optional[Dict[str, Any]]:
        """
        Load existing runbook for incident type.

        Lookup priority:
        1. DB enabled runbook  → convert to catalog format and return
        2. DB disabled runbook → return None (admin disabled it; respect the decision,
                                 do NOT silently fall back to the in-memory entry)
        3. No DB entry at all  → fall back to in-memory catalog (truly novel type)

        Args:
            anomaly_type: Incident anomaly type

        Returns:
            Runbook dict if a usable runbook exists, None otherwise
        """
        try:
            from agentic_os.db.database import SessionLocal
            from agentic_os.db.models import RunbookModel
            from agentic_os.connectors.event_type_utils import normalize_event_type
            canonical_type = normalize_event_type(anomaly_type)

            db = SessionLocal()
            try:
                # Check DB for ANY runbook matching this event_type (enabled or disabled)
                db_rb = db.query(RunbookModel).filter(
                    RunbookModel.event_type == canonical_type,
                ).order_by(RunbookModel.enabled.desc()).first()  # enabled first

                if db_rb is not None:
                    if not db_rb.enabled:
                        # Admin explicitly disabled this runbook — don't use in-memory fallback
                        logger.info(
                            f"[RUNBOOK GENERATOR] Runbook '{db_rb.name}' for event_type "
                            f"'{anomaly_type}' exists in DB but is DISABLED. "
                            f"Skipping in-memory catalog fallback."
                        )
                        return None

                    # Enabled DB runbook found — convert to catalog format
                    logger.info(
                        f"[RUNBOOK GENERATOR] Using enabled DB runbook '{db_rb.name}' "
                        f"(id={db_rb.id}) for event_type '{anomaly_type}'."
                    )
                    return {
                        "id": str(db_rb.id),
                        "name": db_rb.name,
                        "anomaly_type": anomaly_type,
                        "resource_type": "pod",
                        "success_rate": int((db_rb.confidence or 0.85) * 100),
                        "diagnostics_steps": db_rb.diagnostics or [],
                        "remediation_steps": db_rb.actions or [],
                        "rollback_steps": [],
                        "verification_steps": db_rb.verification_steps or [],
                        "estimated_blast_radius": db_rb.blast_radius or 1,
                        "estimated_duration_seconds": 120,
                        "requires_approval": True,
                        "main_args": {},
                        "source": "runbook_library",
                    }

            finally:
                db.close()

        except Exception as exc:
            logger.warning(
                f"[RUNBOOK GENERATOR] DB runbook lookup failed for '{anomaly_type}': {exc}. "
                f"Falling back to in-memory catalog."
            )

        # No DB entry at all (or DB unreachable) → use in-memory catalog
        catalog_entry = self.runbook_catalog.get(anomaly_type)
        if catalog_entry:
            logger.info(
                f"[RUNBOOK GENERATOR] No DB entry for '{anomaly_type}', "
                f"using in-memory catalog entry '{catalog_entry.get('name')}'."
            )
        return catalog_entry

    def _find_similar_runbooks(
        self,
        anomaly_type: str,
        ctx: IncidentWorkflowContext
    ) -> List[Dict[str, Any]]:
        """
        Find runbooks similar to the novel incident.

        Args:
            anomaly_type: Incident anomaly type
            ctx: Incident workflow context

        Returns:
            List of similar runbooks (up to MAX_SIMILAR_RUNBOOKS)
        """
        # In production: use embeddings for similarity search
        # For now: structural similarity based on resource type

        resource_type = ctx.cmdb.resource_info.type if (ctx.cmdb and ctx.cmdb.resource_info) else "unknown"

        # Try exact resource_type match first
        by_type = [rb for rb in self.runbook_catalog.values()
                   if rb.get('resource_type') == resource_type]

        # Fall back to all catalog entries when no type match (still useful as LLM examples)
        similar = by_type if by_type else list(self.runbook_catalog.values())

        return similar[:self.MAX_SIMILAR_RUNBOOKS]

    async def _generate_runbook_with_llm(
        self,
        sentinel_context: Any,
        cmdb_context: Any,
        risk_context: Optional[Any],
        similar_runbooks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Use LLM to generate remediation runbook.

        Args:
            sentinel_context: Incident detection context
            cmdb_context: CMDB/resource context
            risk_context: Risk assessment context
            similar_runbooks: Reference runbooks

        Returns:
            Generated runbook structure
        """
        provider = self._get_provider()
        if not provider.is_configured():
            raise RuntimeError("[RunbookGenerator] LLM is not configured — go to Settings → LLM")

        user_prompt = self._build_generation_prompt(
            sentinel_context,
            cmdb_context,
            risk_context,
            similar_runbooks
        )

        logger.debug(
            "[RunbookGenerator] Sending prompt (%d chars) via %s",
            len(user_prompt), type(provider).__name__,
        )

        generated_text = await provider.generate_agent_completion(
            system_prompt=self._SYSTEM_PROMPT,
            user_content=user_prompt,
            max_tokens=self.LLM_MAX_TOKENS,
            temperature=self.LLM_TEMPERATURE,
        )
        if not generated_text:
            raise RuntimeError("[RunbookGenerator] LLM returned empty response")

        # Parse LLM response into runbook structure
        runbook = self._parse_llm_response(generated_text)

        return runbook

    def _build_generation_prompt(
        self,
        sentinel_context: Any,
        cmdb_context: Any,
        risk_context: Optional[Any],
        similar_runbooks: List[Dict[str, Any]]
    ) -> str:
        """Build LLM prompt for runbook generation."""

        anomaly_type = sentinel_context.anomaly_type
        resource_name = cmdb_context.resource_name if cmdb_context else "unknown"
        environment = cmdb_context.environment if cmdb_context else "unknown"

        prompt = f"""NOVEL INCIDENT:
- Type: {anomaly_type}
- Affected Resource: {resource_name}
- Environment: {environment}
- Detected By: Sentinel/eBPF monitoring
- Alert: {sentinel_context.alert_payload.message if hasattr(sentinel_context.alert_payload, 'message') else 'Unknown'}

"""

        if risk_context:
            prompt += f"""RISK ASSESSMENT:
- Risk Score: {risk_context.risk_score:.1f}/10
- Blast Radius: L{risk_context.blast_radius}
- Complexity: {risk_context.remediation_complexity}

"""

        prompt += f"""SIMILAR SUCCESSFUL RUNBOOKS (as reference):
"""
        for i, runbook in enumerate(similar_runbooks[:3], 1):
            prompt += f"""
Runbook {i}: {runbook.get('name', 'Unknown')}
- Diagnostics: {len(runbook.get('diagnostics_steps', []))} steps
- Remediation: {len(runbook.get('remediation_steps', []))} steps
- Success Rate: {runbook.get('success_rate', 'unknown')}%
"""

        prompt += """
AVAILABLE TOOLS:
- process_kill: Kill or restart processes
- scale_pods: Scale Kubernetes deployments
- drain_node: Drain node from cluster (requires approval)
- force_restart: Force restart service
- pause_workload: Pause workload execution
- collect_logs: Collect application logs
- execute_query: Execute system/database queries
- get_metrics: Query monitoring system — returns "top_process" and "syscall_count" in output
- run_script: Execute remediation script

STEP OUTPUT CHAINING:
When a diagnostic step discovers a value (e.g. get_metrics identifies the offending process),
subsequent steps can reference it using "process_name_from_step": <step_order> in their args.
This passes the "top_process" output from that diagnostic step as the process_name.
Example: if diagnostic step 1 uses get_metrics to identify the process, then remediation step 1
can use: {{ "process_name_from_step": 1, "signal": "SIGKILL" }}

CONSTRAINTS:
- Generate 2-3 diagnostic steps (non-destructive, safe to run)
- Generate 2-4 remediation steps (ordered, each building on previous)
- When process_kill is used and a prior get_metrics step identifies the process, use "process_name_from_step" NOT a hardcoded name
- Scale operations require approval if blast_radius > 2
- Estimated blast radius must be reasonable for incident severity
- Include rollback procedure (must be automated)
- Include 2-3 verification steps (check if remediation worked)
- Do NOT reference tools not in available list
- Do NOT use unresolvable parameters

GENERATE JSON RUNBOOK with structure:
{{
  "name": "Human-readable runbook name",
  "description": "What this runbook does",
  "estimated_blast_radius": 2,
  "estimated_duration_seconds": 300,
  "requires_approval": true/false,
  "diagnostics_steps": [
    {{
      "order": 1,
      "name": "Step name",
      "description": "What this step does",
      "tool": "tool_name",
      "args": {{"param": "value"}},
      "expected_result": "What we expect to see"
    }}
  ],
  "remediation_steps": [
    {{
      "order": 1,
      "name": "Step name",
      "description": "What this step does",
      "tool": "tool_name",
      "args": {{"param": "value"}},
      "expected_result": "What we expect after running"
    }}
  ],
  "rollback_steps": [
    {{"order": 1, "name": "Rollback step", "tool": "...", "args": {{}}}}
  ],
  "verification_steps": [
    {{"order": 1, "name": "Verification", "tool": "...", "expected_result": "..."}}
  ],
  "main_args": {{"key": "value"}},
  "resource_type": "pod|service|node|etc",
  "estimated_time_to_resolution_seconds": 300
}}

Generate ONLY the JSON runbook, no additional text.
Ensure all steps use available tools.
Ensure all parameters are resolvable.
Focus on safety and clarity."""

        return prompt

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse LLM response into runbook structure.

        Args:
            response_text: Raw LLM response

        Returns:
            Parsed runbook dict
        """
        # Extract JSON from response
        try:
            # Try direct JSON parsing first
            runbook = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to find JSON in response (LLM might include extra text)
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                runbook = json.loads(json_match.group())
            else:
                # Fallback: create minimal runbook
                logger.warning("Could not parse LLM response as JSON, using fallback")
                runbook = {
                    "name": "Generated Runbook",
                    "description": "Auto-generated runbook for novel incident",
                    "diagnostics_steps": [],
                    "remediation_steps": [],
                    "rollback_steps": [],
                    "verification_steps": [],
                    "estimated_blast_radius": 2,
                    "estimated_duration_seconds": 300,
                    "requires_approval": True
                }

        return runbook

    def _validate_runbook(
        self,
        runbook: Dict[str, Any],
        sentinel_context: Any,
        cmdb_context: Any,
        risk_context: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Validate generated runbook against constraints.

        Args:
            runbook: Generated runbook
            sentinel_context: Incident context
            cmdb_context: CMDB context
            risk_context: Risk context

        Returns:
            Validation result with valid, issues, warnings, confidence_score
        """
        issues = []
        warnings = []
        # AI-generated runbooks start at a low baseline — they have not been
        # authored or reviewed by an operator.  The maximum attainable score
        # is capped below operator-authored runbooks (0.85) regardless of how
        # well the LLM structured the output.
        # Baseline: 0.45  Cap: 0.60
        AI_CONFIDENCE_BASELINE = 0.45
        AI_CONFIDENCE_CAP      = 0.60
        confidence_score = AI_CONFIDENCE_BASELINE

        # Required fields
        required_fields = ['name', 'diagnostics_steps', 'remediation_steps', 'verification_steps']
        for field in required_fields:
            if field not in runbook or not runbook[field]:
                issues.append(f"Missing or empty required field: {field}")
                confidence_score -= 0.05  # smaller deduction from already-low baseline

        # Check all steps use available tools
        available_tools = {
            'process_kill', 'scale_pods', 'drain_node', 'force_restart',
            'pause_workload', 'collect_logs', 'execute_query', 'get_metrics', 'run_script'
        }

        all_steps = (
            runbook.get('diagnostics_steps', []) +
            runbook.get('remediation_steps', []) +
            runbook.get('rollback_steps', []) +
            runbook.get('verification_steps', [])
        )

        for step in all_steps:
            tool = step.get('tool', '')
            step_type = step.get('step_type', '') or step.get('type', '')
            # 'wait' is a built-in step type, not a catalog tool — skip tool check
            if step_type == 'wait' or tool == 'wait':
                continue
            if tool not in available_tools:
                issues.append(f"Step '{step.get('name')}' uses unavailable tool: {tool}")
                confidence_score -= 0.05

        # Check blast radius
        estimated_radius = runbook.get('estimated_blast_radius', 2)
        if risk_context and risk_context.risk_score > 8:
            if estimated_radius > 3:
                warnings.append("High blast radius for critical incident - requires close monitoring")
                confidence_score -= 0.05

        # Check rollback automation
        if not runbook.get('rollback_steps'):
            warnings.append("No rollback steps defined - recommend adding")
            confidence_score -= 0.05

        # Clamp: AI-generated runbooks can earn up to AI_CONFIDENCE_CAP (0.60)
        # but never reach operator-authored territory regardless of validation quality.
        confidence_score = max(0.0, min(AI_CONFIDENCE_CAP, confidence_score))

        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'warnings': warnings,
            'confidence_score': confidence_score
        }

    async def _save_generated_runbook(
        self,
        anomaly_type: str,
        runbook: Dict[str, Any],
        similar_runbooks: List[Dict[str, Any]],
        validation_result: Dict[str, Any]
    ) -> str:
        """
        Save generated runbook for future use.

        Args:
            anomaly_type: Incident type
            runbook: Generated runbook
            similar_runbooks: Reference runbooks used
            validation_result: Validation results

        Returns:
            Generated runbook ID
        """
        runbook_id = str(uuid4())

        # Add metadata
        runbook['id'] = runbook_id
        runbook['anomaly_type'] = anomaly_type
        runbook['generated'] = True
        runbook['created_at'] = datetime.utcnow().isoformat()
        runbook['source_runbooks'] = [rb.get('id') for rb in similar_runbooks]
        runbook['validation'] = validation_result
        runbook['approval_status'] = 'pending_human_review'

        # In production: save to database
        self.generated_runbooks[runbook_id] = runbook

        logger.info(f"Saved generated runbook {runbook_id} for {anomaly_type}")

        return runbook_id

    def _create_proposal_from_runbook(self, runbook: Dict[str, Any]) -> Proposal:
        """Create proposal from existing runbook."""
        return Proposal(
            runbook_id=runbook.get('id', str(uuid4())),
            runbook_name=runbook.get('name', 'Unknown Runbook'),
            diagnostics_steps=[
                RunbookStep(
                    order=step.get('order', i),
                    type="diagnostic",
                    name=step.get('name', f"Step {i+1}"),
                    description=step.get('description', ''),
                    tool=step.get('tool', 'echo'),
                    args_json=step.get('args', {}),
                )
                for i, step in enumerate(runbook.get('diagnostics_steps', []))
            ],
            remediation_steps=[
                RunbookStep(
                    order=step.get('order', i),
                    type="remediation",
                    name=step.get('name', f"Step {i+1}"),
                    description=step.get('description', ''),
                    tool=step.get('tool', 'echo'),
                    args_json=step.get('args', {}),
                )
                for i, step in enumerate(runbook.get('remediation_steps', []))
            ],
            confidence=runbook.get('validation', {}).get('confidence_score', 0.45),
            blast_radius=runbook.get('estimated_blast_radius', 2),
            approval_required=runbook.get('requires_approval', True),
            main_args=runbook.get('main_args', {}),
            source=runbook.get('source', 'runbook_library'),  # Honour source from catalog entry
        )
