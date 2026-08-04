"""
Watcher Target Management API.

Operator-facing CRUD for SSH watcher target lists, plus watcher-facing
endpoints for pulling active targets and reporting probe/discovery results.
All paths use watcher_id (UUID), not watcher_name.

Operator endpoints (require auth):
GET    /api/monitoring/watchers/{watcher_id}/targets                 → list targets
POST   /api/monitoring/watchers/{watcher_id}/targets                 → add single target
POST   /api/monitoring/watchers/{watcher_id}/targets/cidr            → expand CIDR range
PUT    /api/monitoring/watchers/{watcher_id}/targets/{target_id}     → update target
DELETE /api/monitoring/watchers/{watcher_id}/targets/{target_id}     → delete target
DELETE /api/monitoring/watchers/{watcher_id}/targets/cidr/{cidr}     → delete CIDR group
POST   /api/monitoring/watchers/{watcher_id}/targets/{target_id}/approve → approve pending target
POST   /api/monitoring/watchers/{watcher_id}/targets/approve-all     → bulk approve

Watcher-facing (public, no auth):
GET    /api/monitoring/watchers/{watcher_id}/targets/active          → active targets for polling
POST   /api/monitoring/watchers/{watcher_id}/targets/probe-results   → report probe results
"""

from __future__ import annotations

import ipaddress
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.models import WatcherRegistrationModel
from agentic_os.db.repositories import WatcherTargetRepository

logger = logging.getLogger(__name__)

router = APIRouter()
public_router = APIRouter()

MAX_CIDR_PREFIX = 16  # /16 = 65534 hosts — reject anything larger


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class TargetCreate(BaseModel):
    host: str = Field(..., max_length=255)
    port: int = Field(22, ge=1, le=65535)
    name: str = Field("", max_length=200)
    credential_name: Optional[str] = Field(None, max_length=100)


class TargetCIDR(BaseModel):
    cidr: str = Field(..., max_length=50, description="CIDR notation, e.g. 10.0.1.0/24")
    port: int = Field(22, ge=1, le=65535)
    credential_name: Optional[str] = Field(None, max_length=100)


class TargetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    port: Optional[int] = Field(None, ge=1, le=65535)
    credential_name: Optional[str] = Field(None, max_length=100)


class ProbeResult(BaseModel):
    host: str
    port: int = 22
    status: str = Field(..., description="port_open, port_closed, active, auth_failed, unreachable")
    matched_credential: Optional[str] = None
    error: Optional[str] = None
    cidr_group: Optional[str] = Field(None, description="Set for hosts discovered from a CIDR range scan")
    credential_name: Optional[str] = Field(None, description="Credential used for this target")
    name: Optional[str] = Field(None, description="Discovered hostname from SSH")


class ProbeResultsBatch(BaseModel):
    results: List[ProbeResult]


class TargetResponse(BaseModel):
    id: str
    watcher_id: str
    name: str
    host: str
    port: int
    credential_name: Optional[str]
    status: str
    source: str
    cidr_group: Optional[str]
    auto_approve: bool
    last_probe_at: Optional[str]
    last_connected_at: Optional[str]
    probe_error: Optional[str]
    matched_credential: Optional[str]
    created_at: str
    updated_at: str


class ActiveTargetResponse(BaseModel):
    name: str
    host: str
    port: int
    credential_name: Optional[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _verify_watcher(db: Session, watcher_id: UUID):
    watcher = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_id == watcher_id,
    ).first()
    if not watcher:
        raise HTTPException(404, f"Watcher {watcher_id} not found")
    return watcher


