"""
Log Monitors API — configuration for log file pattern monitoring per watcher.

GET    /api/monitoring/watchers/{watcher_name}/log-monitors          → list monitors
POST   /api/monitoring/watchers/{watcher_name}/log-monitors          → create monitor
PUT    /api/monitoring/watchers/{watcher_name}/log-monitors/{id}     → update monitor
DELETE /api/monitoring/watchers/{watcher_name}/log-monitors/{id}     → delete monitor
POST   /api/monitoring/log-monitors/validate-pattern                 → validate regex pattern
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
import httpx
import asyncio

from agentic_os.db.database import get_session
from agentic_os.db.models import LogMonitorConfigModel, WatcherRegistrationModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LogMonitorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source: Literal["file", "docker"] = "file"
    file: str = Field(default="", max_length=500)
    container: str = Field(default="", max_length=200)
    pattern: str = Field(..., min_length=1, max_length=1000)
    event_type: str = Field(..., min_length=1, max_length=100)
    interval_sec: int = Field(default=5, ge=1, le=3600)
    enabled: bool = Field(default=True)

    @model_validator(mode="after")
    def check_source_fields(self) -> "LogMonitorCreate":
        if self.source == "file" and not self.file.strip():
            raise ValueError("file path is required when source is 'file'")
        if self.source == "docker" and not self.container.strip():
            raise ValueError("container name is required when source is 'docker'")
        return self


class LogMonitorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    source: Optional[Literal["file", "docker"]] = None
    file: Optional[str] = Field(None, max_length=500)
    container: Optional[str] = Field(None, max_length=200)
    pattern: Optional[str] = Field(None, min_length=1, max_length=1000)
    event_type: Optional[str] = Field(None, min_length=1, max_length=100)
    interval_sec: Optional[int] = Field(None, ge=1, le=3600)
    enabled: Optional[bool] = None


class PatternValidationRequest(BaseModel):
    pattern: str = Field(..., min_length=1)


class PatternValidationResponse(BaseModel):
    valid: bool
    error: Optional[str] = None


def _validate_regex_pattern(pattern: str) -> tuple[bool, Optional[str]]:
    """Validate that a string is a valid Python regex pattern."""
    try:
        re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        return True, None
    except re.error as e:
        return False, f"Invalid regex: {str(e)}"


def _monitor_to_dict(row: LogMonitorConfigModel) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "watcher_name": row.watcher_name,
        "name": row.name,
        "source": getattr(row, "source", "file"),
        "file": row.file,
        "container": getattr(row, "container", ""),
        "pattern": row.pattern,
        "event_type": row.event_type,
        "interval_sec": row.interval_sec,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _push_config_to_watcher(
    watcher_name: str,
    monitor_configs: List[Dict[str, Any]],
    kill_api_url: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Push log monitor configuration to watcher via kill-api.
    Returns (success, error_message).
    """
    if not kill_api_url:
        kill_api_url = f"http://{watcher_name}:8080"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{kill_api_url}/log-monitors/reload",
                json=monitor_configs,
            )
            if resp.status_code == 200:
                logger.info(f"[MONITORING] Live-pushed {len(monitor_configs)} log monitors to {watcher_name}")
                return True, None
            else:
                error = resp.text or f"HTTP {resp.status_code}"
                logger.warning(f"[MONITORING] Failed to push log monitors to {watcher_name}: {error}")
                return False, error
    except Exception as exc:
        error = str(exc)
        logger.warning(f"[MONITORING] Could not reach {kill_api_url}: {error}")
        return False, error


# ── Log Monitors CRUD ──────────────────────────────────────────────────────────

@router.get("/monitoring/watchers/{watcher_name}/log-monitors", tags=["Monitoring"])
def list_log_monitors(watcher_name: str, db: Session = Depends(get_session)):
    """Return all log monitors for the given watcher (enabled + disabled)."""
    # Verify watcher exists
    watcher = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not watcher:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")

    rows = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.watcher_name == watcher_name
    ).order_by(LogMonitorConfigModel.created_at).all()
    return [_monitor_to_dict(r) for r in rows]


