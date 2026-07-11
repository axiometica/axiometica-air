"""Metrics and analytics endpoints"""

import logging
import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Dict
from agentic_os.db.database import get_session
from pydantic import BaseModel

# Phase 1: Redis caching for metrics API
try:
    import redis
    redis_client = redis.Redis(
        host='redis',
        port=6379,
        db=1,  # Use separate DB for metrics cache
        decode_responses=True,
    )
    redis_available = True
except Exception:
    redis_client = None
    redis_available = False

logger = logging.getLogger(__name__)
router = APIRouter()


class IncidentMetricsResponse(BaseModel):
    """Incident metrics response"""
    total_incidents: int
    active_incidents: int
    resolved_today: int
    avg_resolution_time: float
    approval_rate: float
    remediation_success_rate: float
    severity_breakdown: Dict[str, int] = {}  # active incidents per severity level


class RemediationMetricsResponse(BaseModel):
    """Remediation metrics response"""
    auto_remediation_success: int
    manual_remediation_success: int
    total_remediations: int
    auto_remediation_attempts: int
    manual_remediation_attempts: int
    remediation_success_rate: float


@router.get("/metrics/incidents", response_model=IncidentMetricsResponse)
async def get_incident_metrics(db: Session = Depends(get_session)):
    """Get incident metrics and statistics"""
    try:
        # Phase 1: Check Redis cache first (5s TTL)
        cache_key = "metrics:incidents:summary"
        if redis_available:
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    logger.debug("📊 [METRICS] Cache hit for incident metrics")
                    data = json.loads(cached)
                    return IncidentMetricsResponse(**data)
            except Exception as cache_err:
                logger.warning(f"[METRICS] Cache lookup failed (non-fatal): {cache_err}")

        # NOTE: workflow_type and lifecycle_state are stored as lowercase enum values
        # e.g. 'incident' not 'INCIDENT', 'failed' not 'FAILED'

        # Total incidents
        result = db.execute(text(
            "SELECT COUNT(*) FROM workflow_states WHERE workflow_type = 'incident'"
        ))
        total_incidents = result.scalar() or 0

        # Active incidents — everything not yet in a terminal state
        result = db.execute(text("""
            SELECT COUNT(*) FROM workflow_states
            WHERE workflow_type = 'incident'
            AND lifecycle_state NOT IN ('resolved', 'deployed', 'rolled_back', 'closed')
        """))
        active_incidents = result.scalar() or 0

        # Resolved in the last 24 hours (rolling window matches the card subtitle)
        result = db.execute(text("""
            SELECT COUNT(*) FROM workflow_states
            WHERE workflow_type = 'incident'
            AND lifecycle_state IN ('resolved', 'deployed', 'closed')
            AND updated_at >= NOW() - INTERVAL '24 hours'
        """))
        resolved_today = result.scalar() or 0

        # Average resolution time in seconds (for resolved/deployed incidents)
        result = db.execute(text("""
            SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)))
            FROM workflow_states
            WHERE workflow_type = 'incident'
            AND lifecycle_state IN ('resolved', 'deployed', 'closed')
        """))
        avg_result = result.scalar()
        avg_resolution_time = float(avg_result) if avg_result else 0.0

        # Approval rate — incidents that passed through the approval step
        result = db.execute(text("""
            SELECT COUNT(*) FROM workflow_states
            WHERE workflow_type = 'incident'
            AND lifecycle_state IN ('approved', 'executing', 'resolved', 'deployed')
        """))
        approval_approved = result.scalar() or 0

        approval_rate = (approval_approved / total_incidents) if total_incidents > 0 else 0.0

        # Remediation success rate — incidents that resolved successfully
        result = db.execute(text("""
            SELECT COUNT(*) FROM workflow_states
            WHERE workflow_type = 'incident'
            AND lifecycle_state IN ('resolved', 'deployed')
        """))
        remediation_success = result.scalar() or 0

        remediation_success_rate = (remediation_success / total_incidents) if total_incidents > 0 else 0.0

        # Severity breakdown — active incidents grouped by severity.
        # Cast severity::text explicitly because it is stored as a PostgreSQL ENUM type;
        # applying LOWER() directly to an ENUM raises a type error in some PG versions.
        # Wrapped in its own try/except so a failure here never breaks the main response.
        severity_breakdown: Dict[str, int] = {}
        try:
            sev_rows = db.execute(text("""
                SELECT COALESCE(CAST(severity AS TEXT), 'unknown') AS sev_level,
                       COUNT(*) AS cnt
                FROM workflow_states
                WHERE workflow_type = 'incident'
                AND lifecycle_state NOT IN ('resolved', 'deployed', 'rolled_back', 'closed')
                GROUP BY severity
            """)).fetchall()
            severity_breakdown = {row[0]: int(row[1]) for row in sev_rows}
        except Exception as _sev_err:
            logger.warning("severity_breakdown query failed (non-fatal): %s", _sev_err)
            db.rollback()  # reset session so subsequent queries still work

        response = IncidentMetricsResponse(
            total_incidents=total_incidents,
            active_incidents=active_incidents,
            resolved_today=resolved_today,
            avg_resolution_time=avg_resolution_time,
            approval_rate=approval_rate,
            remediation_success_rate=remediation_success_rate,
            severity_breakdown=severity_breakdown,
        )

        # Phase 1: Cache the response (5s TTL)
        if redis_available:
            try:
                redis_client.setex(
                    cache_key,
                    5,  # 5-second TTL
                    json.dumps(response.dict())
                )
                logger.debug("📊 [METRICS] Cached incident metrics (5s TTL)")
            except Exception as cache_err:
                logger.warning(f"[METRICS] Cache write failed (non-fatal): {cache_err}")

        return response

    except Exception as e:
        print(f"ERROR in get_incident_metrics: {str(e)}")
        import traceback
        traceback.print_exc()
        return IncidentMetricsResponse(
            total_incidents=0,
            active_incidents=0,
            resolved_today=0,
            avg_resolution_time=0.0,
            approval_rate=0.0,
            remediation_success_rate=0.0,
            severity_breakdown={},
        )


