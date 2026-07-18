"""
Monitoring Checks API — external connectivity check configuration per watcher.

GET    /api/monitoring/watchers                                → list known watchers
POST   /api/monitoring/watchers/register                       → watcher self-registration / heartbeat
POST   /api/monitoring/watchers/{watcher_name}/approve         → operator approves a pending watcher
POST   /api/monitoring/watchers/{watcher_name}/reject          → operator rejects a pending watcher
POST   /api/monitoring/watchers/{watcher_name}/disable         → suspend watcher (reversible)
POST   /api/monitoring/watchers/{watcher_name}/enable          → re-enable disabled watcher
POST   /api/monitoring/watchers/{watcher_name}/reset           → clear in-memory conditions
POST   /api/monitoring/watchers/{watcher_name}/invalidate      → force re-registration on next heartbeat
DELETE /api/monitoring/watchers/{watcher_name}                 → delete registration entirely
GET    /api/monitoring/watchers/{watcher_name}/checks          → list checks for a watcher
POST   /api/monitoring/watchers/{watcher_name}/checks          → create a check
PUT    /api/monitoring/watchers/{watcher_name}/checks/{id}     → update a check
DELETE /api/monitoring/watchers/{watcher_name}/checks/{id}     → delete a check
POST   /api/monitoring/watchers/{watcher_name}/checks/seed     → seed factory defaults
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.models import WatcherExternalCheckModel, WatcherRegistrationModel

logger = logging.getLogger(__name__)
public_router = APIRouter()  # Public endpoint for watcher self-registration bootstrap
router = APIRouter()  # Authenticated endpoints for management operations

# ── Default checks seeded for a new watcher ──────────────────────────────────
# No default external checks — operator must explicitly configure any external
# connectivity checks (PING, HTTP, TCP, DNS, TLS) after watcher approval.
# This aligns with the "no monitoring by default" security model.

DEFAULT_CHECKS = []


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class WatcherRegistration(BaseModel):
    watcher_name: str
    watcher_id: Optional[str] = None  # UUID; omitted on first call, present on heartbeats
    display_name: str = ""
    host: str = ""
    poll_interval: int = 20
    sentinel_container: Optional[str] = None
    nginx_url: str = ""        # Public HTTPS URL the watcher reached this platform through
    kill_api_url: str = ""     # Kill-API callback URL (http://<host>:8080)
    environment: str = "unknown"   # Detected runtime environment
    adapter_mode: str = "docker"   # Execution adapter in use
    watcher_version: Optional[str] = None  # Watcher agent version string
    metrics_history: Optional[list] = None  # Rolling [{ts,cpu,mem,disk,alerts}] buffer
    targets: Optional[dict] = None  # Adapter-specific target metadata (e.g. k8s_namespace)


class ExternalCheckCreate(BaseModel):
    check_type: str = Field(..., pattern="^(ping|http|https|tcp|dns|tls)$")
    target: str
    name: str = ""
    port: Optional[int] = None
    expected_status: int = 200
    timeout_ms: int = 5000
    latency_threshold_ms: int = 0
    tls_expiry_warning_days: int = 30
    enabled: bool = True
    container_name: str = ""
    service_name:   str = ""


class ExternalCheckUpdate(BaseModel):
    check_type: Optional[str] = Field(None, pattern="^(ping|http|https|tcp|dns|tls)$")
    target: Optional[str] = None
    name: Optional[str] = None
    port: Optional[int] = None
    expected_status: Optional[int] = None
    timeout_ms: Optional[int] = None
    latency_threshold_ms: Optional[int] = None
    tls_expiry_warning_days: Optional[int] = None
    enabled: Optional[bool] = None
    container_name: Optional[str] = None
    service_name:   Optional[str] = None


def _check_to_dict(row: WatcherExternalCheckModel) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "watcher_name": row.watcher_name,
        "check_type": row.check_type,
        "target": row.target,
        "name": row.name,
        "port": row.port,
        "expected_status": row.expected_status,
        "timeout_ms": row.timeout_ms,
        "latency_threshold_ms": row.latency_threshold_ms,
        "tls_expiry_warning_days": row.tls_expiry_warning_days,
        "enabled": row.enabled,
        "container_name": row.container_name or "",
        "service_name":   row.service_name or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _reg_to_dict(row: WatcherRegistrationModel) -> Dict[str, Any]:
    now = datetime.utcnow()
    last_seen_s = (now - row.last_seen).total_seconds() if row.last_seen else None
    # Allow up to 150 s gap — watcher heartbeats every ~3 polls (60–90 s at default rate)
    heartbeat_status = "active" if (last_seen_s is not None and last_seen_s < 150) else "inactive"
    reg_status = getattr(row, "registration_status", "approved") or "approved"
    return {
        "watcher_id": str(row.watcher_id) if row.watcher_id else None,
        "watcher_name": row.watcher_name,
        "display_name": row.display_name or row.watcher_name,
        "host": row.host,
        "poll_interval": row.poll_interval,
        "sentinel_container": row.sentinel_container,
        "registration_status": reg_status,
        "nginx_url": getattr(row, "nginx_url", "") or "",
        "kill_api_url": getattr(row, "kill_api_url", "") or "",
        "environment": getattr(row, "environment", "unknown") or "unknown",
        "adapter_mode": getattr(row, "adapter_mode", "docker") or "docker",
        "watcher_version": getattr(row, "watcher_version", None),
        "metrics_history": getattr(row, "metrics_history", None) or [],
        "approved_at": row.approved_at.isoformat() if getattr(row, "approved_at", None) else None,
        "approved_by": getattr(row, "approved_by", "") or "",
        "status": heartbeat_status,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        "last_seen_seconds_ago": int(last_seen_s) if last_seen_s is not None else None,
        "registered_at": row.registered_at.isoformat() if row.registered_at else None,
    }


# ── Watcher registration / heartbeat ─────────────────────────────────────────
# PUBLIC endpoint — watchers self-register without authentication

@public_router.post("/monitoring/watchers/register", tags=["Monitoring"])
def register_watcher(
    payload: WatcherRegistration,
    request: Request,
    db: Session = Depends(get_session),
):
    """
    Called by the watcher on startup and periodically as a heartbeat.

    Identity resolution (in order):
      1. payload.watcher_id present → look up by UUID (authoritative)
      2. payload.watcher_id absent  → look up by watcher_name (first call / legacy)
      3. Neither found              → new registration; platform assigns a UUID

    First call  → creates a registration.
                  If the request includes a valid X-API-Key (automation account),
                  the watcher is auto-approved — no operator approval required.
                  Unknown watchers (no valid key) start as 'pending'.
    Subsequent  → updates heartbeat fields only; registration_status is NOT changed.

    Response always includes 'watcher_id' so the watcher can persist it locally.
    """
    row = None

    # Check if the caller presents a valid API key — used to auto-approve on first registration.
    # A watcher that already knows the API key is a trusted, known entity and should not
    # be held in 'pending' state after a backend restart or full database rebuild.
    _api_key_valid = False
    _raw_api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if _raw_api_key:
        try:
            from agentic_os.api.auth import verify_api_key
            principal = verify_api_key(_raw_api_key, db)
            _api_key_valid = principal.role in ("automation", "admin", "itom_admin")
        except Exception:
            _api_key_valid = False

    # 1. Look up by stable UUID (heartbeat from a known watcher)
    if payload.watcher_id:
        try:
            row = db.query(WatcherRegistrationModel).filter(
                WatcherRegistrationModel.watcher_id == uuid.UUID(payload.watcher_id)
            ).first()
        except (ValueError, AttributeError):
            pass  # malformed UUID — fall through to name lookup

    # 2. Fall back to name lookup (first call, or watcher hasn't persisted its id yet)
    if row is None:
        row = db.query(WatcherRegistrationModel).filter(
            WatcherRegistrationModel.watcher_name == payload.watcher_name
        ).first()

    if row:
        # Heartbeat — update mutable fields; preserve registration_status and watcher_id
        row.watcher_name = payload.watcher_name  # allow rename (sysid stays stable)
        row.display_name = payload.display_name or row.display_name
        row.host = payload.host or row.host
        row.poll_interval = payload.poll_interval
        row.sentinel_container = payload.sentinel_container
        if payload.nginx_url:
            row.nginx_url = payload.nginx_url
        if payload.kill_api_url:
            row.kill_api_url = payload.kill_api_url
        if payload.environment and payload.environment != "unknown":
            row.environment = payload.environment
        if payload.adapter_mode:
            row.adapter_mode = payload.adapter_mode
        if payload.targets is not None:
            row.targets = payload.targets
        if payload.watcher_version:
            row.watcher_version = payload.watcher_version
        if payload.metrics_history is not None:
            row.metrics_history = payload.metrics_history[-20:]
        row.last_seen = datetime.utcnow()
        reg_status = getattr(row, "registration_status", "approved") or "approved"
        logger.debug(
            f"[REGISTER] Heartbeat from '{payload.watcher_name}' "
            f"(id={row.watcher_id}, status={reg_status})"
        )
    else:
        # First-time registration (or re-registration after DB wipe) → assign a new UUID.
        # Auto-approve if the watcher presents a valid API key — it's already a trusted entity.
        # Unknown watchers (no valid key) start as 'pending' until operator approves.
        new_id = uuid.uuid4()
        initial_status = "approved" if _api_key_valid else "pending"
        now = datetime.utcnow()
        row = WatcherRegistrationModel(
            watcher_id=new_id,
            watcher_name=payload.watcher_name,
            display_name=payload.display_name or payload.watcher_name,
            host=payload.host,
            poll_interval=payload.poll_interval,
            sentinel_container=payload.sentinel_container,
            registration_status=initial_status,
            nginx_url=payload.nginx_url,
            kill_api_url=payload.kill_api_url,
            environment=payload.environment,
            adapter_mode=payload.adapter_mode,
            watcher_version=payload.watcher_version,
            metrics_history=payload.metrics_history or [],
            targets=payload.targets,
            registered_at=now,
            last_seen=now,
            approved_at=now if initial_status == "approved" else None,
            approved_by="api_key" if initial_status == "approved" else None,
        )
        db.add(row)
        db.flush()

        # Seed default external checks for this new watcher
        existing = db.query(WatcherExternalCheckModel).filter(
            WatcherExternalCheckModel.watcher_name == payload.watcher_name
        ).count()
        if existing == 0:
            for defaults in DEFAULT_CHECKS:
                db.add(WatcherExternalCheckModel(
                    watcher_name=payload.watcher_name,
                    **defaults,
                ))

        reg_status = initial_status
        if initial_status == "approved":
            logger.info(
                f"✅ [REGISTER] New watcher '{payload.watcher_name}' assigned id={new_id} "
                f"from {payload.host} — auto-approved (valid API key)"
            )
        else:
            logger.info(
                f"[REGISTER] New watcher '{payload.watcher_name}' assigned id={new_id} "
                f"from {payload.host} — awaiting operator approval"
            )

    db.commit()
    _messages = {
        "pending":  "Registration pending operator approval. Approve in Admin → Monitoring Setup.",
        "disabled": "Watcher is disabled by an operator. Events suppressed until re-enabled.",
        "rejected": "Watcher has been rejected. Contact an administrator.",
    }
    return {
        "ok": True,
        "watcher_id": str(row.watcher_id),
        "watcher_name": row.watcher_name,
        "registration_status": reg_status,
        "message": _messages.get(reg_status, "Heartbeat received."),
    }


@router.get("/monitoring/watchers", tags=["Monitoring"])
def list_watchers(db: Session = Depends(get_session)):
    """Return all registered watchers with live status."""
    rows = db.query(WatcherRegistrationModel).order_by(
        WatcherRegistrationModel.last_seen.desc()
    ).all()
    return [_reg_to_dict(r) for r in rows]


@router.post("/monitoring/watchers/{watcher_name}/approve", tags=["Monitoring"])
def approve_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Operator approves a pending watcher registration.
    The watcher will receive 'approved' on its next heartbeat and start monitoring.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")
    row.registration_status = "approved"
    row.approved_at = datetime.utcnow()
    row.approved_by = "operator"  # TODO: replace with auth principal when auth is wired
    db.commit()
    logger.info(f"[REGISTER] Watcher '{watcher_name}' approved by operator")
    return {"ok": True, "watcher_name": watcher_name, "registration_status": "approved"}


@router.post("/monitoring/watchers/{watcher_name}/reject", tags=["Monitoring"])
def reject_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Operator rejects a pending watcher registration.
    The watcher will receive 'rejected' on its next heartbeat and exit.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")
    row.registration_status = "rejected"
    db.commit()
    logger.info(f"[REGISTER] Watcher '{watcher_name}' rejected by operator")
    return {"ok": True, "watcher_name": watcher_name, "registration_status": "rejected"}


@router.delete("/monitoring/watchers/{watcher_name}", status_code=204, tags=["Monitoring"])
def delete_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Remove a watcher registration entirely (and its external checks).
    The watcher will re-register as pending if it restarts.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")
    db.query(WatcherExternalCheckModel).filter(
        WatcherExternalCheckModel.watcher_name == watcher_name
    ).delete()
    db.delete(row)
    db.commit()
    logger.info(f"[REGISTER] Watcher '{watcher_name}' registration deleted")


@router.post("/monitoring/watchers/{watcher_name}/disable", tags=["Monitoring"])
def disable_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Temporarily suspend a watcher — it continues heartbeating and monitoring
    locally but stops submitting events to the platform.  Reversible via /enable.
    Unlike /reject the watcher does NOT exit.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")
    row.registration_status = "disabled"
    db.commit()
    logger.info(f"[REGISTER] Watcher '{watcher_name}' disabled by operator")
    return {"ok": True, "watcher_name": watcher_name, "registration_status": "disabled"}


@router.post("/monitoring/watchers/{watcher_name}/enable", tags=["Monitoring"])
def enable_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Re-enable a disabled (or rejected) watcher.  Sets status back to approved
    so events resume on the next heartbeat without restarting the watcher.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")
    row.registration_status = "approved"
    row.approved_at = datetime.utcnow()
    row.approved_by = "operator"
    db.commit()
    logger.info(f"[REGISTER] Watcher '{watcher_name}' re-enabled by operator")
    return {"ok": True, "watcher_name": watcher_name, "registration_status": "approved"}


@router.post("/monitoring/watchers/{watcher_name}/reset", tags=["Monitoring"])
async def reset_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Clear the watcher's in-memory active conditions and tracked incidents.
    Calls the watcher's Kill-API /reset endpoint, then confirms via response.
    Use after bulk incident cleanup or when a watcher is stuck in a loop.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")

    kill_api_url = (getattr(row, "kill_api_url", "") or f"http://{watcher_name}:8080").rstrip("/")
    watcher_reset = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{kill_api_url}/reset")
            watcher_reset = resp.status_code == 200
    except Exception as exc:
        logger.warning(f"[REGISTER] Reset call to {kill_api_url} failed: {exc}")

    logger.info(f"[REGISTER] Watcher '{watcher_name}' reset — kill_api_reached={watcher_reset}")
    return {
        "ok": True,
        "watcher_name": watcher_name,
        "kill_api_reached": watcher_reset,
        "message": "Active conditions cleared." if watcher_reset else
                   "DB reset done; Kill-API unreachable — watcher will self-reconcile within ~60s.",
    }


@router.post("/monitoring/watchers/{watcher_name}/invalidate", tags=["Monitoring"])
def invalidate_watcher(watcher_name: str, db: Session = Depends(get_session)):
    """
    Invalidate the watcher's registration, forcing operator re-approval before events
    are accepted again.

    Implementation note: previously this deleted the DB row, which caused the watcher
    to immediately re-register and auto-approve itself on the next heartbeat (because
    it presents a valid API key).  The fix keeps the row in place and sets
    registration_status = 'pending'.  The watcher's next heartbeat finds the existing
    row by UUID and updates heartbeat fields but does NOT change registration_status
    (the heartbeat handler explicitly preserves it).  Event submission is blocked while
    the status is not 'approved' — see the monitoring_events route.
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")

    # Set pending — do NOT delete the row.  Deleting causes fresh registration which
    # auto-approves (valid API key).  Keeping the row lets the heartbeat path find
    # the existing record and preserve registration_status = 'pending'.
    # Note: do NOT null-out approved_at / approved_by — approved_by is NOT NULL in
    # the schema and would cause a constraint violation.  The status field alone is
    # the gate; audit trail of who originally approved is kept for reference.
    row.registration_status = "pending"
    db.commit()
    logger.info(
        f"[REGISTER] Watcher '{watcher_name}' invalidated — status set to 'pending', "
        f"events suppressed until operator re-approves"
    )
    return {
        "ok": True,
        "watcher_name": watcher_name,
        "registration_status": "pending",
        "message": "Registration set to pending — events are suppressed. Re-approve in Admin → Monitoring Setup.",
    }


