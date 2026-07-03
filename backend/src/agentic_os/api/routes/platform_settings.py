"""
Platform Settings API — runtime configuration stored in PostgreSQL.

GET  /api/settings/watcher        → current watcher thresholds
PUT  /api/settings/watcher        → update thresholds (DB + live-push to watcher)
POST /api/settings/watcher/reset  → restore factory defaults

GET  /api/settings/storm          → storm detection & behaviour settings
PUT  /api/settings/storm          → update storm settings
POST /api/settings/storm/reset    → restore storm factory defaults
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.models import PlatformSettingModel
from agentic_os.security.crypto import encrypt as encrypt_secret, decrypt_if_encrypted

logger = logging.getLogger(__name__)

# public_router  — no auth required (read-only config for internal services)
# router         — requires any valid principal (PUT/POST mutating endpoints)
public_router = APIRouter()
router = APIRouter()

WATCHER_API_URL = "http://watcher_brain:8080"  # legacy fallback


def _get_approved_watcher_kill_urls(db) -> list[str]:
    """Return kill_api_url for every approved watcher, falling back to legacy URL."""
    try:
        from agentic_os.db.models import WatcherRegistrationModel
        rows = db.query(WatcherRegistrationModel).filter_by(registration_status="approved").all()
        urls = [
            (getattr(r, "kill_api_url", "") or f"http://{r.watcher_name}:8080").rstrip("/")
            for r in rows
        ]
        return urls if urls else [WATCHER_API_URL]
    except Exception:
        return [WATCHER_API_URL]

# ── Watcher defaults ──────────────────────────────────────────────────────────

WATCHER_DEFAULTS: list[dict] = [
    {
        "key": "watcher.poll_interval",
        "value": "10",
        "value_type": "int",
        "label": "Poll Interval (s)",
        "description": "How often the watcher checks all containers for anomalies.",
    },
    {
        "key": "watcher.cpu_threshold",
        "value": "80.0",
        "value_type": "float",
        "label": "CPU Alert Threshold (%)",
        "description": "CPU usage percentage that triggers a high-CPU alert.",
    },
    {
        "key": "watcher.memory_threshold",
        "value": "90.0",
        "value_type": "float",
        "label": "Memory Alert Threshold (%)",
        "description": "Memory usage percentage that triggers a memory-surge alert.",
    },
    {
        "key": "watcher.disk_threshold",
        "value": "90.0",
        "value_type": "float",
        "label": "Disk Alert Threshold (%)",
        "description": "Disk usage percentage that triggers a disk-full alert.",
    },
    {
        "key": "watcher.syscall_threshold",
        "value": "20000",
        "value_type": "int",
        "label": "Syscall Anomaly Threshold",
        "description": "Syscalls per 5-second window that indicates a process anomaly.",
    },
    {
        "key": "watcher.connection_threshold",
        "value": "1000",
        "value_type": "int",
        "label": "Network Connection Threshold",
        "description": "Active network connections count that triggers an alert.",
    },
    {
        "key": "watcher.cooldown_seconds",
        "value": "60",
        "value_type": "int",
        "label": "Incident Cooldown (s)",
        "description": "Seconds to wait before re-opening an incident for the same resource after it clears.",
    },
    {
        "key": "watcher.min_consecutive_polls",
        "value": "3",
        "value_type": "int",
        "label": "Min Consecutive Polls",
        "description": "Number of consecutive polls showing the same anomaly before opening an incident. Filters transient spikes.",
    },
    {
        "key": "watcher.discovery_enabled",
        "value": "true",
        "value_type": "bool",
        "label": "CMDB Discovery Enabled",
        "description": "Automatically discover and update the CMDB with live container configuration.",
    },
    {
        "key": "watcher.discovery_interval_polls",
        "value": "15",
        "value_type": "int",
        "label": "Discovery Interval (polls)",
        "description": "Run CMDB discovery every N poll cycles.",
    },
]


def seed_watcher_defaults(db: Session) -> None:
    """Insert watcher defaults if they don't already exist."""
    for item in WATCHER_DEFAULTS:
        existing = db.get(PlatformSettingModel, item["key"])
        if existing is None:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="watcher",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()


# ── Type coercion helper ──────────────────────────────────────────────────────

def _coerce(value: str, value_type: str):
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() in ("true", "1", "yes")
    return value


