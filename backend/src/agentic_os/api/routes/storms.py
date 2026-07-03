"""
Storm Management API

Endpoints for viewing and acting on correlated event storms detected by the Storm Agent.

A storm is represented as a parent incident (workflow_state) with:
    context.is_storm_parent = true
    context.storm_children  = [list of child workflow IDs]
    lifecycle_state          = awaiting_manual

Child incidents are held in lifecycle_state = storm_hold until the operator
acts on the storm from the Event Storms page:
    - Resolve as Storm  → bulk-close all children with a shared resolution note
    - Handle Individually → release children to their own individual pipelines

Endpoints:
    GET  /api/storms                      — list active storms
    GET  /api/storms/{storm_id}           — storm detail (analysis + children)
    POST /api/storms/{storm_id}/release   — dismiss storm, release children to their pipelines
    POST /api/storms/{storm_id}/resolve   — manually resolve all storm children
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────

class StormChild(BaseModel):
    workflow_id:          str
    title:                Optional[str]
    lifecycle_state:      str
    severity:             Optional[str]
    resource_name:        Optional[str]
    event_type:           Optional[str]
    created_at:           str
    # Enrichment (v1.0.1)
    incident_number_str:  Optional[str] = None
    source_connector:     Optional[str] = None   # 'watcher_brain' | 'splunk' | etc.
    signal_value:         Optional[float] = None
    signal_threshold:     Optional[float] = None


class StormSummary(BaseModel):
    storm_id:        str
    incident_number: Optional[str]   # INC0042 — storm is still an incident
    storm_number:    Optional[str]   # STRM0003 — storm-specific human ID
    title:           str
    lifecycle_state: str
    severity:        Optional[str]
    pattern:         Optional[str]
    confidence:      Optional[float]
    hypothesis:      Optional[str]
    affected_count:  int
    child_count:     int
    detected_at:     Optional[str]
    created_at:      str


class StormDetail(StormSummary):
    children:              List[StormChild]
    root_cause_candidates: List[Dict[str, Any]]
    topology_evidence:     Dict[str, Any]
    affected_resources:    List[str]
    event_types:           List[str]
    llm_used:              bool
    neo4j_available:       bool


class StormActionRequest(BaseModel):
    notes: Optional[str] = None
    resolution_note: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_storm_summary(row, live_child_count: int = 0) -> StormSummary:
    """Convert a DB row (from the storm query) to a StormSummary.

    live_child_count should come from a subquery on the storm_id column,
    NOT from ctx['storm_children'] which is frozen at storm creation time.
    """
    ctx      = row.context or {}
    analysis = ctx.get("storm_analysis", {})
    ap       = ctx.get("alert_payload", {})

    return StormSummary(
        storm_id=str(row.workflow_id),
        incident_number=getattr(row, "incident_number_str", None),
        storm_number=getattr(row, "storm_number_str", None),
        title=row.title or "Unknown Storm",
        lifecycle_state=row.lifecycle_state,
        severity=row.severity.value if row.severity else "critical",
        pattern=analysis.get("event_type_pattern") or ap.get("pattern"),
        confidence=analysis.get("confidence"),
        hypothesis=(
            analysis.get("llm_hypothesis")
            or ap.get("description")
        ),
        affected_count=len(analysis.get("affected_resources", [])),
        child_count=live_child_count,
        detected_at=(
            ctx.get("storm_detected_at")
            or getattr(row, "storm_detected_at", None)
        ),
        created_at=row.created_at.isoformat(),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/storms", response_model=List[StormSummary])
async def list_storms(
    active_only: bool = True,
    limit: int = 50,
    db: Session = Depends(get_session),
):
    """
    List storm parent incidents.

    Args:
        active_only: If True (default), only return storms that are not resolved/closed.
        limit: Maximum storms to return.
    """
    state_filter = (
        "AND lifecycle_state NOT IN ('resolved', 'closed')"
        if active_only else ""
    )

    rows = db.execute(sql_text(f"""
        SELECT
            ws.workflow_id, ws.title, ws.lifecycle_state, ws.severity,
            ws.context, ws.created_at, ws.incident_number_str, ws.storm_detected_at,
            ws.storm_number_str,
            (SELECT COUNT(*)
             FROM workflow_states c
             WHERE c.storm_id = ws.workflow_id
               AND c.workflow_id != ws.workflow_id) AS live_child_count
        FROM workflow_states ws
        WHERE ws.workflow_type = 'incident'
          AND (
              ws.is_storm_parent = TRUE
              OR (ws.context ->> 'is_storm_parent')::boolean = true
          )
          {state_filter}
        ORDER BY ws.created_at DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    # Column order: workflow_id[0], title[1], lifecycle_state[2], severity[3],
    #               context[4], created_at[5], incident_number_str[6], storm_detected_at[7],
    #               storm_number_str[8], live_child_count[9]
    return [
        StormSummary(
            storm_id=str(r[0]),
            incident_number=r[6],
            storm_number=r[8],
            title=r[1] or "Unknown Storm",
            lifecycle_state=r[2],
            severity=r[3].value if hasattr(r[3], 'value') else r[3],
            pattern=(r[4] or {}).get("storm_analysis", {}).get("event_type_pattern"),
            confidence=(r[4] or {}).get("storm_analysis", {}).get("confidence"),
            hypothesis=(r[4] or {}).get("storm_analysis", {}).get("llm_hypothesis")
                       or (r[4] or {}).get("alert_payload", {}).get("description"),
            affected_count=len((r[4] or {}).get("storm_analysis", {}).get("affected_resources", [])),
            child_count=int(r[9] or 0),
            detected_at=(r[4] or {}).get("storm_detected_at") or (r[7].isoformat() if r[7] else None),
            created_at=r[5].isoformat() if hasattr(r[5], 'isoformat') else str(r[5]),
        )
        for r in rows
    ]


