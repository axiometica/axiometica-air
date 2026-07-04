"""
LLM Provider abstraction for multi-provider support
Supports OpenAI, Anthropic, and other providers
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict
import os
import re
import logging

logger = logging.getLogger(__name__)



# ── System prompt for rich summary generation ──────────────────────────────────
# Separating the persona into role="system" gives the model clearer role framing
# before it sees the incident data, producing better-structured outputs.
RICH_SUMMARY_SYSTEM_PROMPT = (
    "You are a senior SRE and infrastructure expert writing incident reports. "
    "When given structured incident data, produce exactly TWO sections using "
    "the headers [EXECUTIVE SUMMARY] and [TECHNICAL DIGEST]. "
    "Do NOT repeat the section headers in your prose. "
    "CRITICAL: Use ONLY the exact severity, risk score, and field values provided in "
    "the incident data — never infer or upgrade severity from other fields. "
    "CRITICAL: Every metric you cite (percentages, counts, durations) must come verbatim from "
    "the incident data and must be labelled correctly — e.g. do not call a syscall count "
    "a 'disk usage' figure. If you cannot identify the metric type from the data, omit the number. "
    "Never copy any numbers or names from prompt examples into your output. "
    "The executive summary is for managers: exactly 3 sentences, separated by blank lines — "
    "what happened, what was decided, what was the outcome. No step lists, no repetition, no padding. "
    "The technical digest is for senior engineers: detailed, precise, mechanism-focused."
)


def _build_rich_prompt(full_context: dict) -> str:
    """Build a two-section prompt from full post-agent incident context."""
    event_type             = full_context.get("event_type", "Unknown")
    resource               = full_context.get("resource", "Unknown")
    environment            = full_context.get("environment", "unknown")
    severity               = full_context.get("severity", "unknown")
    risk_score             = full_context.get("risk_score", "N/A")
    blast_radius           = full_context.get("blast_radius", "N/A")
    remediation_complexity = full_context.get("remediation_complexity", "")
    anomaly_process        = full_context.get("anomaly_process", "")
    anomaly_metrics        = full_context.get("anomaly_metrics", "")
    runbook                = full_context.get("runbook", "")
    execution_results      = full_context.get("execution_results", [])
    verification           = full_context.get("verification", "")
    lifecycle_state        = full_context.get("lifecycle_state", "unknown")
    impacted_services      = full_context.get("impacted_services", [])
    description            = full_context.get("description", "")
    governance_decision    = full_context.get("governance_decision", "")
    approval_required      = full_context.get("approval_required", False)
    governance_notes       = full_context.get("governance_notes", "")
    matching_policies      = full_context.get("matching_policies", [])

    # ── Format per-action lines ──────────────────────────────────────────────
    action_lines = []
    for r in execution_results:
        tool    = r.get("tool", "?")
        status  = r.get("status", "?")
        command = r.get("command", "")
        output  = str(r.get("output", ""))
        # Keep output short: first meaningful line, max 120 chars
        first_line = next((ln.strip() for ln in output.splitlines() if ln.strip()), output[:120])
        line = f"  [{status.upper()}] {tool}"
        if command:
            line += f" | cmd: {command[:80]}"
        if first_line:
            line += f" | output: {first_line[:100]}"
        action_lines.append(line)
    actions_block = "\n".join(action_lines) if action_lines else "  (no actions recorded)"

    # ── Format governance ────────────────────────────────────────────────────
    if approval_required:
        gov_str = f"Manual approval required. Decision: {governance_decision or 'pending'}."
    else:
        gov_str = "Auto-approved by governance (no manual sign-off required)."
    if governance_notes:
        gov_str += f" Notes: {governance_notes}"
    if matching_policies:
        policy_names = ", ".join(
            p.get("name", str(p)) if isinstance(p, dict) else str(p)
            for p in matching_policies[:3]
        )
        gov_str += f" Matching policies: {policy_names}."

    # ── Format resolution ────────────────────────────────────────────────────
    resolution_map = {
        "resolved":        "Resolved — automated remediation successful.",
        "failed":          "Failed — automated remediation unsuccessful; manual intervention required.",
        "monitoring":      "Resolved with post-remediation monitoring active.",
        "waiting_approval":"Awaiting manual approval before remediation can proceed.",
        "executing":       "Remediation currently in progress.",
    }
    resolution_str = resolution_map.get(str(lifecycle_state).lower(), f"State: {lifecycle_state}")

    # ── Format dependencies ──────────────────────────────────────────────────
    deps_str = ", ".join(
        s.get("name", str(s)) if isinstance(s, dict) else str(s)
        for s in impacted_services[:5]
    ) or "None detected"

    # ── Inject LLM pre-analysis if InsightAgent ran ──────────────────────────
    llm_insights = full_context.get("llm_insights") or {}
    insight_block = ""
    if llm_insights and not llm_insights.get("parse_error"):
        insight_block = (
            "\n\nPRE-COMPUTED ROOT CAUSE ANALYSIS:\n"
            f"Hypothesis: {llm_insights.get('root_cause_hypothesis', 'N/A')}\n"
            f"Confidence: {llm_insights.get('confidence', '?')} "
            f"({llm_insights.get('confidence_reason', '')})\n"
            f"Remediation rationale: {llm_insights.get('remediation_rationale', 'N/A')}\n"
            f"Key concerns: {', '.join(llm_insights.get('key_concerns') or [])}\n"
            f"Pattern match: {llm_insights.get('similar_pattern', 'N/A')}\n"
            f"Est. resolution: {llm_insights.get('estimated_resolution_time', 'unknown')}\n"
        )

    prompt = f"""A workflow has just finished processing a production incident. \
