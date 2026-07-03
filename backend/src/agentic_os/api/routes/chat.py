"""
Operator Chat — Phase 3

POST /api/chat/stream   SSE streaming (primary)
POST /api/chat          JSON fallback

Phase 3 additions:
  A. Page-context awareness  — optional context_workflow_id in request body;
     the incident currently open in the UI is pre-fetched and injected first.

  B. Actions from chat — message is scanned for approve/reject intent + an INC
     number.  If found: the action spec is emitted as a final SSE metadata event
     {"action": {...}} so the frontend can show Confirm/Cancel buttons.

  C. Runbook RAG — messages containing runbook/procedure keywords trigger a
     keyword search against the runbooks table.  Matching steps are injected
     as a MATCHING RUNBOOKS section in the snapshot.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from agentic_os.db.database import get_session
from agentic_os.api.rate_limit import RateLimit

# 60 chat requests per minute per IP (Fix 7)
_chat_rate_limit = RateLimit(times=60, seconds=60)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_HISTORY = 10
TERMINAL    = "('resolved','closed','deployed','rolled_back')"

# ── Pydantic models ──────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str        # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    context_workflow_id: Optional[str] = None   # Phase 3A: incident open in UI


class ChatResponse(BaseModel):
    reply: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt(seconds) -> str:
    if seconds is None: return "—"
    s = float(seconds)
    if s < 60:    return f"{s:.0f}s"
    if s < 3600:  return f"{s / 60:.0f}m"
    if s < 86400: return f"{s / 3600:.1f}h"
    return f"{s / 86400:.0f}d"


def _extract_incident_numbers(text: str) -> list[str]:
    """Extract INC and STRM identifiers from free text (case-insensitive)."""
    upper = text.upper()
    inc_matches  = re.findall(r'\bINC[-\s]?\d+\b',  upper)
    strm_matches = re.findall(r'\bSTRM[-\s]?\d+\b', upper)
    return list(dict.fromkeys(inc_matches + strm_matches))


# ── Phase 3A: Page-context ────────────────────────────────────────────────────

def _fetch_workflow_incident_number(db: Session, workflow_id: str) -> str | None:
    """Resolve a workflow UUID to its INC number string."""
    try:
        row = db.execute(text("""
            SELECT incident_number_str FROM workflow_states
            WHERE workflow_id = :wid
            AND   CAST(workflow_type AS TEXT) = 'incident'
            LIMIT 1
        """), {"wid": workflow_id}).fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("context_workflow lookup failed: %s", exc)
        return None


# ── Incident drill-down ──────────────────────────────────────────────────────

def _fetch_incident_detail(db: Session, inc_number: str) -> str:
    """Return a detailed text block for a specific INC-XXXX number."""
    try:
        row = db.execute(text("""
            SELECT
                ws.incident_number_str,
                ws.title,
                COALESCE(CAST(ws.severity AS TEXT), 'unknown') AS severity,
                CAST(ws.lifecycle_state AS TEXT)               AS state,
                ws.risk_score,
                ws.summary,
                ws.technical_summary,
                ws.resolution_source,
                ws.resolution_notes,
                ws.remediation_outcome,
                ws.created_at,
                ws.updated_at,
                ws.resolved_at,
                ws.state_history,
                ws.execution_log
            FROM workflow_states ws
            WHERE ws.incident_number_str = :num
            AND   CAST(ws.workflow_type AS TEXT) = 'incident'
            LIMIT 1
        """), {"num": inc_number}).fetchone()

        if not row:
            return f"DETAIL for {inc_number}: not found.\n"

        lines = [f"\nDETAIL — {inc_number}:"]
        lines.append(f"  Title:      {row[1] or 'Untitled'}")
        lines.append(f"  Severity:   {(row[2] or '?').upper()}")
        lines.append(f"  State:      {row[3] or '?'}")
        lines.append(f"  Risk score: {f'{row[4]:.0f}/100' if row[4] else '—'}")
        lines.append(f"  Resolution: {row[7] or '—'}")
        lines.append(f"  Outcome:    {row[9] or '—'}")

        created  = row[10]
        resolved = row[12] or row[11]
        if created:
            age_s = (resolved - created).total_seconds() if resolved \
                    else (datetime.utcnow() - created).total_seconds()
            lines.append(f"  Age/MTTR:   {_fmt(age_s)}")

        if row[5]:
            lines.append(f"  Summary:    {row[5][:300]}")
        if row[6]:
            # technical_summary contains event analysis, remediation reasoning, and
            # resolution evidence — this is the primary source for "what were the steps?"
            lines.append(f"  Technical analysis:\n    {row[6][:800]}")
        if row[8]:
            lines.append(f"  Notes:      {row[8][:200]}")

        history = row[13] or []
        if history:
            lines.append("  State history:")
            for h in history[-5:]:
                if not isinstance(h, dict): continue
                ts  = h.get("timestamp", "")[:19]
                st  = h.get("state", "?")
                rsn = h.get("reason", "")
                lines.append(f"    {ts}  →  {st}{'  ('+rsn+')' if rsn else ''}")

        exec_log = row[14] or []
        if exec_log:
            lines.append("  Last actions:")
            for entry in exec_log[-3:]:
                if not isinstance(entry, dict): continue
                tool   = entry.get("tool", entry.get("action", "?"))
                status = entry.get("status", "?")
                out    = str(entry.get("output", ""))[:120]
                lines.append(f"    [{status.upper()}] {tool}  {out}")

        # ── CI / CMDB context ────────────────────────────────────────────────
        try:
            ctx_row = db.execute(text("""
                SELECT context FROM workflow_states
                WHERE incident_number_str = :num
                AND   CAST(workflow_type AS TEXT) = 'incident'
                LIMIT 1
            """), {"num": inc_number}).fetchone()
            if ctx_row and ctx_row[0]:
                import json as _json
                ctx   = ctx_row[0] if isinstance(ctx_row[0], dict) else _json.loads(ctx_row[0])
                alert = ctx.get("alert_payload", {})
                cmdb  = ctx.get("cmdb_context") or ctx.get("cmdb") or {}

                ci_lines: list[str] = []
                resource = cmdb.get("resource_name") or alert.get("resource_name", "")
                if resource:
                    ci_lines.append(f"  CI name:         {resource}")
                env = cmdb.get("environment", "")
                if env and env not in ("unknown", ""):
                    ci_lines.append(f"  Environment:     {env}")
                criticality = cmdb.get("business_criticality")
                if criticality:
                    ci_lines.append(f"  Criticality:     {criticality}")
                tier = cmdb.get("ci_tier")
                if tier:
                    ci_lines.append(f"  CI tier:         {tier}")
                is_spof = cmdb.get("is_spof")
                if is_spof is not None:
                    ci_lines.append(f"  Single point of failure: {'Yes' if is_spof else 'No'}")
                failover = cmdb.get("failover_available")
                if failover is not None:
                    ci_lines.append(f"  Failover:        {'Available' if failover else 'None configured'}")
                sla = cmdb.get("sla_percent")
                if sla:
                    ci_lines.append(f"  SLA target:      {sla}%")
                deps = cmdb.get("dependencies") or []
                if deps:
                    ci_lines.append(f"  Dependencies:    {', '.join(str(d) for d in deps[:5])}")
                impacted = cmdb.get("impacted_services") or []
                if impacted:
                    ci_lines.append(f"  Impacted svcs:   {', '.join(str(s) for s in impacted[:5])}")

                # Alert / event specifics
                a_type  = alert.get("type", "")
                a_desc  = alert.get("description", "")
                a_proc  = alert.get("anomaly_process") or alert.get("culprit_process", "")
                if a_type:
                    ci_lines.append(f"  Alert type:      {a_type}")
                if a_proc:
                    ci_lines.append(f"  Culprit process: {a_proc}")
                if a_desc:
                    # Pull a numeric metric if present (e.g. "CPU usage 99.2%")
                    m = re.search(r'(\d+\.?\d*)\s*%', a_desc)
                    if m:
                        ci_lines.append(f"  Metric value:    {m.group(0)}")
                    ci_lines.append(f"  Alert detail:    {a_desc[:200]}")

                # ServiceNow CMDB lookup — exact or fuzzy name match
                if resource:
                    sn = db.execute(text("""
                        SELECT name, ci_class, fields_json
                        FROM snow_ci_cache
                        WHERE name ILIKE :n
                        LIMIT 1
                    """), {"n": f"%{resource}%"}).fetchone()
                    if sn:
                        fj = sn[2] if isinstance(sn[2], dict) else _json.loads(sn[2])
                        ci_lines.append(f"  CMDB (ServiceNow): {sn[0]}  [{sn[1]}]")
                        for fld, lbl in [
                            ("operational_status", "Op status"),
                            ("managed_by",         "Managed by"),
                            ("owned_by",           "Owned by"),
                            ("environment",        "Environment"),
                            ("support_group",      "Support group"),
                            ("used_for",           "Used for"),
                        ]:
                            val = fj.get(fld, "")
                            if val:
                                ci_lines.append(f"    {lbl}: {val}")

                if ci_lines:
                    lines.append("  CI Context:")
                    lines.extend(ci_lines)

                # ── Recent health / monitoring events for this CI ──────────
                if resource:
                    mon = db.execute(text("""
                        SELECT event_type, raw_criticality, signal_value, signal_threshold,
                               anomaly_process, qualified_as_incident, status, detected_at
                        FROM monitoring_events
                        WHERE resource_name = :r
                        ORDER BY detected_at DESC
                        LIMIT 6
                    """), {"r": resource}).fetchall()
                    if mon:
                        lines.append("  Recent monitoring events:")
                        for mr in mon:
                            e_type, crit, sig_val, sig_thresh, proc, is_inc, mstatus, det_at = mr
                            ts_str = str(det_at)[:16] if det_at else "?"
                            val_str = ""
                            if sig_val is not None and sig_thresh is not None:
                                val_str = f"  ({sig_val:,.0f} / threshold {sig_thresh:,.0f})"
                            proc_str = f"  [{proc}]" if proc else ""
                            inc_str  = " → raised as incident" if is_inc else ""
                            lines.append(
                                f"    {ts_str}  {e_type}{val_str}{proc_str}  [{mstatus}{inc_str}]"
                            )

        except Exception:
            pass

        try:
            appr = db.execute(text("""
                SELECT status,
                       decided_by,
                       decided_at,
                       decision_notes,
                       proposed_action,
                       ROUND(EXTRACT(EPOCH FROM (NOW() - requested_at)) / 60) AS wait_min
                FROM approvals
                WHERE workflow_id = (
                    SELECT workflow_id FROM workflow_states
                    WHERE incident_number_str = :num LIMIT 1
                )
                ORDER BY requested_at DESC LIMIT 1
            """), {"num": inc_number}).fetchone()
            if appr:
                status     = appr[0] or "unknown"
                decided_by = appr[1]
                decided_at = appr[2]
                notes      = (appr[3] or "").strip()
                pa         = appr[4]
                wait_min   = appr[5] or 0

                if status == "pending":
                    lines.append(f"  Approval:   pending  (waiting {_fmt(wait_min * 60)} for a decision)")
                else:
                    who  = decided_by or "unknown"
                    when = decided_at.strftime("%Y-%m-%d %H:%M UTC") if decided_at else "unknown time"
                    line = f"  Approval:   {status}  |  decided by {who}  at {when}"
                    if notes:
                        line += f"  |  notes: {notes[:150]}"
                    lines.append(line)

                # Proposed action — the remediation the agent intends to run.
                # Present for pending AND decided approvals; this is the answer
                # to "what are the proposed remediation steps?"
                if pa:
                    try:
                        if isinstance(pa, str):
                            import json as _json
                            pa = _json.loads(pa)
                    except Exception:
                        pa = None
                if pa and isinstance(pa, dict):
                    lines.append("  Proposed remediation action:")
                    tool   = pa.get("tool") or pa.get("action", "")
                    target = pa.get("target", "")
                    br     = pa.get("blast_radius", "")
                    mttr   = pa.get("estimated_mttr", "")
                    if tool:
                        tline = f"    Tool: {tool}"
                        if target: tline += f"  |  Target: {target}"
                        lines.append(tline)
                    if br:
                        mline = f"    Blast radius: {br}"
                        if mttr: mline += f"  |  Est. recovery: {mttr}"
                        lines.append(mline)
                    # Steps / args
                    args = pa.get("args") or pa.get("steps") or pa.get("parameters")
                    if isinstance(args, dict):
                        for k, v in list(args.items())[:6]:
                            lines.append(f"    {k}: {v}")
                    elif isinstance(args, list):
                        for i, s in enumerate(args[:6], 1):
                            step = s.get("name", str(s)) if isinstance(s, dict) else str(s)
                            lines.append(f"    {i}. {step}")
        except Exception:
            pass

        lines.append("")
        return "\n".join(lines)

    except Exception as exc:
        logger.warning("drill-down for %s failed: %s", inc_number, exc)
        return f"DETAIL for {inc_number}: (query failed)\n"


# ── Phase 3B: Action intent detection ────────────────────────────────────────

_APPROVE_WORDS = {'approve', 'approved', 'authorization', 'authorize', 'green light',
                  'go ahead', 'proceed', 'looks good', 'lgtm', 'confirm', 'confirmed'}
_REJECT_WORDS  = {'reject', 'rejected', 'decline', 'deny', 'denied',
                  'false positive', 'not real', "don't approve", 'not approved'}


def _detect_action_intent(
    message: str,
    db: Session,
    context_workflow_id: str | None = None,
) -> dict | None:
    """
    Detect approve/reject intent + resolve an INC number to its workflow_id.

    INC resolution order:
      1. Explicit INC number in the message  (e.g. "approve INC0031")
      2. context_workflow_id from the UI  (operator has that incident open)
      3. The single incident currently in waiting_approval state  (unambiguous)

    If none of the above yields a resolvable incident, returns None so the LLM
    asks the operator to specify — rather than hallucinating a success.
    """
    msg_lower = message.lower()

    action_type = None
    if any(w in msg_lower for w in _APPROVE_WORDS):
        action_type = 'approve'
    elif any(w in msg_lower for w in _REJECT_WORDS):
        action_type = 'reject'

    if not action_type:
        return None

    # ── 1. Explicit INC number in the message ────────────────────────────────
    inc_numbers = _extract_incident_numbers(message)
    inc_num: str | None = inc_numbers[0] if inc_numbers else None

    # ── 2. Context workflow from the UI ──────────────────────────────────────
    if not inc_num and context_workflow_id:
        inc_num = _fetch_workflow_incident_number(db, context_workflow_id)

    # ── 3. Single unambiguous waiting_approval incident ───────────────────────
    if not inc_num:
        try:
            rows = db.execute(text("""
                SELECT incident_number_str FROM workflow_states
                WHERE CAST(workflow_type   AS TEXT) = 'incident'
                AND   CAST(lifecycle_state AS TEXT) = 'waiting_approval'
                LIMIT 2
            """)).fetchall()
            # Only infer when there is exactly one candidate — avoids wrong guesses
            if len(rows) == 1:
                inc_num = rows[0][0]
        except Exception as exc:
            logger.warning("waiting_approval fallback lookup failed: %s", exc)

    if not inc_num:
        return None  # Cannot determine which incident — LLM will ask for clarification

    try:
        row = db.execute(text("""
            SELECT workflow_id::text FROM workflow_states
            WHERE incident_number_str = :num
            AND   CAST(workflow_type AS TEXT) = 'incident'
            LIMIT 1
        """), {"num": inc_num}).fetchone()

        if not row:
            return None

        notes = ""
        if action_type == 'reject':
            reason_match = re.search(
                rf'{re.escape(inc_num)}\s*[-–—,]?\s*(.+)',
                message, re.IGNORECASE
            )
            if reason_match:
                notes = reason_match.group(1).strip()[:200]

        return {
            "type":             action_type,
            "incident_number":  inc_num,
            "workflow_id":      row[0],
            "notes":            notes,
        }

    except Exception as exc:
        logger.warning("action intent detection failed: %s", exc)
        return None


# ── Phase 3C: Runbook RAG ─────────────────────────────────────────────────────

_RUNBOOK_TRIGGERS = {
    'runbook', 'playbook', 'procedure', 'steps', 'how to', 'how do',
    'remediate', 'remediation', 'recover', 'recovery', 'restart', 'diagnose',
    'troubleshoot', 'what does our', 'walk me through',
    'recommendation', 'recommend', 'suggest', 'which runbook', 'where did',
    'based on', 'come from', 'from a runbook', 'from the runbook',
}

_STOP_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'for', 'on', 'in', 'our', 'my', 'what',
    'does', 'say', 'about', 'how', 'do', 'to', 'with', 'and', 'or', 'at',
    'from', 'by', 'this', 'that', 'it', 'be', 'can', 'we', 'us', 'taken',
    'was', 'which', 'one', 'did', 'come', 'where', 'based',
}


def _fetch_incident_event_type(db: Session, inc_number: str) -> tuple[str, str]:
    """Return (event_type, title) for an incident, used to seed runbook search."""
    try:
        row = db.execute(text("""
            SELECT
                COALESCE(technical_summary, summary, title, '') AS hint,
                title
            FROM workflow_states
            WHERE incident_number_str = :num
            AND   CAST(workflow_type AS TEXT) = 'incident'
            LIMIT 1
        """), {"num": inc_number}).fetchone()
        return (row[0] or "", row[1] or "") if row else ("", "")
    except Exception:
        return ("", "")


def _fetch_incident_platform(db: Session, inc_number: str) -> str | None:
    """Extract platform from incident's CMDBContext (from context JSONB field)."""
    try:
        row = db.execute(text("""
            SELECT context
            FROM workflow_states
            WHERE incident_number_str = :num
            AND   CAST(workflow_type AS TEXT) = 'incident'
            LIMIT 1
        """), {"num": inc_number}).fetchone()

        if not row or not row[0]:
            # logger.debug(f"[CHAT] No context found for {inc_number}")
            return None

        context = row[0]
        # logger.debug(f"[CHAT] Context keys for {inc_number}: {list(context.keys()) if isinstance(context, dict) else 'not a dict'}")

        if isinstance(context, dict) and 'cmdb' in context:
            cmdb = context.get('cmdb', {})
            # logger.debug(f"[CHAT] CMDB context keys for {inc_number}: {list(cmdb.keys()) if isinstance(cmdb, dict) else 'not a dict'}")

            platform = cmdb.get('platform')
            # logger.info(f"[CHAT] Platform value for {inc_number}: {platform}")

            if platform and platform != 'any':
                # logger.info(f"[CHAT] Using platform '{platform}' for {inc_number}")
                return platform
            # else:
            #     logger.debug(f"[CHAT] Platform is None or 'any' for {inc_number}")
        # else:
        #     logger.debug(f"[CHAT] No 'cmdb' key in context for {inc_number}")

        return None
    except Exception as exc:
        # logger.debug(f"[CHAT] Could not extract platform from {inc_number}: {exc}")
        return None


