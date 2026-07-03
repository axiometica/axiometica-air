"""
Connector Hub REST API.

Connector management:
  GET    /api/connectors                          — list all connectors + live status
  GET    /api/connectors/{id}                     — detail + last sync info
  POST   /api/connectors/{id}/config              — save / update config (URL, creds)
  POST   /api/connectors/{id}/test                — test connection → {ok, latency_ms, message}
  POST   /api/connectors/{id}/sync                — trigger manual sync (async Celery task)
  GET    /api/connectors/{id}/sync-logs           — sync history (paginated)

ServiceNow CMDB (reads local cache):
  GET    /api/connectors/servicenow/cmdb                    — summary counts per class
  GET    /api/connectors/servicenow/cmdb/{ci_class}         — paginated CI list
  GET    /api/connectors/servicenow/cmdb/search?q=          — cross-class name search
  GET    /api/connectors/servicenow/cmdb/record/{sys_id}    — single CI detail

ServiceNow Incident push:
  POST   /api/connectors/servicenow/push-incident/{workflow_id}   — create SN incident
  PUT    /api/connectors/servicenow/push-incident/{workflow_id}   — update SN incident
  GET    /api/connectors/servicenow/incident-map/{workflow_id}    — SN ticket info
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agentic_os.db.database import SessionLocal
from agentic_os.db.models import (
    ConnectorConfigModel, SNowSyncLogModel, SNowIncidentMapModel,
)
from agentic_os.connectors.servicenow.client import ServiceNowClient
from agentic_os.connectors.servicenow.cmdb_sync import CMDBSync
from agentic_os.connectors.servicenow.field_maps import CI_CLASSES, get_incident_sync_config
from agentic_os.security.crypto import encrypt_fields, decrypt_fields

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Connectors"])

# config_json keys that hold secrets and must be encrypted at rest
_SECRET_FIELDS = ["password", "token", "webhook_secret", "routing_key"]

# ── Known connector definitions (static metadata) ────────────────────────────

CONNECTOR_DEFS = [
    {
        "id":           "servicenow",
        "display_name": "ServiceNow",
        "description":  "CMDB sync (Services, Offerings, Servers) + Incident push",
        "icon":         "servicenow",
        "version":      "1.0.0",
        "capabilities": ["cmdb_pull", "incident_push"],
    },
    {
        "id":           "splunk",
        "display_name": "Splunk",
        "description":  "Ingest Splunk saved-search alerts as monitoring events via webhook",
        "icon":         "splunk",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "datadog",
        "display_name": "Datadog",
        "description":  "Ingest Datadog monitor alerts as monitoring events via webhook",
        "icon":         "datadog",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "dynatrace",
        "display_name": "Dynatrace",
        "description":  "Ingest Dynatrace problem notifications as monitoring events via webhook",
        "icon":         "dynatrace",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "prometheus",
        "display_name": "Prometheus",
        "description":  "Ingest Prometheus Alertmanager firing alerts as monitoring events via webhook",
        "icon":         "prometheus",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "pagerduty",
        "display_name": "PagerDuty",
        "description":  "Ingest PagerDuty incident triggers as monitoring events, and escalate/notify back to PagerDuty for on-call paging",
        "icon":         "pagerduty",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest", "escalation"],
    },
    {
        "id":           "zabbix",
        "display_name": "Zabbix",
        "description":  "Ingest Zabbix trigger problem notifications as monitoring events via webhook",
        "icon":         "zabbix",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "grafana",
        "display_name": "Grafana",
        "description":  "Ingest Grafana Unified Alerting firing alerts as monitoring events via webhook",
        "icon":         "grafana",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
    {
        "id":           "generic",
        "display_name": "Generic Webhook",
        "description":  "Ingest events from any custom source using a simple documented JSON schema",
        "icon":         "generic",
        "version":      "1.0.0",
        "capabilities": ["alert_ingest"],
    },
]

# Connector IDs that use inbound-webhook-only auth (no base_url / credentials required)
_WEBHOOK_ONLY_CONNECTORS = {"datadog", "dynatrace", "prometheus", "pagerduty", "zabbix", "grafana", "generic"}


# ── Pydantic request / response models ───────────────────────────────────────

class IncidentSyncConfig(BaseModel):
    enabled:               bool       = False
    auto_create:           bool       = True
    auto_update_on_states: list[str]  = ["in_progress", "waiting_approval", "resolved", "failed", "rejected"]
    include_ai_summary:    bool       = True
    append_agent_notes:    bool       = True
    platform_url:          str        = "http://localhost:3000"


class ConnectorConfigRequest(BaseModel):
    base_url:          str
    username:          str
    password:          Optional[str] = None   # blank = keep existing
    sync_interval_min: int = 0
    enabled:           bool = True
    incident_sync:     Optional[IncidentSyncConfig] = None


class IncidentPushRequest(BaseModel):
    title:           str
    summary:         Optional[str] = None
    severity:        Optional[str] = None
    lifecycle_state: Optional[str] = None
    service_name:    Optional[str] = None
    work_notes:      Optional[str] = None


class IncidentUpdateRequest(BaseModel):
    severity:        Optional[str] = None
    lifecycle_state: Optional[str] = None
    title:           Optional[str] = None
    summary:         Optional[str] = None
    work_notes:      Optional[str] = None


class SplunkConfigRequest(BaseModel):
    """Config payload for the Splunk connector (token-based auth)."""
    base_url:               str                    # https://splunk.example.com:8089
    token:                  str                    # Splunk API token
    webhook_secret:          Optional[str] = None   # blank = keep existing; "-" = clear
    default_criticality:     str = "warning"        # warning | critical | info
    default_event_type:      str = "unknown"        # fallback when search name can't be parsed
    enabled:                 bool = True
    allow_auto_remediation:  bool = False           # When False, runbook steps are recommendations only
    allow_storm_detection:   bool = True            # When False, this connector's events cannot trigger storms
    # ↑ Set to False for connectors that push historical or batch-synced alerts —
    #   a bulk import inserting 50 alerts at once would otherwise trigger a false
    #   storm because all events share the same DB insertion timestamp (created_at).


class AlertConnectorConfigRequest(BaseModel):
    """Config payload for pure-inbound webhook connectors (Datadog, Dynatrace, etc.)

    No base_url or credentials required — these connectors only receive POSTs.
    """
    webhook_secret:          Optional[str] = None   # blank = keep existing; "-" = clear
    default_criticality:     str = "warning"
    default_event_type:      str = "unknown"
    enabled:                 bool = True
    allow_auto_remediation:  bool = False           # When False, runbook steps are recommendations only
    allow_storm_detection:   bool = True            # When False, events from this connector cannot trigger storms
    event_type_mappings:     Optional[dict] = None  # {external_name: canonical_platform_type}
    # Outbound — PagerDuty Events API v2 integration key (lets the platform trigger/
    # resolve PagerDuty incidents, not just receive them). Ignored by connectors that
    # have no outbound direction; blank = keep existing; "-" = clear.
    routing_key:             Optional[str] = None


# ── Helper: load connector config from DB ────────────────────────────────────

def _get_config(db, connector_id: str) -> ConnectorConfigModel:
    cfg = db.query(ConnectorConfigModel).filter_by(id=connector_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not configured")
    return cfg


def get_pagerduty_client(db):
    """Return a configured PagerDutyEventsClient, or None if PagerDuty isn't
    enabled or has no routing_key saved. Used by the alert_escalate/alert_update
    runbook actions to actually page on-call instead of failing as unconfigured."""
    cfg = db.query(ConnectorConfigModel).filter_by(id="pagerduty").first()
    if not cfg or not cfg.enabled:
        return None
    routing_key = decrypt_fields(cfg.config_json or {}, _SECRET_FIELDS).get("routing_key")
    if not routing_key:
        return None
    from agentic_os.connectors.pagerduty.events_client import PagerDutyEventsClient
    return PagerDutyEventsClient(routing_key)


def _require_creds(cfg: ConnectorConfigModel) -> dict:
    c = decrypt_fields(cfg.config_json or {}, _SECRET_FIELDS)
    if not c.get("base_url") or not c.get("username") or not c.get("password"):
        raise HTTPException(status_code=422, detail="Connector not configured — save credentials first")
    return c


# ══════════════════════════════════════════════════════════════════════════════
#  Generic connector management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/connectors")
def list_connectors():
    """Return all connector definitions enriched with DB config/status."""
    db = SessionLocal()
    try:
        configs = {c.id: c for c in db.query(ConnectorConfigModel).all()}
        result  = []
        for defn in CONNECTOR_DEFS:
            cfg = configs.get(defn["id"])
            cid = defn["id"]
            # Webhook-only connectors are "configured" as soon as a DB row exists
            if cid in _WEBHOOK_ONLY_CONNECTORS:
                configured = cfg is not None
            else:
                configured = cfg is not None and bool((cfg.config_json or {}).get("base_url"))
            result.append({
                **defn,
                "configured":        configured,
                "enabled":           cfg.enabled if cfg else False,
                "last_sync_at":      cfg.last_sync_at.isoformat() if cfg and cfg.last_sync_at else None,
                "last_sync_status":  cfg.last_sync_status if cfg else "never",
                "sync_interval_min": cfg.sync_interval_min if cfg else 0,
            })
        return result
    finally:
        db.close()


@router.get("/connectors/{connector_id}")
def get_connector(connector_id: str):
    """Return detailed info for a single connector."""
    defn = next((d for d in CONNECTOR_DEFS if d["id"] == connector_id), None)
    if not defn:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")

    db = SessionLocal()
    try:
        cfg = db.query(ConnectorConfigModel).filter_by(id=connector_id).first()

        # Last 5 sync logs
        logs = (
            db.query(SNowSyncLogModel)
            .filter_by(connector_id=connector_id)
            .order_by(SNowSyncLogModel.started_at.desc())
            .limit(5)
            .all()
        )
        recent_logs = [
            {
                "started_at":     l.started_at.isoformat(),
                "finished_at":    l.finished_at.isoformat() if l.finished_at else None,
                "records_pulled": l.records_pulled,
                "status":         l.status,
                "error_message":  l.error_message,
            }
            for l in logs
        ]

        c = cfg.config_json or {} if cfg else {}

        # Webhook-only connectors are configured as soon as a DB row exists
        if connector_id in _WEBHOOK_ONLY_CONNECTORS:
            configured = cfg is not None
        else:
            configured = cfg is not None and bool(c.get("base_url"))

        base: dict[str, Any] = {
            **defn,
            "configured":        configured,
            "enabled":           cfg.enabled if cfg else False,
            "last_sync_at":      cfg.last_sync_at.isoformat() if cfg and cfg.last_sync_at else None,
            "last_sync_status":  cfg.last_sync_status if cfg else "never",
            "sync_interval_min": cfg.sync_interval_min if cfg else 0,
            "recent_sync_logs":  recent_logs,
        }
        if connector_id == "servicenow":
            base.update({
                "base_url":     c.get("base_url", ""),
                "username":     c.get("username", ""),
                "incident_sync": c.get("incident_sync"),
            })
        elif connector_id == "splunk":
            base.update({
                "base_url":               c.get("base_url", ""),
                "default_criticality":    c.get("default_criticality", "warning"),
                "default_event_type":     c.get("default_event_type", "unknown"),
                "webhook_secret_set":     bool(c.get("webhook_secret")),
                "allow_auto_remediation": bool(c.get("allow_auto_remediation", False)),
                "allow_storm_detection":  bool(c.get("allow_storm_detection", True)),
                # Never expose the actual token or secret
            })
        elif connector_id in _WEBHOOK_ONLY_CONNECTORS:
            base.update({
                "default_criticality":    c.get("default_criticality", "warning"),
                "default_event_type":     c.get("default_event_type", "unknown"),
                "webhook_secret_set":     bool(c.get("webhook_secret")),
                "allow_auto_remediation": bool(c.get("allow_auto_remediation", False)),
                "allow_storm_detection":  bool(c.get("allow_storm_detection", True)),
                "event_type_mappings":    c.get("event_type_mappings", {}),
            })
            if connector_id == "pagerduty":
                # Outbound — lets the platform trigger/resolve PagerDuty incidents,
                # not just receive them. Never expose the actual key.
                base["routing_key_set"] = bool(c.get("routing_key"))
        return base
    finally:
        db.close()


# ── Splunk-specific config endpoint (MUST be before the generic {connector_id} route) ─
# FastAPI matches routes in definition order; the parameterised /{connector_id}/config
# route would shadow /splunk/config if it were defined first.

@router.post("/connectors/splunk/config")
def save_splunk_config(body: SplunkConfigRequest):
    """Save Splunk connector credentials and defaults."""
    db = SessionLocal()
    try:
        cfg = db.query(ConnectorConfigModel).filter_by(id="splunk").first()
        existing = cfg.config_json if cfg else {}

        # Webhook secret logic: "-" = clear; "" = keep; <str> = replace
        if body.webhook_secret == "-":
            webhook_secret = None
        elif body.webhook_secret:
            webhook_secret = body.webhook_secret
        else:
            webhook_secret = existing.get("webhook_secret")

        config_json = {
            **existing,
            "base_url":               body.base_url.rstrip("/"),
            "token":                  body.token,
            "default_criticality":    body.default_criticality,
            "default_event_type":     body.default_event_type,
            "allow_auto_remediation": body.allow_auto_remediation,
            "allow_storm_detection":  body.allow_storm_detection,
        }
        if webhook_secret is not None:
            config_json["webhook_secret"] = webhook_secret
        elif "webhook_secret" in config_json:
            del config_json["webhook_secret"]

        config_json = encrypt_fields(config_json, _SECRET_FIELDS)

        if cfg:
            cfg.config_json       = config_json
            cfg.enabled           = body.enabled
            cfg.sync_interval_min = 0
            cfg.updated_at        = datetime.utcnow()
        else:
            cfg = ConnectorConfigModel(
                id            = "splunk",
                display_name  = "Splunk",
                enabled       = body.enabled,
                config_json   = config_json,
                sync_interval_min = 0,
            )
            db.add(cfg)
        db.commit()
        return {"status": "saved", "connector_id": "splunk"}
    finally:
        db.close()


@router.post("/connectors/{connector_id}/config")
def save_connector_config(connector_id: str, body: ConnectorConfigRequest):
    """Save (create or update) connector credentials and settings."""
    defn = next((d for d in CONNECTOR_DEFS if d["id"] == connector_id), None)
    if not defn:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")

    db = SessionLocal()
    try:
        cfg = db.query(ConnectorConfigModel).filter_by(id=connector_id).first()
        # Preserve existing config fields (e.g. incident_sync) not in this request
        existing_json = cfg.config_json if cfg else {}
        config_json = {
            **existing_json,
            "base_url": body.base_url.rstrip("/"),
            "username": body.username,
            **({"password": body.password} if body.password else {}),
        }
        if body.incident_sync is not None:
            config_json["incident_sync"] = body.incident_sync.dict()
        config_json = encrypt_fields(config_json, _SECRET_FIELDS)
        if cfg:
            cfg.config_json        = config_json
            cfg.enabled            = body.enabled
            cfg.sync_interval_min  = body.sync_interval_min
            cfg.updated_at         = datetime.utcnow()
        else:
            cfg = ConnectorConfigModel(
                id                 = connector_id,
                display_name       = defn["display_name"],
                enabled            = body.enabled,
                config_json        = config_json,
                sync_interval_min  = body.sync_interval_min,
            )
            db.add(cfg)
        db.commit()
        return {"status": "saved", "connector_id": connector_id}
    finally:
        db.close()


@router.post("/connectors/{connector_id}/test")
async def test_connector(connector_id: str):
    """Test connectivity using stored credentials."""
    db = SessionLocal()
    try:
        cfg = _get_config(db, connector_id)
        c   = decrypt_fields(cfg.config_json or {}, _SECRET_FIELDS)
    finally:
        db.close()

    if connector_id == "servicenow":
        if not c.get("base_url") or not c.get("username") or not c.get("password"):
            raise HTTPException(status_code=422, detail="Connector not fully configured")
        async with ServiceNowClient(c["base_url"], c["username"], c["password"]) as client:
            ok, latency_ms, message = await client.test_auth()
        return {"ok": ok, "latency_ms": latency_ms, "message": message}

    if connector_id == "splunk":
        if not c.get("base_url") or not c.get("token"):
            raise HTTPException(status_code=422, detail="Splunk connector not fully configured — save base URL and token first")
        from agentic_os.connectors.splunk.client import SplunkClient
        client = SplunkClient(c["base_url"], c["token"])
        ok, latency_ms, message = await client.test_connection()
        return {"ok": ok, "latency_ms": latency_ms, "message": message}

    raise HTTPException(status_code=422, detail=f"Test not supported for connector: {connector_id}")


@router.post("/connectors/{connector_id}/alert-config")
def save_alert_connector_config(connector_id: str, body: AlertConnectorConfigRequest):
    """Save config for pure-inbound webhook alert connectors (Datadog, Dynatrace, etc.)."""
    if connector_id not in _WEBHOOK_ONLY_CONNECTORS:
        raise HTTPException(
            status_code=422,
            detail=f"'{connector_id}' is not a webhook-only alert connector",
        )

    defn = next((d for d in CONNECTOR_DEFS if d["id"] == connector_id), None)
    if not defn:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")

    db = SessionLocal()
    try:
        cfg      = db.query(ConnectorConfigModel).filter_by(id=connector_id).first()
        existing = cfg.config_json if cfg else {}

        # Webhook secret logic: "-" = clear; "" = keep; <str> = replace
        if body.webhook_secret == "-":
            webhook_secret = None
        elif body.webhook_secret:
            webhook_secret = body.webhook_secret
        else:
            webhook_secret = existing.get("webhook_secret")

        # Outbound routing key — same clear/keep/replace semantics as webhook_secret
        if body.routing_key == "-":
            routing_key = None
        elif body.routing_key:
            routing_key = body.routing_key
        else:
            routing_key = existing.get("routing_key")

        config_json = {
            **existing,
            "default_criticality":    body.default_criticality,
            "default_event_type":     body.default_event_type,
            "allow_auto_remediation": body.allow_auto_remediation,
            "allow_storm_detection":  body.allow_storm_detection,
            "event_type_mappings":    body.event_type_mappings or {},
        }
        if webhook_secret is not None:
            config_json["webhook_secret"] = webhook_secret
        elif "webhook_secret" in config_json:
            del config_json["webhook_secret"]
        if routing_key is not None:
            config_json["routing_key"] = routing_key
        elif "routing_key" in config_json:
            del config_json["routing_key"]

        config_json = encrypt_fields(config_json, _SECRET_FIELDS)

        if cfg:
            cfg.config_json       = config_json
            cfg.enabled           = body.enabled
            cfg.sync_interval_min = 0
            cfg.updated_at        = datetime.utcnow()
        else:
            cfg = ConnectorConfigModel(
                id                = connector_id,
                display_name      = defn["display_name"],
                enabled           = body.enabled,
                config_json       = config_json,
                sync_interval_min = 0,
            )
            db.add(cfg)
        db.commit()
        return {"status": "saved", "connector_id": connector_id}
    finally:
        db.close()


@router.post("/connectors/{connector_id}/sync")
def trigger_sync(connector_id: str):
    """
    Enqueue a background CMDB sync via Celery and return immediately.
    The sync runs in the celery_worker — the frontend does not need to wait.
    """
    if connector_id != "servicenow":
        raise HTTPException(status_code=422, detail=f"Sync not supported for {connector_id}")

    db = SessionLocal()
    try:
        cfg = _get_config(db, connector_id)
        _require_creds(cfg)  # validates credentials exist before queuing
    finally:
        db.close()

    from agentic_os.tasks.snow_sync import snow_cmdb_sync
    task = snow_cmdb_sync.delay()
    logger.info("[SNOW] CMDB sync enqueued: task_id=%s", task.id)
    return {"status": "queued", "task_id": task.id}


@router.get("/connectors/{connector_id}/sync-logs")
def get_sync_logs(
    connector_id: str,
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0,  ge=0),
):
    """Return paginated sync history for a connector."""
    db = SessionLocal()
    try:
        q     = db.query(SNowSyncLogModel).filter_by(connector_id=connector_id)
        total = q.count()
        rows  = q.order_by(SNowSyncLogModel.started_at.desc()).offset(offset).limit(limit).all()
        return {
            "total": total,
            "items": [
                {
                    "id":             str(r.id),
                    "started_at":     r.started_at.isoformat(),
                    "finished_at":    r.finished_at.isoformat() if r.finished_at else None,
                    "records_pulled": r.records_pulled,
                    "status":         r.status,
                    "error_message":  r.error_message,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  ServiceNow CMDB browser (reads from Neo4j)
# ══════════════════════════════════════════════════════════════════════════════

def _neo4j_driver():
    """Return a short-lived Neo4j driver for one request."""
    import os
    from neo4j import GraphDatabase
    uri  = os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER",     "neo4j")
    pw   = os.getenv("NEO4J_PASSWORD")
    if not pw:
        raise RuntimeError("NEO4J_PASSWORD is not set.")
    return GraphDatabase.driver(uri, auth=(user, pw))


@router.get("/connectors/servicenow/cmdb")
def snow_cmdb_summary():
    """Return record counts per CI class from Neo4j."""
    driver = _neo4j_driver()
    try:
        classes = CMDBSync.get_summary(driver)
        return {
            "classes":       classes,
            "total_records": sum(c["count"] for c in classes),
        }
    finally:
        driver.close()


@router.get("/connectors/servicenow/cmdb/search")
def snow_cmdb_search(
    q:        str           = Query(..., min_length=1),
    ci_class: Optional[str] = Query(None),
    limit:    int           = Query(50, ge=1, le=200),
):
    """Search CIs in Neo4j by name (case-insensitive partial match)."""
    driver = _neo4j_driver()
    try:
        return CMDBSync.search(driver, q=q, ci_class=ci_class, limit=limit)
    finally:
        driver.close()


@router.get("/connectors/servicenow/cmdb/record/{sys_id}")
def snow_cmdb_record(sys_id: str):
    """Return full CI record from Neo4j by ServiceNow sys_id."""
    driver = _neo4j_driver()
    try:
        record = CMDBSync.get_by_sys_id(driver, sys_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"CI not found: {sys_id}")
        return record
    finally:
        driver.close()


@router.get("/connectors/servicenow/cmdb/{ci_class}")
def snow_cmdb_list(
    ci_class: str,
    limit:    int = Query(100, ge=1, le=500),
    offset:   int = Query(0,   ge=0),
):
    """Return paginated CI nodes for a given SN class from Neo4j."""
    valid_classes = {c["ci_class"] for c in CI_CLASSES if c["ci_class"] != "cmdb_rel_ci"}
    if ci_class not in valid_classes:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown ci_class: {ci_class}. Valid: {sorted(valid_classes)}",
        )

    driver = _neo4j_driver()
    try:
        records, total = CMDBSync.get_by_class(driver, ci_class=ci_class, limit=limit, offset=offset)
        return {
            "ci_class": ci_class,
            "total":    total,
            "offset":   offset,
            "limit":    limit,
            "items":    records,
        }
    finally:
        driver.close()


# ══════════════════════════════════════════════════════════════════════════════
#  ServiceNow Incident push
# ══════════════════════════════════════════════════════════════════════════════

def _get_snow_push(db) -> tuple[str, str, str]:
    """Return (base_url, username, password) from stored config."""
    cfg   = _get_config(db, "servicenow")
    creds = _require_creds(cfg)
    return creds["base_url"], creds["username"], creds["password"]


@router.post("/connectors/servicenow/push-incident/{workflow_id}")
async def push_incident_create(workflow_id: str, body: IncidentPushRequest):
    """Create a new ServiceNow incident from a platform workflow."""
    db = SessionLocal()
    try:
        base_url, username, password = _get_snow_push(db)
        from agentic_os.connectors.servicenow.incident_push import IncidentPush
        pusher = IncidentPush(base_url, username, password)
        _sync_cfg = get_incident_sync_config(_get_config(db, "servicenow").config_json or {})
        result = await pusher.create_incident(
            db_session      = db,
            workflow_id     = workflow_id,
            title           = body.title,
            description     = body.summary or body.title,
            work_notes      = body.work_notes or "",
            severity        = body.severity,
            lifecycle_state = body.lifecycle_state,
            service_name    = body.service_name,
            platform_url    = _sync_cfg.get("platform_url", "http://localhost:3000"),
        )
        return result
    finally:
        db.close()


@router.put("/connectors/servicenow/push-incident/{workflow_id}")
async def push_incident_update(workflow_id: str, body: IncidentUpdateRequest):
    """Update an existing ServiceNow incident."""
    db = SessionLocal()
    try:
        base_url, username, password = _get_snow_push(db)
        from agentic_os.connectors.servicenow.incident_push import IncidentPush
        pusher  = IncidentPush(base_url, username, password)
        _sync_cfg2 = get_incident_sync_config(_get_config(db, "servicenow").config_json or {})
        updates = body.dict(exclude_none=True, exclude={"work_notes"})
        result  = await pusher.update_incident(
            db_session          = db,
            workflow_id         = workflow_id,
            updates             = updates,
            work_notes          = body.work_notes or "",
            platform_url        = _sync_cfg2.get("platform_url", "http://localhost:3000"),
            new_lifecycle_state = updates.get("lifecycle_state", ""),
        )
        return result
    finally:
        db.close()


@router.get("/connectors/servicenow/incident-map/{workflow_id}")
async def get_incident_map(workflow_id: str):
    """Return the ServiceNow ticket linked to a platform workflow."""
    db = SessionLocal()
    try:
        mapping = db.query(SNowIncidentMapModel).filter_by(
            platform_workflow_id=workflow_id
        ).first()
        if not mapping:
            return {"mapped": False}

        # Also try to fetch live SN status if credentials available
        try:
            base_url, username, password = _get_snow_push(db)
            from agentic_os.connectors.servicenow.incident_push import IncidentPush
            pusher = IncidentPush(base_url, username, password)
            return await pusher.get_snow_status(db, workflow_id)
        except HTTPException:
            return {
                "mapped":        True,
                "snow_sys_id":   mapping.snow_sys_id,
                "snow_number":   mapping.snow_number,
                "push_status":   mapping.push_status,
                "last_pushed_at": mapping.last_pushed_at.isoformat() if mapping.last_pushed_at else None,
            }
    finally:
        db.close()