Write exactly TWO sections using the headers below. \
⚠️ RULE: The Severity field in INCIDENT DATA is the authoritative severity — copy it verbatim. \
Never upgrade or infer severity from risk score, description, or any other field.

## [EXECUTIVE SUMMARY]
Write EXACTLY 3 sentences separated by blank lines. ALL THREE are mandatory — a summary that
stops after sentence 2 is incomplete and unacceptable, no matter how final the decision sentence
sounds. Hard limit — stop after the third sentence, but never stop before it.

The labels below ("What happened", "Decision", "Outcome") describe what each sentence must
cover — they are instructions to you, not text to write. Do NOT print the words "What happened:",
"Decision:", or "Outcome:" (or any other section label) in your output. Output plain prose only.

Sentence 1 (covers: what happened):
Name the resource, environment, and event. Quote the error briefly. State {severity.upper()} severity and risk score {risk_score}/100.

Sentence 2 (covers: decision):
State whether manual approval was required and what was decided. Name the runbook selected. Do NOT list steps.

Sentence 3 (covers: outcome — MANDATORY, do not omit):
State whether resolved. One clause of evidence (e.g. service returned to running). Done.

Example of correct output shape — structure only, do NOT copy numbers, resource names, or wording:
The <resource> service in <environment> experienced a <event_type> event, triggering a <threshold> threshold breach. <SEVERITY> severity, risk score <N>/100.

Manual approval was required and granted, selecting the "<Runbook Name>" runbook.

The incident was resolved after <remediation action> restored <primary metric> to normal levels.

⚠️ USE ONLY DATA FROM THE INCIDENT BELOW. Never use numbers, names, or phrases from the example above.
⛔ STOP after 3 sentences — but all 3 must be present. Do not add context, implications, or step descriptions.

## [TECHNICAL DIGEST]
Write this as a technical expert explaining the incident to a senior engineer. Cover:

EVENT ANALYSIS:
Explain what "{event_type}" means at the OS/infrastructure/application layer. What actually happens \
inside the system when this event type occurs? What are the typical root causes? How does it manifest \
and what are the cascading effects if left unaddressed?

REMEDIATION REASONING:
For each action that was executed, explain in 1-2 sentences WHY that specific action was chosen and \
HOW it technically addresses the root cause. Explain the mechanism — not just "we restarted the \
service" but WHY a restart solves this class of problem.

RESOLUTION EVIDENCE:
What technical evidence confirms the issue was resolved (or not)? What metrics or signals indicate \
the system is back to normal?

Use precise technical language. Be specific. The audience is engineers doing post-mortem analysis.