def _serialize(t) -> dict:
    return {
        "id": str(t.id),
        "watcher_id": str(t.watcher_id),
        "name": t.name or "",
        "host": t.host,
        "port": t.port,
        "credential_name": t.credential_name,
        "status": t.status,
        "source": t.source,
        "cidr_group": t.cidr_group,
        "auto_approve": t.auto_approve,
        "last_probe_at": t.last_probe_at.isoformat() if t.last_probe_at else None,
        "last_connected_at": t.last_connected_at.isoformat() if t.last_connected_at else None,
        "probe_error": t.probe_error,
        "matched_credential": t.matched_credential,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# ── Operator endpoints (authenticated) ───────────────────────────────────────

@router.get("/monitoring/watchers/{watcher_id}/targets")
def list_targets(watcher_id: UUID, status: Optional[str] = None, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    targets = repo.list_targets(watcher_id, status_filter=status)
    return [_serialize(t) for t in targets]


@router.post("/monitoring/watchers/{watcher_id}/targets", status_code=201)
def create_target(watcher_id: UUID, body: TargetCreate, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    try:
        target = repo.create_target(
            watcher_id=watcher_id,
            host=body.host,
            port=body.port,
            name=body.name,
            credential_name=body.credential_name,
        )
    except Exception as e:
        if "uq_watcher_target_host_port" in str(e):
            raise HTTPException(409, f"Target {body.host}:{body.port} already exists for this watcher")
        raise
    return _serialize(target)


@router.post("/monitoring/watchers/{watcher_id}/targets/cidr", status_code=201)
def create_targets_cidr(watcher_id: UUID, body: TargetCIDR, db: Session = Depends(get_session)):
    """Create a single CIDR range row. Approval triggers network scanning."""
    _verify_watcher(db, watcher_id)
    try:
        network = ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(400, f"Invalid CIDR notation: {body.cidr}")

    if network.prefixlen < MAX_CIDR_PREFIX:
        raise HTTPException(400, f"CIDR range too large (max /{MAX_CIDR_PREFIX}). Got /{network.prefixlen}")

    host_count = sum(1 for _ in network.hosts())
    if host_count == 0:
        raise HTTPException(400, "CIDR range contains no usable host addresses")

    cidr_str = str(network)
    repo = WatcherTargetRepository(db)
    try:
        target = repo.create_target(
            watcher_id=watcher_id,
            host=cidr_str,
            port=body.port,
            name=cidr_str,
            credential_name=body.credential_name,
            source="cidr_range",
            cidr_group=cidr_str,
        )
    except Exception as e:
        if "uq_watcher_target_host_port" in str(e):
            raise HTTPException(409, f"CIDR range {cidr_str} already exists for this watcher")
        raise
    return {"cidr": cidr_str, "total_hosts": host_count, "target": _serialize(target)}


@router.put("/monitoring/watchers/{watcher_id}/targets/{target_id}")
def update_target(watcher_id: UUID, target_id: UUID, body: TargetUpdate, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    target = repo.update_target(target_id, **fields)
    if not target:
        raise HTTPException(404, "Target not found")
    return _serialize(target)


@router.delete("/monitoring/watchers/{watcher_id}/targets/{target_id}")
def delete_target(watcher_id: UUID, target_id: UUID, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    if not repo.delete_target(target_id):
        raise HTTPException(404, "Target not found")
    return {"deleted": True}


@router.delete("/monitoring/watchers/{watcher_id}/targets/cidr/{cidr_group:path}")
def delete_cidr_group(watcher_id: UUID, cidr_group: str, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    count = repo.delete_cidr_group(watcher_id, cidr_group)
    return {"deleted": count}


@router.post("/monitoring/watchers/{watcher_id}/targets/{target_id}/approve")
def approve_target(watcher_id: UUID, target_id: UUID, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    target = repo.approve_target(target_id)
    if not target:
        raise HTTPException(404, "Target not found or not in pending status")
    return _serialize(target)


@router.post("/monitoring/watchers/{watcher_id}/targets/approve-all")
def approve_all_targets(watcher_id: UUID, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    count = repo.approve_all_pending(watcher_id)
    return {"approved": count}


@router.post("/monitoring/watchers/{watcher_id}/targets/cidr/{cidr_group:path}/approve")
def approve_cidr_group(watcher_id: UUID, cidr_group: str, db: Session = Depends(get_session)):
    _verify_watcher(db, watcher_id)
    repo = WatcherTargetRepository(db)
    target = repo.approve_cidr_range(watcher_id, cidr_group)
    if not target:
        raise HTTPException(404, "CIDR range not found or not in pending status")
    return _serialize(target)


# ── Watcher-facing endpoints (public, no auth) ──────────────────────────────

@public_router.get("/monitoring/watchers/{watcher_id}/targets/active")
def get_active_targets(watcher_id: UUID, db: Session = Depends(get_session)):
    repo = WatcherTargetRepository(db)
    targets = repo.get_active_targets(watcher_id)
    return [
        {"name": t.name or "", "host": t.host, "port": t.port, "credential_name": t.credential_name}
        for t in targets
    ]


@public_router.post("/monitoring/watchers/{watcher_id}/targets/probe-results")
def report_probe_results(watcher_id: UUID, body: ProbeResultsBatch, db: Session = Depends(get_session)):
    repo = WatcherTargetRepository(db)
    all_targets = repo.list_targets(watcher_id)
    host_port_map = {(t.host, t.port): t for t in all_targets}

    updates = []
    created = 0
    not_found = []
    for r in body.results:
        target = host_port_map.get((r.host, r.port))
        if not target and r.cidr_group:
            try:
                target = repo.create_target(
                    watcher_id=watcher_id,
                    host=r.host,
                    port=r.port,
                    name=r.name or "",
                    credential_name=r.credential_name,
                    source="cidr_discovery",
                    cidr_group=r.cidr_group,
                    auto_approve=True,
                )
                created += 1
            except Exception:
                pass
        if not target:
            not_found.append(f"{r.host}:{r.port}")
            continue
        update_entry: dict = {
            "target_id": target.id,
            "status": r.status,
            "error": r.error,
            "matched_credential": r.matched_credential,
        }
        if r.name and (not target.name or target.name == target.host):
            update_entry["name"] = r.name
        updates.append(update_entry)

    if updates:
        repo.update_probe_results(updates)

    return {"updated": len(updates), "created": created, "not_found": not_found}