@router.get("/metrics/remediation", response_model=dict)
async def get_remediation_metrics(db: Session = Depends(get_session)):
    """Get remediation metrics and statistics"""
    try:
        result = db.execute(text("""
            SELECT lifecycle_state FROM workflow_states
            WHERE workflow_type = 'incident'
        """))
        rows = result.fetchall()

        auto_remediation_attempts = 0
        manual_remediation_attempts = 0
        auto_remediation_success = 0
        manual_remediation_success = 0

        for row in rows:
            state = row[0]
            if state in ('approved',):
                manual_remediation_attempts += 1
            elif state in ('executing', 'resolved', 'deployed', 'failed'):
                auto_remediation_attempts += 1
                if state in ('resolved', 'deployed'):
                    auto_remediation_success += 1

        total_remediations = auto_remediation_attempts + manual_remediation_attempts
        remediation_success_rate = (
            (auto_remediation_success + manual_remediation_success) / total_remediations
        ) if total_remediations > 0 else 0.0

        return {
            "auto_remediation_success": auto_remediation_success,
            "manual_remediation_success": manual_remediation_success,
            "total_remediations": total_remediations,
            "auto_remediation_attempts": auto_remediation_attempts,
            "manual_remediation_attempts": manual_remediation_attempts,
            "remediation_success_rate": remediation_success_rate,
        }

    except Exception as e:
        return {
            "auto_remediation_success": 0,
            "manual_remediation_success": 0,
            "total_remediations": 0,
            "auto_remediation_attempts": 0,
            "manual_remediation_attempts": 0,
            "remediation_success_rate": 0.0,
        }