# ── External checks CRUD ──────────────────────────────────────────────────────

@router.post("/monitoring/watchers/{watcher_name}/checks/test", tags=["Monitoring"])
async def test_check(
    watcher_name: str,
    payload: ExternalCheckCreate,
    db: Session = Depends(get_session),
):
    """
    Run an external check immediately from the named watcher's network context.
    Proxies to the watcher's Kill-API /test-check endpoint so the probe originates
    from inside the watcher container (correct Docker network, DNS, etc.).
    """
    row = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")

    kill_api_url = (getattr(row, "kill_api_url", "") or f"http://{watcher_name}:8080").rstrip("/")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{kill_api_url}/test-check",
                json=payload.model_dump(),
            )
        return resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach watcher Kill-API at {kill_api_url}: {exc}",
        )


@router.get("/monitoring/watchers/{watcher_name}/checks", tags=["Monitoring"])
def list_checks(watcher_name: str, db: Session = Depends(get_session)):
    """Return all external checks for the given watcher (enabled + disabled)."""
    rows = db.query(WatcherExternalCheckModel).filter(
        WatcherExternalCheckModel.watcher_name == watcher_name
    ).order_by(WatcherExternalCheckModel.created_at).all()
    return [_check_to_dict(r) for r in rows]


@router.post("/monitoring/watchers/{watcher_name}/checks", status_code=201, tags=["Monitoring"])
def create_check(
    watcher_name: str,
    payload: ExternalCheckCreate,
    db: Session = Depends(get_session),
):
    """Add a new external check for the specified watcher."""
    row = WatcherExternalCheckModel(
        watcher_name=watcher_name,
        check_type=payload.check_type,
        target=payload.target,
        name=payload.name or payload.target,
        port=payload.port,
        expected_status=payload.expected_status,
        timeout_ms=payload.timeout_ms,
        latency_threshold_ms=payload.latency_threshold_ms,
        tls_expiry_warning_days=payload.tls_expiry_warning_days,
        enabled=payload.enabled,
        container_name=payload.container_name,
        service_name=payload.service_name,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"[MONITORING] Created {payload.check_type.upper()} check '{row.name}' for {watcher_name}")
    return _check_to_dict(row)