@router.get("/storms/{storm_id}", response_model=StormDetail)
async def get_storm(
    storm_id: str,
    db: Session = Depends(get_session),
):
    """
    Get full storm detail including analysis results and child incidents.
    """
    try:
        storm_uuid = UUID(storm_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid storm ID format")

    row = db.execute(sql_text("""
        SELECT
            workflow_id, title, lifecycle_state, severity,
            context, created_at, incident_number_str, storm_detected_at,
            storm_number_str
        FROM workflow_states
        WHERE workflow_id   = :storm_id
          AND workflow_type = 'incident'
          AND (
              is_storm_parent = TRUE
              OR (context ->> 'is_storm_parent')::boolean = true
          )
    """), {"storm_id": storm_uuid}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Storm not found")

    ctx      = row[4] or {}
    analysis = ctx.get("storm_analysis", {})

    # Fetch children by storm_id column — catches ALL children including those added
    # via merge or Phase-2 expansion, not just those in the frozen storm_children list.
    children: List[StormChild] = []
    child_rows = db.execute(sql_text("""
        SELECT
            workflow_id,
            title,
            lifecycle_state,
            severity,
            COALESCE(
                context -> 'alert_payload' ->> 'resource_name',
                context -> 'sentinel' -> 'alert_payload' ->> 'resource_name',
                context -> 'cmdb' ->> 'resource_name'
            )                                                           AS resource_name,
            COALESCE(
                context -> 'alert_payload' ->> 'type',
                context -> 'sentinel' ->> 'anomaly_type',
                context -> 'alert_payload' ->> 'event_type'
            )                                                           AS event_type,
            created_at,
            incident_number_str,
            COALESCE(
                context -> 'alert_payload' ->> 'source_connector',
                'watcher_brain'
            )                                                           AS source_connector,
            (context -> 'alert_payload' ->> 'signal_value')::float     AS signal_value,
            (context -> 'alert_payload' ->> 'signal_threshold')::float AS signal_threshold
        FROM workflow_states
        WHERE storm_id::text = :storm_id
          AND workflow_id::text != :storm_id
        ORDER BY created_at ASC
    """), {"storm_id": storm_id}).fetchall()

    def _safe_float(v) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    children = [
        StormChild(
            workflow_id=str(cr[0]),
            title=cr[1],
            lifecycle_state=cr[2],
            severity=cr[3].value if hasattr(cr[3], 'value') else cr[3],
            resource_name=cr[4],
            event_type=cr[5],
            created_at=cr[6].isoformat(),
            incident_number_str=cr[7],
            source_connector=cr[8] or 'watcher_brain',
            signal_value=_safe_float(cr[9]),
            signal_threshold=_safe_float(cr[10]),
        )
        for cr in child_rows
    ]

    detected_at = ctx.get("storm_detected_at") or (
        row[7].isoformat() if row[7] else None
    )
    created_at_val = row[5].isoformat() if hasattr(row[5], 'isoformat') else str(row[5])

    return StormDetail(
        storm_id=str(row[0]),
        incident_number=row[6],
        storm_number=row[8],
        title=row[1] or "Unknown Storm",
        lifecycle_state=row[2],
        severity=row[3].value if hasattr(row[3], 'value') else row[3],
        pattern=analysis.get("event_type_pattern"),
        confidence=analysis.get("confidence"),
        hypothesis=analysis.get("llm_hypothesis")
                   or ctx.get("alert_payload", {}).get("description"),
        affected_count=len(analysis.get("affected_resources", [])),
        child_count=len(children),
        detected_at=detected_at,
        created_at=created_at_val,
        children=children,
        root_cause_candidates=analysis.get("root_cause_candidates", []),
        topology_evidence=analysis.get("topology_evidence", {}),
        affected_resources=analysis.get("affected_resources", []),
        event_types=list(set(ctx.get("alert_payload", {}).get("event_types", []))),
        llm_used=analysis.get("llm_used", False),
        neo4j_available=analysis.get("neo4j_available", False),
    )


@router.post("/storms/{storm_id}/release")
async def release_storm(
    storm_id: str,
    body: StormActionRequest,
    db: Session = Depends(get_session),
):
    """
    Dismiss the storm — release all children to proceed through their
    individual pipelines independently.

    Use this when the operator determines that the incidents are NOT actually
    correlated (false positive), or when they want each incident handled on
    its own merits.
    """
    try:
        storm_uuid = UUID(storm_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid storm ID format")

    # Verify storm exists
    row = db.execute(sql_text("""
        SELECT workflow_id, context
        FROM workflow_states
        WHERE workflow_id = :storm_id
          AND workflow_type = 'incident'
          AND (context ->> 'is_storm_parent')::boolean = true
    """), {"storm_id": storm_uuid}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Storm not found")

    now = datetime.utcnow()
    notes = body.notes or "Storm dismissed — incidents released to individual pipelines"

    # Release ALL children by storm_id column — catches every incident ever
    # enrolled in the storm, including those added via merge or Phase-2 expansion.
    # Use RETURNING to capture IDs before storm_id is nulled out.
    rows = db.execute(sql_text("""
        UPDATE workflow_states
        SET lifecycle_state = 'open',
            storm_id        = NULL,
            updated_at      = :now
        WHERE storm_id::text = :storm_id
          AND workflow_id::text != :storm_id
          AND lifecycle_state = 'storm_hold'
        RETURNING workflow_id
    """), {"storm_id": storm_id, "now": now}).fetchall()
    children_ids = [str(r[0]) for r in rows]
    released = len(children_ids)
    logger.info(f"[STORMS API] Released {released} children from storm {storm_id}")

    # Resolve the storm parent
    db.execute(sql_text("""
        UPDATE workflow_states
        SET lifecycle_state   = 'resolved',
            resolution_source = 'manual',
            resolution_notes  = :notes,
            updated_at        = :now
        WHERE workflow_id = :storm_id
    """), {"storm_id": storm_uuid, "notes": notes, "now": now})

    # Write system note
    try:
        from agentic_os.db.models import IncidentNoteModel
        db.add(IncidentNoteModel(
            workflow_id=storm_uuid,
            author="operator",
            note_type="system",
            body=f"Storm dismissed by operator.\n{notes}\n\n"
                 f"{len(children_ids)} child incident(s) released to individual pipelines.",
        ))
    except Exception:
        pass

    db.commit()

    # Re-queue released children
    try:
        from agentic_os.tasks.celery_app import execute_workflow_task
        from agentic_os.core.models import WorkflowType
        for child_id in children_ids:
            execute_workflow_task.delay(
                workflow_id=child_id,
                workflow_type=WorkflowType.INCIDENT.value,
            )
        logger.info(f"[STORMS API] Re-queued {len(children_ids)} children after storm release")
    except Exception as exc:
        logger.warning(f"[STORMS API] Child re-queue failed (non-fatal): {exc}")

    return {
        "status":           "released",
        "storm_id":         storm_id,
        "children_released": len(children_ids),
        "message":          notes,
    }


@router.post("/storms/{storm_id}/resolve")
async def resolve_storm(
    storm_id: str,
    body: StormActionRequest,
    db: Session = Depends(get_session),
):
    """
    Manually resolve all child incidents in the storm and close the parent.

    Use this after the root cause has been addressed manually (e.g., the
    network issue was fixed by the network team) and all affected services
    have recovered.
    """
    try:
        storm_uuid = UUID(storm_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid storm ID format")

    row = db.execute(sql_text("""
        SELECT workflow_id, context
        FROM workflow_states
        WHERE workflow_id = :storm_id
          AND workflow_type = 'incident'
          AND (context ->> 'is_storm_parent')::boolean = true
    """), {"storm_id": storm_uuid}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Storm not found")

    now = datetime.utcnow()
    resolution_note = (
        body.resolution_note
        or body.notes
        or "Resolved as part of storm remediation"
    )

    # Resolve ALL children by storm_id column — catches every incident ever
    # enrolled in the storm, including those added via merge or Phase-2 expansion.
    resolved_count = db.execute(sql_text("""
        UPDATE workflow_states
        SET lifecycle_state    = 'resolved',
            resolution_source  = 'manual',
            resolution_notes   = :note,
            updated_at         = :now
        WHERE storm_id::text = :storm_id
          AND workflow_id::text != :storm_id
          AND lifecycle_state NOT IN ('resolved', 'closed')
    """), {"note": resolution_note, "storm_id": storm_id, "now": now}).rowcount

    # Resolve storm parent
    db.execute(sql_text("""
        UPDATE workflow_states
        SET lifecycle_state   = 'resolved',
            resolution_source = 'manual',
            resolution_notes  = :note,
            updated_at        = :now
        WHERE workflow_id = :storm_id
    """), {"storm_id": storm_uuid, "note": resolution_note, "now": now})

    # System note on parent
    try:
        from agentic_os.db.models import IncidentNoteModel
        db.add(IncidentNoteModel(
            workflow_id=storm_uuid,
            author="operator",
            note_type="system",
            body=(
                f"Storm manually resolved.\n\n"
                f"Resolution: {resolution_note}\n\n"
                f"{resolved_count} child incident(s) closed."
            ),
        ))
    except Exception:
        pass

    db.commit()

    return {
        "status":           "resolved",
        "storm_id":         storm_id,
        "children_resolved": resolved_count,
        "message":          resolution_note,
    }