---
INCIDENT DATA:
Event type:        {event_type}
Description:       {description or 'N/A'}
Offending process: {anomaly_process or 'N/A'}
Anomaly metrics:   {anomaly_metrics or 'N/A'}
Affected resource: {resource} ({environment} environment)
Severity:          {severity.upper()}
Risk score:        {risk_score}/100
Blast radius:      {blast_radius}
Complexity:        {remediation_complexity or 'N/A'}
Governance:        {gov_str}
Runbook selected:  {runbook or 'N/A'}
Actions executed:
{actions_block}
Verification:      {verification or 'N/A'}
Resolution:        {resolution_str}
Impacted services: {deps_str}{insight_block}
"""
    return prompt


def _cap_executive_summary(text: str, max_sentences: int = 3) -> str:
    """
    Hard-cap the executive summary at max_sentences sentences and insert
    a blank line between each so the UI renders them as separate paragraphs.
    Splitting on sentence-ending punctuation followed by whitespace + capital letter
    is reliable for LLM-generated prose.
    """
    # Split on . / ! / ? followed by whitespace and an uppercase letter or quote
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"‘’])', text.strip())
    kept = [s.strip() for s in parts[:max_sentences] if s.strip()]
    # Ensure each sentence ends with a period
    result = []
    for s in kept:
        result.append(s if s[-1] in ".!?" else s + ".")
    return "\n\n".join(result)


# ── Known service roles for container name → human-readable description ──────
_SERVICE_ROLES: Dict[str, str] = {
    "neo4j":                  "graph database (CMDB / topology store)",
    "backend":                "FastAPI application server (API layer)",
    "frontend":               "React web frontend (UI)",
    "celery_worker":          "Celery async task worker (general queue)",
    "celery_beat":            "Celery periodic task scheduler",
    "celery_default_worker":  "Celery default-queue worker",
    "redis":                  "Redis in-memory cache / Celery message broker",
    "postgres":               "PostgreSQL relational database (primary store)",
    "postgres_backup":        "PostgreSQL backup service",
    "nginx":                  "Nginx reverse proxy / TLS termination",
    "flower":                 "Celery monitoring dashboard (Flower)",
    "watcher_brain":          "Infrastructure monitoring agent (watcher)",
    "sentinel_senses":        "Metrics & log collection agent",
}


def _resolve_service_role(resource_name: str) -> str:
    """Return a human-readable role for a container/resource name."""
    lower = resource_name.lower()
    for key, role in _SERVICE_ROLES.items():
        if key in lower:
            return role
    return "service"


def _build_storm_prompt(ctx: dict) -> tuple:
    """
    Return (system_prompt, user_prompt) for a storm root-cause hypothesis.

    ctx keys (all optional, degraded gracefully):
        pattern           — e.g. "resource_exhaustion"
        n_resources       — int
        resource_lines    — list of "name: role" strings
        event_types       — list of event type strings
        all_same_type     — bool
        dominant_type     — str or None
        topo_context      — str
        app_context       — str
        shared_infra_hint — str
    """
    pattern          = ctx.get("pattern", "unknown").replace("_", " ").title()
    n_resources      = ctx.get("n_resources", 0)
    resource_lines   = ctx.get("resource_lines", [])
    event_types      = list(set(ctx.get("event_types", [])))
    topo_context     = ctx.get("topo_context", "No topology data available.")
    app_context      = ctx.get("app_context", "")
    shared_infra_hint = ctx.get("shared_infra_hint", "")

    resource_block = "\n".join(f"  • {line}" for line in resource_lines) or "  (none)"

    system_prompt = (
        "You are a senior Site Reliability Engineer (SRE) and IT operations analyst. "
        "Your job is to diagnose the ROOT CAUSE of a correlated event storm across "
        "multiple infrastructure components. You think at the infrastructure layer — "
        "shared disks, host machines, network segments, shared daemons — not just "
        "at the individual service level. You write in plain, direct operational language. "
        "Do NOT suggest remediation steps. Do NOT use bullet points. "
        "Write 3–4 sentences maximum."
    )

    user_prompt = f"""Diagnose the root cause of this event storm.

