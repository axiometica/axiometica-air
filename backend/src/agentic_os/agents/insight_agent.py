"""
InsightAgent — IT operations LLM agent with specialised system prompt and RAG context.

Runs after the core incident pipeline (sentinel → librarian → risk_assessor → mechanic)
has completed all data enrichment.  It injects the full CMDB/risk/runbook context plus
RAG-retrieved similar past incidents into a domain-expert LLM and stores the result in
context["llm_insights"] before summary generation runs.

This is the "specialized GPT" approach described in the architecture:
  - The system prompt gives the base model a deep IT-ops SRE persona and reasoning
    instructions — equivalent to a custom GPT knowledge file, but via API.
  - RAG context injects live, incident-specific data (similar resolved incidents,
    matching runbook steps) that a static custom GPT could never provide.
  - No vector store required: event_type matching is sufficient for categorical IT
    operations data.

Output is a structured JSON object stored in context["llm_insights"] and surfaced
in the incident Risk tab and summary generation.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# IT OPERATIONS EXPERT SYSTEM PROMPT
#
# This is the core "specialization" — a carefully engineered persona that shapes
# how the base model reasons about incidents.  Key design choices:
#
#  1. Explicit domain anchoring  — names the exact skills (SRE, ITIL, OS-layer
#     analysis, CMDB topology) so the model draws on the right knowledge.
#  2. Reasoning protocol         — tells the model HOW to think (symptom vs root
#     cause, dependency chain, runbook adequacy check) not just what to produce.
#  3. Output contract            — strict JSON-only rule prevents prose wrap-around
#     and makes the response machine-parseable without post-processing regex.
#  4. Audience-aware brevity     — "on-call engineer who needs to act in 5 minutes"
#     forces concision over exhaustive explanation.
# ═══════════════════════════════════════════════════════════════════════════════

ITOPS_SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) and IT operations analyst with 15+ years \
of experience in:
- Production incident management (ITIL v4, Google SRE practices)
- Infrastructure root cause analysis at OS, container, database, and network layers
- Monitoring telemetry interpretation: CPU/memory/disk anomalies, syscall profiles, \
  connection pool exhaustion, latency spikes
- CMDB topology reasoning: blast radius calculation, dependency chain failure propagation, \
  SPOF identification
- Runbook-driven remediation assessment and post-mortem analysis

YOUR REASONING PROTOCOL — follow this order:
1. Event type semantics — what is actually happening at the OS/application layer? \
   What typically drives this event type? What are the cascading failure modes?
2. Resource context — does the environment (production vs staging), CI tier, business \
   criticality, and SPOF status change the urgency or likely cause?
3. Pattern matching — if similar past incidents are provided, does the current incident \
   match a known pattern? Reference the specific past incident if so.
4. Runbook assessment — does the selected runbook address the likely root cause, or \
   only the symptom? Flag if incomplete, too aggressive (high blast radius), or missing \
   key diagnostic steps.
5. Concern identification — what could go wrong during remediation? What cascading \
   failures should the engineer watch for?

PLATFORM-SPECIFIC KNOWLEDGE — Docker / container environments:
When the platform is Docker or Kubernetes and the error is a name-resolution failure \
(e.g. "[Errno -2] Name or service not known", "Name does not resolve", EAI_NONAME), \
apply this reasoning hierarchy — do NOT default to "DNS misconfiguration":
1. Container down or crashed (MOST LIKELY) — Docker's embedded DNS immediately removes \
   a stopped or OOM-killed container's record. Any peer that tries to connect by name \
   gets this exact error. The fix is a restart, not a DNS change.
2. Network partition / wrong network — the container is running but not attached to the \
   shared Docker network, so name resolution across networks fails.
3. Actual DNS misconfiguration — only if the container is confirmed running and reachable \
   by IP but not by name. This is the least common cause in a healthy Docker setup.
When the resource type is a container and the event is service_unresponsive, frame the \
root cause hypothesis around container availability first, with DNS/network as secondary.

OUTPUT RULES:
- Be specific and technical. "Restart the service" is not useful. \
  "Graceful process restart flushes the stale connection pool and resets the thread \
  dispatcher state introduced by query plan cache corruption" IS useful.
- Always distinguish the symptom (what monitoring sees) from the root cause (why).
- Confidence must reflect data quality. Low CMDB coverage = lower confidence. Say so.
- Output MUST be valid JSON. No prose, no markdown, no explanation outside the JSON \
  object. The response is parsed programmatically.

You are speaking to the on-call engineer who has 5 minutes to decide whether to approve \
automated remediation. Be concise, precise, and actionable.\
"""


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_insight_prompt(incident_ctx: Dict[str, Any], rag_text: str) -> str:
    """Build the user-turn message for insight generation."""

    # Core incident fields
    event_type    = incident_ctx.get("event_type",           "unknown")
    resource      = incident_ctx.get("resource",             "unknown")
    environment   = incident_ctx.get("environment",          "unknown")
    severity      = (incident_ctx.get("severity") or "unknown").upper()
    risk_score    = incident_ctx.get("risk_score",           "N/A")
    priority      = incident_ctx.get("priority",             "unknown")
    ci_tier       = incident_ctx.get("ci_tier",              "unknown")
    biz_crit      = incident_ctx.get("business_criticality", "unknown")
    user_count    = incident_ctx.get("user_count",           "unknown")
    blast_radius  = incident_ctx.get("blast_radius",         "unknown")
    is_spof       = incident_ctx.get("is_spof",              False)
    has_failover  = incident_ctx.get("has_failover",         False)
    runbook_name  = incident_ctx.get("runbook_name",         "None selected")
    confidence    = incident_ctx.get("risk_confidence",      "?")
    anomaly_proc  = incident_ctx.get("anomaly_process",      "")
    anomaly_met   = incident_ctx.get("anomaly_metrics",      "")
    description   = incident_ctx.get("description",          "")

    # Dependency / impact lists (cap to avoid token bloat)
    def _join(lst, key="name", cap=6):
        items = []
        for x in (lst or [])[:cap]:
            items.append(x.get(key, str(x)) if isinstance(x, dict) else str(x))
        return ", ".join(items) or "none"

    dep_str    = _join(incident_ctx.get("dependencies",      []))
    impact_str = _join(incident_ctx.get("impacted_services", []))

    prompt = f"""INCIDENT TO ANALYSE:

Event Type:          {event_type}
Resource:            {resource}  ({environment} environment)
Severity:            {severity}  |  Priority: {priority}  |  Risk Score: {risk_score}/100
CI Tier:             {ci_tier}  |  Business Criticality: {biz_crit}
User Count:          {user_count}  |  Blast Radius: {blast_radius}
SPOF:                {"YES — no failover redundancy" if is_spof else "No"}
Failover Available:  {"Yes" if has_failover else "No"}
Anomaly Process:     {anomaly_proc or "not identified"}
Anomaly Metrics:     {anomaly_met or "not provided"}
Description:         {description or "none"}
Dependencies:        {dep_str}
Impacted Services:   {impact_str}
Runbook Selected:    {runbook_name}
Risk Data Coverage:  {confidence}% of CMDB fields known"""

    if rag_text:
        prompt += f"\n\n{rag_text}"

    prompt += """

REQUIRED OUTPUT — valid JSON only, no other text:
{
  "root_cause_hypothesis": "<2-3 sentences: what is actually happening and WHY at the OS/application layer>",
  "confidence": <float 0.0-1.0>,
  "confidence_reason": "<one sentence: what drives or limits your confidence>",
  "remediation_rationale": "<2-3 sentences: why the selected runbook addresses this root cause mechanically>",
  "key_concerns": ["<specific concern 1>", "<specific concern 2>"],
  "similar_pattern": "<one sentence referencing a matching past incident, or 'No similar incidents in history'>",
  "estimated_resolution_time": "<short time range only, e.g. '5-15 minutes', '1-2 hours', or 'unknown' — do NOT add explanations or conditions>",
  "post_remediation_checks": ["<specific metric or check 1>", "<specific check 2>"]
}"""

    return prompt