@router.post("/monitoring/watchers/{watcher_name}/log-monitors", status_code=201, tags=["Monitoring"])
async def create_log_monitor(
    watcher_name: str,
    payload: LogMonitorCreate,
    db: Session = Depends(get_session),
):
    """Add a new log monitor for the specified watcher."""
    # Verify watcher exists
    watcher = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not watcher:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")

    # Check name uniqueness within watcher
    existing = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.watcher_name == watcher_name,
        LogMonitorConfigModel.name == payload.name,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Log monitor with name '{payload.name}' already exists for this watcher"
        )

    # Validate regex pattern
    valid, error = _validate_regex_pattern(payload.pattern)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    # Create monitor
    row = LogMonitorConfigModel(
        watcher_name=watcher_name,
        name=payload.name,
        source=payload.source,
        file=payload.file,
        container=payload.container,
        pattern=payload.pattern,
        event_type=payload.event_type,
        interval_sec=payload.interval_sec,
        enabled=payload.enabled,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    logger.info(
        f"[MONITORING] Created log monitor '{row.name}' for {watcher_name} "
        f"(file={row.file}, pattern={row.pattern[:50]}...)"
    )

    # Live-push configuration to watcher (optional — watcher will pick up on next poll if push fails)
    all_monitors = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.watcher_name == watcher_name,
        LogMonitorConfigModel.enabled == True,
    ).all()
    configs_to_push = [
        {
            "name": m.name,
            "source": getattr(m, "source", "file"),
            "file": m.file,
            "container": getattr(m, "container", ""),
            "pattern": m.pattern,
            "event_type": m.event_type,
            "interval_sec": m.interval_sec,
            "enabled": m.enabled,
        }
        for m in all_monitors
    ]

    await _push_config_to_watcher(
        watcher_name,
        configs_to_push,
        getattr(watcher, "kill_api_url", None),
    )

    return _monitor_to_dict(row)


@router.put("/monitoring/watchers/{watcher_name}/log-monitors/{monitor_id}", tags=["Monitoring"])
async def update_log_monitor(
    watcher_name: str,
    monitor_id: str,
    payload: LogMonitorUpdate,
    db: Session = Depends(get_session),
):
    """Update fields on an existing log monitor."""
    # Verify watcher exists
    watcher = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not watcher:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")

    # Find monitor
    row = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.id == monitor_id,
        LogMonitorConfigModel.watcher_name == watcher_name,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Log monitor not found")

    # Check name uniqueness if being updated
    if payload.name and payload.name != row.name:
        existing = db.query(LogMonitorConfigModel).filter(
            LogMonitorConfigModel.watcher_name == watcher_name,
            LogMonitorConfigModel.name == payload.name,
            LogMonitorConfigModel.id != monitor_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Log monitor with name '{payload.name}' already exists for this watcher"
            )

    # Validate regex pattern if being updated
    if payload.pattern:
        valid, error = _validate_regex_pattern(payload.pattern)
        if not valid:
            raise HTTPException(status_code=400, detail=error)

    # Update fields
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)

    logger.info(f"[MONITORING] Updated log monitor {monitor_id} for {watcher_name}")

    # Live-push configuration to watcher (optional — watcher will pick up on next poll if push fails)
    all_monitors = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.watcher_name == watcher_name,
        LogMonitorConfigModel.enabled == True,
    ).all()
    configs_to_push = [
        {
            "name": m.name,
            "source": getattr(m, "source", "file"),
            "file": m.file,
            "container": getattr(m, "container", ""),
            "pattern": m.pattern,
            "event_type": m.event_type,
            "interval_sec": m.interval_sec,
            "enabled": m.enabled,
        }
        for m in all_monitors
    ]

    await _push_config_to_watcher(
        watcher_name,
        configs_to_push,
        getattr(watcher, "kill_api_url", None),
    )

    return _monitor_to_dict(row)


@router.delete("/monitoring/watchers/{watcher_name}/log-monitors/{monitor_id}", status_code=204, tags=["Monitoring"])
async def delete_log_monitor(
    watcher_name: str,
    monitor_id: str,
    db: Session = Depends(get_session),
):
    """Delete a log monitor."""
    # Verify watcher exists
    watcher = db.query(WatcherRegistrationModel).filter(
        WatcherRegistrationModel.watcher_name == watcher_name
    ).first()
    if not watcher:
        raise HTTPException(status_code=404, detail=f"Watcher '{watcher_name}' not found")

    # Find monitor
    row = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.id == monitor_id,
        LogMonitorConfigModel.watcher_name == watcher_name,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Log monitor not found")

    db.delete(row)
    db.commit()

    logger.info(f"[MONITORING] Deleted log monitor {monitor_id} for {watcher_name}")

    # Live-push configuration to watcher (optional — watcher will pick up on next poll if push fails)
    remaining_monitors = db.query(LogMonitorConfigModel).filter(
        LogMonitorConfigModel.watcher_name == watcher_name,
        LogMonitorConfigModel.enabled == True,
    ).all()
    configs_to_push = [
        {
            "name": m.name,
            "source": getattr(m, "source", "file"),
            "file": m.file,
            "container": getattr(m, "container", ""),
            "pattern": m.pattern,
            "event_type": m.event_type,
            "interval_sec": m.interval_sec,
            "enabled": m.enabled,
        }
        for m in remaining_monitors
    ]

    await _push_config_to_watcher(
        watcher_name,
        configs_to_push,
        getattr(watcher, "kill_api_url", None),
    )


# ── Pattern Validation ────────────────────────────────────────────────────────

@router.post("/monitoring/log-monitors/validate-pattern", tags=["Monitoring"])
def validate_pattern(payload: PatternValidationRequest):
    """
    Validate a regex pattern without saving.
    Used by frontend on form blur to provide immediate feedback.
    """
    valid, error = _validate_regex_pattern(payload.pattern)
    return PatternValidationResponse(valid=valid, error=error)