def _fetch_runbook_rag(
    db: Session,
    message: str,
    history: list | None = None,
    mentioned_incidents: list[str] | None = None,
    platform: str | None = None,
) -> str:
    """
    Search the runbooks table and return matching steps for context injection.

    Triggers on:
      • runbook/procedure/remediation keywords in the current message
      • follow-up questions about where a recommendation came from

    When an incident number is known (from message or recent history), the
    incident's title and technical summary are added as search seeds so that
    "was this from a runbook?" still finds the right runbook even when the
    message itself has no technical terms.

    Platform-aware filtering:
      • If platform is provided (e.g., 'docker', 'kubernetes'), prioritize
        platform-specific runbooks and include generic ('any') runbooks.
      • Platform comes from the incident's CMDBContext when available.
    """
    msg_lower = message.lower()
    triggered = any(kw in msg_lower for kw in _RUNBOOK_TRIGGERS)

    # Also trigger when history mentions runbook-related topics
    if not triggered and history:
        for h in reversed((history or [])[-6:]):
            if any(kw in (getattr(h, 'content', '') or '').lower() for kw in _RUNBOOK_TRIGGERS):
                triggered = True
                break

    if not triggered:
        return ""

    # ── Build search terms ────────────────────────────────────────────────────
    # Start from message words, then enrich with incident context
    terms = [
        w for w in re.findall(r'\b\w{3,}\b', msg_lower)
        if w not in _STOP_WORDS and w not in _RUNBOOK_TRIGGERS
    ]

    # Enrich terms using incident title / technical summary when an INC is known
    if mentioned_incidents:
        for inc in mentioned_incidents[:2]:
            hint, title = _fetch_incident_event_type(db, inc)
            # Extract meaningful words from incident hint (first 200 chars)
            inc_words = [
                w for w in re.findall(r'\b\w{4,}\b', (hint[:200] + " " + title).lower())
                if w not in _STOP_WORDS and len(w) > 3
            ]
            terms = list(dict.fromkeys(terms + inc_words[:8]))  # deduplicate, keep order

    try:
        # Build platform filter if provided
        platform_filter = ""
        if platform and platform != "any":
            # Match platform-specific runbooks + generic ('any') runbooks
            # Prioritize platform-specific by sorting platform ASC (specific platforms first)
            platform_filter = f" AND (platform = :platform OR platform = 'any')"
            platform_order = ", CASE WHEN platform = :platform THEN 0 ELSE 1 END"
            # logger.info(f"[CHAT] Runbook RAG: filtering by platform='{platform}'")
        else:
            # logger.debug(f"[CHAT] Runbook RAG: no platform filter (platform={platform})")
            platform_order = ""

        if terms:
            conditions = " OR ".join(
                f"(LOWER(name) LIKE :t{i} OR LOWER(description) LIKE :t{i} "
                f"OR LOWER(event_type) LIKE :t{i})"
                for i in range(min(len(terms), 8))
            )
            params = {f"t{i}": f"%{t}%" for i, t in enumerate(terms[:8])}
            if platform and platform != "any":
                params["platform"] = platform

            rows = db.execute(text(f"""
                SELECT name, event_type, description, diagnostics, actions,
                       success_rate, blast_radius
                FROM runbooks
                WHERE enabled = true AND ({conditions}){platform_filter}
                ORDER BY COALESCE(success_rate, 0) DESC{platform_order}
                LIMIT 3
            """), params).fetchall()
        else:
            params = {}
            if platform and platform != "any":
                params["platform"] = platform

            rows = db.execute(text(f"""
                SELECT name, event_type, description, diagnostics, actions,
                       success_rate, blast_radius
                FROM runbooks
                WHERE enabled = true{platform_filter}
                ORDER BY COALESCE(success_rate, 0) DESC{platform_order}
                LIMIT 5
            """), params).fetchall()

        if not rows:
            return ""

        lines = ["MATCHING RUNBOOKS:"]
        for r in rows:
            name, event_type, desc = r[0], r[1], r[2] or ""
            diag, actions = r[3] or [], r[4] or []
            sr = f"{r[5]:.0%}" if r[5] else "?"
            br = r[6] or "?"

            lines.append(f"\n  {name}  (event_type: {event_type})")
            if desc:
                lines.append(f"    Description: {desc[:200]}")
            lines.append(f"    Success rate: {sr}  |  Blast radius: {br}")

            if diag:
                lines.append("    Diagnostics:")
                for i, s in enumerate(diag[:4], 1):
                    step = (s.get('name', 'Step') if isinstance(s, dict) else str(s))
                    lines.append(f"      {i}. {step}")

            if actions:
                lines.append("    Actions:")
                for i, s in enumerate(actions[:6], 1):
                    if not isinstance(s, dict):
                        lines.append(f"      {i}. {s}")
                        continue
                    step  = s.get('name', 'Step')
                    tool  = s.get('tool', '')
                    sdesc = s.get('description', '')[:100]
                    line  = f"      {i}. {step}"
                    if tool:  line += f" [{tool}]"
                    if sdesc: line += f" — {sdesc}"
                    lines.append(line)

        return "\n".join(lines)

    except Exception as exc:
        logger.warning("runbook RAG failed: %s", exc)
        return ""