STORM PATTERN: {pattern}
TOTAL AFFECTED SERVICES: {n_resources}
OBSERVED EVENT TYPES: {", ".join(event_types) if event_types else "unknown"}

AFFECTED SERVICES AND THEIR ROLES:
{resource_block}

APPLICATION / INFRASTRUCTURE CONTEXT:
{app_context if app_context else "Standard containerised application stack."}
{shared_infra_hint}

CMDB TOPOLOGY EVIDENCE:
{topo_context}

Based on the above, state: (1) the single most probable root cause at the infrastructure level, (2) why every service in this list is affected simultaneously, and (3) whether addressing individual services will resolve the issue or whether the fix must target the shared resource."""

    return system_prompt, user_prompt


def _parse_rich_response(text: str) -> Dict[str, str]:
    """Split LLM response into executive summary and technical digest."""
    summary = []
    technical = []
    current = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip horizontal rule lines (━, ─, =, -, *) returned verbatim by the LLM
        if len(stripped) >= 8 and all(ch in "━─=—-*#" for ch in stripped):
            continue
        lower = stripped.lower()
        if "executive summary" in lower:
            current = "summary"
            continue
        if "technical digest" in lower:
            current = "technical"
            continue
        # Skip the "INCIDENT DATA" section separator — it's a prompt artefact
        if "incident data" in lower and "use all available" in lower:
            break
        if current == "summary":
            summary.append(stripped)
        elif current == "technical":
            technical.append(line.rstrip())   # preserve bullet indentation

    # Fallback: if headers weren't found, treat the whole thing as summary
    if not summary and not technical:
        return {"summary": text.strip(), "technical_summary": ""}

    raw_summary = " ".join(summary).strip()
    return {
        "summary": _cap_executive_summary(raw_summary),
        "technical_summary": "\n".join(technical).strip(),
    }


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    async def generate_summary(self, incident_data: dict) -> str:
        """Generate a one-paragraph incident summary (legacy, minimal context)"""
        pass

    @abstractmethod
    async def generate_rich_summary(self, full_context: dict) -> Dict[str, str]:
        """
        Generate a two-section summary with full post-agent context.
        Returns: {"summary": "<executive narrative>", "technical_summary": "<bullet digest>"}
        """
        pass

    @abstractmethod
    async def generate_storm_hypothesis(self, storm_context: dict) -> str:
        """
        Generate a root-cause hypothesis for a correlated event storm.

        storm_context keys:
            pattern            — classified storm pattern string
            n_resources        — total number of affected resources
            resource_lines     — list of "<name>: <role>" strings for every resource
            event_types        — list of distinct event type strings
            all_same_type      — bool: all events are the same type
            dominant_type      — the most common event type (or None)
            topo_context       — topology evidence text from Neo4j (or "none found")
            app_context        — application grouping hint (e.g. "all share prefix agentic_os")
            shared_infra_hint  — inferred infra hint (e.g. shared host disk reasoning)
        """
        pass

    @abstractmethod
    async def generate_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """
        General-purpose completion with a custom system prompt.
        Used by InsightAgent and other per-agent LLM calls.
        Returns raw response text or None on failure.
        """
        pass

    async def stream_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 600,
        temperature: float = 0.25,
    ):
        """
        Stream text chunks from the LLM as an async generator.
        Default implementation falls back to a single non-streaming call.
        Override in subclasses for real token-by-token streaming.
        """
        result = await self.generate_agent_completion(
            system_prompt, user_content, max_tokens, temperature
        )
        if result:
            yield result

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if provider is properly configured with API key"""
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider"""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or "gpt-3.5-turbo"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def generate_summary(self, incident_data: dict) -> str:
        """Legacy simple summary (minimal context, used only as fallback)"""
        if not self.is_configured():
            return None

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, max_retries=3)

            event_type = incident_data.get("event_type", "Unknown event")
            resource   = incident_data.get("resource", "Unknown resource")
            severity   = incident_data.get("severity", "Unknown")
            impact     = incident_data.get("impact", f"Severity: {severity}")

            prompt = (
                f"In 3-4 professional sentences summarise this IT incident: "
                f"event={event_type}, resource={resource}, severity={severity}, impact={impact}. "
                f"Cover what happened, business impact, and resolution status."
            )
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250, temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI generate_summary failed: {e}")
            return None

    async def generate_rich_summary(self, full_context: dict) -> Dict[str, str]:
        """Rich two-section summary with full post-agent context."""
        if not self.is_configured():
            return {"summary": None, "technical_summary": None}

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, max_retries=3)

            key_preview = (self.api_key[:10] + "...") if self.api_key else "NONE"
            logger.info(f"OpenAI rich summary: key={key_preview}, model={self.model}")

            prompt = _build_rich_prompt(full_context)
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": RICH_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=2000, temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            logger.debug(f"OpenAI raw response ({len(raw)} chars): {raw[:200]}")
            return _parse_rich_response(raw)

        except Exception as e:
            logger.error(f"OpenAI generate_rich_summary failed: {e}", exc_info=True)
            return {"summary": None, "technical_summary": None}

    async def generate_storm_hypothesis(self, storm_context: dict) -> str:
        """Storm root-cause hypothesis — uses a dedicated SRE-analyst prompt."""
        if not self.is_configured():
            return None
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, max_retries=3)
            system_p, user_p = _build_storm_prompt(storm_context)
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_p},
                    {"role": "user",   "content": user_p},
                ],
                max_tokens=350, temperature=0.2,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI generate_storm_hypothesis failed: {e}", exc_info=True)
            return None

    async def generate_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """General-purpose completion with a custom system prompt."""
        if not self.is_configured():
            return None
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, max_retries=3)
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI generate_agent_completion failed: {e}", exc_info=True)
            return None

    async def stream_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 600,
        temperature: float = 0.25,
    ):
        """Stream response tokens from OpenAI."""
        if not self.is_configured():
            yield "LLM is not configured. Go to Settings → LLM to add an API key."
            return
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, max_retries=3)
            stream = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"OpenAI stream_agent_completion failed: {e}", exc_info=True)
            yield "\n[Stream error — please try again]"


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider"""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-3-haiku-20240307"):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or "claude-3-haiku-20240307"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def generate_summary(self, incident_data: dict) -> str:
        """Legacy simple summary."""
        if not self.is_configured():
            return None

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key, max_retries=3)

            event_type = incident_data.get("event_type", "Unknown event")
            resource   = incident_data.get("resource", "Unknown resource")
            severity   = incident_data.get("severity", "Unknown")
            impact     = incident_data.get("impact", f"Severity: {severity}")

            prompt = (
                f"In 3-4 professional sentences summarise this IT incident: "
                f"event={event_type}, resource={resource}, severity={severity}, impact={impact}. "
                f"Cover what happened, business impact, and resolution status."
            )
            message = await client.messages.create(
                model=self.model, max_tokens=250,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as e:
            logger.error(f"Anthropic generate_summary failed: {e}")
            return None

    async def generate_rich_summary(self, full_context: dict) -> Dict[str, str]:
        """Rich two-section summary with full post-agent context."""
        if not self.is_configured():
            return {"summary": None, "technical_summary": None}

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key, max_retries=3)

            key_preview = (self.api_key[:10] + "...") if self.api_key else "NONE"
            logger.info(f"Anthropic rich summary: key={key_preview}, model={self.model}")

            prompt = _build_rich_prompt(full_context)
            message = await client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=RICH_SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            logger.debug(f"Anthropic raw response ({len(raw)} chars): {raw[:200]}")
            return _parse_rich_response(raw)

        except Exception as e:
            logger.error(f"Anthropic generate_rich_summary failed: {e}", exc_info=True)
            return {"summary": None, "technical_summary": None}

    async def generate_storm_hypothesis(self, storm_context: dict) -> str:
        """Storm root-cause hypothesis — uses a dedicated SRE-analyst prompt."""
        if not self.is_configured():
            return None
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key, max_retries=3)
            system_p, user_p = _build_storm_prompt(storm_context)
            message = await client.messages.create(
                model=self.model,
                max_tokens=350,
                system=system_p,
                messages=[{"role": "user", "content": user_p}],
            )
            return message.content[0].text.strip()
        except Exception as e:
            logger.error(f"Anthropic generate_storm_hypothesis failed: {e}", exc_info=True)
            return None

    async def generate_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """General-purpose completion with a custom system prompt."""
        if not self.is_configured():
            return None
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key, max_retries=3)
            message = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return message.content[0].text.strip()
        except Exception as e:
            logger.error(f"Anthropic generate_agent_completion failed: {e}", exc_info=True)
            return None

    async def stream_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 600,
        temperature: float = 0.25,
    ):
        """Stream response tokens from Anthropic."""
        if not self.is_configured():
            yield "LLM is not configured. Go to Settings → LLM to add an API key."
            return
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key, max_retries=3)
            async with client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error(f"Anthropic stream_agent_completion failed: {e}", exc_info=True)
            yield "\n[Stream error — please try again]"


