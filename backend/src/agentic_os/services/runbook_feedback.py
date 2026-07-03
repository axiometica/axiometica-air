"""
Runbook Feedback Service
========================
Records remediation outcomes against runbooks and updates their dynamic
confidence score + trend indicator.

Confidence formula (Laplace smoothing with 5 virtual observations at 0.85):
    blended = (successful + 4.25) / (total + 5)
    confidence = clamp(blended, 0.25, 0.99)

This means:
  - A fresh runbook with no executions stays at ~0.85
  - 5 successes in a row → ~0.925
  - 5 failures in a row → ~0.425
  - 20 successes, 0 failures → ~0.977

Trend is derived by comparing the success rate of the older half of
recent_outcomes against the newer half (requires >= 4 outcomes).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Laplace smoothing prior: 5 virtual executions at 85% success rate
_PRIOR_N          = 5
_PRIOR_SUCCESSES  = _PRIOR_N * 0.85   # 4.25

# Keep at most this many recent outcomes for trend analysis
_MAX_RECENT = 10


def _calc_confidence(successful: int, total: int) -> float:
    blended = (successful + _PRIOR_SUCCESSES) / (total + _PRIOR_N)
    return round(max(0.25, min(0.99, blended)), 4)


def _calc_trend(recent: list[bool]) -> str:
    """
    Returns "up", "down", "stable", or "new" (< 4 data points).
    Compares the success rate of the first half vs. the second half.
    """
    n = len(recent)
    if n < 4:
        return "new"
    mid = n // 2
    older_rate = sum(1 for x in recent[:mid] if x) / mid
    newer_rate = sum(1 for x in recent[mid:] if x) / (n - mid)
    diff = newer_rate - older_rate
    if diff >= 0.30:
        return "up"
    if diff <= -0.30:
        return "down"
    return "stable"


def record_runbook_execution(
    db,
    runbook_id: str,
    succeeded: bool,
) -> None:
    """
    Update a runbook's execution stats and recalculate confidence + trend.

    Safe to call fire-and-forget — errors are logged and swallowed so they
    never affect the incident workflow.
    """
    from agentic_os.db.models import RunbookModel

    try:
        rb = db.query(RunbookModel).filter_by(id=uuid.UUID(runbook_id)).first()
        if not rb:
            logger.debug(f"[FEEDBACK] Runbook {runbook_id} not found — skipping feedback")
            return

        # Update counters
        rb.total_executions      = (rb.total_executions or 0) + 1
        rb.successful_executions = (rb.successful_executions or 0) + (1 if succeeded else 0)
        rb.failed_executions     = (rb.failed_executions or 0) + (0 if succeeded else 1)
        rb.last_executed_at      = datetime.utcnow()

        # Rolling success rate
        rb.success_rate = round(rb.successful_executions / rb.total_executions, 4)

        # Append to recent outcomes window (keep last _MAX_RECENT)
        recent: list = list(rb.recent_outcomes or [])
        recent.append(succeeded)
        recent = recent[-_MAX_RECENT:]
        rb.recent_outcomes = recent

        # Recalculate confidence (Laplace-smoothed)
        rb.confidence = _calc_confidence(rb.successful_executions, rb.total_executions)

        # Trend indicator
        rb.confidence_trend = _calc_trend(recent)

        db.commit()
        outcome_label = "SUCCESS" if succeeded else "FAILURE"
        logger.info(
            f"[FEEDBACK] {rb.name} ({runbook_id[:8]}): {outcome_label} "
            f"→ conf={rb.confidence:.2f} trend={rb.confidence_trend} "
            f"rate={rb.success_rate:.0%} ({rb.successful_executions}/{rb.total_executions})"
        )
    except Exception as exc:
        logger.warning(f"[FEEDBACK] Failed to update runbook {runbook_id}: {exc}", exc_info=True)


def record_from_workflow(db, workflow_state) -> None:
    """
    Convenience wrapper: extract runbook_id and succeeded from a WorkflowState
    and call record_runbook_execution.

    Only records feedback when:
      - source is "runbook_library" (a real configured runbook, not LLM/playbook)
      - runbook_id is a valid UUID
      - remediation_outcome is "succeeded" or "failed"
    """
    try:
        ctx      = workflow_state.context or {}
        proposal = ctx.get("proposal") or {}
        source   = proposal.get("source", "")
        rb_id    = proposal.get("runbook_id", "")
        outcome  = getattr(workflow_state, "remediation_outcome", None)

        logger.debug(
            f"[FEEDBACK] record_from_workflow: source={source!r} rb_id={rb_id!r} "
            f"outcome={outcome!r} proposal_keys={list(proposal.keys())}"
        )

        if source != "runbook_library":
            logger.debug(f"[FEEDBACK] Skipping — source is {source!r}, not runbook_library")
            return  # only track human-authored runbooks
        if not rb_id or rb_id in ("fallback-escalate",):
            logger.debug(f"[FEEDBACK] Skipping — runbook_id is empty or fallback ({rb_id!r})")
            return
        if outcome not in ("succeeded", "failed"):
            logger.debug(f"[FEEDBACK] Skipping — remediation_outcome is {outcome!r}, need succeeded/failed")
            return

        succeeded = outcome == "succeeded"
        record_runbook_execution(db, rb_id, succeeded)

    except Exception as exc:
        logger.warning(f"[FEEDBACK] record_from_workflow error: {exc}", exc_info=True)