# ── Platform snapshot ─────────────────────────────────────────────────────────

def _build_context(
    db: Session,
    message: str,
    context_workflow_id: str | None = None,
    history: list | None = None,
) -> str:
    ts    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"=== LIVE PLATFORM SNAPSHOT  ({ts}) ===", ""]

    # ── Phase 3A: current UI context ─────────────────────────────────────────
    if context_workflow_id:
        ctx_inc = _fetch_workflow_incident_number(db, context_workflow_id)
        if ctx_inc:
            lines.append("=== INCIDENT OPEN IN UI (current context) ===")
            lines.append(_fetch_incident_detail(db, ctx_inc))

    # ── 1. Active incidents ───────────────────────────────────────────────────
    try:
        total = db.execute(text(f"""
            SELECT COUNT(*) FROM workflow_states
            WHERE CAST(workflow_type  AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) NOT IN {TERMINAL}
        """)).scalar() or 0

        rows = db.execute(text(f"""
            SELECT
                ws.incident_number_str,
                ws.title,
                COALESCE(CAST(ws.severity AS TEXT), 'unknown') AS severity,
                CAST(ws.lifecycle_state AS TEXT)               AS state,
                ROUND(EXTRACT(EPOCH FROM (NOW() - ws.created_at)) / 60) AS age_min
            FROM workflow_states ws
            WHERE CAST(ws.workflow_type  AS TEXT) = 'incident'
            AND   CAST(ws.lifecycle_state AS TEXT) NOT IN {TERMINAL}
            ORDER BY
                CASE CAST(ws.severity AS TEXT)
                    WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                    WHEN 'medium'   THEN 3 ELSE 4
                END,
                ws.created_at ASC
            LIMIT 20
        """)).fetchall()

        lines.append(f"ACTIVE INCIDENTS  ({total} open):")
        for r in rows:
            num   = r[0] or "—"
            title = (r[1] or "Untitled")[:80]
            sev   = (r[2] or "?").upper()
            state = r[3] or "?"
            age   = _fmt((r[4] or 0) * 60)
            lines.append(f"  [{sev}] {num}  |  {state}  |  age {age}  |  {title}")
        if not rows:
            lines.append("  (none)")
        lines.append("")
    except Exception as exc:
        logger.warning("snapshot: active incidents failed: %s", exc)
        lines.append("ACTIVE INCIDENTS: (unavailable)\n")

    # ── 2. Pending approvals ──────────────────────────────────────────────────
    try:
        stuck = db.execute(text("""
            SELECT
                ws.incident_number_str,
                ws.title,
                COALESCE(CAST(ws.severity AS TEXT), 'unknown') AS severity,
                ROUND(EXTRACT(EPOCH FROM (NOW() - a.requested_at)) / 60) AS wait_min
            FROM workflow_states ws
            JOIN approvals a
              ON a.workflow_id = ws.workflow_id AND a.status = 'pending'
            WHERE CAST(ws.workflow_type AS TEXT) = 'incident'
            ORDER BY a.requested_at ASC
            LIMIT 10
        """)).fetchall()

        lines.append(f"PENDING APPROVALS  ({len(stuck)}):")
        for r in stuck:
            num   = r[0] or "—"
            title = (r[1] or "Untitled")[:80]
            sev   = (r[2] or "?").upper()
            wait  = _fmt((r[3] or 0) * 60)
            lines.append(f"  [{sev}] {num}  |  waiting {wait}  |  {title}")
        if not stuck:
            lines.append("  (none)")
        lines.append("")
    except Exception as exc:
        logger.warning("snapshot: pending approvals failed: %s", exc)
        lines.append("PENDING APPROVALS: (unavailable)\n")

    # ── 3. 7-day MTTR summary ─────────────────────────────────────────────────
    try:
        cutoff = datetime.utcnow() - timedelta(days=7)
        r = db.execute(text(f"""
            SELECT
                COUNT(*),
                SUM(CASE WHEN resolution_source IN
                        ('automated_remediation','watcher_all_clear') THEN 1 ELSE 0 END),
                SUM(CASE WHEN resolution_source='manual'
                          OR resolution_source IS NULL THEN 1 ELSE 0 END),
                AVG(EXTRACT(EPOCH FROM (COALESCE(resolved_at,updated_at)-created_at))),
                AVG(CASE WHEN resolution_source IN
                        ('automated_remediation','watcher_all_clear')
                         THEN EXTRACT(EPOCH FROM (COALESCE(resolved_at,updated_at)-created_at))
                         ELSE NULL END),
                AVG(CASE WHEN resolution_source='manual' OR resolution_source IS NULL
                         THEN EXTRACT(EPOCH FROM (COALESCE(resolved_at,updated_at)-created_at))
                         ELSE NULL END)
            FROM workflow_states
            WHERE CAST(workflow_type  AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) IN {TERMINAL}
            AND   created_at >= :cutoff
        """), {"cutoff": cutoff}).fetchone()

        total_r  = int(r[0] or 0)
        auto_c   = int(r[1] or 0)
        human_c  = int(r[2] or 0)
        auto_pct = f"{auto_c * 100 // total_r}%" if total_r else "—"

        lines.append("7-DAY RESOLUTION SUMMARY:")
        lines.append(
            f"  {total_r} resolved  |  "
            f"{auto_c} auto ({auto_pct})  |  {human_c} manual"
        )
        lines.append(
            f"  Avg MTTR: overall {_fmt(r[3])}  |  "
            f"auto {_fmt(r[4])}  |  manual {_fmt(r[5])}"
        )
        lines.append("")
    except Exception as exc:
        logger.warning("snapshot: MTTR failed: %s", exc)
        lines.append("7-DAY RESOLUTION SUMMARY: (unavailable)\n")

    # ── 4. Severity breakdown ─────────────────────────────────────────────────
    try:
        sev_rows = db.execute(text(f"""
            SELECT COALESCE(CAST(severity AS TEXT),'unknown') AS sev, COUNT(*) AS cnt
            FROM workflow_states
            WHERE CAST(workflow_type  AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) NOT IN {TERMINAL}
            GROUP BY severity
            ORDER BY CASE COALESCE(CAST(severity AS TEXT),'unknown')
                WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                WHEN 'medium'   THEN 3 WHEN 'low'  THEN 4 ELSE 5 END
        """)).fetchall()
        if sev_rows:
            breakdown = "  |  ".join(f"{r[0].upper()}: {r[1]}" for r in sev_rows)
            lines.append(f"ACTIVE BY SEVERITY:  {breakdown}\n")
    except Exception as exc:
        logger.warning("snapshot: severity breakdown failed: %s", exc)

    # ── 5. Top 5 by risk score ────────────────────────────────────────────────
    try:
        risk_rows = db.execute(text(f"""
            SELECT incident_number_str, title, risk_score,
                   COALESCE(CAST(severity AS TEXT),'unknown') AS sev
            FROM workflow_states
            WHERE CAST(workflow_type  AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) NOT IN {TERMINAL}
            AND   risk_score IS NOT NULL
            ORDER BY risk_score DESC LIMIT 5
        """)).fetchall()
        if risk_rows:
            lines.append("TOP 5 BY RISK SCORE:")
            for r in risk_rows:
                lines.append(
                    f"  {r[0] or '—'}  |  risk {r[2]:.0f}/100  "
                    f"|  [{r[3].upper()}]  {(r[1] or '')[:70]}"
                )
            lines.append("")
    except Exception as exc:
        logger.warning("snapshot: risk top-5 failed: %s", exc)

    # ── 6. Resolved in last 7 days ───────────────────────────────────────────
    # Complete per-incident list so the LLM never has to invent resolution details.
    # 7-day window matches the MTTR summary window above.
    try:
        resolved_rows = db.execute(text(f"""
            SELECT
                incident_number_str,
                title,
                COALESCE(CAST(severity AS TEXT), 'unknown')  AS severity,
                resolution_source,
                remediation_outcome,
                COALESCE(resolved_at, updated_at)            AS closed_at,
                summary,
                resolution_notes
            FROM workflow_states
            WHERE CAST(workflow_type   AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) IN {TERMINAL}
            AND   (
                resolved_at  >= NOW() - INTERVAL '7 days'
                OR (resolved_at IS NULL AND updated_at >= NOW() - INTERVAL '7 days')
            )
            ORDER BY COALESCE(resolved_at, updated_at) DESC
            LIMIT 20
        """)).fetchall()

        lines.append(f"RESOLVED IN LAST 7 DAYS  ({len(resolved_rows)} shown, most recent first):")
        lines.append("  NOTE: This is the complete list for this window. Do not reference any resolved")
        lines.append("  incident not listed here — it is either older than 7 days or does not exist.")
        if resolved_rows:
            for r in resolved_rows:
                num       = r[0] or "—"
                title     = (r[1] or "Untitled")[:80]
                sev       = (r[2] or "?").upper()
                res_src   = r[3] or "manual"
                outcome   = r[4] or "—"
                closed_at = r[5].strftime("%Y-%m-%d %H:%M UTC") if r[5] else "?"
                summary   = (r[6] or "")[:200]
                notes     = (r[7] or "").strip()

                lines.append(
                    f"  {num}  |  [{sev}]  |  {res_src}  |  outcome: {outcome}  |  closed {closed_at}"
                )
                lines.append(f"    Title: {title}")
                if summary:
                    lines.append(f"    Summary: {summary}")
                if notes:
                    lines.append(f"    Notes: {notes}")
        else:
            lines.append("  (none in this period)")
        lines.append("")
    except Exception as exc:
        logger.warning("snapshot: recently resolved failed: %s", exc)
        lines.append("RESOLVED IN LAST 7 DAYS: (unavailable)\n")

    # ── 7. 30-day MTTR summary (aggregate only — no per-incident data) ──────────
    try:
        cutoff_30 = datetime.utcnow() - timedelta(days=30)
        r30 = db.execute(text(f"""
            SELECT
                COUNT(*),
                SUM(CASE WHEN resolution_source IN
                        ('automated_remediation','watcher_all_clear') THEN 1 ELSE 0 END),
                AVG(EXTRACT(EPOCH FROM (COALESCE(resolved_at,updated_at)-created_at)))
            FROM workflow_states
            WHERE CAST(workflow_type  AS TEXT) = 'incident'
            AND   CAST(lifecycle_state AS TEXT) IN {TERMINAL}
            AND   created_at >= :cutoff
        """), {"cutoff": cutoff_30}).fetchone()

        total_30 = int(r30[0] or 0)
        auto_30  = int(r30[1] or 0)
        pct_30   = f"{auto_30 * 100 // total_30}%" if total_30 else "—"
        lines.append("30-DAY RESOLUTION SUMMARY (aggregate only):")
        lines.append(
            f"  {total_30} resolved  |  {auto_30} auto ({pct_30})  |  avg MTTR {_fmt(r30[2])}"
        )
        lines.append("  NOTE: Per-incident details are only available for the last 7 days above.")
        lines.append("")
    except Exception as exc:
        logger.warning("snapshot: 30-day MTTR failed: %s", exc)

    # ── 8. Named incident drill-down ─────────────────────────────────────────
    # Scan both the current message AND recent history so follow-up questions
    # like "what were the steps?" still resolve the incident being discussed.
    mentioned: list[str] = _extract_incident_numbers(message)
    if not mentioned and history:
        # Walk back through the last 8 turns (4 exchanges) looking for an INC reference
        for h in reversed((history or [])[-8:]):
            found = _extract_incident_numbers(getattr(h, 'content', '') or '')
            if found:
                mentioned = found
                break
    if mentioned:
        lines.append("=== DETAILED VIEW ===")
        for num in mentioned[:3]:
            lines.append(_fetch_incident_detail(db, num))

    # ── 9. Phase 3C: Runbook RAG ──────────────────────────────────────────────
    # Extract platform from the incident context if available
    # Priority: UI context (context_workflow_id) > mentioned incidents > None
    incident_platform = None
    if context_workflow_id:
        # Prefer platform from incident currently open in UI
        ui_inc = _fetch_workflow_incident_number(db, context_workflow_id)
        if ui_inc:
            incident_platform = _fetch_incident_platform(db, ui_inc)

    if not incident_platform and mentioned:
        # Fallback to platform from first mentioned incident
        incident_platform = _fetch_incident_platform(db, mentioned[0])

    runbook_section = _fetch_runbook_rag(
        db, message, history=history, mentioned_incidents=mentioned, platform=incident_platform
    )
    if runbook_section:
        lines.append("")
        lines.append(runbook_section)

    # ── End-of-snapshot fence ─────────────────────────────────────────────────
    lines.append("")
    lines.append("=== END OF SNAPSHOT ===")
    lines.append("All data above is complete for the covered time windows.")
    lines.append("Any incident, metric, or fact not listed above is outside this snapshot.")
    lines.append("Do not reference, invent, or extrapolate anything beyond what is listed.")

    return "\n".join(lines)


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are an AI Operations Assistant embedded in the Agentic Platform, an autonomous IT \
incident management system. You help operators understand platform state, interpret incident \
data, track approvals, and make faster decisions.