@router.put("/monitoring/watchers/{watcher_name}/checks/{check_id}", tags=["Monitoring"])
def update_check(
    watcher_name: str,
    check_id: str,
    payload: ExternalCheckUpdate,
    db: Session = Depends(get_session),
):
    """Update fields on an existing external check."""
    row = db.query(WatcherExternalCheckModel).filter(
        WatcherExternalCheckModel.id == check_id,
        WatcherExternalCheckModel.watcher_name == watcher_name,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Check not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    if payload.container_name is not None:
        row.container_name = payload.container_name
    if payload.service_name is not None:
        row.service_name = payload.service_name
    row.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)
    logger.info(f"[MONITORING] Updated check {check_id} for {watcher_name}")
    return _check_to_dict(row)


@router.delete("/monitoring/watchers/{watcher_name}/checks/{check_id}", status_code=204, tags=["Monitoring"])
def delete_check(
    watcher_name: str,
    check_id: str,
    db: Session = Depends(get_session),
):
    """Delete an external check."""
    row = db.query(WatcherExternalCheckModel).filter(
        WatcherExternalCheckModel.id == check_id,
        WatcherExternalCheckModel.watcher_name == watcher_name,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Check not found")
    db.delete(row)
    db.commit()
    logger.info(f"[MONITORING] Deleted check {check_id} for {watcher_name}")



# ── Remediation preview (dry-run) ─────────────────────────────────────────────

class PreviewRequest(BaseModel):
    event_type:      str
    resource_name:   str
    anomaly_process: Optional[str] = None
    signal_value:    Optional[float] = None
    # Override the watcher's real adapter_mode for simulation purposes.
    # Omit to use the watcher's registered adapter.
    adapter_mode_override: Optional[str] = None

class PreviewStep(BaseModel):
    order:        int
    step_type:    str          # diagnostic | remediation
    name:         str
    tool:         str
    raw_command:  Optional[str]   # template before interpolation
    resolved_cmd: Optional[str]   # after {param} substitution
    variant_used: Optional[str]   # which key was matched (docker/kubernetes/ssh/…)
    exec_mode:    str              # host | target | simulate
    args:         dict

class PreviewResponse(BaseModel):
    watcher_name: str
    adapter_mode: str
    event_type:   str
    resource_name: str
    runbook_name: Optional[str]
    runbook_platform: Optional[str]
    confidence:   Optional[float]
    steps:        list

@router.post("/monitoring/watchers/{watcher_name}/preview-remediation", tags=["Monitoring"])
def preview_remediation(
    watcher_name: str,
    body: PreviewRequest,
    db: Session = Depends(get_session),
):
    """
    Dry-run remediation preview: resolve which runbook matches event_type + watcher
    environment, then show each step with the exact command that would run —
    fully interpolated with resource_name and anomaly_process.
    No incident is created; nothing executes.
    """
    from agentic_os.agents.incident_agents import (
        _lookup_runbook, _resolve_watcher_info, ToolRegistryAgent
    )

    # 1. Look up watcher's adapter_mode
    row = db.query(WatcherRegistrationModel).filter_by(watcher_name=watcher_name).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")
    adapter_mode = getattr(row, "adapter_mode", "docker") or "docker"

    # Apply adapter_mode override for simulation
    if body.adapter_mode_override:
        adapter_mode = body.adapter_mode_override.lower().strip()

    # 2. Find best matching runbook
    runbook = None
    try:
        runbook = _lookup_runbook(body.event_type, body.resource_name, adapter_mode)
        if not runbook:
            runbook = _lookup_runbook(body.event_type, None, adapter_mode)
        if not runbook:
            runbook = _lookup_runbook(body.event_type, None, "any")
    except Exception as _rb_err:
        logger.warning(f"[PREVIEW] Runbook lookup error: {_rb_err}")

    _TARGET_MODE_ADAPTERS = {"vcenter", "aws_ssm", "azure"}

    def _build_step(step: dict, step_type: str) -> dict:
        tool_name = step.get("tool", "")
        args_raw   = step.get("args") or step.get("args_json") or {}

        # Resolve command from approved_actions catalog
        from agentic_os.db.repositories import ApprovedActionRepository
        from agentic_os.db.models import ApprovedActionModel
        repo = ApprovedActionRepository(db)
        tool_norm = tool_name.lower().replace(" ", "_").replace("(", "").replace(")", "")
        action_obj = repo.get_by_tool_name(tool_norm)

        raw_cmd     = None
        resolved    = None
        variant_key = None
        exec_mode   = "simulate"

        if action_obj and action_obj.enabled:
            variants = getattr(action_obj, "command_variants", None) or {}
            raw_cmd = (
                variants.get(adapter_mode)
                or variants.get("any")
                or getattr(action_obj, "command", None)
            )
            if raw_cmd:
                variant_key = (
                    adapter_mode if adapter_mode in variants
                    else ("any" if "any" in variants else "default")
                )
                exec_mode = "target" if adapter_mode in _TARGET_MODE_ADAPTERS else "host"

                # Interpolate — step args first, then runtime context overrides
                subs: dict = {}
                if isinstance(args_raw, dict):
                    subs.update({k: str(v) for k, v in args_raw.items()})
                # Runtime context always wins over runbook-baked defaults
                subs["container"]     = body.resource_name
                subs["pod"]           = body.resource_name
                subs["host"]          = body.resource_name
                subs["target"]        = body.resource_name
                subs["service"]       = body.resource_name
                subs["deployment"]    = body.resource_name
                subs["resource_name"] = body.resource_name
                if body.anomaly_process:
                    subs["process_name"] = body.anomaly_process  # override empty runbook default
                # Fill remaining {param} with sensible defaults from parameter schema
                for param in (action_obj.parameters or []):
                    pname = param.get("name", "")
                    if pname and pname not in subs and param.get("default") is not None:
                        subs[pname] = str(param["default"])

                try:
                    resolved = raw_cmd.format_map({k: str(v) for k, v in subs.items()})
                except (KeyError, ValueError):
                    resolved = raw_cmd  # leave template as-is if missing params
        elif not action_obj:
            exec_mode = "unknown"  # tool not in catalog

        return {
            "order":        step.get("order", 0),
            "step_type":    step_type,
            "name":         step.get("name", tool_name),
            "tool":         tool_name,
            "raw_command":  raw_cmd,
            "resolved_cmd": resolved,
            "variant_used": variant_key,
            "exec_mode":    exec_mode,
            "args":         args_raw if isinstance(args_raw, dict) else {},
        }

    steps = []
    if runbook:
        for s in (runbook.diagnostics or []):
            steps.append(_build_step(s, "diagnostic"))
        for s in (runbook.actions or []):
            steps.append(_build_step(s, "remediation"))
        steps.sort(key=lambda x: x["order"])

    return {
        "watcher_name":      watcher_name,
        "adapter_mode":      adapter_mode,      # effective adapter (may be overridden)
        "simulated":         body.adapter_mode_override is not None,
        "event_type":        body.event_type,
        "resource_name":     body.resource_name,
        "runbook_name":      runbook.name if runbook else None,
        "runbook_platform":  getattr(runbook, "platform", "any") if runbook else None,
        "confidence":        float(runbook.confidence) if runbook else None,
        "steps":             steps,
    }


@router.post("/monitoring/watchers/{watcher_name}/checks/seed", tags=["Monitoring"])
def seed_defaults(watcher_name: str, db: Session = Depends(get_session)):
    """
    Seed the factory-default external checks for a watcher.
    Skips any check whose (check_type, target) combination already exists.
    """
    added = 0
    for defaults in DEFAULT_CHECKS:
        exists = db.query(WatcherExternalCheckModel).filter(
            WatcherExternalCheckModel.watcher_name == watcher_name,
            WatcherExternalCheckModel.check_type == defaults["check_type"],
            WatcherExternalCheckModel.target == defaults["target"],
        ).first()
        if not exists:
            db.add(WatcherExternalCheckModel(watcher_name=watcher_name, **defaults))
            added += 1
    db.commit()
    return {"seeded": added, "watcher_name": watcher_name}
