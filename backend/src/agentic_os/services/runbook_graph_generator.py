"""
RunbookGraphGenerator — LLM-powered generator that produces decision-graph runbooks.

Unlike RunbookGeneratorAgent (which is embedded in the incident workflow), this
service is invoked on demand from the editor API.  It accepts a free-form
description and returns graph-format JSON (steps + graph_edges) that the editor
can import directly.

Output schema matches the editor's RunbookJSON type:
  name, trigger_type, description, platform, blast_radius, steps[], graph_edges[]
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── Schema example injected into every prompt ─────────────────────────────────

_SCHEMA_EXAMPLE = """{
  "name": "High CPU — Kill Runaway Process or Scale",
  "trigger_type": "infrastructure.compute.cpu_high",
  "description": "Identifies the highest-CPU process and either kills it or scales out.",
  "platform": "docker",
  "blast_radius": 3,
  "steps": [
    {
      "id": "diag_cpu",
      "type": "diagnostic",
      "name": "Check CPU Usage",
      "tool": "check_cpu",
      "args": {},
      "output_capture": {"cpu_pct": "$.cpu_percent"}
    },
    {
      "id": "diag_top_proc",
      "type": "diagnostic",
      "name": "Top CPU Processes",
      "tool": "top_processes",
      "args": {"limit": "10", "sort": "cpu"},
      "output_capture": {"top_cpu_pct": "$.top_cpu_percent", "top_pid": "$.top_process_pid", "top_proc_name": "$.top_process"}
    },
    {
      "id": "dec_runaway",
      "type": "decision",
      "condition": "top_cpu_pct > 60",
      "on_true": "action_kill",
      "on_false": "action_scale"
    },
    {
      "id": "action_kill",
      "type": "action",
      "name": "Kill Runaway Process",
      "tool": "process_kill",
      "args": {"signal": "SIGTERM", "pid": "{{top_pid}}", "process_name": "{{top_proc_name}}"}
    },
    {
      "id": "action_scale",
      "type": "action",
      "name": "Scale Up Replicas",
      "tool": "scale_up",
      "args": {"replicas": "2"}
    },
    {
      "id": "verify_cpu",
      "type": "verification",
      "name": "Verify CPU Normal",
      "tool": "check_cpu",
      "args": {},
      "output_capture": {"cpu_after": "$.cpu_percent"},
      "metric": "cpu_after",
      "check": "less_than",
      "value": "60"
    },
    {
      "id": "incident_update_resolve",
      "type": "incident_update",
      "name": "Mark Resolved",
      "state": "resolved"
    },
    {
      "id": "notify_done",
      "type": "notify",
      "name": "Notify Resolution",
      "tool": "send_alert",
      "args": {"severity": "info", "message": "CPU remediation complete. CPU now: {{cpu_after}}%"}
    }
  ],
  "graph_edges": [
    {"source": "start",       "target": "diag_cpu",    "sourceHandle": null},
    {"source": "diag_cpu",    "target": "dec_runaway",  "sourceHandle": null},
    {"source": "dec_runaway", "target": "action_kill",  "sourceHandle": "true"},
    {"source": "dec_runaway", "target": "action_scale", "sourceHandle": "false"},
    {"source": "action_kill", "target": "verify_cpu",   "sourceHandle": null},
    {"source": "action_scale","target": "verify_cpu",   "sourceHandle": null},
    {"source": "verify_cpu",  "target": "incident_update_resolve", "sourceHandle": null},
    {"source": "incident_update_resolve", "target": "notify_done", "sourceHandle": null},
    {"source": "notify_done", "target": "end",          "sourceHandle": null}
  ]
}"""


# ── Tool loading ──────────────────────────────────────────────────────────────

def _load_approved_tools() -> list[dict]:
    """Load enabled approved actions from DB, ordered by category."""
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import ApprovedActionModel
        db = SessionLocal()
        try:
            rows = (
                db.query(ApprovedActionModel)
                .filter(ApprovedActionModel.enabled.is_(True))
                .order_by(ApprovedActionModel.category, ApprovedActionModel.tool_name)
                .all()
            )
            return [
                {
                    "tool_name": r.tool_name,
                    "name": r.name,
                    "category": r.category,
                    "description": getattr(r, "description", None) or r.name,
                    "output_fields": getattr(r, "output_fields", None) or [],
                    "parameters": getattr(r, "parameters", None) or [],
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[GraphGenerator] Could not load approved tools from DB: %s", exc)
        return []


# Field names ToolRegistryAgent._parse_tool_output's legacy hardcoded branches actually
# produce, for tools not yet migrated to the output_fields DB column. This exists purely
# to ground the LLM's output_capture choices in real field names instead of plausible-
# sounding invented ones (e.g. "$.status_code" when the tool actually returns "http_code").
# Keep in sync with incident_agents.py's _parse_tool_output legacy branches.
_LEGACY_OUTPUT_FIELDS: dict[str, list[str]] = {
    "check_disk_usage":       ["disk_percent", "available"],
    "host_disk_usage":        ["disk_percent", "available"],
    "check_memory":           ["mem_percent", "mem_used_gb", "mem_total_gb", "mem_available_gb"],
    "check_cpu":              ["cpu_percent", "cpu_user_percent", "cpu_sys_percent"],
    "check_health_endpoint":  ["http_code", "response_body", "reachable"],
    "get_error_rate":         ["error_count", "has_errors"],
    "top_processes":          ["top_process", "top_process_pid", "top_cpu_percent", "top_mem_percent"],
    "host_top_processes":     ["top_process", "top_process_pid", "top_cpu_percent", "top_mem_percent"],
    "host_service_status":    ["service_status", "service_running"],
    "win_service_status":     ["service_status", "service_running"],
    "check_swap":             ["swap_percent", "swap_used_kb", "swap_total_kb"],
    "query_metrics":          ["metric_value"],
    "check_container_status": ["container_status", "container_running", "container_restart_count",
                                "container_health", "container_exit_code"],
}


def _output_fields_for(tool: dict) -> list[str]:
    """
    Real, available output field names for a tool. DB output_fields (the modern,
    per-tool-defined mechanism — see approved_actions.output_fields) take priority;
    falls back to the legacy parser mirror above. Empty list means the tool has no
    structured/captureable output at all — the LLM must not write a condition or
    output_capture entry that depends on it.
    """
    db_fields = tool.get("output_fields") or []
    if db_fields:
        return [f.get("field") for f in db_fields if isinstance(f, dict) and f.get("field")]
    return _LEGACY_OUTPUT_FIELDS.get(tool["tool_name"], [])


def _required_params_for(tool: dict) -> list[str]:
    """Required parameter names for a tool — without these, the LLM tends to emit empty
    args for tools that can't do anything useful without them (e.g. get_thread_dump needs
    process_name; ping_service needs host)."""
    params = tool.get("parameters") or []
    return [p.get("name") for p in params if isinstance(p, dict) and p.get("required") and p.get("name")]


def _build_tools_block(tools: list[dict]) -> str:
    """Format the approved tools as a grouped text block for the prompt, including each
    tool's real output field names (so the LLM can't invent plausible-sounding ones) and
    required parameters (so it doesn't emit empty args for tools that need them)."""
    by_cat: dict[str, list] = {}
    for t in tools:
        by_cat.setdefault(t["category"], []).append(t)

    # One line per tool, kept terse — this block is repeated for every one of the (often 80+)
    # catalog tools on every generation call, so per-tool verbosity directly costs prompt
    # tokens against the configured model's context limit. Full instructions on how to use
    # "fields"/"requires" live once in the RULES section below, not repeated here.
    lines: list[str] = []
    for cat, items in by_cat.items():
        lines.append(f"[{cat.upper().replace('_', ' ')}]")
        for t in items:
            fields = _output_fields_for(t)
            fields_str = ", ".join(fields) if fields else "none"
            required = _required_params_for(t)
            req_str = f" | requires: {', '.join(required)}" if required else ""
            desc = (t['description'] or "")[:55].rstrip()
            lines.append(f"  {t['tool_name']} — {desc} | fields: {fields_str}{req_str}")
    return "\n".join(lines) if lines else "  (no tools loaded)"


def _repair_graph_edges(steps: list[dict], edges: list[dict]) -> list[dict]:
    """
    Clean up LLM-produced graph_edges. LLMs are unreliable at hand-crafting a consistent
    edge list, and a bad one is exactly what causes "multiple lines from a source" /
    confusing branch rendering: a node ending up with more than one outgoing edge for the
    same branch (or any branch at all, for non-decision steps).

    Rules enforced:
      - Drop edges referencing unknown step ids (hallucinated/typo'd targets).
      - At most one outgoing edge per (source, sourceHandle) — first one wins. A node
        only ever has ONE "true" path, ONE "false" path, and non-decision nodes only have
        ONE outgoing path, period.
      - Every decision's on_true/on_false is guaranteed a real edge, even if the LLM's
        graph_edges array omitted it or got the handle wrong.

    Multiple INCOMING edges into the same node (branches converging back together) are
    untouched — that's legitimate and common, only outgoing ambiguity is the actual bug.
    """
    step_by_id = {s.get("id"): s for s in steps if s.get("id")}
    valid_ids  = set(step_by_id) | {"start", "end"}

    def _is_decision(node_id) -> bool:
        s = step_by_id.get(node_id)
        return bool(s) and (s.get("type") or "").lower() == "decision"

    cleaned = [
        e for e in edges
        if e.get("source") in valid_ids and e.get("target") in valid_ids
        # Decisions only ever route via true/false — any other handle is noise/an LLM mistake.
        and not (_is_decision(e.get("source")) and e.get("sourceHandle") not in ("true", "false"))
    ]

    seen_outgoing: set[tuple] = set()
    deduped: list[dict] = []
    for e in cleaned:
        key = (e.get("source"), e.get("sourceHandle"))
        if key in seen_outgoing:
            continue
        seen_outgoing.add(key)
        deduped.append(e)

    for s in steps:
        if (s.get("type") or "").lower() != "decision":
            continue
        for branch, target_key in (("true", "on_true"), ("false", "on_false")):
            target = s.get(target_key)
            if not target or target not in valid_ids:
                continue
            key = (s.get("id"), branch)
            if key not in seen_outgoing:
                deduped.append({"source": s.get("id"), "target": target, "sourceHandle": branch})
                seen_outgoing.add(key)

    # Connect any non-decision step that has no outgoing edge to "end".
    # This prevents terminal notify/action/diagnostic nodes from being dead ends
    # when the LLM forgets to wire them forward.
    sources_with_edges = {e.get("source") for e in deduped}
    for s in steps:
        sid = s.get("id")
        stype = (s.get("type") or "").lower()
        if stype in ("start", "end", "decision"):
            continue
        if sid and sid not in sources_with_edges:
            deduped.append({"source": sid, "target": "end", "sourceHandle": None})
            sources_with_edges.add(sid)

    return deduped


def _repair_output_capture(steps: list[dict], tools: list[dict]) -> None:
    """
    Deterministic backstop for output_capture, mirroring _repair_graph_edges for edges.

    The prompt tells the LLM to only capture a tool's real output fields and never touch
    a tool with no known output — but that's a soft instruction, and LLMs don't always
    follow it. This strips (and logs) any output_capture entry that doesn't actually
    correspond to a real field, rather than leaving it to silently resolve to None at
    runtime. Mutates `steps` in place.

    A stripped capture means nothing downstream produces that variable name — which is
    intentional: it surfaces as a visible "not produced by this tool" warning in the
    editor's decision-condition validation, instead of a decision that silently always
    takes the same branch.
    """
    tools_by_name = {t["tool_name"]: t for t in tools}
    for step in steps:
        tool_name = step.get("tool")
        output_capture = step.get("output_capture")
        if not tool_name or not output_capture:
            continue

        tool = tools_by_name.get(tool_name)
        known = set(_output_fields_for(tool)) if tool else set()

        if not known:
            logger.warning(
                "[GraphGenerator] Stripped output_capture on step '%s' — tool '%s' has no "
                "known output fields", step.get("id"), tool_name,
            )
            step["output_capture"] = {}
            continue

        cleaned: dict = {}
        for var_name, jpath in output_capture.items():
            field = jpath.lstrip("$").lstrip(".") if isinstance(jpath, str) else ""
            leaf = field.split(".")[-1] if field else ""
            if leaf in known or var_name in known:
                cleaned[var_name] = jpath
            else:
                logger.warning(
                    "[GraphGenerator] Stripped output_capture '%s': '%s' on step '%s' — "
                    "tool '%s' produces %s, not '%s'",
                    var_name, jpath, step.get("id"), tool_name, sorted(known), leaf or var_name,
                )
        step["output_capture"] = cleaned


def _repair_incident_update(steps: list[dict], edges: list[dict]) -> list[dict]:
    """
    Deterministic backstop for incident_update, mirroring _repair_graph_edges/
    _repair_output_capture: the prompt tells the LLM to follow every resolving
    verification step with an incident_update step, but that's a soft instruction.

    Must run AFTER _repair_graph_edges, which guarantees every non-decision step
    (including verification) already has exactly one outgoing edge — either what
    the LLM intended, or auto-connected to "end" if it forgot. So the only thing
    left to check here is whether that one edge already points at an
    incident_update step; if not, splice one in between, mirroring exactly what
    the seeded-runbook migration did by hand.
    """
    step_by_id = {s.get("id"): s for s in steps if s.get("id")}
    existing_ids = set(step_by_id)
    new_steps: list[dict] = []
    new_edges = list(edges)

    for i, step in enumerate(steps):
        if (step.get("type") or "").lower() != "verification":
            continue
        sid = step.get("id")
        out_edges = [e for e in new_edges if e.get("source") == sid]
        if not out_edges:
            continue
        edge = out_edges[0]
        target = edge.get("target")
        target_step = step_by_id.get(target)
        if target_step and (target_step.get("type") or "").lower() == "incident_update":
            continue  # already wired correctly

        new_id = f"incident_update_{sid}"
        n = 2
        while new_id in existing_ids:
            new_id = f"incident_update_{sid}_{n}"
            n += 1
        existing_ids.add(new_id)

        new_steps.append({
            "id": new_id, "type": "incident_update", "name": "Mark Resolved", "state": "resolved",
        })
        new_edges.remove(edge)
        new_edges.append({"source": sid, "target": new_id, "sourceHandle": edge.get("sourceHandle")})
        new_edges.append({"source": new_id, "target": target, "sourceHandle": None})
        logger.info(
            "[GraphGenerator] Inserted incident_update step '%s' between verification '%s' and '%s' "
            "— LLM omitted it", new_id, sid, target,
        )

    steps.extend(new_steps)
    return new_edges


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(
    description: str,
    event_type: str,
    platform: str,
    tools: list[dict],
) -> str:
    tools_block = _build_tools_block(tools)
    return f"""You are an expert SRE engineer building a visual decision-graph runbook for an incident operations platform.

USER REQUEST:
{description}

EVENT TYPE: {event_type or "(infer from description — use hierarchical format e.g. infrastructure.compute.cpu_high)"}
PLATFORM: {platform}

=== STEP TYPES ===
- "diagnostic"   — non-destructive read-only check.
                   MUST have: tool, args (including every parameter listed as "requires" for that tool).
                   SHOULD have: output_capture (key → JSONPath, e.g. "cpu_pct": "$.cpu_percent")
                   The JSONPath field name MUST be one of that tool's listed "output fields" below —
                   never guess or invent a field name based on what sounds plausible.
- "action"       — executes a remediation command.
                   MUST have: tool, args (including every parameter listed as "requires" for that tool).
                   OPTIONAL: run_if (boolean expression using captured variables)
- "verification" — re-measures the same thing the incident was triggered by, AFTER remediation,
                   and checks the fresh value against a fixed health threshold.
                   MUST have: tool, args, output_capture, metric, check, value.
                   The "tool" here is the SAME diagnostic tool used earlier to detect the problem
                   (e.g. check_cpu before remediation, check_cpu again here) — the executor actually
                   re-runs it at this point in the graph to get a fresh post-remediation reading.
                   Do NOT omit "tool" and just re-check an earlier diagnostic's stale value — that
                   measures whether the problem existed before you acted, not whether your action
                   fixed it.
                   "metric" MUST be a key from THIS step's own output_capture (not an earlier
                   diagnostic step's) — name it with an "_after" suffix to make the re-measurement
                   explicit, e.g. "cpu_after", "reachable_after", "disk_after".
                   "check"/"value" express an ABSOLUTE health threshold, never a comparison to the
                   "before" value — e.g. cpu_after less_than 80, not "cpu_after less_than cpu_before".
                   A relative improvement (90%→85%) can still be unhealthy; only an absolute
                   threshold on the after-value means "actually fixed."
                   "check" MUST be EXACTLY one of: equals | not_equals | less_than | greater_than |
                   less_than_or_equal | greater_than_or_equal | contains — full words only, never
                   abbreviations like "eq"/"lt"/"gt"/"==", they will not be recognized.
                   "value" is the expected value to compare against, as a bare string or number —
                   NEVER wrap it in quote characters (write running, not 'running' or "running").
                   The comparison is against the literal characters you provide; a quoted value
                   will never match an unquoted captured one.
- "incident_update" — declares the incident resolved (or escalated). MUST have: state (one of
                   "resolved", "escalated", "acknowledged"). MUST NOT have a "tool" field.
                   This is the ONLY way an incident is ever marked resolved — without this step
                   on a path, that path always ends up awaiting manual review, no matter how the
                   verification or actions went. Place it immediately after a "verification" step
                   succeeds, with state="resolved". Needs no "run_if" and no decision node guarding
                   it: every step defaults to on_failure="abort", so if the verification step it
                   follows fails, the runbook halts before ever reaching this step — it is only
                   ever reached when everything before it, including verification, succeeded.
- "decision"     — branches the graph.
                   MUST have: condition (expression using captured vars), on_true (step id or "end"), on_false (step id or "end")
                   MUST NOT have a "tool" field
                   on_true and on_false MUST be different targets — if both branches do the same
                   thing next, the decision is pointless; merge them into one step instead.
- "notify"       — sends an alert or webhook.
                   MUST have: tool=send_alert, args with severity + message (may use {{{{var_name}}}} placeholders)
- "wait"         — pauses execution for a fixed duration.
                   MUST have: duration_seconds (integer, e.g. 30).
                   Use after a restart/scale action to give the service time to stabilise before verifying.

=== APPROVED TOOLS — use ONLY these exact tool_name values ===
{tools_block}

=== RULES ===
1. Only use tool_name values from the approved list — never invent a tool name.
2. Every step except "decision" MUST have a "tool" field.
3. Every "decision" step MUST have "condition", "on_true", "on_false" and NO "tool" field.
4. All step IDs must be snake_case and unique (e.g. diag_memory, dec_container_down, action_restart).
5. Every condition and output_capture value MUST reference only the exact "output fields" names
   listed for that tool above. A tool listed as having NONE must never appear in a condition or
   output_capture — diagnose with it, but don't try to branch on its result. This applies even if
   you skip writing an output_capture for that step: a decision MUST NOT assume a variable exists
   just because a diagnostic step ran — the variable must actually be captured (via that step's
   output_capture, or be one of the tool's real output fields directly) somewhere in the runbook,
   or it will always resolve to nothing and the decision will always take the same branch.
6. graph_edges MUST include an outgoing edge for EVERY step — no step (other than "end") may be
   a dead end. Before finishing, scan your step list and confirm each step ID appears as a "source"
   in graph_edges at least once. Any step missing an outgoing edge is a bug.
7. sourceHandle: "true" for the TRUE branch of a decision, "false" for FALSE branch, null for all other edges.
8. A node has AT MOST ONE outgoing edge per branch — a decision has exactly one "true" edge and
   one "false" edge; every other step type has exactly one outgoing edge, period. Never give the
   same node two different outgoing edges with the same (or no) sourceHandle.
9. Include at least 2 diagnostic steps, at least 1 decision node with meaningful branching.
10. blast_radius: 1=read-only, 2=graceful ops, 3=restart/scale, 4=data impact, 5=destructive.
11. When multiple paths converge on the same node (e.g. both branches rejoin a verify step), include
    one incoming edge from each source — that's fine; rule 8 is about OUTGOING edges only.
12. Every parameter listed as "requires" for a tool MUST appear in that step's args with a real
    value — a tool needing process_name/host/port etc. does nothing useful with empty args. Use a
    literal value from the description, or reference an earlier captured variable as a placeholder.
13. If a diagnostic step's tool has "fields: none", do NOT place a decision right after it whose
    condition depends on that step. A tool with no output fields cannot tell you anything to branch
    on — either drop the decision and continue straight to the next step/notify, or pick a tool that
    has real output fields and capture from that instead.
14. Use positive conditions only — never "x == false". Write "healthy == true" (true→exit, false→diagnose)
    or "has_errors == true" (true→act, false→skip). Double negatives mis-wire branches.
15. For HTTP health checks on a service incident, use "url": "{{service_url}}" in the step args.
    The executor resolves {{service_url}} from the incident's service_url field at runtime (the full URL
    the operator submitted, e.g. http://api-server:8080). Only fall back to "http://{{target}}:PORT/health"
    when you have strong reason to believe the port is known and the incident has no service_url.
16. Every "verification" step that you intend to lead to resolution MUST be followed by an
    "incident_update" step with state="resolved" before any final "notify". A runbook with no
    incident_update step reachable after its verification can never auto-resolve a real incident —
    it will always end up awaiting manual review even when everything actually worked.

=== OUTPUT FORMAT ===
Follow this exact JSON schema. Output ONLY the JSON — no markdown fences, no explanation.

{_SCHEMA_EXAMPLE}

Now generate a complete, realistic runbook for the user request above.
Think through the failure modes and what branch points make sense before writing the JSON."""


# ── LLM callers ──────────────────────────────────────────────────────────────

async def _call_openai(provider, prompt: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=provider.api_key)
    model = getattr(provider, "model", None) or "gpt-4o"
    json_models = {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo-1106", "gpt-4-1106-preview"}
    kwargs = {}
    if any(m in model for m in json_models):
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=2500,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return resp.choices[0].message.content


async def _call_anthropic(provider, prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=provider.api_key)
    resp = await client.messages.create(
        model=getattr(provider, "model", None) or "claude-sonnet-4-6",
        max_tokens=2500,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_and_normalise(raw: str, event_type: str, platform: str, tools: list[dict]) -> dict:
    """Strip markdown fences, parse JSON, fill missing defaults."""
    text = re.sub(r"```(?:json)?\s*\n?", "", raw).strip().rstrip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise RuntimeError(
                f"[GraphGenerator] LLM did not return valid JSON. Preview: {text[:300]}"
            )
        data = json.loads(m.group())

    # Top-level defaults
    data.setdefault("name", "Generated Runbook")
    data.setdefault("trigger_type", event_type)
    data.setdefault("description", "")
    data.setdefault("platform", platform)
    data.setdefault("blast_radius", 2)
    data.setdefault("enabled", True)
    data.setdefault("steps", [])
    data.setdefault("graph_edges", [])

    # Compat: if LLM fell back to the old 4-array format, flatten it
    if not data["steps"] and (
        "diagnostics_steps" in data
        or "remediation_steps" in data
        or "verification_steps" in data
    ):
        steps: list = []

        def _add(raw_steps: list, step_type: str) -> None:
            for s in raw_steps:
                steps.append({
                    **s,
                    "type": step_type,
                    "id": s.get("id") or f"{step_type[:4]}_{len(steps) + 1}",
                })

        _add(data.get("diagnostics_steps", []), "diagnostic")
        _add(data.get("remediation_steps", []), "action")
        _add(data.get("verification_steps", []), "verification")
        data["steps"] = steps

        # Auto-generate linear edges as last-resort fallback
        if not data["graph_edges"] and steps:
            edges = [{"source": "start", "target": steps[0]["id"], "sourceHandle": None}]
            for i in range(len(steps) - 1):
                edges.append({
                    "source": steps[i]["id"],
                    "target": steps[i + 1]["id"],
                    "sourceHandle": None,
                })
            edges.append({"source": steps[-1]["id"], "target": "end", "sourceHandle": None})
            data["graph_edges"] = edges

    # Repair graph_edges regardless of source format — LLMs are unreliable at hand-crafting
    # a consistent edge list, and a bad one is exactly what produces confusing renders
    # (a node with multiple, ambiguous outgoing edges for the same branch).
    if data["steps"]:
        before = len(data["graph_edges"])
        data["graph_edges"] = _repair_graph_edges(data["steps"], data["graph_edges"])
        after = len(data["graph_edges"])
        if after != before:
            logger.info(
                "[GraphGenerator] Repaired graph_edges: %d → %d (removed duplicates/orphans, "
                "filled missing decision branches)", before, after,
            )

        # Deterministic backstop for output_capture — the prompt instructs the LLM to only
        # capture real fields, but that's a soft constraint it doesn't always follow.
        _repair_output_capture(data["steps"], tools)

        # Deterministic backstop for incident_update — must run after _repair_graph_edges
        # (which guarantees every verification step already has exactly one outgoing edge
        # to check against). Without this, a runbook the LLM "finished" without ever adding
        # the resolve step can never auto-resolve a real incident.
        data["graph_edges"] = _repair_incident_update(data["steps"], data["graph_edges"])

        # Replace placeholder URLs with {service_url} so the executor resolves
        # them from the incident context at runtime.  LLMs reliably produce
        # patterns like "http://service-url/health" despite rule 15.
        _repair_service_urls(data["steps"])

    return data


_PLACEHOLDER_URL_RE = re.compile(
    r"https?://(service-url|SERVICE-URL|<service[_-]url>|example\.com|localhost|127\.0\.0\.1)",
    re.IGNORECASE,
)

def _repair_service_urls(steps: list[dict]) -> None:
    """
    Replace LLM-generated placeholder URLs in step args with {service_url} so
    the executor substitutes the real incident URL at runtime.  Mutates in place.
    """
    for step in steps:
        args = step.get("args") or {}
        url_val = args.get("url", "")
        if url_val and _PLACEHOLDER_URL_RE.match(url_val):
            args["url"] = "{service_url}"
            logger.info(
                "[GraphGenerator] Replaced placeholder URL '%s' → '{service_url}' on step '%s'",
                url_val, step.get("id"),
            )
        # Also fix output_capture: check_health_endpoint emits 'reachable', not 'healthy'
        tool = step.get("tool", "")
        oc = step.get("output_capture") or {}
        if tool == "check_health_endpoint" and "healthy" in oc:
            oc["reachable"] = oc.pop("healthy").replace("$.healthy", "$.reachable")
            logger.info(
                "[GraphGenerator] Fixed output_capture 'healthy'→'reachable' on step '%s'",
                step.get("id"),
            )
        # Fix decision conditions that reference the stale 'healthy' field
        if step.get("type") == "decision":
            cond = step.get("condition", "")
            if "healthy" in cond:
                step["condition"] = cond.replace("healthy", "reachable")
                logger.info(
                    "[GraphGenerator] Fixed condition 'healthy'→'reachable' on step '%s'",
                    step.get("id"),
                )


# ── Public entry point ────────────────────────────────────────────────────────

async def generate_runbook_graph(
    description: str,
    event_type: str = "",
    platform: str = "any",
) -> dict:
    """
    Generate a decision-graph runbook from a free-form description.

    Uses the platform's configured LLM (OpenAI or Anthropic).
    Returns graph JSON with keys: name, trigger_type, description,
    platform, blast_radius, steps, graph_edges.
    """
    from agentic_os.services.summary_service import get_summary_service

    service = get_summary_service()
    provider = service.provider
    provider_name = type(provider).__name__.lower()

    tools = _load_approved_tools()
    prompt = _build_prompt(description, event_type, platform, tools)

    logger.info(
        "[GraphGenerator] Generating runbook via %s (%d tools, prompt %d chars)",
        provider_name, len(tools), len(prompt),
    )

    if "openai" in provider_name:
        raw = await _call_openai(provider, prompt)
    elif "anthropic" in provider_name:
        raw = await _call_anthropic(provider, prompt)
    else:
        raise RuntimeError(f"[GraphGenerator] Unsupported LLM provider: {provider_name}")

    result = _parse_and_normalise(raw, event_type, platform, tools)
    result["generation_prompt"] = description  # original user description, used to pre-fill regenerate

    logger.info(
        "[GraphGenerator] Done: '%s' — %d steps, %d edges",
        result.get("name"), len(result.get("steps", [])), len(result.get("graph_edges", [])),
    )
    return result