═══════════════════════════════════════════════════════
ANTI-FABRICATION RULES  — hard constraints, never violate
═══════════════════════════════════════════════════════
1. THE SNAPSHOT IS YOUR ONLY SOURCE OF TRUTH.
   Every number, incident ID, title, state, MTTR value, resolution detail, risk score, and
   summary you mention MUST appear verbatim in the LIVE PLATFORM SNAPSHOT below.
   Never invent, estimate, approximate, or extrapolate any platform data.

2. INCIDENT NUMBERS ARE SACRED.
   Never mention an incident number (e.g. INC0042) that does not appear in the snapshot.
   If an operator asks about a specific number not in the snapshot, say:
   "INC-XXXX does not appear in the current snapshot. It may be outside the 7-day window,
   still being processed, or may not exist. Check the Incidents page for the full list."

3. USE WHAT YOU HAVE — ACKNOWLEDGE WHAT YOU DON'T.
   If the snapshot contains partial data relevant to the question, USE IT and clearly
   attribute it (e.g. "Based on the technical analysis in the snapshot: …").
   Only say "I don't have that data" when there is truly zero relevant information in
   the snapshot for the question asked.
   For questions about remediation steps, check the "Technical analysis" field — it
   contains event analysis, remediation reasoning, and resolution evidence.
   Never use "approximately", "typically", or "usually" as a substitute for real data.
   For data genuinely outside the snapshot (>7 days, on-call, per-team metrics, change
   requests), say: "That's outside the current snapshot. The snapshot covers [what is
   relevant]. Try asking about [a covered alternative]."

