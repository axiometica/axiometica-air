"""
Event Type Taxonomy API

Exposes the canonical event-type taxonomy so the runbook editor and other
consumers can:
  - list all types (with optional domain/category filter)
  - get a single type by code
  - list available domains/categories

All system-defined types are read-only (is_system=True). Operators may add
custom types via POST (is_system is always False for operator-created entries).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session as get_db
from agentic_os.db.models import EventTypeTaxonomyModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/event-types", tags=["Event Type Taxonomy"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class EventTypeOut(BaseModel):
    code: str
    label: str
    description: Optional[str]
    category: str
    aliases: list[str]
    is_system: bool
    enabled: bool
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class EventTypeCreate(BaseModel):
    code: str = Field(..., pattern=r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*){1,3}$",
                      description="dot-separated canonical code, e.g. 'infrastructure.compute.cpu_high'")
    label: str = Field(..., max_length=200)
    description: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class EventTypeUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    enabled: Optional[bool] = None


class DomainSummary(BaseModel):
    category: str
    count: int
    enabled_count: int


# ── Helper ────────────────────────────────────────────────────────────────────

def _row_to_out(row: EventTypeTaxonomyModel) -> EventTypeOut:
    aliases = row.aliases if isinstance(row.aliases, list) else (
        json.loads(row.aliases) if isinstance(row.aliases, str) else []
    )
    return EventTypeOut(
        code=row.code,
        label=row.label,
        description=row.description,
        category=row.category,
        aliases=aliases,
        is_system=row.is_system,
        enabled=row.enabled,
        created_at=row.created_at,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EventTypeOut], summary="List all event types")
def list_event_types(
    category: Optional[str] = Query(None, description="Filter by domain/category"),
    enabled_only: bool = Query(True, description="Return only enabled types"),
    q: Optional[str] = Query(None, description="Search label, code, or aliases"),
    db: Session = Depends(get_db),
):
    """Return the full taxonomy, optionally filtered by category or search term."""
    query = db.query(EventTypeTaxonomyModel)

    if category:
        query = query.filter(EventTypeTaxonomyModel.category == category)
    if enabled_only:
        query = query.filter(EventTypeTaxonomyModel.enabled.is_(True))

    rows = query.order_by(EventTypeTaxonomyModel.code).all()

    if q:
        q_lower = q.lower()
        rows = [
            r for r in rows
            if q_lower in r.code.lower()
            or q_lower in (r.label or "").lower()
            or any(q_lower in alias for alias in (r.aliases or []))
        ]

    return [_row_to_out(r) for r in rows]


@router.get("/domains", response_model=list[DomainSummary], summary="List all domains")
def list_domains(db: Session = Depends(get_db)):
    """Return each domain with a count of total and enabled types."""
    from collections import defaultdict
    all_rows = db.query(EventTypeTaxonomyModel).all()
    domain_total: dict[str, int] = defaultdict(int)
    domain_enabled: dict[str, int] = defaultdict(int)
    for r in all_rows:
        domain_total[r.category] += 1
        if r.enabled:
            domain_enabled[r.category] += 1
    return [
        DomainSummary(category=cat, count=domain_total[cat], enabled_count=domain_enabled[cat])
        for cat in sorted(domain_total.keys())
    ]


@router.get("/{code:path}", response_model=EventTypeOut, summary="Get single event type by code")
def get_event_type(code: str, db: Session = Depends(get_db)):
    """Fetch a single taxonomy entry by its full dot-notation code."""
    row = db.query(EventTypeTaxonomyModel).filter(EventTypeTaxonomyModel.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Event type '{code}' not found")
    return _row_to_out(row)


@router.post("", response_model=EventTypeOut, status_code=201, summary="Create custom event type")
def create_event_type(payload: EventTypeCreate, db: Session = Depends(get_db)):
    """Create a new operator-defined event type. is_system is always False for API-created types."""
    existing = db.query(EventTypeTaxonomyModel).filter(
        EventTypeTaxonomyModel.code == payload.code
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Event type '{payload.code}' already exists")

    category = payload.code.split(".")[0]
    row = EventTypeTaxonomyModel(
        code=payload.code,
        label=payload.label,
        description=payload.description,
        category=category,
        aliases=payload.aliases,
        is_system=False,
        enabled=True,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("Created custom event type: %s", payload.code)
    return _row_to_out(row)


@router.patch("/{code:path}", response_model=EventTypeOut, summary="Update an event type")
def update_event_type(code: str, payload: EventTypeUpdate, db: Session = Depends(get_db)):
    """Update label, description, aliases, or enabled flag. System types cannot be deleted."""
    row = db.query(EventTypeTaxonomyModel).filter(EventTypeTaxonomyModel.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Event type '{code}' not found")

    if payload.label is not None:
        row.label = payload.label
    if payload.description is not None:
        row.description = payload.description
    if payload.aliases is not None:
        row.aliases = payload.aliases
    if payload.enabled is not None:
        row.enabled = payload.enabled

    db.commit()
    db.refresh(row)
    return _row_to_out(row)


@router.delete("/{code:path}", status_code=204, summary="Delete a custom event type")
def delete_event_type(code: str, db: Session = Depends(get_db)):
    """Delete an operator-created event type. System types (is_system=True) cannot be deleted."""
    row = db.query(EventTypeTaxonomyModel).filter(EventTypeTaxonomyModel.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Event type '{code}' not found")
    if row.is_system:
        raise HTTPException(
            status_code=403,
            detail=f"'{code}' is a system-defined type and cannot be deleted. Set enabled=false to hide it.",
        )
    db.delete(row)
    db.commit()