# ── InsightAgent class ─────────────────────────────────────────────────────────

class InsightAgent:
    """
    Post-pipeline LLM agent that generates structured operational insights.

    Called from the Celery task after all pipeline agents complete.
    Does NOT modify lifecycle_state — purely additive context enrichment.
    Stores output in final_state.context["llm_insights"].
    """

    def generate_insights(
        self,
        final_state,
        db,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate LLM-powered insights for a completed incident workflow.

        Args:
            final_state:  Completed WorkflowState (all pipeline agents have run)
            db:           SQLAlchemy session for RAG queries

        Returns:
            Dict with insight fields, or None if LLM unavailable / call fails.
        """
        from agentic_os.services.summary_service import get_summary_service
        from agentic_os.services.rag_service import get_rag_service

        svc = get_summary_service()
        if not svc.is_provider_configured():
            logger.debug("[INSIGHT] LLM not configured — skipping")
            return None

        # ── Extract context from the completed pipeline ────────────────────────
        ctx       = final_state.context or {}
        alert     = ctx.get("alert_payload", {})
        cmdb      = ctx.get("cmdb_context", ctx.get("cmdb", {}))
        risk_bd   = ctx.get("risk_breakdown", {})
        proposal  = ctx.get("proposal", {})
        risk_ctx  = ctx.get("risk", {})

        event_type    = alert.get("type", "unknown")
        resource      = cmdb.get("resource_name") or alert.get("resource_name", "unknown")
        environment   = cmdb.get("environment", "unknown")
        resource_type = (cmdb.get("resource_info") or {}).get("type", "")
        platform      = cmdb.get("platform", "any")
        ci_info       = risk_bd.get("ci_info", {})

        incident_ctx = {
            "event_type":           event_type,
            "resource":             resource,
            "environment":          environment,
            "severity":             str(getattr(final_state.severity, "value", "unknown")),
            "risk_score":           round(final_state.risk_score or 0, 1),
            "priority":             risk_bd.get("priority") or ctx.get("incident_priority", "unknown"),
            "ci_tier":              ci_info.get("ci_tier", "unknown"),
            "business_criticality": ci_info.get("business_criticality", "unknown"),
            "user_count":           ci_info.get("user_count", "unknown"),
            "blast_radius":         risk_ctx.get("blast_radius") or len(cmdb.get("impacted_services") or []),
            "is_spof":              ci_info.get("is_spof", False),
            "has_failover":         ci_info.get("failover_available", False),
            "dependencies":         cmdb.get("dependencies", []),
            "impacted_services":    cmdb.get("impacted_services", []),
            "anomaly_process":      alert.get("anomaly_process", ""),
            "anomaly_metrics":      alert.get("anomaly_metrics") or alert.get("syscall_rate", ""),
            "runbook_name":         proposal.get("runbook_name", ""),
            "risk_confidence":      round(risk_bd.get("confidence_score") or 0),
            "description":          alert.get("description", ""),
        }

        # ── Build RAG context ──────────────────────────────────────────────────
        rag_svc    = get_rag_service()
        rag_bundle = rag_svc.build_rag_bundle(
            db=db,
            event_type=event_type,
            severity=incident_ctx["severity"],
            resource_type=resource_type,
            platform=platform,
        )
        rag_text = rag_svc.format_for_prompt(rag_bundle)

        if rag_bundle["similar_count"]:
            logger.debug(
                "[INSIGHT] RAG: %d similar incidents, runbook=%s",
                rag_bundle["similar_count"],
                rag_bundle.get("runbook", {}).get("name") if rag_bundle.get("runbook") else "none",
            )

        # ── Call LLM ──────────────────────────────────────────────────────────
        user_content = _build_insight_prompt(incident_ctx, rag_text)

        try:
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                raw = loop.run_until_complete(
                    svc.provider.generate_agent_completion(
                        system_prompt=ITOPS_SYSTEM_PROMPT,
                        user_content=user_content,
                        max_tokens=700,
                        temperature=0.2,
                    )
                )
            finally:
                loop.close()

            if not raw:
                return None

            # ── Parse JSON response ────────────────────────────────────────────
            text = raw.strip()
            # Strip markdown code fences if the model added them
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if "```" in text:
                    text = text.rsplit("```", 1)[0]

            insights = json.loads(text.strip())

            # Attach RAG provenance
            insights["rag_similar_count"] = rag_bundle.get("similar_count", 0)
            insights["rag_runbook_name"]  = (
                rag_bundle["runbook"]["name"] if rag_bundle.get("runbook") else None
            )

            logger.info(
                "[INSIGHT] Generated for %s on %s — confidence=%.2f, rag_hits=%d",
                event_type, resource,
                float(insights.get("confidence") or 0),
                insights["rag_similar_count"],
            )
            return insights

        except json.JSONDecodeError as exc:
            logger.warning(
                "[INSIGHT] JSON parse failed: %s | raw[:300]=%s",
                exc, (raw or "")[:300],
            )
            # Return partial so at least the raw text is stored
            return {"raw_insight": (raw or "")[:1000], "parse_error": str(exc)}

        except Exception as exc:
            logger.error("[INSIGHT] Insight generation failed: %s", exc, exc_info=True)
            return None