4. LIFECYCLE STATES ARE LITERAL.
   If the snapshot shows an incident as "waiting_approval", it IS still waiting.
   Never say it has been approved, rejected, or that remediation is underway unless the
   snapshot explicitly shows a different state.

5. THE RESOLVED LIST IS COMPLETE FOR ITS WINDOW.
   The "RESOLVED IN LAST 7 DAYS" section lists every resolved incident in that period.
   If a resolved incident is not in that list, it is outside the window or does not exist.

═══════════════════════════════════════════════════════
DATA BOUNDARIES — what this snapshot covers
═══════════════════════════════════════════════════════
  ✓  All currently active (open) incidents
  ✓  All currently pending approvals
  ✓  Incidents resolved in the last 7 days — complete list, up to 20, with titles & summaries
  ✓  7-day and 30-day aggregate MTTR stats (counts and averages only)
  ✓  Active incident severity breakdown and top-5 by risk score
  ✓  Full detail for any incident number explicitly mentioned in the operator's message
  ✓  CI Context for each detailed incident: resource name, environment, criticality, CI tier,
     SPOF status, failover, SLA, dependencies, impacted services, alert type, culprit process,
     metric values, and ServiceNow CMDB record if one exists (operational status, owner, support group)
  ✓  Recent monitoring events for the CI: event type, signal value vs threshold, culprit process,
     qualification status — use this to answer current health questions
  ✗  Incidents resolved more than 7 days ago — not available; say so
  ✗  Change requests / change workflows — not in this snapshot
  ✗  On-call schedules, team or user assignments — not available
  ✗  Per-service, per-team, or per-engineer metrics — not available
  ✗  Infrastructure, CI/CD, or deployment data beyond the CI Context section — not available

