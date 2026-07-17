"""
Platform Intelligence API Routes

Exposes the TuningAgent's recommendations and system health metrics.

Endpoints:
  GET  /platform-intelligence/recommendations         — list recommendations
  PUT  /platform-intelligence/recommendations/{id}/accept — accept + apply
  PUT  /platform-intelligence/recommendations/{id}/reject — reject with reason
  GET  /platform-intelligence/health                  — system health summary
  GET  /platform-intelligence/config-history          — applied recs timeline
  POST /platform-intelligence/analyze                 — queue analysis (returns job_id)
  GET  /platform-intelligence/analyze/status/{job_id} — poll queued analysis job
  GET  /platform-intelligence/runs                    — analysis run history
  GET  /platform-intelligence/kpis                    — KPI snapshot series (trend)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.repositories import (
    OptimizationRecommendationRepository,
    RiskWeightConfigRepository,
    PlatformIntelRunRepository,
)
from agentic_os.db.models import WorkflowStateModel, OptimizationRecommendationModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class RecommendationResponse(BaseModel):
    id: str
    category: str
    parameter: str
    current_value: Optional[object] = None
    suggested_value: Optional[object] = None
    title: str
    rationale: str
    impact: Optional[str] = None
    confidence: float
    priority: str
    evidence: Optional[dict] = None
    status: str
    reviewed_by: Optional[str] = None
    review_reason: Optional[str] = None
    reviewed_at: Optional[str] = None
    applied: bool
    applied_at: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True


class AcceptRequest(BaseModel):
    reviewed_by: str = "admin"


class RejectRequest(BaseModel):
    reviewed_by: str = "admin"
    reason: str = ""


class AnalyzeRequest(BaseModel):
    period_days: int = 30
    # Bypasses the accepted/rejected cooldown for this run only — does not delete
    # decision history. Use to force a fresh look (e.g. testing/demo) without
    # losing the pattern-persistence memory a destructive "reset" would lose.
    ignore_cooldown: bool = False


class SystemHealthResponse(BaseModel):
    period_days: int
    total_incidents: int
    resolved_incidents: int
    automated_resolutions: int
    manual_resolutions: int
    automation_rate: Optional[float]  # None when resolved_incidents == 0 — no data, not a real 0%
    false_positive_count: int
    false_positive_rate: Optional[float]  # None when resolved_incidents == 0 — no data, not a real 0%
    avg_mttr_hours: Optional[float]
    p1p2_avg_mttr_hours: Optional[float]
    pending_recommendations: int
    avg_cmdb_coverage: Optional[float]
    last_analysis_at: Optional[str]


# ── Helper ───────────────────────────────────────────────────────────────────

def _to_response(r: OptimizationRecommendationModel) -> RecommendationResponse:
    return RecommendationResponse(
        id=str(r.id),
        category=r.category,
        parameter=r.parameter,
        current_value=r.current_value,
        suggested_value=r.suggested_value,
        title=r.title,
        rationale=r.rationale,
        impact=r.impact,
        confidence=r.confidence,
        priority=r.priority,
        evidence=r.evidence,
        status=r.status,
        reviewed_by=r.reviewed_by,
        review_reason=r.review_reason,
        reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None,
        applied=r.applied,
        applied_at=r.applied_at.isoformat() if r.applied_at else None,
        created_at=r.created_at.isoformat(),
        expires_at=r.expires_at.isoformat() if r.expires_at else None,
    )


# Top-level weights keys whose values are FLAT maps where the key itself may
# legitimately contain dots (taxonomy codes like "log.error.spike", or resource
# names) — must be written as one literal key, never split further. Confirmed
# against EventQualificationService's lookup code, which does overrides.get(key)
# directly on these maps, not a nested traversal.
_FLAT_KEY_PARAM_MAPS = {"event_type_multipliers", "resource_overrides", "domain_multipliers", "environment_multipliers"}


def _write_param(weights: dict, param: str, value) -> bool:
    """
    Write a dotted-path parameter into the weights dict.

    Two shapes exist in weights and must be handled differently:
      - Flat maps (_FLAT_KEY_PARAM_MAPS): the key itself is a dotted string, e.g.
        weights["event_type_multipliers"]["log.error.spike"] — splitting "log.error.spike"
        further into nested dicts would write somewhere the qualification lookup never
        reads (confirmed: that lookup is a flat overrides.get(canonical), not nested).
      - Structural paths (e.g. "factors.spof.missing_data"): genuinely nested, each
        segment is a distinct dict level.
    """
    parts = param.split(".")
    if not parts or not parts[0]:
        logger.warning(f"[PI] Cannot apply empty param path: {param!r}")
        return False

    if parts[0] in _FLAT_KEY_PARAM_MAPS and len(parts) >= 2:
        flat_key = ".".join(parts[1:])
        weights.setdefault(parts[0], {})[flat_key] = value
        return True

    node = weights
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            logger.warning(f"[PI] Cannot apply param path {param!r} — '{part}' is not a dict in weights")
            return False
    node[parts[-1]] = value
    return True


def _apply_recommendation(
    rec: OptimizationRecommendationModel,
    risk_repo: RiskWeightConfigRepository,
) -> bool:
    """
    Apply an accepted recommendation to the live risk weight config.

    Supports two modes:
      • Single-parameter: uses rec.parameter + rec.suggested_value directly
      • Multi-parameter:  iterates evidence.parameter_changes list, each entry
                          has {parameter, suggested_value} — used when one
                          recommendation covers several related config paths.

    Returns True if a config change was written; False for general/informational recs.
    """
    if rec.category in ("runbook_step", "governance"):
        # These describe changes to RunbookModel/PolicyModel rows, gated by the
        # draft/publish workflow (RunbookRepository.publish / PolicyRepository.publish
        # in db/repositories.py) — never a direct column write from here. This
        # function only writes RiskWeightConfigModel.weights. If a future change
        # makes these categories carry a concrete suggested_value, route it through
        # save_draft()+publish() instead of through this function.
        return False
    if rec.category == "general" or rec.suggested_value is None:
        return False

    cfg = risk_repo.get_by_key("default")
    if not cfg:
        return False

    import copy
    weights = copy.deepcopy(cfg.weights)

    # Multi-parameter change (e.g. several missing_data factors at once)
    param_changes = (rec.evidence or {}).get("parameter_changes")
    if param_changes:
        applied_any = False
        for change in param_changes:
            ok = _write_param(weights, change["parameter"], change["suggested_value"])
            if ok:
                logger.info(f"[PI] Applied: {change['parameter']} → {change['suggested_value']}")
                applied_any = True
        if not applied_any:
            return False
        risk_repo.create_or_update("default", weights)
        return True

    # Single-parameter change
    ok = _write_param(weights, rec.parameter, rec.suggested_value)
    if not ok:
        return False
    risk_repo.create_or_update("default", weights)
    logger.info(f"[PI] Applied: {rec.parameter} → {rec.suggested_value}")
    return True


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/platform-intelligence/recommendations", response_model=List[RecommendationResponse])
async def list_recommendations(
    status: Optional[str] = Query(None, description="Filter by status: pending|accepted|rejected|expired"),
    limit: int  = Query(50, ge=1, le=200),
    offset: int = Query(0,  ge=0),
    db: Session = Depends(get_session),
):
    """List optimization recommendations."""
    repo = OptimizationRecommendationRepository(db)
    recs = repo.list_all(status=status, limit=limit, offset=offset)
    return [_to_response(r) for r in recs]


@router.get("/platform-intelligence/recommendations/count")
async def count_recommendations(db: Session = Depends(get_session)):
    """Return count of pending recommendations (used by sidebar badge)."""
    repo = OptimizationRecommendationRepository(db)
    return {"pending": repo.count_pending()}


@router.put("/platform-intelligence/recommendations/{rec_id}/accept", response_model=RecommendationResponse)
async def accept_recommendation(
    rec_id: UUID,
    body: AcceptRequest = AcceptRequest(),
    db: Session = Depends(get_session),
):
    """Accept a recommendation and optionally apply it to the live config."""
    repo      = OptimizationRecommendationRepository(db)
    risk_repo = RiskWeightConfigRepository(db)

    rec = repo.accept(rec_id, reviewed_by=body.reviewed_by)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found or already reviewed")

    applied = _apply_recommendation(rec, risk_repo)
    if applied:
        repo.mark_applied(rec_id)

    db.refresh(rec)
    return _to_response(rec)


@router.put("/platform-intelligence/recommendations/{rec_id}/reject", response_model=RecommendationResponse)
async def reject_recommendation(
    rec_id: UUID,
    body: RejectRequest = RejectRequest(),
    db: Session = Depends(get_session),
):
    """Reject a recommendation."""
    repo = OptimizationRecommendationRepository(db)
    rec  = repo.reject(rec_id, reviewed_by=body.reviewed_by, reason=body.reason)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found or already reviewed")
    return _to_response(rec)


@router.get("/platform-intelligence/health", response_model=SystemHealthResponse)
async def get_system_health(
    period_days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_session),
):
    """Return system health metrics derived from resolved incidents."""
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    # All incidents in window
    total_incidents = (
        db.query(WorkflowStateModel)
        .filter(
            WorkflowStateModel.workflow_type == "incident",
            WorkflowStateModel.created_at >= cutoff,
        )
        .count()
    )

    resolved = (
        db.query(WorkflowStateModel)
        .filter(
            WorkflowStateModel.workflow_type == "incident",
            WorkflowStateModel.lifecycle_state.in_(["resolved", "closed"]),
            WorkflowStateModel.updated_at >= cutoff,
        )
        .all()
    )
    resolved_count = len(resolved)

    automated = sum(1 for w in resolved if w.resolution_source == "automated_remediation")
    manual    = resolved_count - automated
    # None (not 0.0) when there's nothing resolved — a real 0% and "no data yet"
    # must render differently, or a fresh/reset platform looks like a healthy one.
    auto_rate = automated / resolved_count if resolved_count else None

    fp_count = sum(
        1 for w in resolved
        if (w.resolution_category or "").lower() in ("wont_fix", "noise", "duplicate")
    )
    fp_rate = fp_count / resolved_count if resolved_count else None

    # MTTR
    mttr_vals = [
        (w.resolved_at - w.created_at).total_seconds() / 3600
        for w in resolved
        if w.resolved_at and w.created_at
    ]
    avg_mttr = sum(mttr_vals) / len(mttr_vals) if mttr_vals else None

    p1p2_mttr = [
        (w.resolved_at - w.created_at).total_seconds() / 3600
        for w in resolved
        if w.resolved_at and w.created_at
        and str((w.context or {}).get("incident_priority", "")).upper() in ("P1", "P2")
    ]
    p1p2_avg_mttr = sum(p1p2_mttr) / len(p1p2_mttr) if p1p2_mttr else None

    # CMDB coverage
    coverages = []
    for w in resolved:
        ctx = w.context or {}
        rb  = ctx.get("risk_breakdown") or {}
        score = rb.get("confidence_score")
        if score is not None:
            coverages.append(float(score))
    avg_cmdb = sum(coverages) / len(coverages) if coverages else None

    # Pending recs
    pending = OptimizationRecommendationRepository(db).count_pending()

    # Last analysis time = newest recommendation's created_at
    last_rec = (
        db.query(OptimizationRecommendationModel)
        .order_by(OptimizationRecommendationModel.created_at.desc())
        .first()
    )
    last_analysis = last_rec.created_at.isoformat() if last_rec else None

    return SystemHealthResponse(
        period_days=period_days,
        total_incidents=total_incidents,
        resolved_incidents=resolved_count,
        automated_resolutions=automated,
        manual_resolutions=manual,
        automation_rate=round(auto_rate, 3) if auto_rate is not None else None,
        false_positive_count=fp_count,
        false_positive_rate=round(fp_rate, 3) if fp_rate is not None else None,
        avg_mttr_hours=round(avg_mttr, 2) if avg_mttr is not None else None,
        p1p2_avg_mttr_hours=round(p1p2_avg_mttr, 2) if p1p2_avg_mttr is not None else None,
        pending_recommendations=pending,
        avg_cmdb_coverage=round(avg_cmdb, 1) if avg_cmdb is not None else None,
        last_analysis_at=last_analysis,
    )


@router.get("/platform-intelligence/config-history")
async def get_config_history(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    """
    Return a timeline of applied recommendations (config changes made via Platform Intelligence).
    """
    from agentic_os.db.models import OptimizationRecommendationModel
    from sqlalchemy import desc
    applied = (
        db.query(OptimizationRecommendationModel)
        .filter(OptimizationRecommendationModel.applied == True)
        .order_by(desc(OptimizationRecommendationModel.applied_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "id":              str(r.id),
            "parameter":       r.parameter,
            "previous_value":  r.current_value,
            "new_value":       r.suggested_value,
            "title":           r.title,
            "reviewed_by":     r.reviewed_by,
            "applied_at":      r.applied_at.isoformat() if r.applied_at else None,
            "category":        r.category,
            "priority":        r.priority,
        }
        for r in applied
    ]


@router.post("/platform-intelligence/analyze")
async def trigger_analysis(body: AnalyzeRequest = AnalyzeRequest()):
    """
    Queue a TuningAgent analysis cycle as a background Celery job rather than
    running it on the request thread — a large incident window (LLM call +
    aggregation) can take long enough to risk an HTTP gateway timeout.

    Returns a job_id; poll GET /platform-intelligence/analyze/status/{job_id}
    until state is SUCCESS or FAILURE.
    """
    try:
        from agentic_os.tasks.celery_app import run_platform_intelligence_analysis_task
        task = run_platform_intelligence_analysis_task.delay(
            period_days=body.period_days,
            ignore_cooldown=body.ignore_cooldown,
            trigger="force_refresh" if body.ignore_cooldown else "manual",
        )
        return {"status": "queued", "job_id": task.id}
    except Exception as exc:
        logger.exception("[PI] Failed to queue analysis")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/platform-intelligence/analyze/status/{job_id}")
async def get_analysis_status(job_id: str):
    """Poll the status of a queued analysis job (see POST /analyze)."""
    from agentic_os.tasks.celery_app import app as celery_app
    task = celery_app.AsyncResult(job_id)
    result = task.result
    if task.state == "FAILURE":
        result = {"reason": str(result)}
    return {"job_id": job_id, "state": task.state, "result": result}


# ── Run history / KPI trend ─────────────────────────────────────────────────

class RunResponse(BaseModel):
    id: str
    created_at: str
    period_days: int
    trigger: str
    source: str
    incidents_analysed: int
    recommendations_generated: int
    recommendations_skipped: int
    llm_raw_response: Optional[str] = None
    kpis: dict


class RunListResponse(BaseModel):
    runs: List[RunResponse]
    total_count: int


@router.get("/platform-intelligence/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    """
    Analysis run history — one entry per TuningAgent pass (scheduled, manual,
    or force refresh), each with its raw LLM response and KPI snapshot.

    Previously this reasoning was discarded the moment run_analysis() returned,
    so there was no way to audit why a given cycle behaved a certain way (e.g.
    confirm LLM vs rules fired) without grepping backend logs.
    """
    repo = PlatformIntelRunRepository(db)
    runs = repo.list_recent(limit=limit, offset=offset)
    return RunListResponse(
        runs=[RunResponse(**repo.to_dict(r)) for r in runs],
        total_count=repo.count(),
    )


class KpiSeriesResponse(BaseModel):
    points: List[dict]  # [{created_at, kpis: {...}}, ...] oldest first


@router.get("/platform-intelligence/kpis", response_model=KpiSeriesResponse)
async def get_kpi_series(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_session),
):
    """
    KPI snapshot series for trend charting — one point per analysis run within
    the window, oldest first. The System Health/KPI tab's "current value" cards
    are just the last point in this series; this endpoint is what makes a trend
    line possible instead of only ever showing a single instantaneous number.
    """
    repo = PlatformIntelRunRepository(db)
    since = datetime.utcnow() - timedelta(days=days)
    runs = repo.kpi_series(since)
    return KpiSeriesResponse(
        points=[
            {"created_at": r.created_at.isoformat(), "kpis": r.kpis or {}}
            for r in runs
        ]
    )