def _row_to_dict(row: PlatformSettingModel) -> dict:
    return {
        "key": row.key,
        "value": _coerce(row.value, row.value_type),
        "value_type": row.value_type,
        "label": row.label,
        "description": row.description,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class WatcherSettingsUpdate(BaseModel):
    poll_interval: Optional[int] = None
    cpu_threshold: Optional[float] = None
    memory_threshold: Optional[float] = None
    disk_threshold: Optional[float] = None
    syscall_threshold: Optional[int] = None
    connection_threshold: Optional[int] = None
    cooldown_seconds: Optional[int] = None
    min_consecutive_polls: Optional[int] = None
    discovery_enabled: Optional[bool] = None
    discovery_interval_polls: Optional[int] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@public_router.get("/settings/watcher")
def get_watcher_settings(db: Session = Depends(get_session)):
    """Return all watcher settings with metadata. Public — read-only, non-sensitive config."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "watcher"
    ).all()

    if not rows:
        seed_watcher_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "watcher"
        ).all()

    return {
        "category": "watcher",
        "settings": [_row_to_dict(r) for r in rows],
    }


@router.put("/settings/watcher")
async def update_watcher_settings(
    payload: WatcherSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update watcher settings in DB and push live to the running watcher."""
    field_to_key = {
        "poll_interval":          "watcher.poll_interval",
        "cpu_threshold":          "watcher.cpu_threshold",
        "memory_threshold":       "watcher.memory_threshold",
        "disk_threshold":         "watcher.disk_threshold",
        "syscall_threshold":      "watcher.syscall_threshold",
        "connection_threshold":   "watcher.connection_threshold",
        "cooldown_seconds":       "watcher.cooldown_seconds",
        "min_consecutive_polls":  "watcher.min_consecutive_polls",
        "discovery_enabled":      "watcher.discovery_enabled",
        "discovery_interval_polls": "watcher.discovery_interval_polls",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    applied: dict = {}
    for field, new_value in updates.items():
        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            # Auto-create if seed hasn't run
            defaults = {d["key"]: d for d in WATCHER_DEFAULTS}
            meta = defaults.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="watcher",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        row.value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        applied[field] = new_value

    db.commit()

    # Live-push to all approved watchers (non-fatal if any watcher is down)
    watcher_applied = False
    _watcher_urls = _get_approved_watcher_kill_urls(db)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            for _wurl in _watcher_urls:
                try:
                    resp = await client.put(f"{_wurl}/config", json=applied)
                    if resp.status_code == 200:
                        watcher_applied = True
                    else:
                        logger.warning(f"Watcher config push to {_wurl} returned {resp.status_code}")
                except Exception as _exc:
                    logger.warning(f"Could not push config to {_wurl}: {_exc}")
    except Exception as exc:
        logger.warning(f"Could not push config to watchers: {exc}")

    return {
        "saved": applied,
        "watcher_live_applied": watcher_applied,
        "message": (
            "Settings saved and applied to running watcher."
            if watcher_applied
            else "Settings saved. Will apply to watcher on next poll cycle."
        ),
    }


@router.post("/settings/watcher/reset")
async def reset_watcher_settings(db: Session = Depends(get_session)):
    """Restore all watcher settings to factory defaults."""
    for item in WATCHER_DEFAULTS:
        row = db.get(PlatformSettingModel, item["key"])
        if row:
            row.value = item["value"]
        else:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="watcher",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()

    # Push defaults to all approved live watchers
    default_payload = {
        d["key"].split(".", 1)[1]: _coerce(d["value"], d["value_type"])
        for d in WATCHER_DEFAULTS
    }
    _reset_urls = _get_approved_watcher_kill_urls(db)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            for _wurl in _reset_urls:
                try:
                    await client.put(f"{_wurl}/config", json=default_payload)
                except Exception as _exc:
                    logger.warning(f"Could not push reset config to {_wurl}: {_exc}")
    except Exception as exc:
        logger.warning(f"Could not push reset config to watchers: {exc}")

    return {"message": "Watcher settings reset to defaults.", "defaults": default_payload}


# ═══════════════════════════════════════════════════════════════════════════════
# Storm Agent Settings
# ═══════════════════════════════════════════════════════════════════════════════

STORM_DEFAULTS: list[dict] = [
    # ── Detection parameters ─────────────────────────────────────────────────
    {
        "key": "storm.enabled",
        "value": "true",
        "value_type": "bool",
        "label": "Storm Detection Enabled",
        "description": (
            "Enable the Storm Agent. When disabled, correlated events are processed "
            "individually through the standard 7-agent pipeline."
        ),
    },
    {
        "key": "storm.window_seconds",
        "value": "120",
        "value_type": "int",
        "label": "Detection Window (s)",
        "description": (
            "Look-back window in seconds. Incidents created within this window are "
            "considered for correlation. Increase for slower-developing storms."
        ),
    },
    {
        "key": "storm.min_incidents",
        "value": "3",
        "value_type": "int",
        "label": "Minimum Incidents",
        "description": (
            "Minimum number of incidents required in the detection window to trigger "
            "a storm. Lower values increase sensitivity; higher values reduce false positives."
        ),
    },
    {
        "key": "storm.min_resources",
        "value": "2",
        "value_type": "int",
        "label": "Minimum Affected Resources",
        "description": (
            "Minimum number of distinct resources that must be affected. Ensures storms "
            "are truly multi-resource events, not a single noisy service."
        ),
    },
    {
        "key": "storm.merge_window_minutes",
        "value": "5",
        "value_type": "int",
        "label": "Storm Merge Window (min)",
        "description": (
            "Time window in minutes during which concurrent storm detections are merged "
            "into a single storm parent rather than creating duplicate storms."
        ),
    },
    # ── Behaviour parameters ─────────────────────────────────────────────────
    {
        "key": "storm.require_cab_approval",
        "value": "true",
        "value_type": "bool",
        "label": "Require CAB Approval",
        "description": (
            "Storm parent incidents always require Change Advisory Board approval "
            "before coordinated remediation proceeds."
        ),
    },
    {
        "key": "storm.auto_hold_children",
        "value": "true",
        "value_type": "bool",
        "label": "Auto-Hold Child Incidents",
        "description": (
            "Automatically place child incidents in storm_hold state, suppressing "
            "individual pipeline processing until the storm is resolved or released."
        ),
    },
    {
        "key": "storm.llm_hypothesis_enabled",
        "value": "true",
        "value_type": "bool",
        "label": "LLM Root Cause Hypothesis",
        "description": (
            "Use the configured LLM provider to generate a natural-language root cause "
            "hypothesis. Falls back to rule-based hypothesis when disabled or if the "
            "LLM is unavailable."
        ),
    },
    {
        "key": "storm.neo4j_topology_enabled",
        "value": "true",
        "value_type": "bool",
        "label": "Neo4j Topology Analysis",
        "description": (
            "Query the Neo4j CMDB to identify shared upstream dependencies as root "
            "cause candidates. Disable if Neo4j is not deployed or unavailable."
        ),
    },
    {
        "key": "storm.pipeline_hold_seconds",
        "value": "0",
        "value_type": "int",
        "label": "Pipeline Hold (Storm Buffer)",
        "description": (
            "Seconds to delay the incident processing pipeline after creation, giving storm "
            "detection time to fire and group the incident before any enrichment, approval, "
            "or remediation begins. 0 = no delay (default). Recommended: 30–120 seconds for "
            "environments where correlated storms are common and false individual remediations "
            "are a concern. When an incident is adopted into a storm during the hold window, "
            "the pipeline exits immediately without executing any remediation steps."
        ),
    },
    {
        "key": "storm.exclude_external_events",
        "value": "false",
        "value_type": "bool",
        "label": "Exclude External Connector Events",
        "description": (
            "When enabled, incidents from external connectors (Splunk, Datadog, Dynatrace, "
            "PagerDuty, Zabbix, etc.) are excluded from storm detection entirely. "
            "Use this to prevent bulk syncs or historical imports from triggering false storms. "
            "For finer control, configure allow_storm_detection per connector instead. "
            "Internal watcher events are never affected by this setting."
        ),
    },
]


def seed_storm_defaults(db: Session) -> None:
    """Insert storm defaults if they don't already exist."""
    for item in STORM_DEFAULTS:
        existing = db.get(PlatformSettingModel, item["key"])
        if existing is None:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="storm",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()


# ── Pydantic schema ───────────────────────────────────────────────────────────

class StormSettingsUpdate(BaseModel):
    # Detection parameters
    enabled:               Optional[bool]  = None
    window_seconds:        Optional[int]   = None
    min_incidents:         Optional[int]   = None
    min_resources:         Optional[int]   = None
    merge_window_minutes:  Optional[int]   = None
    # Behaviour parameters
    require_cab_approval:   Optional[bool] = None
    auto_hold_children:     Optional[bool] = None
    llm_hypothesis_enabled: Optional[bool] = None
    neo4j_topology_enabled: Optional[bool] = None
    # Storm buffer — delay incident pipeline to let detection win
    pipeline_hold_seconds:    Optional[int]  = None
    # External event exclusion
    exclude_external_events:  Optional[bool] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@public_router.get("/settings/storm")
def get_storm_settings(db: Session = Depends(get_session)):
    """Return all Storm Agent settings with metadata. Public — read-only, non-sensitive config."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "storm"
    ).all()

    if not rows:
        seed_storm_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "storm"
        ).all()

    return {
        "category": "storm",
        "settings": [_row_to_dict(r) for r in rows],
    }


@router.put("/settings/storm")
def update_storm_settings(
    payload: StormSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update Storm Agent settings in DB."""
    field_to_key = {
        "enabled":                "storm.enabled",
        "window_seconds":         "storm.window_seconds",
        "min_incidents":          "storm.min_incidents",
        "min_resources":          "storm.min_resources",
        "merge_window_minutes":   "storm.merge_window_minutes",
        "require_cab_approval":   "storm.require_cab_approval",
        "auto_hold_children":     "storm.auto_hold_children",
        "llm_hypothesis_enabled": "storm.llm_hypothesis_enabled",
        "neo4j_topology_enabled": "storm.neo4j_topology_enabled",
        "pipeline_hold_seconds":   "storm.pipeline_hold_seconds",
        "exclude_external_events": "storm.exclude_external_events",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    applied: dict = {}
    defaults_index = {d["key"]: d for d in STORM_DEFAULTS}

    for field, new_value in updates.items():
        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            meta = defaults_index.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="storm",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        row.value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        applied[field] = new_value

    db.commit()

    return {
        "saved": applied,
        "message": "Storm Agent settings saved successfully.",
    }


@router.post("/settings/storm/reset")

def reset_storm_settings(db: Session = Depends(get_session)):
    """Restore all Storm Agent settings to factory defaults."""
    for item in STORM_DEFAULTS:
        row = db.get(PlatformSettingModel, item["key"])
        if row:
            row.value = item["value"]
        else:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="storm",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()

    return {
        "message": "Storm Agent settings reset to defaults.",
        "defaults": {
            d["key"].split(".", 1)[1]: _coerce(d["value"], d["value_type"])
            for d in STORM_DEFAULTS
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# General / Application Settings
# ═══════════════════════════════════════════════════════════════════════════════

GENERAL_DEFAULTS: list[dict] = [
    {
        "key": "general.session_timeout_minutes",
        "value": "480",
        "value_type": "int",
        "label": "Session Timeout (minutes)",
        "description": "How long a login session remains valid. Applies to all new logins after saving.",
    },
    {
        "key": "general.data_retention_days",
        "value": "90",
        "value_type": "int",
        "label": "Data Retention (days)",
        "description": "Resolved incidents older than this are deleted by the nightly cleanup task.",
    },
    {
        "key": "general.cache_enabled",
        "value": "true",
        "value_type": "bool",
        "label": "Enable Caching",
        "description": "Cache frequently-read configuration (risk weights, etc.) in memory to reduce DB load.",
    },
    {
        "key": "general.in_app_alerts",
        "value": "true",
        "value_type": "bool",
        "label": "In-App Alerts",
        "description": "Show in-app notification banners for new incidents and approvals.",
    },
    {
        "key": "general.sound_alerts",
        "value": "false",
        "value_type": "bool",
        "label": "Sound Alerts",
        "description": "Play an audio notification when a new critical incident is created.",
    },
    {
        "key": "general.env_name",
        "value": "development",
        "value_type": "str",
        "label": "Environment Name",
        "description": "Identifies this deployment environment. Shown in the UI header.",
    },
    {
        "key": "general.debug_mode",
        "value": "false",
        "value_type": "bool",
        "label": "Debug Mode",
        "description": "Enable verbose backend logging for troubleshooting. Not recommended in production.",
    },
    {
        "key": "general.email_notifications_enabled",
        "value": "false",
        "value_type": "bool",
        "label": "Email Notifications",
        "description": "Send email notifications for P1/P2 incidents. Requires SMTP configuration.",
    },
    {
        "key": "general.backup_retention_days",
        "value": "7",
        "value_type": "int",
        "label": "Backup Retention (days)",
        "description": "How many days of backup files to keep on disk. Older files are deleted automatically after each backup run.",
    },
    {
        "key": "general.backup_enabled",
        "value": "true",
        "value_type": "bool",
        "label": "Automatic Backups",
        "description": "Enable scheduled rotating backups of PostgreSQL, Neo4j CMDB, and watcher config. When enabled, backups run daily at the scheduled time.",
    },
    {
        "key": "general.backup_schedule",
        "value": "0 1 * * *",
        "value_type": "str",
        "label": "Backup Schedule (Cron)",
        "description": "Cron expression for backup timing (UTC). Default: '0 1 * * *' = 01:00 UTC daily. Format: minute hour day month day_of_week",
    },
]


def seed_general_defaults(db: Session) -> None:
    """Insert general defaults if they don't already exist."""
    for item in GENERAL_DEFAULTS:
        existing = db.get(PlatformSettingModel, item["key"])
        if existing is None:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="general",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()


# ── Pydantic schema ───────────────────────────────────────────────────────────

class GeneralSettingsUpdate(BaseModel):
    session_timeout_minutes:     Optional[int]  = None
    data_retention_days:         Optional[int]  = None
    cache_enabled:               Optional[bool] = None
    in_app_alerts:               Optional[bool] = None
    sound_alerts:                Optional[bool] = None
    env_name:                    Optional[str]  = None
    debug_mode:                  Optional[bool] = None
    email_notifications_enabled: Optional[bool] = None
    backup_retention_days:       Optional[int]  = None
    backup_enabled:              Optional[bool] = None  # Enable/disable scheduled backups
    backup_schedule:             Optional[str]  = None  # Cron expression for backup timing


# ── Routes ────────────────────────────────────────────────────────────────────

@public_router.get("/settings/general")
def get_general_settings(db: Session = Depends(get_session)):
    """Return all general application settings. Public — non-sensitive config."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "general"
    ).all()

    if not rows:
        seed_general_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "general"
        ).all()

    return {
        "category": "general",
        "settings": [_row_to_dict(r) for r in rows],
    }


@router.put("/settings/general")
def update_general_settings(
    payload: GeneralSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update general application settings in DB."""
    field_to_key = {
        "session_timeout_minutes":    "general.session_timeout_minutes",
        "data_retention_days":        "general.data_retention_days",
        "cache_enabled":              "general.cache_enabled",
        "in_app_alerts":              "general.in_app_alerts",
        "sound_alerts":               "general.sound_alerts",
        "env_name":                   "general.env_name",
        "debug_mode":                 "general.debug_mode",
        "email_notifications_enabled": "general.email_notifications_enabled",
        "backup_retention_days":       "general.backup_retention_days",
        "backup_enabled":             "general.backup_enabled",
        "backup_schedule":            "general.backup_schedule",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    applied: dict = {}
    defaults_index = {d["key"]: d for d in GENERAL_DEFAULTS}

    for field, new_value in updates.items():
        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            meta = defaults_index.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="general",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        row.value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        applied[field] = new_value

    db.commit()

    return {
        "saved": applied,
        "message": "General settings saved successfully.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SMTP / Email Settings
# ═══════════════════════════════════════════════════════════════════════════════

SMTP_DEFAULTS: list[dict] = [
    {
        "key": "smtp.host",
        "value": "",
        "value_type": "str",
        "label": "SMTP Host",
        "description": "Hostname or IP of your outbound mail server (e.g. smtp.gmail.com).",
    },
    {
        "key": "smtp.port",
        "value": "587",
        "value_type": "int",
        "label": "SMTP Port",
        "description": "Port used to connect to the mail server. 587 for STARTTLS, 465 for SSL.",
    },
    {
        "key": "smtp.username",
        "value": "",
        "value_type": "str",
        "label": "SMTP Username",
        "description": "Login username for SMTP authentication.",
    },
    {
        "key": "smtp.password",
        "value": "",
        "value_type": "str",
        "label": "SMTP Password",
        "description": "Login password for SMTP authentication.",
    },
    {
        "key": "smtp.from_address",
        "value": "",
        "value_type": "str",
        "label": "From Address",
        "description": "Sender email address shown to recipients (e.g. alerts@yourcompany.com).",
    },
    {
        "key": "smtp.use_tls",
        "value": "true",
        "value_type": "bool",
        "label": "Use STARTTLS",
        "description": "Upgrade the connection to TLS after connecting. Required for port 587.",
    },
    {
        "key": "smtp.notification_on_p1",
        "value": "true",
        "value_type": "bool",
        "label": "Notify on P1 Incidents",
        "description": "Send an email notification when a P1 (critical) incident is created.",
    },
    {
        "key": "smtp.notification_on_p2",
        "value": "false",
        "value_type": "bool",
        "label": "Notify on P2 Incidents",
        "description": "Send an email notification when a P2 (high) incident is created.",
    },
    {
        "key": "smtp.to_addresses",
        "value": "",
        "value_type": "str",
        "label": "Alert Recipients",
        "description": "Comma-separated list of email addresses that receive incident and approval alerts.",
    },
]


def seed_smtp_defaults(db: Session) -> None:
    """Insert SMTP defaults if they don't already exist."""
    for item in SMTP_DEFAULTS:
        existing = db.get(PlatformSettingModel, item["key"])
        if existing is None:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="smtp",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()


# ── Pydantic schema ───────────────────────────────────────────────────────────

class SmtpSettingsUpdate(BaseModel):
    host:               Optional[str]  = None
    port:               Optional[int]  = None
    username:           Optional[str]  = None
    password:           Optional[str]  = None
    from_address:       Optional[str]  = None
    use_tls:            Optional[bool] = None
    notification_on_p1: Optional[bool] = None
    notification_on_p2: Optional[bool] = None
    to_addresses:       Optional[str]  = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/settings/smtp")
def get_smtp_settings(db: Session = Depends(get_session)):
    """Return SMTP settings with password masked. Auth required."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "smtp"
    ).all()

    if not rows:
        seed_smtp_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "smtp"
        ).all()

    def _smtp_row(r: PlatformSettingModel) -> dict:
        d = _row_to_dict(r)
        if r.key == "smtp.password":
            d["value"] = "••••••••" if r.value else ""
            d["is_set"] = bool(r.value)
        return d

    return {
        "category": "smtp",
        "settings": [_smtp_row(r) for r in rows],
    }


@router.put("/settings/smtp")
def update_smtp_settings(
    payload: SmtpSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update SMTP settings in DB."""
    field_to_key = {
        "host":               "smtp.host",
        "port":               "smtp.port",
        "username":           "smtp.username",
        "password":           "smtp.password",
        "from_address":       "smtp.from_address",
        "use_tls":            "smtp.use_tls",
        "notification_on_p1": "smtp.notification_on_p1",
        "notification_on_p2": "smtp.notification_on_p2",
        "to_addresses":       "smtp.to_addresses",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    applied: dict = {}
    defaults_index = {d["key"]: d for d in SMTP_DEFAULTS}

    for field, new_value in updates.items():
        # Skip password update if placeholder value sent back unchanged
        if field == "password" and str(new_value) == "••••••••":
            continue
        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            meta = defaults_index.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="smtp",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        new_str_value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        row.value = encrypt_secret(new_str_value) if field == "password" else new_str_value
        applied[field] = "***" if field == "password" else new_value

    db.commit()

    return {
        "saved": applied,
        "message": "SMTP settings saved successfully.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Slack ChatOps Settings
# ═══════════════════════════════════════════════════════════════════════════════

SLACK_DEFAULTS: list[dict] = [
    {
        "key": "slack.app_token",
        "value": "",
        "value_type": "str",
        "label": "App-Level Token",
        "description": "Socket Mode app-level token (starts with xapp-). Required for inbound chat without a public URL. Generate at api.slack.com/apps → Basic Information → App-Level Tokens (scope: connections:write).",
    },
    {
        "key": "slack.enabled",
        "value": "false",
        "value_type": "bool",
        "label": "Slack ChatOps Enabled",
        "description": "Enable the Slack ChatOps integration. Requires a valid bot token and signing secret.",
    },
    {
        "key": "slack.bot_token",
        "value": "",
        "value_type": "str",
        "label": "Bot Token",
        "description": "Slack Bot User OAuth Token (xoxb-…). Required to post messages.",
    },
    {
        "key": "slack.signing_secret",
        "value": "",
        "value_type": "str",
        "label": "Signing Secret",
        "description": "Slack app signing secret. Used to verify requests originate from Slack.",
    },
    {
        "key": "slack.default_channel",
        "value": "",
        "value_type": "str",
        "label": "Default Channel",
        "description": "Optional. Channel ID or name for proactive notifications (e.g. #incidents). Leave blank to disable proactive posts.",
    },
    {
        "key": "slack.notify_on_new_incident",
        "value": "false",
        "value_type": "bool",
        "label": "Notify on New Incidents",
        "description": "Post a message to the default channel when a new incident is created.",
    },
    {
        "key": "slack.notify_on_storm_detected",
        "value": "true",
        "value_type": "bool",
        "label": "Notify on Storm Detected",
        "description": "Post a Slack alert when the Storm Agent groups incidents into a new event storm.",
    },
    {
        "key": "slack.notify_on_approval_required",
        "value": "true",
        "value_type": "bool",
        "label": "Notify on Approval Required",
        "description": "Post an interactive approval request to the default channel when an incident needs approval.",
    },
    {
        "key": "slack.notify_on_incident_resolved",
        "value": "true",
        "value_type": "bool",
        "label": "Notify on Incident Resolved",
        "description": "Post a Slack message when an incident reaches a terminal state (resolved, deployed, rolled back, or rejected).",
    },
]

_SLACK_MASKED_KEYS = {"slack.bot_token", "slack.signing_secret", "slack.app_token"}


def seed_slack_defaults(db: Session) -> None:
    """Insert Slack defaults if they don't already exist.
    Uses a try/except on the commit so two uvicorn workers racing on the same
    new keys don't raise an unhandled UniqueViolation.
    """
    try:
        for item in SLACK_DEFAULTS:
            existing = db.get(PlatformSettingModel, item["key"])
            if existing is None:
                db.add(PlatformSettingModel(
                    key=item["key"],
                    value=item["value"],
                    value_type=item["value_type"],
                    category="slack",
                    label=item["label"],
                    description=item["description"],
                ))
        db.commit()
    except Exception:
        db.rollback()   # Another worker won the race — DB is already correct


# ── Pydantic schema ───────────────────────────────────────────────────────────

class SlackSettingsUpdate(BaseModel):
    app_token:                   Optional[str]  = None
    enabled:                     Optional[bool] = None
    bot_token:                   Optional[str]  = None
    signing_secret:              Optional[str]  = None
    default_channel:             Optional[str]  = None
    notify_on_new_incident:      Optional[bool] = None
    notify_on_storm_detected:    Optional[bool] = None
    notify_on_approval_required: Optional[bool] = None
    notify_on_incident_resolved: Optional[bool] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/settings/slack")
def get_slack_settings(db: Session = Depends(get_session)):
    """Return Slack settings with sensitive fields masked. Auth required."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "slack"
    ).all()

    if not rows:
        seed_slack_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "slack"
        ).all()

    def _slack_row(r: PlatformSettingModel) -> dict:
        d = _row_to_dict(r)
        if r.key in _SLACK_MASKED_KEYS:
            d["value"]  = "••••••••" if r.value else ""
            d["is_set"] = bool(r.value)
        return d

    return {
        "category": "slack",
        "settings": [_slack_row(r) for r in rows],
    }


@router.put("/settings/slack")
def update_slack_settings(
    payload: SlackSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update Slack ChatOps settings in DB."""
    field_to_key = {
        "app_token":                  "slack.app_token",
        "enabled":                    "slack.enabled",
        "bot_token":                  "slack.bot_token",
        "signing_secret":             "slack.signing_secret",
        "default_channel":            "slack.default_channel",
        "notify_on_new_incident":      "slack.notify_on_new_incident",
        "notify_on_storm_detected":    "slack.notify_on_storm_detected",
        "notify_on_approval_required": "slack.notify_on_approval_required",
        "notify_on_incident_resolved": "slack.notify_on_incident_resolved",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    applied: dict = {}
    defaults_index = {d["key"]: d for d in SLACK_DEFAULTS}

    _slack_secret_fields = ("bot_token", "signing_secret", "app_token")

    for field, new_value in updates.items():
        # Skip masked placeholder values sent back unchanged
        if field in _slack_secret_fields and str(new_value) == "••••••••":
            continue

        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            meta = defaults_index.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="slack",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        new_str_value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        row.value = encrypt_secret(new_str_value) if field in _slack_secret_fields else new_str_value
        applied[field] = "***" if field in _slack_secret_fields else new_value

    db.commit()

    return {
        "saved": applied,
        "message": "Slack settings saved successfully.",
    }


@router.post("/settings/slack/test")
async def test_slack_connection(db: Session = Depends(get_session)):
    """Verify the Slack bot token by calling auth.test."""
    import os

    token_row = db.get(PlatformSettingModel, "slack.bot_token")
    token = decrypt_if_encrypted(token_row.value if token_row else "") or os.getenv("SLACK_BOT_TOKEN", "")

    if not token:
        raise HTTPException(status_code=400, detail="No Slack bot token configured.")

    try:
        from slack_sdk import WebClient  # type: ignore[import]
        resp = WebClient(token=token).auth_test()
        return {
            "ok": True,
            "bot_user": resp.get("user"),
            "workspace": resp.get("team"),
            "message": f"Connected as @{resp.get('user')} in workspace '{resp.get('team')}'.",
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="slack-sdk not installed. Rebuild the Docker image.")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Slack auth.test failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Platform Intelligence Settings
# ═══════════════════════════════════════════════════════════════════════════════

PLATFORM_INTELLIGENCE_DEFAULTS: list[dict] = [
    {
        "key": "platform_intelligence.auto_apply_enabled",
        "value": "false",
        "value_type": "bool",
        "label": "Auto-Apply Trusted Recommendations",
        "description": (
            "When enabled, a recommendation parameter that has earned trust (consecutive "
            "accepted+applied cycles independently verified to have improved their metric) "
            "applies itself on future analysis runs instead of waiting for manual review. "
            "Disabled by default — recommendations always wait for human accept/reject until "
            "you turn this on. A verified regression after auto-apply reverts that parameter "
            "to mandatory review automatically, with or without this setting."
        ),
    },
    {
        "key": "platform_intelligence.auto_apply_min_cycles",
        "value": "3",
        "value_type": "int",
        "label": "Trust Threshold (cycles)",
        "description": (
            "Number of consecutive accepted+applied+verified-improved cycles a parameter "
            "needs before it's eligible to auto-apply. Only takes effect when Auto-Apply is enabled."
        ),
    },
    {
        "key": "platform_intelligence.verification_delay_days",
        "value": "7",
        "value_type": "int",
        "label": "Verification Delay (days)",
        "description": (
            "How many days to wait after a recommendation is applied before checking whether "
            "its targeted metric actually improved. Runs daily via a scheduled task regardless "
            "of whether Auto-Apply is enabled, so trust/regression tracking stays accurate."
        ),
    },
    {
        "key": "platform_intelligence.analysis_schedule_enabled",
        "value": "false",
        "value_type": "bool",
        "label": "Scheduled Analysis",
        "description": (
            "When enabled, analysis runs automatically on the schedule below, in addition to "
            "the \"Run Analysis Now\" button. Off by default — analysis only runs on-demand "
            "until you turn this on. Note: changing the cron expression below requires a "
            "backend/celery_beat restart to take effect, the same as other scheduled settings "
            "in this platform (e.g. backup schedule)."
        ),
    },
    {
        "key": "platform_intelligence.analysis_schedule",
        "value": "0 6 * * *",
        "value_type": "str",
        "label": "Analysis Schedule (Cron)",
        "description": (
            "Cron expression for automatic analysis timing (UTC). Default: '0 6 * * *' = "
            "06:00 UTC daily. Format: minute hour day month day_of_week."
        ),
    },
]


def seed_platform_intelligence_defaults(db: Session) -> None:
    """Insert Platform Intelligence defaults if they don't already exist."""
    for item in PLATFORM_INTELLIGENCE_DEFAULTS:
        existing = db.get(PlatformSettingModel, item["key"])
        if existing is None:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="platform_intelligence",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()


# ── Pydantic schema ───────────────────────────────────────────────────────────

class PlatformIntelligenceSettingsUpdate(BaseModel):
    auto_apply_enabled:         Optional[bool] = None
    auto_apply_min_cycles:      Optional[int]  = None
    verification_delay_days:    Optional[int]  = None
    analysis_schedule_enabled:  Optional[bool] = None
    analysis_schedule:          Optional[str]  = None


# ── Routes ────────────────────────────────────────────────────────────────────

@public_router.get("/settings/platform-intelligence")
def get_platform_intelligence_settings(db: Session = Depends(get_session)):
    """Return Platform Intelligence settings. Public — read-only, non-sensitive config."""
    rows = db.query(PlatformSettingModel).filter(
        PlatformSettingModel.category == "platform_intelligence"
    ).all()

    if not rows:
        seed_platform_intelligence_defaults(db)
        rows = db.query(PlatformSettingModel).filter(
            PlatformSettingModel.category == "platform_intelligence"
        ).all()

    return {
        "category": "platform_intelligence",
        "settings": [_row_to_dict(r) for r in rows],
    }


@router.put("/settings/platform-intelligence")
def update_platform_intelligence_settings(
    payload: PlatformIntelligenceSettingsUpdate,
    db: Session = Depends(get_session),
):
    """Update Platform Intelligence settings in DB."""
    field_to_key = {
        "auto_apply_enabled":         "platform_intelligence.auto_apply_enabled",
        "auto_apply_min_cycles":      "platform_intelligence.auto_apply_min_cycles",
        "verification_delay_days":    "platform_intelligence.verification_delay_days",
        "analysis_schedule_enabled":  "platform_intelligence.analysis_schedule_enabled",
        "analysis_schedule":          "platform_intelligence.analysis_schedule",
    }

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    if "analysis_schedule" in updates:
        cron_parts = updates["analysis_schedule"].strip().split()
        if len(cron_parts) != 5:
            raise HTTPException(
                status_code=400,
                detail="analysis_schedule must be a 5-field cron expression: minute hour day month day_of_week",
            )

    applied: dict = {}
    defaults_index = {d["key"]: d for d in PLATFORM_INTELLIGENCE_DEFAULTS}

    for field, new_value in updates.items():
        db_key = field_to_key[field]
        row = db.get(PlatformSettingModel, db_key)
        if row is None:
            meta = defaults_index.get(db_key, {})
            row = PlatformSettingModel(
                key=db_key,
                category="platform_intelligence",
                value_type=meta.get("value_type", "str"),
                label=meta.get("label", field),
                description=meta.get("description", ""),
            )
            db.add(row)

        row.value = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
        applied[field] = new_value

    db.commit()

    return {
        "saved": applied,
        "message": "Platform Intelligence settings saved successfully.",
    }


@router.post("/settings/platform-intelligence/reset")
def reset_platform_intelligence_settings(db: Session = Depends(get_session)):
    """Restore all Platform Intelligence settings to factory defaults (auto-apply disabled)."""
    for item in PLATFORM_INTELLIGENCE_DEFAULTS:
        row = db.get(PlatformSettingModel, item["key"])
        if row:
            row.value = item["value"]
        else:
            db.add(PlatformSettingModel(
                key=item["key"],
                value=item["value"],
                value_type=item["value_type"],
                category="platform_intelligence",
                label=item["label"],
                description=item["description"],
            ))
    db.commit()

    return {
        "message": "Platform Intelligence settings reset to defaults.",
        "defaults": {
            d["key"].split(".", 1)[1]: _coerce(d["value"], d["value_type"])
            for d in PLATFORM_INTELLIGENCE_DEFAULTS
        },
    }
