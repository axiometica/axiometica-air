"""
RAG (Retrieval-Augmented Generation) service for incident context enrichment.

Provides structural similarity-based retrieval without requiring a vector store.
Uses event_type + severity matching to find relevant past incidents and runbook
content for injection into LLM prompts.

No embeddings or external vector store required — SQL-based structural similarity
is sufficient for production incident data where event_type is already a strong
categorical signal.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RAGContextService:
    """
    Structural RAG context builder for incident analysis.

    Retrieves relevant past incidents and runbook content using SQL-based
    structural similarity (event_type, lifecycle_state) rather than vector
    embeddings.  Fast, zero-dependency, and effective for the categorical
    nature of IT operations event data.
    """

    LOOKBACK_DAYS = 90   # how far back to search for similar incidents
    MAX_SIMILAR   = 3    # max past incidents to inject into prompt
    MAX_STEPS     = 8    # max runbook steps (avoids context bloat)

    # ── Similar incident retrieval ─────────────────────────────────────────────

    def find_similar_incidents(
        self,
        db,
        event_type: str,
        severity: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find recently resolved incidents with the same event type.

        Returns a list (most recent first) of dicts, each containing:
          event_type, resource, environment, severity, risk_score, priority,
          runbook_used, summary, resolution_notes, resolution_time_min.
        """
        limit  = limit or self.MAX_SIMILAR
        cutoff = datetime.utcnow() - timedelta(days=self.LOOKBACK_DAYS)

        try:
            from sqlalchemy import text as sql_text

            rows = db.execute(sql_text("""
                SELECT
                    workflow_id::text,
                    severity::text,
                    risk_score,
                    summary,
                    resolution_notes,
                    updated_at,
                    created_at,
                    context->'alert_payload'->>'type'          AS event_type,
                    context->'alert_payload'->>'resource_name' AS resource,
                    context->'cmdb'->>'environment'            AS environment,
                    context->'risk_breakdown'->>'priority'     AS priority,
                    context->'proposal'->>'runbook_name'       AS runbook_used
                FROM workflow_states
                WHERE workflow_type   = 'incident'
                  AND lifecycle_state IN ('resolved', 'closed')
                  AND context->'alert_payload'->>'type' = :event_type
                  AND created_at >= :cutoff
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"event_type": event_type, "cutoff": cutoff, "lim": limit}).fetchall()

            results = []
            for row in rows:
                created = row[6]
                updated = row[5]
                res_time_min = None
                if created and updated:
                    res_time_min = round((updated - created).total_seconds() / 60)

                results.append({
                    "incident_id":         row[0][:8],
                    "severity":            row[1],
                    "risk_score":          row[2],
                    "summary":             (row[3] or "")[:300],
                    "resolution_notes":    (row[4] or "")[:400],
                    "resolved_at":         row[5].isoformat() if row[5] else None,
                    "event_type":          row[7],
                    "resource":            row[8],
                    "environment":         row[9],
                    "priority":            row[10],
                    "runbook_used":        row[11],
                    "resolution_time_min": res_time_min,
                })

            logger.debug(
                "[RAG] Found %d similar incident(s) for event_type=%s",
                len(results), event_type,
            )
            return results

        except Exception as exc:
            logger.warning("[RAG] find_similar_incidents failed: %s", exc)
            return []

    # ── Runbook context retrieval ──────────────────────────────────────────────

    def get_runbook_context(
        self,
        db,
        event_type: str,
        resource_type: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch the best matching runbook and format its steps as readable text.

        Returns a dict with name, description, diagnostics_text, actions_text,
        confidence, success_rate, blast_radius — or None if no runbook found.
        """
        try:
            from sqlalchemy import text as sql_text

            row = db.execute(sql_text("""
                SELECT
                    name, description, diagnostics, actions,
                    confidence, success_rate, blast_radius
                FROM runbooks
                WHERE event_type = :event_type
                  AND enabled    = true
                ORDER BY
                    CASE WHEN platform = :platform THEN 0
                         WHEN platform = 'any'     THEN 1
                         ELSE 2 END,
                    COALESCE(success_rate, 0) DESC
                LIMIT 1
            """), {
                "event_type": event_type,
                "platform":   platform or "any",
            }).fetchone()

            if not row:
                return None

            name, description, diagnostics, actions, confidence, success_rate, blast_radius = row

            def _fmt(steps, label):
                if not steps:
                    return f"  {label}: none defined"
                lines = [f"  {label}:"]
                for i, s in enumerate(steps[:self.MAX_STEPS], 1):
                    step_name = s.get("name", "Step")
                    tool      = s.get("tool", "")
                    desc      = s.get("description", "")
                    line = f"    {i}. {step_name}"
                    if tool:
                        line += f" [{tool}]"
                    if desc:
                        line += f" — {desc}"
                    lines.append(line)
                return "\n".join(lines)

            return {
                "name":             name,
                "description":      description or "",
                "diagnostics_text": _fmt(diagnostics or [], "Diagnostics"),
                "actions_text":     _fmt(actions or [], "Actions"),
                "confidence":       confidence,
                "success_rate":     success_rate,
                "blast_radius":     blast_radius,
            }

        except Exception as exc:
            logger.warning("[RAG] get_runbook_context failed: %s", exc)
            return None

    # ── Bundle builder ─────────────────────────────────────────────────────────

    def build_rag_bundle(
        self,
        db,
        event_type: str,
        severity: Optional[str] = None,
        resource_type: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build the full RAG context bundle for LLM prompt injection.

        Returns:
          {
            has_context:       bool,
            similar_incidents: List[dict],
            runbook:           dict | None,
            similar_count:     int,
          }
        """
        similar = self.find_similar_incidents(db, event_type, severity)
        runbook = self.get_runbook_context(db, event_type, resource_type, platform)

        return {
            "has_context":       bool(similar or runbook),
            "similar_incidents": similar,
            "runbook":           runbook,
            "similar_count":     len(similar),
        }

    # ── Prompt formatter ───────────────────────────────────────────────────────

    def format_for_prompt(self, bundle: Dict[str, Any]) -> str:
        """
        Format a RAG bundle as a structured text block for LLM prompt injection.
        Returns an empty string when no context is available.
        """
        if not bundle.get("has_context"):
            return ""

        sections: List[str] = []

        # ── Similar past incidents ─────────────────────────────────────────────
        similar = bundle.get("similar_incidents", [])
        if similar:
            lines = ["SIMILAR PAST INCIDENTS (use for pattern matching):"]
            for i, inc in enumerate(similar, 1):
                risk = f", Risk: {inc['risk_score']:.0f}" if inc.get("risk_score") else ""
                time = f", Resolved in: {inc['resolution_time_min']}m" if inc.get("resolution_time_min") else ""
                header = (
                    f"  [{i}] {inc['event_type']} on {inc['resource'] or 'unknown'} "
                    f"(Severity: {inc['severity']}, Priority: {inc['priority']}{risk}{time})"
                )
                lines.append(header)
                if inc.get("runbook_used"):
                    lines.append(f"       Runbook: {inc['runbook_used']}")
                if inc.get("summary"):
                    lines.append(f"       Summary: {inc['summary'][:200]}")
                if inc.get("resolution_notes"):
                    lines.append(f"       Resolution: {inc['resolution_notes'][:200]}")
            sections.append("\n".join(lines))

        # ── Runbook knowledge ──────────────────────────────────────────────────
        rb = bundle.get("runbook")
        if rb:
            conf_str = f"{(rb['confidence'] or 0):.0%}" if rb.get("confidence") else "?"
            sr_str   = f"{(rb['success_rate'] or 0):.0%}" if rb.get("success_rate") else "?"
            sections.append(
                f"RUNBOOK: {rb['name']}\n"
                f"  Description: {rb['description']}\n"
                f"  Confidence: {conf_str} | Success rate: {sr_str} | Blast radius: {rb['blast_radius']}\n"
                f"{rb['diagnostics_text']}\n"
                f"{rb['actions_text']}"
            )

        return "\n\n".join(sections)


# ── Module-level singleton ─────────────────────────────────────────────────────

_rag_service: Optional[RAGContextService] = None


def get_rag_service() -> RAGContextService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGContextService()
    return _rag_service