@router.get("/metrics/mttr-breakdown")
async def get_mttr_breakdown(
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_session),
):
    """
    Graphical MTTR breakdown for the dashboard card.

    Returns per-severity stats split into three resolution buckets — auto-
    remediated with no approval gate, auto-remediated that went through a
    governance approval, and manually-handled — plus any incidents currently
    stuck waiting for approval.

    The three-bucket split replaces an earlier two-column (auto vs "human")
    model whose "human adds X delay" framing compared two unrelated incident
    populations (manual vs automated) rather than measuring anything about
    approvals. Auto-remediated-with-approval and auto-remediated-without-
    approval are now distinguished directly via a join against `approvals`,
    so the table only ever shows real sub-populations of the same path.

    Uses SUM(CASE WHEN CAST(enum AS TEXT) ...) rather than COUNT(*) FILTER
    to avoid PostgreSQL type-mismatch errors on enum columns.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Terminal state values (stored as plain text in the DB enum)
    TERMINAL = "('resolved','closed','deployed','rolled_back')"

    try:
        # ── 1. Per-severity stats, three resolution buckets ──────────────────
        # approval_gate: workflow_ids that went through a governance approval —
        # joined once via LEFT JOIN rather than a per-row EXISTS subquery.
        # SUM(CASE WHEN ...) avoids FILTER-clause quirks with PostgreSQL enums.
        # Explicit CAST(lifecycle_state AS TEXT) ensures string comparison works.
        sev_rows = db.execute(text(f"""
            WITH approval_gate AS (
                SELECT DISTINCT workflow_id FROM approvals WHERE approval_type = 'governance'
            )
            SELECT
                COALESCE(CAST(ws.severity AS TEXT), 'unknown')                AS sev,
                SUM(CASE WHEN CAST(ws.lifecycle_state AS TEXT)
                              NOT IN {TERMINAL}
                         THEN 1 ELSE 0 END)                                   AS active,
                SUM(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                         THEN 1 ELSE 0 END)                                   AS resolved,
                SUM(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND (ws.resolution_source = 'watcher_all_clear'
                               OR (ws.resolution_source = 'automated_remediation'
                                   AND ag.workflow_id IS NULL))
                         THEN 1 ELSE 0 END)                                   AS no_approval_count,
                AVG(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND (ws.resolution_source = 'watcher_all_clear'
                               OR (ws.resolution_source = 'automated_remediation'
                                   AND ag.workflow_id IS NULL))
                         THEN EXTRACT(EPOCH FROM (
                                  COALESCE(ws.resolved_at, ws.updated_at) - ws.created_at
                              ))
                         ELSE NULL END)                                       AS no_approval_avg_s,
                SUM(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND ws.resolution_source = 'automated_remediation'
                          AND ag.workflow_id IS NOT NULL
                         THEN 1 ELSE 0 END)                                   AS with_approval_count,
                AVG(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND ws.resolution_source = 'automated_remediation'
                          AND ag.workflow_id IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (
                                  COALESCE(ws.resolved_at, ws.updated_at) - ws.created_at
                              ))
                         ELSE NULL END)                                       AS with_approval_avg_s,
                SUM(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND (ws.resolution_source = 'manual'
                               OR ws.resolution_source IS NULL)
                         THEN 1 ELSE 0 END)                                   AS manual_count,
                AVG(CASE WHEN CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
                          AND ws.created_at >= :cutoff
                          AND (ws.resolution_source = 'manual'
                               OR ws.resolution_source IS NULL)
                         THEN EXTRACT(EPOCH FROM (
                                  COALESCE(ws.resolved_at, ws.updated_at) - ws.created_at
                              ))
                         ELSE NULL END)                                       AS manual_avg_s,
                MIN(CASE WHEN CAST(ws.lifecycle_state AS TEXT)
                              NOT IN {TERMINAL}
                         THEN EXTRACT(EPOCH FROM (NOW() - ws.created_at))
                         ELSE NULL END)                                       AS oldest_open_age_s
            FROM workflow_states ws
            LEFT JOIN approval_gate ag ON ag.workflow_id = ws.workflow_id
            WHERE CAST(ws.workflow_type AS TEXT) = 'incident'
            GROUP BY ws.severity
        """), {"cutoff": cutoff}).fetchall()

        # Columns: 0=sev 1=active 2=resolved 3=no_approval_count 4=no_approval_avg_s
        #          5=with_approval_count 6=with_approval_avg_s 7=manual_count
        #          8=manual_avg_s 9=oldest_open_age_s
        by_severity: dict = {}
        for row in sev_rows:
            sev = (row[0] or "unknown").lower()
            by_severity[sev] = {
                "active":               int(row[1] or 0),
                "resolved":             int(row[2] or 0),
                "no_approval_count":    int(row[3] or 0),
                "no_approval_avg_s":    float(row[4]) if row[4] is not None else None,
                "with_approval_count":  int(row[5] or 0),
                "with_approval_avg_s":  float(row[6]) if row[6] is not None else None,
                "manual_count":         int(row[7] or 0),
                "manual_avg_s":         float(row[8]) if row[8] is not None else None,
                "oldest_open_age_s":    float(row[9]) if row[9] is not None else None,
            }
        logger.info("mttr_breakdown: by_severity keys=%s", list(by_severity.keys()))

        # ── 2. Bucket totals (for the summary strip) ─────────────────────────
        bucket_rows = db.execute(text(f"""
            WITH approval_gate AS (
                SELECT DISTINCT workflow_id FROM approvals WHERE approval_type = 'governance'
            )
            SELECT
                CASE
                    WHEN ws.resolution_source = 'watcher_all_clear' THEN 'no_approval'
                    WHEN ws.resolution_source = 'automated_remediation'
                         AND ag.workflow_id IS NULL THEN 'no_approval'
                    WHEN ws.resolution_source = 'automated_remediation'
                         AND ag.workflow_id IS NOT NULL THEN 'with_approval'
                    ELSE 'manual'
                END                                       AS bucket,
                COUNT(*)                                  AS cnt,
                AVG(EXTRACT(EPOCH FROM (
                    COALESCE(ws.resolved_at, ws.updated_at) - ws.created_at
                )))                                       AS avg_mttr_s
            FROM workflow_states ws
            LEFT JOIN approval_gate ag ON ag.workflow_id = ws.workflow_id
            WHERE CAST(ws.workflow_type AS TEXT) = 'incident'
            AND   CAST(ws.lifecycle_state AS TEXT) IN {TERMINAL}
            AND   ws.created_at >= :cutoff
            GROUP BY bucket
        """), {"cutoff": cutoff}).fetchall()

        by_path: dict = {}
        for row in bucket_rows:
            by_path[row[0]] = {
                "count":      int(row[1] or 0),
                "avg_mttr_s": float(row[2]) if row[2] is not None else None,
            }

        # ── 3. Stuck in approval ─────────────────────────────────────────────
        try:
            stuck_rows = db.execute(text("""
                SELECT
                    ws.workflow_id::text,
                    ws.incident_number_str,
                    ws.title,
                    COALESCE(CAST(ws.severity AS TEXT), 'unknown') AS severity,
                    EXTRACT(EPOCH FROM (NOW() - ws.created_at))    AS age_s,
                    EXTRACT(EPOCH FROM (NOW() - a.requested_at))   AS waiting_s
                FROM workflow_states ws
                JOIN approvals a
                  ON a.workflow_id = ws.workflow_id
                 AND a.status      = 'pending'
                WHERE ws.workflow_type = 'incident'
                ORDER BY a.requested_at ASC
                LIMIT 5
            """)).fetchall()

            stuck = [
                {
                    "workflow_id":     row[0],
                    "incident_number": row[1] or "",
                    "title":           row[2] or "Untitled incident",
                    "severity":        (row[3] or "unknown").lower(),
                    "age_s":           int(row[4] or 0),
                    "waiting_s":       int(row[5] or 0),
                }
                for row in stuck_rows
            ]
        except Exception as _stuck_err:
            logger.warning("stuck_in_approval query failed (non-fatal): %s", _stuck_err)
            db.rollback()
            stuck = []

        return {
            "period_days":      days,
            "by_severity":      by_severity,
            "by_path":          by_path,
            "stuck_in_approval": stuck,
        }

    except Exception as exc:
        logger.exception("mttr_breakdown error: %s", exc)
        return {
            "period_days":      days,
            "by_severity":      {},
            "by_path":          {},
            "stuck_in_approval": [],
        }


@router.get("/metrics/trend")
async def get_incident_trend(
    days: int = Query(default=7, ge=1, le=30),
    db: Session = Depends(get_session),
):
    """
    Return per-day incident counts for the last N days.
    Each entry: { date, label, created, resolved }
    """
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = []

        for i in range(days - 1, -1, -1):
            day_start = today - timedelta(days=i)
            day_end   = day_start + timedelta(days=1)

            created = db.execute(text("""
                SELECT COUNT(*) FROM workflow_states
                WHERE workflow_type = 'incident'
                AND created_at >= :start AND created_at < :end
            """), {"start": day_start, "end": day_end}).scalar() or 0

            resolved = db.execute(text("""
                SELECT COUNT(*) FROM workflow_states
                WHERE workflow_type = 'incident'
                AND lifecycle_state IN ('resolved', 'deployed', 'closed')
                AND updated_at >= :start AND updated_at < :end
            """), {"start": day_start, "end": day_end}).scalar() or 0

            # Average MTTR for incidents resolved on this day (seconds)
            avg_mttr = db.execute(text("""
                SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)))
                FROM workflow_states
                WHERE workflow_type = 'incident'
                AND lifecycle_state IN ('resolved', 'deployed', 'closed')
                AND updated_at >= :start AND updated_at < :end
            """), {"start": day_start, "end": day_end}).scalar()

            result.append({
                "date":             day_start.strftime("%Y-%m-%d"),
                "label":            day_start.strftime("%a"),   # Mon, Tue …
                "created":          int(created),
                "resolved":         int(resolved),
                "avg_mttr_seconds": float(avg_mttr) if avg_mttr is not None else None,
            })

        return result

    except Exception as e:
        print(f"ERROR in get_incident_trend: {e}")
        return []


# ── Nav badge counts ──────────────────────────────────────────────────────────

class NavBadgeCountsResponse(BaseModel):
    active_incidents: int
    pending_approvals: int
    new_events: int
    active_storms: int


@router.get("/metrics/nav-counts", response_model=NavBadgeCountsResponse)
async def get_nav_badge_counts(db: Session = Depends(get_session)):
    """
    Lightweight sidebar badge counts — four COUNTs in one query.

    active_incidents  — incidents not in a terminal state, excluding storm_hold
                        (storm_hold incidents are surfaced by the storms badge)
    pending_approvals — approvals awaiting a decision
    new_events        — monitoring events not yet qualified or dismissed
    active_storms     — storm parent incidents not yet resolved/closed
    """
    try:
        row = db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM workflow_states
                 WHERE workflow_type = 'incident'
                   AND lifecycle_state NOT IN (
                       'resolved', 'deployed', 'rolled_back', 'closed', 'storm_hold'
                   )
                ) AS active_incidents,

                (SELECT COUNT(*) FROM approvals
                 WHERE status = 'pending'
                ) AS pending_approvals,

                (SELECT COUNT(*) FROM monitoring_events
                 WHERE status = 'new'
                ) AS new_events,

                (SELECT COUNT(*) FROM workflow_states
                 WHERE workflow_type = 'incident'
                   AND (context ->> 'is_storm_parent')::boolean = true
                   AND lifecycle_state NOT IN ('resolved', 'closed')
                ) AS active_storms
        """)).fetchone()

        return NavBadgeCountsResponse(
            active_incidents=int(row[0] or 0),
            pending_approvals=int(row[1] or 0),
            new_events=int(row[2] or 0),
            active_storms=int(row[3] or 0),
        )
    except Exception as exc:
        logger.error(f"[NAV] Badge count query failed: {exc}")
        return NavBadgeCountsResponse(
            active_incidents=0,
            pending_approvals=0,
            new_events=0,
            active_storms=0,
        )