═══════════════════════════════════════════════════════
CLARIFICATION FIRST  — mandatory default behaviour
═══════════════════════════════════════════════════════
RULE: When you are unsure what the operator means, or the snapshot does not contain
enough information to answer accurately, you MUST ask one short, specific clarifying
question. Never guess, never fabricate, never give a hedged non-answer.

A wrong or fabricated answer is always worse than pausing to ask.

Ask ONE question — never a list of questions in the same reply. Keep it to one sentence.
Then wait for the operator's reply before attempting an answer.

═ ALWAYS ask when ═════════════════════════════════════

AMBIGUOUS TARGET
- "the incident" / "this incident" / "that one" — no INC number, no UI context open
  → "Which incident? Active ones are: [INC list from snapshot]."
- "approve it" / "reject that" — multiple incidents waiting_approval
  → "Which one? Currently waiting: [list]."
- "the last incident" / "the recent one" — multiple candidates
  → "Did you mean [INC-A] (resolved 2h ago) or [INC-B] (resolved 6h ago)?"

VAGUE SCOPE / TIME FRAME
- "recently" / "lately" / "this week" — when the right window isn't clear
  → "How far back? The snapshot covers 7 days. Do you mean today, or the full week?"
- "a lot" / "some" / "many" — when a number would matter to the answer
  → "Do you mean above a specific count or risk score threshold?"