class GenericOpenAIProvider(LLMProvider):
    """Generic OpenAI-compatible provider — works with any endpoint that speaks the OpenAI chat API.

    Covers: Azure OpenAI, LiteLLM proxy, vLLM, Groq, Together AI, Mistral, Perplexity,
    Ollama, and any enterprise GPU inference server. No token caps — enterprise endpoints
    are assumed to have adequate capacity.
    """

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: str = ""):
        self.base_url = (base_url or os.getenv("CUSTOM_LLM_BASE_URL") or "").rstrip("/")
        self.api_key  = api_key or os.getenv("CUSTOM_LLM_API_KEY") or "none"
        self.model    = model or os.getenv("CUSTOM_LLM_MODEL") or ""

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def _client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(base_url=f"{self.base_url}/v1", api_key=self.api_key)

    async def _chat(self, messages: list, max_tokens: int = 700, temperature: float = 0.3) -> Optional[str]:
        try:
            resp = await self._client().chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Custom LLM request failed: {e}", exc_info=True)
            return None

    async def generate_summary(self, incident_data: dict) -> str:
        event_type = incident_data.get("event_type", "Unknown event")
        resource   = incident_data.get("resource", "Unknown resource")
        severity   = incident_data.get("severity", "Unknown")
        impact     = incident_data.get("impact", f"Severity: {severity}")
        prompt = (
            f"In 3-4 professional sentences summarise this IT incident: "
            f"event={event_type}, resource={resource}, severity={severity}, impact={impact}. "
            f"Cover what happened, business impact, and resolution status."
        )
        return await self._chat([{"role": "user", "content": prompt}], max_tokens=250)

    async def generate_rich_summary(self, full_context: dict) -> Dict[str, str]:
        if not self.is_configured():
            return {"summary": None, "technical_summary": None}
        prompt = _build_rich_prompt(full_context)
        raw = await self._chat(
            [{"role": "system", "content": RICH_SUMMARY_SYSTEM_PROMPT},
             {"role": "user",   "content": prompt}],
            max_tokens=2000,
        )
        if raw is None:
            return {"summary": None, "technical_summary": None}
        return _parse_rich_response(raw)

    async def generate_storm_hypothesis(self, storm_context: dict) -> Optional[str]:
        if not self.is_configured():
            return None
        system_p, user_p = _build_storm_prompt(storm_context)
        return await self._chat(
            [{"role": "system", "content": system_p},
             {"role": "user",   "content": user_p}],
            max_tokens=350, temperature=0.2,
        )

    async def generate_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> Optional[str]:
        if not self.is_configured():
            return None
        return await self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user",   "content": user_content}],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def stream_agent_completion(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 600,
        temperature: float = 0.25,
    ):
        if not self.is_configured():
            yield "LLM is not configured. Go to Settings → LLM to configure the custom provider."
            return
        try:
            stream = await self._client().chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user",   "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"Custom LLM stream failed: {e}", exc_info=True)
            yield "\n[Stream error — please try again]"


def get_llm_provider(
    provider_name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMProvider:
    """Factory function to get LLM provider by name"""
    provider_name = (provider_name or "openai").lower()

    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key, model=model or "gpt-3.5-turbo")
    elif provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model or "claude-3-haiku-20240307")
    elif provider_name == "custom":
        return GenericOpenAIProvider(base_url=base_url, api_key=api_key, model=model or "")
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")
