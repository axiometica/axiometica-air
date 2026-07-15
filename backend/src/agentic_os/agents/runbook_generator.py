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

    @staticmethod
    def _get_diagnostic_tools() -> List[Dict[str, Any]]:
        """Return the live diagnostic-category tools from the real approved-actions
        catalog. Used for both the LLM prompt and output validation so the two stay
        in sync with the execution engine — previously this was a hardcoded list of
        9 tool names, most of which didn't exist in the catalog at all."""
        from agentic_os.db.approved_actions_seed import APPROVED_ACTIONS
        return [a for a in APPROVED_ACTIONS if a.get("category") == "diagnostic"]

    @staticmethod
    def _format_tool_for_prompt(tool: Dict[str, Any]) -> str:
        """Render one catalog tool as a single prompt line with its exact parameter
        names — without this, the LLM guesses plausible-sounding but wrong arg keys
        (e.g. passing {"url": ...} to a tool that actually takes {"host": ...})."""
        params = tool.get("parameters") or []
        param_strs = []
        for p in params:
            name = p.get("name", "?")
            ptype = p.get("type", "string")
            if p.get("required"):
                param_strs.append(f"{name}: {ptype} (required)")
            else:
                default = p.get("default")
                suffix = f", default={default!r}" if default is not None else ""
                param_strs.append(f"{name}: {ptype} (optional{suffix})")
        params_block = ", ".join(param_strs) if param_strs else "no parameters"
        return f"- {tool['tool_name']}({params_block}): {tool.get('description', '')}"

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

        diagnostic_tools = self._get_diagnostic_tools()
        tools_block = "\n".join(
            self._format_tool_for_prompt(t) for t in diagnostic_tools
        )
        prompt += f"""
AVAILABLE TOOLS (diagnostic / read-only only — this incident type has no matched
runbook, so you do not have enough grounding to safely propose remediation):
{tools_block}

WHY NO REMEDIATION TOOLS ARE OFFERED:
This is a novel incident type with no ops-authored runbook. Past experience shows
that when an LLM guesses at remediation for an unfamiliar event type, it tends to
propose intrusive actions (restarts, kills) without first establishing whether the
resource is actually broken or just failing an external check (e.g. DNS, TLS,
reachability, wrong content) — and those causes are not fixed by a restart. So for
novel events, your job is ONLY to produce a diagnostic plan a human can run to
understand the problem. A human decides remediation after reviewing your output.

CONSTRAINTS:
- Generate 3-5 diagnostic steps (non-destructive, safe to run) that would let a
  human determine the ACTUAL root cause — not just "is it broken" but "why".
- For anything involving a URL/hostname/endpoint, prefer reachability/DNS/port/
  health-check style tools before generic log/metric collection.
- remediation_steps, rollback_steps, and verification_steps MUST be empty arrays
  — do not populate them, even if you have a strong hypothesis for a fix. Put any
  hypothesis or recommended next step in "description", not in an executable step.
- Do NOT reference tools not in the AVAILABLE TOOLS list above.
- Each tool's "args" MUST use exactly the parameter names shown in parentheses
  next to that tool above (e.g. ping_service takes "host", not "url" — copy the
  parameter names verbatim, do not invent or rename them). Omit optional params
  you don't need; never omit a required one.

GENERATE JSON RUNBOOK with structure:
{{
  "name": "Human-readable runbook name",
  "description": "What this diagnostic plan investigates, and any root-cause hypothesis for a human to consider",
  "estimated_blast_radius": 1,
  "estimated_duration_seconds": 120,
  "requires_approval": true,
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
  "remediation_steps": [],
  "rollback_steps": [],
  "verification_steps": [],
  "main_args": {{}},
  "resource_type": "pod|service|node|etc",
  "estimated_time_to_resolution_seconds": 120
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

        # Novel-incident runbooks are diagnostics-only by policy (see
        # _build_generation_prompt) — the LLM is never given remediation tools,
        # since it has no matched runbook to ground a remediation decision in.
        # If it produced remediation/rollback/verification steps anyway (either
        # ignoring instructions, or hallucinating tools not in AVAILABLE TOOLS),
        # strip them here rather than trusting or executing them — this is a
        # policy enforcement point, not just a lint check.
        for stripped_field in ('remediation_steps', 'rollback_steps', 'verification_steps'):
            if runbook.get(stripped_field):
                warnings.append(
                    f"LLM produced '{stripped_field}' for a novel/unmatched incident type — "
                    f"stripped. Only diagnostics are allowed until an operator authors a "
                    f"real runbook for this event type."
                )
                runbook[stripped_field] = []
        runbook['estimated_blast_radius'] = 1  # diagnostics-only is always blast_radius 1

        # Required fields
        required_fields = ['name', 'diagnostics_steps']
        for field in required_fields:
            if field not in runbook or not runbook[field]:
                issues.append(f"Missing or empty required field: {field}")
                confidence_score -= 0.05  # smaller deduction from already-low baseline

        # Check all steps use tools that actually exist in the approved-actions
        # catalog (diagnostic-only) — synced from the same source as the prompt,
        # not a hardcoded list, so this can't silently drift out of sync again.
        diagnostic_tools_by_name = {t['tool_name']: t for t in self._get_diagnostic_tools()}

        for step in runbook.get('diagnostics_steps', []):
            tool = step.get('tool', '')
            step_type = step.get('step_type', '') or step.get('type', '')
            # 'wait' is a built-in step type, not a catalog tool — skip tool check
            if step_type == 'wait' or tool == 'wait':
                continue
            tool_def = diagnostic_tools_by_name.get(tool)
            if tool_def is None:
                issues.append(f"Step '{step.get('name')}' uses unavailable tool: {tool}")
                confidence_score -= 0.05
                continue

            # Tool exists — also check the args the LLM passed match its real
            # parameter names, since a right tool with a wrong/invented arg key
            # (e.g. ping_service called with "url" instead of "host") silently
            # fails or no-ops at execution just like an unknown tool would.
            declared_params = {p['name'] for p in (tool_def.get('parameters') or [])}
            required_params = {
                p['name'] for p in (tool_def.get('parameters') or []) if p.get('required')
            }
            step_args = step.get('args', {}) or {}
            unknown_args = set(step_args.keys()) - declared_params
            missing_required = required_params - set(step_args.keys())
            if unknown_args:
                warnings.append(
                    f"Step '{step.get('name')}' ({tool}) passed unrecognized arg(s) "
                    f"{sorted(unknown_args)} — not in this tool's parameter list, likely "
                    f"invented by the LLM. Step may fail or ignore the intended value."
                )
                confidence_score -= 0.03
            if missing_required:
                issues.append(
                    f"Step '{step.get('name')}' ({tool}) is missing required arg(s): "
                    f"{sorted(missing_required)}"
                )
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