INCOMPLETE REQUEST
- "tell me more" / "go deeper" / "expand on that" — after a multi-topic answer
  → "Which part would you like me to expand — [topic A] or [topic B]?"
- "what should I do?" — without specifying the incident or problem
  → "Which incident are you deciding on? I'll pull the details."

UNCLEAR INTENT
- A message that could mean two different things and the answer differs by intent
  → State the two interpretations and ask which one the operator meant.
- A request that mixes several possible actions (approve AND change AND escalate)
  → "I can only do one thing at a time — which would you like first?"

═ Do NOT ask when ═════════════════════════════════════

- The snapshot makes the target unambiguous: only one active incident, or only one
  incident in waiting_approval, or the UI context clearly identifies the subject.
- The operator explicitly names an INC number and the snapshot contains it.
- A follow-up message clearly continues a specific previous turn in the conversation.

In those cases, answer directly without a clarifying question.

═══════════════════════════════════════════════════════
RESPONSE GUIDELINES
═══════════════════════════════════════════════════════
- Operators are under time pressure. Be concise and direct.
- If the snapshot includes "INCIDENT OPEN IN UI", treat that as the primary context.
- If the snapshot includes a DETAILED VIEW for a named incident, use all fields shown.
- If the snapshot includes MATCHING RUNBOOKS, quote step names and tool names precisely.
- Quote incident numbers (e.g. INC0031) when referring to specific incidents.
- Plain text output. Short lists where helpful. No markdown headings or bold text.
- Aim for concise answers. For simple queries (counts, status) keep it under 100 words.
  For detailed questions (remediation steps, runbook walkthrough) give the complete answer —
  never truncate a numbered list. Only summarise when the operator clearly doesn't need full detail.

