"""Runbook CRUD endpoints + graph-editor execute endpoint"""

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, Dict


class RunbookGenerateRequest(BaseModel):
    description: str
    event_type: str = ""
    platform: str = "any"
from uuid import UUID
from datetime import datetime
import re
import time
import asyncio
import logging

from agentic_os.db.database import get_session
from agentic_os.db.models import EventTypeTaxonomyModel
from agentic_os.db.repositories import RunbookRepository

router = APIRouter(tags=["runbooks"])
read_router = APIRouter(tags=["runbooks"])   # GET endpoints — registered with _any (viewer+)
logger = logging.getLogger(__name__)


# ── Event-type helpers ────────────────────────────────────────────────────────

_EVENT_TYPE_CODE_RE = r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*){1,3}$"


def _resolve_event_type(event_type: str, db: Session) -> str:
    """
    Normalise an event_type alias to its canonical hierarchical code, auto-registering
    it as an operator-defined taxonomy entry (is_system=False) if it's well-formed but
    not yet known.

    A runbook (especially an AI-generated one — the generator infers a hierarchical
    code from the description with no way to know the existing taxonomy) introducing a
    new, well-formed event type is the normal way the taxonomy grows, via the same
    mechanism as POST /api/event-types. It is NOT an error and must not block the save —
    that previously surfaced as a confusing, silent save failure with no indication that
    the cause was an unregistered event type. Only a malformed code (doesn't match the
    dot-separated lowercase pattern) is actually rejected.

    Returns the canonical code on success. The sentinel value "unknown" bypasses
    validation entirely.
    """
    if event_type == "unknown":
        return event_type          # special sentinel — skip validation

    try:
        from agentic_os.connectors.event_type_utils import normalize_event_type
        canonical = normalize_event_type(event_type)
    except ImportError:
        canonical = event_type     # normaliser unavailable — use as-is

    try:
        # No enabled filter here: a code that exists but is disabled is still a
        # known code (e.g. an admin turned it off via Event Types) — treating it
        # as "not found" tried to INSERT a duplicate primary key every time a
        # runbook referenced it, which (see except below) poisoned the session
        # for the rest of the request and surfaced as an unrelated 500 on save.
        exists = db.query(EventTypeTaxonomyModel).filter(
            EventTypeTaxonomyModel.code == canonical,
        ).first()
        if exists is None:
            if not re.match(_EVENT_TYPE_CODE_RE, canonical):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"'{event_type}' is not a valid event type code — expected dot-separated "
                        f"lowercase, e.g. 'infrastructure.compute.cpu_high'. "
                        f"Use GET /api/event-types to browse existing codes."
                    ),
                )
            new_row = EventTypeTaxonomyModel(
                code=canonical,
                label=canonical.replace(".", " ").replace("_", " ").title(),
                description=f"Auto-registered when a runbook using '{canonical}' was saved.",
                category=canonical.split(".")[0],
                aliases=[],
                is_system=False,
                enabled=True,
                created_at=datetime.utcnow(),
            )
            db.add(new_row)
            db.commit()
            logger.info("Auto-registered new event type from runbook save: %s", canonical)
    except HTTPException:
        raise
    except Exception as exc:
        # Taxonomy DB unavailable — log and continue rather than breaking saves.
        # Must roll back: a failed commit (e.g. the duplicate-key case above)
        # leaves the session in a failed-transaction state, and the runbook
        # save that happens right after this call would otherwise fail too —
        # surfacing as an unrelated 500 with no indication this was the cause.
        logger.warning("Could not validate event_type against taxonomy: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

    if canonical != event_type:
        logger.info("Runbook event_type resolved: %r → %r", event_type, canonical)

    return canonical


# ── helpers ───────────────────────────────────────────────────────────────────

# Canonical check operator names, plus every abbreviation/symbol an LLM (or a human used
# to a different convention) is likely to write instead. A mismatch here silently falls
# through to "no operator matched" — which previously meant FAIL regardless of whether the
# actual value satisfied the intended check. Normalizing protects against that class of bug.
_CHECK_ALIASES: Dict[str, str] = {
    "equals": "equals", "eq": "equals", "==": "equals", "=": "equals", "is": "equals",
    "not_equals": "not_equals", "ne": "not_equals", "!=": "not_equals", "neq": "not_equals",
    "less_than": "less_than", "lt": "less_than", "<": "less_than",
    "greater_than": "greater_than", "gt": "greater_than", ">": "greater_than",
    "less_than_or_equal": "less_than_or_equal", "lte": "less_than_or_equal", "le": "less_than_or_equal", "<=": "less_than_or_equal",
    "greater_than_or_equal": "greater_than_or_equal", "gte": "greater_than_or_equal", "ge": "greater_than_or_equal", ">=": "greater_than_or_equal",
    "contains": "contains", "in": "contains",
}


def _unquote(s: str) -> str:
    """Strip one layer of matching surrounding quotes — an LLM (or a human pasting a
    code-style literal) will sometimes write the expected value as 'running' or "running"
    instead of a bare running, which then never string-equals an unquoted actual value."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def _verify_metric(actual, check: str, expected: str) -> bool:
    """Evaluate a verification check against a captured value."""
    canon = _CHECK_ALIASES.get((check or "").strip().lower(), check)
    try:
        a = float(_unquote(str(actual)))
        e = float(_unquote(str(expected)))
        return {
            "less_than":              a < e,
            "greater_than":           a > e,
            "less_than_or_equal":     a <= e,
            "greater_than_or_equal":  a >= e,
            "equals":                 abs(a - e) < 1e-9,
            "not_equals":             abs(a - e) >= 1e-9,
        }.get(canon, False)
    except (ValueError, TypeError):
        s = _unquote(str(actual)).lower()
        t = _unquote(str(expected)).lower()
        return {"equals": s == t, "not_equals": s != t, "contains": t in s}.get(canon, False)


def _editor_steps_to_db(steps: list) -> dict:
    """
    Translate editor's unified steps array into the DB column format
    {diagnostics, actions, verification_steps}.
    Also returns a flat check/value verification list compatible with DB schema.
    """
    from agentic_os.agents.incident_agents import ToolRegistryAgent
    translated = ToolRegistryAgent._translate_editor_steps(steps)
    # verification_steps DB format uses threshold_type / threshold
    db_verify = []
    for s in translated["verification"]:
        db_verify.append({
            "name":           s.get("name", ""),
            "description":    s.get("description", ""),
            "metric":         s.get("metric", ""),
            "threshold_type": s.get("check", "less_than"),
            "threshold":      s.get("value", ""),
        })
    return {
        "diagnostics":        translated["diagnostics"],
        "actions":            translated["actions"],
        "verification_steps": db_verify,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

@read_router.get("/runbooks")
async def list_runbooks(
    event_type: Optional[str] = None,
    platform: Optional[str] = None,
    enabled: Optional[bool] = None,
    limit: int = 100,
    db: Session = Depends(get_session),
):
    enabled_only = enabled if enabled is not None else False
    repo = RunbookRepository(db)
    runbooks = repo.list(event_type=event_type, enabled_only=enabled_only, platform=platform, limit=limit)
    return [RunbookRepository.to_dict(r) for r in runbooks]


@read_router.get("/runbooks/{runbook_id}")
async def get_runbook(runbook_id: str, db: Session = Depends(get_session)):
    repo = RunbookRepository(db)
    runbook = repo.get(UUID(runbook_id))
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return RunbookRepository.to_dict(runbook)


@router.post("/runbooks/generate")
async def generate_runbook(req: RunbookGenerateRequest):
    """
    Generate a decision-graph runbook from a free-form description using the
    platform's configured LLM.  Returns a graph-format runbook JSON that the
    editor can import directly (steps + graph_edges).
    """
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="description is required")
    try:
        from agentic_os.services.runbook_graph_generator import generate_runbook_graph
        result = await generate_runbook_graph(
            req.description.strip(),
            req.event_type.strip(),
            req.platform.strip() or "any",
        )
        return result
    except Exception as exc:
        logger.error("[RunbookGenerate] Failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/runbooks", status_code=201)
async def create_runbook(data: dict, db: Session = Depends(get_session)):
    data.pop("runbook_id", None)
    data.pop("created_at", None)
    data.pop("updated_at", None)

    if not data.get("name"):
        raise HTTPException(status_code=422, detail="name is required")

    # ── Editor format: unified steps array ───────────────────────────────────
    # When the graph editor saves, it sends {name, trigger_type, steps: [...],
    # graph_edges: [...]}. Translate to the legacy column format the DB expects.
    if "steps" in data and isinstance(data.get("steps"), list):
        if not data.get("event_type"):
            # Use trigger_type if present and non-empty; fall back to "unknown"
            data["event_type"] = data.pop("trigger_type", None) or "unknown"
        source_steps = data.get("steps")                # save before popping
        graph_edges     = data.pop("graph_edges", None)     # graph routing edges
        graph_positions = data.pop("graph_positions", None)  # node layout positions
        translated = _editor_steps_to_db(data.pop("steps"))
        data.update(translated)
        # Store steps + edges + positions together so the graph editor can
        # reload the full canvas exactly as drawn.
        data["source_steps"] = {
            "steps":     source_steps,
            "edges":     graph_edges     or [],
            "positions": graph_positions or {},
        }
        # Mark as operator-authored (came from the editor)
        data.setdefault("source", "operator_authored")
        data.setdefault("enabled", True)
        data.setdefault("blast_radius", 2)
        data.setdefault("confidence", 0.80)
        data.pop("trigger_type", None)  # already moved to event_type
        data.pop("description", None) if data.get("description") == "" else None

    # ── Legacy format: 3-array form ───────────────────────────────────────────
    if not data.get("event_type"):
        raise HTTPException(status_code=422, detail="event_type (or trigger_type) is required")

    # Normalise alias → canonical code and validate against taxonomy
    data["event_type"] = _resolve_event_type(data["event_type"], db)

    if "verification_steps" in data and data["verification_steps"]:
        vs = data["verification_steps"]
        if vs and isinstance(vs[0], dict) and "check" in vs[0]:
            # Frontend check/value format → DB threshold_type/threshold
            data["verification_steps"] = [
                {
                    "description":    s.get("description", ""),
                    "metric":         s.get("metric", ""),
                    "threshold_type": s.get("check", "less_than"),
                    "threshold":      s.get("value", ""),
                }
                for s in vs
            ]

    repo = RunbookRepository(db)
    runbook = repo.create(data)
    return RunbookRepository.to_dict(runbook)


@router.put("/runbooks/{runbook_id}")
async def update_runbook(runbook_id: str, data: dict, db: Session = Depends(get_session)):
    data.pop("runbook_id", None)
    data.pop("created_at", None)
    data.pop("updated_at", None)

    # ── Editor format: unified steps array ───────────────────────────────────
    if "steps" in data and isinstance(data.get("steps"), list):
        if not data.get("event_type"):
            data["event_type"] = data.pop("trigger_type", None) or "unknown"
        source_steps = data.get("steps")
        graph_edges     = data.pop("graph_edges", None)
        graph_positions = data.pop("graph_positions", None)
        translated = _editor_steps_to_db(data.pop("steps"))
        data.update(translated)
        data["source_steps"] = {
            "steps":     source_steps,
            "edges":     graph_edges     or [],
            "positions": graph_positions or {},
        }
        data.pop("trigger_type", None)
    elif "verification_steps" in data:
        # ── Legacy format: 3-array check/value → threshold_type/threshold ──
        verification_steps = data.get("verification_steps", [])
        transformed_verification = []
        for step in verification_steps:
            transformed_verification.append({
                "description":    step.get("description", ""),
                "metric":         step.get("metric", ""),
                "threshold_type": step.get("check", "less_than"),
                "threshold":      step.get("value", ""),
            })
        data["verification_steps"] = transformed_verification

    # Normalise and validate event_type if present in the update payload
    if data.get("event_type"):
        data["event_type"] = _resolve_event_type(data["event_type"], db)

    repo = RunbookRepository(db)
    # `enabled` is an instant kill-switch — apply it immediately, outside
    # draft/publish, rather than letting it sit in draft_snapshot unapplied.
    enabled = data.pop("enabled", None)
    if enabled is not None:
        repo.set_enabled(UUID(runbook_id), bool(enabled))
    runbook = repo.save_draft(UUID(runbook_id), data)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return RunbookRepository.to_dict(runbook)


def _check_publish_validation_paths(source_steps: dict) -> list:
    """Best-effort lint, never blocking: warn if any path from start to end that
    runs a remediation action doesn't also pass a verification step followed by
    an incident_update step. VerifierAgent defaults to AWAITING_MANUAL without an
    incident_update signal, so such a path can never auto-resolve a real incident
    — almost certainly unintentional, but the runbook author may have other plans
    for it (e.g. an intentional escalate-only branch), so this only warns."""
    steps = (source_steps or {}).get("steps") or []
    edges = (source_steps or {}).get("edges") or []
    if not steps or not edges:
        return []

    step_by_id = {s.get("id"): s for s in steps}
    adj: dict = {}
    for e in edges:
        adj.setdefault(e.get("source"), []).append(e.get("target"))

    warnings: list = []
    seen_paths: set = set()

    def walk(node_id, path, has_action, has_verification, resolves):
        if node_id is None or node_id in path:  # cycle guard
            return
        path = path + [node_id]
        step = step_by_id.get(node_id)
        node_type = (step.get("type") or "").lower() if step else ""

        if node_type == "action":
            has_action = True
        if node_type == "verification":
            has_verification = True
        if node_type == "incident_update" and has_verification:
            resolves = True

        nexts = adj.get(node_id) or []
        if not nexts or node_id == "end":
            if has_action and not resolves:
                key = tuple(path)
                if key not in seen_paths:
                    seen_paths.add(key)
                    warnings.append(
                        f"Path {' → '.join(path)} runs a remediation action but never reaches "
                        f"a verification step followed by an incident_update step — this path "
                        f"can never auto-resolve a real incident (it will always end up "
                        f"awaiting manual review)."
                    )
            return

        for nxt in nexts:
            walk(nxt, path, has_action, has_verification, resolves)

    walk("start", [], False, False, False)
    return warnings


@router.post("/runbooks/{runbook_id}/publish")
async def publish_runbook(runbook_id: str, body: dict = Body(default={}), db: Session = Depends(get_session)):
    """Promote the current draft to live — the next matching incident uses it,
    and a version-history row is recorded."""
    repo = RunbookRepository(db)
    runbook = repo.publish(UUID(runbook_id), change_note=body.get("change_note"))
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
    result = RunbookRepository.to_dict(runbook)
    result["warnings"] = _check_publish_validation_paths(runbook.source_steps or {})
    return result


@router.post("/runbooks/{runbook_id}/discard-draft")
async def discard_runbook_draft(runbook_id: str, db: Session = Depends(get_session)):
    """Discard pending draft edits — resets draft_snapshot to mirror live state."""
    repo = RunbookRepository(db)
    runbook = repo.discard_draft(UUID(runbook_id))
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return RunbookRepository.to_dict(runbook)


@router.patch("/runbooks/{runbook_id}/enabled")
async def set_runbook_enabled(runbook_id: str, body: dict = Body(...), db: Session = Depends(get_session)):
    """Instant kill-switch — bypasses draft/publish entirely."""
    if "enabled" not in body:
        raise HTTPException(status_code=422, detail="enabled is required")
    repo = RunbookRepository(db)
    runbook = repo.set_enabled(UUID(runbook_id), bool(body["enabled"]))
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return RunbookRepository.to_dict(runbook)


@read_router.get("/runbooks/{runbook_id}/versions")
async def list_runbook_versions(runbook_id: str, db: Session = Depends(get_session)):
    repo = RunbookRepository(db)
    if not repo.get(UUID(runbook_id)):
        raise HTTPException(status_code=404, detail="Runbook not found")
    versions = repo.list_versions(UUID(runbook_id))
    return [
        {
            "version": v.version,
            "created_at": v.created_at.isoformat(),
            "created_by": v.created_by,
            "change_note": v.change_note,
            "snapshot": v.snapshot,
        }
        for v in versions
    ]


@router.post("/runbooks/{runbook_id}/versions/{version}/restore")
async def restore_runbook_version(runbook_id: str, version: int, db: Session = Depends(get_session)):
    """Load a historical version into draft_snapshot for review — does not
    publish it directly. A separate POST .../publish call is required."""
    repo = RunbookRepository(db)
    runbook = repo.restore_version_to_draft(UUID(runbook_id), version)
    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook or version not found")
    return RunbookRepository.to_dict(runbook)


@router.delete("/runbooks/{runbook_id}", status_code=204)
async def delete_runbook(runbook_id: str, db: Session = Depends(get_session)):
    from agentic_os.db.models import RunbookModel
    rb = db.query(RunbookModel).filter_by(id=UUID(runbook_id)).first()
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
    if rb.is_seeded:
        raise HTTPException(
            status_code=403,
            detail="Seeded runbooks cannot be deleted. Disable the runbook instead.",
        )
    repo = RunbookRepository(db)
    if not repo.delete(UUID(runbook_id)):
        raise HTTPException(status_code=404, detail="Runbook not found")


# ── Graph-editor live execution ───────────────────────────────────────────────

@router.post("/runbooks/execute-editor")
async def execute_editor_runbook(data: dict, db: Session = Depends(get_session)):
    """
    Execute a runbook built in the graph editor against a real target container.

    Unlike the full incident workflow, this runs tools directly via the watcher
    without creating an incident or going through the approval pipeline.
    Intended for testing/validation from the editor UI.

    Request body:
      steps             — editor's unified steps array (required)
      edges             — graph edges array (optional; enables decision-node routing)
      target            — container / resource name to run tools against (required)
      watcher_name      — which watcher to route through (default: watcher_brain)
      on_step_failure   — "continue" (default) | "stop_actions" | "stop_all"
                          continue:      always advance to the next node
                          stop_actions:  halt if an action step fails (diagnostics
                                         and verifications always continue)
                          stop_all:      halt on any step failure
    """
    from agentic_os.agents.incident_agents import ToolRegistryAgent, _resolve_watcher_info

    steps            = data.get("steps", [])
    edges            = data.get("edges", [])   # graph edges for decision routing
    target           = (data.get("target") or "").strip()
    watcher_name     = data.get("watcher_name", "watcher_brain")
    on_step_failure  = data.get("on_step_failure", "continue")
    incident_context = data.get("incident_context") or {}   # structured incident fields (service_url, etc.)
    dry_run          = bool(data.get("dry_run", False))     # skip action execution; diagnostics still run

    if not steps:
        raise HTTPException(status_code=422, detail="steps array is required")
    if not target:
        raise HTTPException(status_code=422, detail="target (container name) is required")

    # Resolve watcher URL + adapter
    watcher_base, adapter_mode = _resolve_watcher_info(watcher_name)
    logger.info(f"[EXECUTE-EDITOR] target={target} watcher={watcher_base} adapter={adapter_mode} graph_edges={len(edges)}")

    results:      list = []
    step_outputs: dict = {}   # keyed by BOTH int-idx and str step-id
    t0 = time.time()
    idx = 0  # monotonic execution counter

    # Maximum raw_output lines/chars returned to the UI — prevents huge SSH/log
    # dumps from flooding the results panel and the response payload.
    MAX_OUTPUT_LINES = 10
    MAX_OUTPUT_CHARS = 800

    # ── Helper: run one executable step ──────────────────────────────────────
    def _run_step(step: dict, step_type: str) -> bool:
        """Run a single step. Returns True if execution should continue."""
        nonlocal idx
        idx += 1
        step_t0  = time.time()
        tool_key = (step.get("tool") or "").strip()
        step_id  = step.get("id") or f"step_{idx}"
        step_name = step.get("name") or tool_key or step_id

        # Evaluate run_if (skip guard)
        run_if = (step.get("run_if") or "").strip()
        if run_if:
            should_run = ToolRegistryAgent._evaluate_condition(run_if, step_outputs)
            if not should_run:
                results.append({
                    "step": idx, "node_id": step_id, "name": step_name, "tool": tool_key,
                    "step_type": step_type, "skipped": True,
                    "skip_reason": f"condition false: {run_if}",
                    "elapsed_ms": int((time.time() - step_t0) * 1000),
                })
                return True  # skip → always continue

        # Resolve arg references to previous step outputs
        args = dict(step.get("args_json") or step.get("args") or {})
        args = ToolRegistryAgent._resolve_step_references(args, step_outputs, extra_context=incident_context)

        # Inject incident context fields (service_url, service_port, etc.) as fallback subs.
        # Step args always win — only fills gaps so {service_url} placeholders resolve.
        args = ToolRegistryAgent._inject_incident_context(args, incident_context)

        # Last-resort: a target-identity arg (target/container_name/pod/host/etc.) still
        # holding a bare unresolved placeholder (e.g. no diagnostic step ever captured an
        # output_capture variable named exactly "container_name") falls back to the run's
        # actual target instead of reaching the tool as literal placeholder text.
        args = ToolRegistryAgent._fill_unresolved_target_aliases(args, target)

        # Dry-run: skip execution for action steps; diagnostics/verifications run normally
        if dry_run and step_type == "action":
            dry_msg = f"DRY RUN — action not executed. Resolved args: {args}"
            results.append({
                "step": idx, "node_id": step_id, "name": step_name, "tool": tool_key,
                "step_type": step_type, "skipped": False, "success": True,
                "dry_run": True,
                "raw_output": dry_msg, "truncated": False,
                "structured": {"dry_run": True, "resolved_args": args},
                "message": dry_msg, "error": "", "command": "",
                "elapsed_ms": int((time.time() - step_t0) * 1000),
            })
            return True

        # Execute
        if step_type == "wait":
            duration = int(step.get("duration_seconds") or 0)
            if dry_run:
                duration = 0   # skip sleep in dry-run mode
            if duration > 0:
                time.sleep(duration)
            results.append({
                "step": idx, "node_id": step_id, "name": step_name, "tool": "wait",
                "step_type": "wait", "skipped": False, "success": True,
                "raw_output": f"Waited {duration}s",
                "truncated": False, "structured": {"duration_seconds": duration},
                "message": f"Paused for {duration} seconds",
                "error": "", "command": "",
                "elapsed_ms": int((time.time() - step_t0) * 1000),
            })
            return True

        if step_type == "verification":
            metric   = step.get("metric", "")
            check    = step.get("check", "")
            expected = str(step.get("value", ""))
            verify_raw = ""
            fresh_struct: dict = {}

            # Re-run the step's tool to get a fresh post-action measurement.
            # This is the core of real verification — we don't trust pre-action values.
            if tool_key:
                fresh_result = ToolRegistryAgent._execute_tool(
                    tool_key, args, target, watcher_base, adapter_mode
                )
                verify_raw   = fresh_result.get("raw_output") or fresh_result.get("output") or ""
                fresh_struct = fresh_result.get("structured") or {}
                # Apply output_capture mapping (same logic as for diagnostics)
                fresh_struct = ToolRegistryAgent._apply_output_capture(fresh_struct, step.get("output_capture") or {})
                # Store so downstream steps can reference post-verify values
                step_outputs[idx]     = fresh_struct
                step_outputs[step_id] = fresh_struct

            # Resolve actual metric value: fresh measurement first, historical fallback
            actual = fresh_struct.get(metric) if metric else None
            if actual is None and metric:
                for sout in step_outputs.values():
                    if isinstance(sout, dict) and metric in sout and sout[metric] is not None:
                        actual = sout[metric]
                        break

            if actual is not None and metric and check:
                passed = _verify_metric(actual, check, expected)
                step_result = {
                    "success":      passed,
                    "message":      f"{metric} = {actual} — {check} {expected} → {'PASSED' if passed else 'FAILED'}",
                    "raw_output":   verify_raw,
                    "actual_value": actual,
                    "verified":     passed,
                }
            else:
                # Fail closed: a metric that was never measured (no tool wired, or the
                # tool ran but never produced this field — e.g. a missing output_capture
                # alias) must not be silently treated as a pass. Mirrors the real incident
                # executor's verification fallback in incident_agents.py.
                step_result = {
                    "success":    False,
                    "message":    f"Verification: {metric or '(no metric)'} {check} {expected} — metric was never measured (no tool wired, or the tool ran but didn't produce '{metric}'). Cannot confirm.",
                    "raw_output": verify_raw,
                    "verified":   False,
                }
        else:
            if not tool_key:
                results.append({
                    "step": idx, "node_id": step_id, "name": step_name, "tool": "",
                    "step_type": step_type, "skipped": True,
                    "skip_reason": "no tool specified",
                    "elapsed_ms": int((time.time() - step_t0) * 1000),
                })
                return True

            retry_count = int(step.get("retry_count") or 0)
            retry_delay = float(step.get("retry_delay_seconds") or 5)
            attempt = 0
            while True:
                step_result = ToolRegistryAgent._execute_tool(
                    tool_key, args, target, watcher_base, adapter_mode
                )
                if step_result.get("success") or attempt >= retry_count:
                    break
                attempt += 1
                logger.info(
                    f"[EXECUTE-EDITOR] retrying step {step_id} (attempt {attempt}/{retry_count}) "
                    f"after {retry_delay}s"
                )
                if retry_delay > 0:
                    time.sleep(retry_delay)
            if attempt > 0:
                step_result["message"] = (
                    f"(succeeded on attempt {attempt + 1}) " + (step_result.get("message") or "")
                    if step_result.get("success")
                    else f"(failed after {attempt + 1} attempts) " + (step_result.get("message") or "")
                )

        # Capture structured output — store by BOTH int-idx and str step-id
        structured = step_result.get("structured") or {}
        # If the tool didn't produce structured data, parse raw output automatically.
        # This mirrors what the full incident agent does via _parse_tool_output.
        if not structured and step_result.get("success") and step_result.get("raw_output"):
            structured = ToolRegistryAgent._parse_tool_output(tool_key, step_result["raw_output"]) or {}
        if step_result.get("success") and structured:
            # Apply output_capture mapping: variable_name → JSONPath (e.g. "$.field")
            # This translates tool output field names to the variable names used in
            # decision conditions (e.g. "memory_pct" instead of "used_percent").
            structured = ToolRegistryAgent._apply_output_capture(structured, step.get("output_capture") or {})
            step_outputs[idx]     = structured   # int key  (legacy run_if: step_N.field)
            step_outputs[step_id] = structured   # str key  (editor format: diag_11.field)

        # Truncate raw_output by lines first, then chars, to keep the payload lean
        raw_out = step_result.get("raw_output") or step_result.get("output") or ""
        truncated = False
        lines = raw_out.splitlines()
        if len(lines) > MAX_OUTPUT_LINES:
            raw_out   = "\n".join(lines[:MAX_OUTPUT_LINES])
            truncated = True
        if len(raw_out) > MAX_OUTPUT_CHARS:
            raw_out   = raw_out[:MAX_OUTPUT_CHARS]
            truncated = True

        succeeded = step_result.get("success", False)
        results.append({
            "step":       idx,
            "node_id":    step_id,   # ← maps result back to graph node for UI animation
            "name":       step_name,
            "tool":       tool_key or f"verify:{step.get('metric', '')}",
            "step_type":  step_type,
            "skipped":    False,
            "success":    succeeded,
            "raw_output": raw_out,
            "truncated":  truncated,
            "structured": structured,
            "message":    step_result.get("message", ""),
            "error":      step_result.get("error", ""),
            "command":    step_result.get("command", ""),
            "elapsed_ms": int((time.time() - step_t0) * 1000),
        })

        # ── Failure policy ────────────────────────────────────────────────────
        # Priority order:
        #   1. Per-step "continue" → always keep going
        #   2. Per-step "abort/halt/stop" → always halt (even if global is continue)
        #   3. Global "continue" → keep going (overrides the action-step default)
        #   4. Global "stop_all" → halt on any failure
        #   5. Global "stop_actions" → halt only if this is an action step
        #   6. Fallback: action/notify steps abort by default; diagnostic/verification continue
        if not succeeded:
            step_policy = (step.get("on_failure") or "").lower()
            if step_policy == "continue":
                return True
            if step_policy in ("abort", "halt", "stop"):
                return False
            # No per-step policy — defer to global setting
            if on_step_failure == "continue":
                return True
            if on_step_failure == "stop_all":
                return False
            if on_step_failure == "stop_actions" and step_type == "action":
                return False
            # Final fallback: action/notify abort by default, diagnostics/verification continue
            if step_type == "action":
                return False
        return True

    # ── Route: graph-aware walk (when edges are present) ─────────────────────
    if edges:
        _TYPE_MAP_EDITOR = {
            "diagnostic":   "diagnostic",
            "action":       "action",
            "notify":       "action",
            "notification": "action",
            "verification": "verification",
            "wait":         "wait",
        }
        for _node, _node_type in ToolRegistryAgent._walk_graph(steps, edges, step_outputs):
            if _node_type == "decision":
                condition = _node.get("_condition", "")
                result    = _node.get("_decision_result")
                branch    = _node.get("_decision_branch", "")
                next_node = _node.get("_next_node", "end")
                logger.info(
                    f"[EXECUTE-EDITOR] decision node={_node.get('id')} condition='{condition}' "
                    f"result={result} → branch={branch} → next={next_node}"
                )
                idx += 1
                results.append({
                    "step":       idx,
                    "node_id":    _node.get("id", ""),
                    "name":       _node.get("name") or f"Decision: {condition}",
                    "tool":       "decision",
                    "step_type":  "decision",
                    "skipped":    False,
                    "success":    True,
                    "raw_output": f"{condition} → {result}",
                    "structured": {"condition": condition, "result": result, "branch": branch, "next": next_node},
                    "message":    f"'{condition}' is {result} → taking {branch} branch → {next_node}",
                    "error":      "",
                    "command":    "",
                    "elapsed_ms": 0,
                })
                continue
            if _node_type == "incident_update":
                _iu_state = _node.get("state", "resolved")
                idx += 1
                results.append({
                    "step":       idx,
                    "node_id":    _node.get("id", ""),
                    "name":       _node.get("name") or "Incident Update",
                    "tool":       "incident_update",
                    "step_type":  "incident_update",
                    "skipped":    False,
                    "success":    True,
                    "raw_output": f"state → {_iu_state}",
                    "structured": {"state": _iu_state},
                    "message":    f"Incident state set to {_iu_state!r} (only reached because every prior step, including verification, succeeded)",
                    "error":      "",
                    "command":    "",
                    "elapsed_ms": 0,
                })
                continue
            exec_type = _TYPE_MAP_EDITOR.get(_node.get("type", "").lower(), "action")
            # Offload to a thread — _run_step makes blocking httpx/subprocess calls
            # (via ToolRegistryAgent._execute_tool's sync httpx.post to the watcher).
            # Calling it directly here would freeze this worker's entire asyncio event
            # loop for the duration of each step, including any *new* inbound request
            # routed to this same worker — e.g. a verification step's own curl call
            # looping back to check this backend's health would sit unaccepted until
            # the freeze ends, surfacing as an inexplicable timeout/000 with no
            # apparent cause. asyncio.to_thread keeps the loop free to accept and
            # serve other requests while this step runs.
            should_continue = await asyncio.to_thread(_run_step, _node, exec_type)
            if not should_continue:
                logger.info(f"[EXECUTE-EDITOR] halting after step {_node.get('id')} (on_step_failure={on_step_failure})")
                break

    # ── Route: legacy flat execution (no edges sent) ──────────────────────────
    else:
        translated   = ToolRegistryAgent._translate_editor_steps(steps)
        ordered_flat = (
            [(s, "diagnostic")   for s in translated["diagnostics"]]
            + [(s, "action")     for s in translated["actions"]]
            + [(s, "verification") for s in translated["verification"]]
        )
        for step, step_type in ordered_flat:
            # See graph-walk branch above for why this needs to run off-loop.
            should_continue = await asyncio.to_thread(_run_step, step, step_type)
            if not should_continue:
                break

    elapsed_ms    = int((time.time() - t0) * 1000)
    success_count = sum(1 for r in results if r.get("success") and not r.get("skipped"))
    skip_count    = sum(1 for r in results if r.get("skipped"))
    fail_count    = sum(1 for r in results if not r.get("success") and not r.get("skipped"))

    return {
        "success":      fail_count == 0,
        "target":       target,
        "watcher":      watcher_base,
        "adapter":      adapter_mode,
        "total_steps":  idx,
        "succeeded":    success_count,
        "skipped":      skip_count,
        "failed":       fail_count,
        "elapsed_ms":   elapsed_ms,
        "results":      results,
        "step_outputs": {k: v for k, v in step_outputs.items() if isinstance(k, str)},
    }