═══════════════════════════════════════════════════════
ACTION RULES — you cannot execute anything
═══════════════════════════════════════════════════════
- You CANNOT approve, reject, assign, escalate, or trigger any workflow.
  Only the operator can act by clicking the Confirm button in the UI.
- When an OPERATOR ACTION REQUEST is injected below: explain what will happen, state the
  incident number and effect, and tell the operator to click Confirm.
- NEVER say "I have approved", "approval processed", "you approved", "remediation queued",
  "remediation is underway", or any phrase implying an action was completed.
- If asked to approve/reject but no Confirm button appeared, say:
  "I couldn't identify which incident to target. Please specify the number
  (e.g. 'approve INC0031') so I can show you a confirmation button."

{snapshot}
"""


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt_inputs(body: ChatRequest, db: Session) -> tuple[str, str, dict | None]:
    """Return (system_prompt, user_content, action_spec | None)."""
    snapshot      = _build_context(db, body.message, body.context_workflow_id, body.history)
    action_spec   = _detect_action_intent(body.message, db, body.context_workflow_id)

    # Augment system prompt when an action is requested
    extra = ""
    if action_spec:
        verb = "approving" if action_spec["type"] == "approve" else "rejecting"
        effect = ("remediation will begin immediately"
                  if action_spec["type"] == "approve"
                  else "the incident will be marked rejected and no automated remediation will run")
        extra = (
            f"\n\nOPERATOR ACTION REQUEST:\n"
            f"  Action:   {action_spec['type'].upper()}\n"
            f"  Incident: {action_spec['incident_number']}\n\n"
            f"Confirm the action clearly. State that {verb} {action_spec['incident_number']} "
            f"means {effect}. Then ask the operator to confirm. "
            f"Do NOT perform any action yourself — only prepare the operator."
        )

    system_prompt = _SYSTEM.format(snapshot=snapshot) + extra

    history      = body.history[-MAX_HISTORY:]
    turns        = "\n".join(
        f"{'Operator' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in history
    )
    user_content = f"{turns}\nOperator: {body.message}" if turns else body.message

    return system_prompt, user_content, action_spec


# ── SSE streaming endpoint (primary) ─────────────────────────────────────────

@router.post("/chat/stream")
async def operator_chat_stream(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_session),
    _rl: None = Depends(_chat_rate_limit),       # Fix 7 — 60 req/min per IP
):
    """
    Stream the LLM reply as SSE chunks, then emit an optional action metadata event.

    Event types:
      data: {"chunk": "..."}          — text fragment
      data: {"action": {...}}         — pending action spec (Phase 3B)
      data: [DONE]                    — stream complete
    """
    async def generate():
        try:
            from agentic_os.services.summary_service import get_summary_service
            provider = get_summary_service().provider

            if not provider.is_configured():
                msg = "LLM is not configured. Go to Settings → LLM to add an API key."
                yield f"data: {json.dumps({'chunk': msg})}\n\n"
                yield "data: [DONE]\n\n"
                return

            system_prompt, user_content, action_spec = _build_prompt_inputs(body, db)

            async for chunk in provider.stream_agent_completion(
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=1200,
                temperature=0.25,
            ):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            # Phase 3B: emit action spec after all text chunks
            if action_spec:
                yield f"data: {json.dumps({'action': action_spec})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.exception("chat stream error: %s", exc)
            err = "An error occurred. Please try again."
            yield f"data: {json.dumps({'chunk': err})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── JSON fallback ─────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def operator_chat(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_session),
    _rl: None = Depends(_chat_rate_limit),       # Fix 7 — 60 req/min per IP
):
    """Non-streaming fallback — returns complete reply as JSON."""
    try:
        from agentic_os.services.summary_service import get_summary_service
        provider = get_summary_service().provider

        if not provider.is_configured():
            return ChatResponse(reply=(
                "LLM is not configured. Go to Settings → LLM to add an API key."
            ))

        system_prompt, user_content, _ = _build_prompt_inputs(body, db)
        reply = await provider.generate_agent_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=1200,
            temperature=0.25,
        )
        return ChatResponse(reply=reply or "I couldn't generate a response. Please try again.")

    except Exception as exc:
        logger.exception("operator_chat error: %s", exc)
        return ChatResponse(reply="An error occurred. Please try again.")
