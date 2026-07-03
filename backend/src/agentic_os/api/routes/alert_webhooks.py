"""
Alert ingestion webhooks — public endpoints, no JWT required.

Seven monitoring connectors all share this module:

  POST /api/connectors/datadog/webhook      — Datadog monitor alert
  POST /api/connectors/dynatrace/webhook    — Dynatrace problem notification
  POST /api/connectors/prometheus/webhook   — Prometheus Alertmanager
  POST /api/connectors/pagerduty/webhook    — PagerDuty incident trigger
  POST /api/connectors/zabbix/webhook       — Zabbix trigger problem
  POST /api/connectors/grafana/webhook      — Grafana Unified Alerting
  POST /api/connectors/generic/webhook      — Generic / custom event source

Each endpoint:
  1. Loads the connector config from DB (must be enabled)
  2. Validates an optional webhook secret header
  3. Parses the vendor-specific payload
  4. Submits each event through submit_monitoring_event()
     → dedup, incident numbering, SN sync, LLM summary all apply

Secret headers:
  Datadog    — X-Datadog-Webhook-Secret
  Dynatrace  — X-Dynatrace-Webhook-Secret
  Prometheus — X-Prometheus-Webhook-Secret   (or leave blank; Alertmanager doesn't sign)
  PagerDuty  — X-PagerDuty-Webhook-Secret
  Zabbix     — X-Zabbix-Webhook-Secret
  Grafana    — X-Grafana-Webhook-Secret
  Generic    — X-Webhook-Secret
"""
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy.orm import Session

# When WEBHOOK_REQUIRE_SECRET=true, connectors with no configured secret are
# also rejected — prevents unauthenticated ingestion in strict environments.
_REQUIRE_SECRET = os.getenv("WEBHOOK_REQUIRE_SECRET", "").lower() in ("1", "true", "yes")

from agentic_os.db.database import SessionLocal
from agentic_os.db.models import ConnectorConfigModel
from agentic_os.api.routes.monitoring_events import (
    MonitoringEventSubmit,
    submit_monitoring_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Alert Webhooks"])


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _load_config(connector_id: str) -> dict:
    """Load and validate connector config from DB. Raises 503/404 on failure."""
    db: Session = SessionLocal()
    try:
        cfg = db.query(ConnectorConfigModel).filter_by(id=connector_id).first()
        if not cfg:
            raise HTTPException(
                status_code=404,
                detail=f"Connector '{connector_id}' is not configured",
            )
        if not cfg.enabled:
            raise HTTPException(
                status_code=503,
                detail=f"Connector '{connector_id}' is disabled",
            )
        return cfg.config_json or {}
    finally:
        db.close()


def _validate_secret(config: dict, provided: Optional[str], connector_id: str) -> None:
    """Validate webhook shared secret.

    • If a secret IS configured: provided value must match exactly.
    • If NO secret is configured and WEBHOOK_REQUIRE_SECRET=true: reject (strict mode).
    • If NO secret is configured and WEBHOOK_REQUIRE_SECRET is unset/false: log a
      warning and pass through (permissive default for easy onboarding).
    """
    from agentic_os.security.crypto import decrypt_if_encrypted

    stored_secret = decrypt_if_encrypted(config.get("webhook_secret") or "") or None
    if not stored_secret:
        if _REQUIRE_SECRET:
            logger.error(
                "%s webhook: rejected — no webhook_secret configured and "
                "WEBHOOK_REQUIRE_SECRET is enabled",
                connector_id,
            )
            raise HTTPException(
                status_code=401,
                detail="Webhook secret is required but not configured for this connector",
            )
        logger.warning(
            "%s webhook: no webhook_secret configured — accepting unauthenticated request. "
            "Set WEBHOOK_REQUIRE_SECRET=true to enforce strict mode.",
            connector_id,
        )
        return
    if not provided or provided != stored_secret:
        logger.warning("%s webhook: bad or missing webhook secret", connector_id)
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


async def _ingest_event(
    fields: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session,
    config: Optional[dict] = None,
) -> dict:
    """
    Submit one parsed event through the standard pipeline.

    Applies event-type normalization before ingestion so that external
    alert names are translated to canonical platform types (high_cpu,
    disk_full, etc.) required for runbook matching.

    Normalization cascade:
      1. Operator-configured event_type_mappings  (exact / case-insensitive)
      2. Already a canonical type                 (pass-through)
      3. Keyword heuristics                       (title + alert name)
      4. LLM classification                       (async, with timeout)
      5. Raw value                                (best-effort fallback)
    """
    from agentic_os.connectors.event_type_normalizer import normalize_event_type_async

    raw_event_type = fields["event_type"]
    mappings       = (config or {}).get("event_type_mappings") or {}

    # Best available hint for heuristic / LLM: alert title → resource name → raw type
    raw_payload = fields.get("raw_payload") or {}
    hint_text   = (
        raw_payload.get("title")
        or raw_payload.get("summary")
        or raw_payload.get("alert_name")
        or fields.get("resource_name", "")
        or raw_event_type
    )

    normalized_type = await normalize_event_type_async(
        raw_type=raw_event_type,
        mappings=mappings,
        hint_text=hint_text,
    )

    if normalized_type != raw_event_type:
        logger.info(
            "_ingest_event: event_type normalized '%s' → '%s' (source=%s resource=%s)",
            raw_event_type, normalized_type, fields.get("source"), fields.get("resource_name"),
        )

    event = MonitoringEventSubmit(
        source          = fields["source"],
        event_type      = normalized_type,
        resource_name   = fields["resource_name"],
        raw_criticality = fields["raw_criticality"],
        signal_value    = fields.get("signal_value"),
        signal_threshold= fields.get("signal_threshold"),
        anomaly_process = fields.get("anomaly_process"),
        raw_payload     = {
            **raw_payload,
            "_original_event_type": raw_event_type,   # preserve for audit
            "_normalized_event_type": normalized_type,
        },
    )
    result = await submit_monitoring_event(event=event, background_tasks=background_tasks, db=db)
    return {
        "accepted":             True,
        "event_id":             result.event_id,
        "qualified":            result.qualified_as_incident,
        "score":                result.qualification_score,
        "incident_workflow_id": result.incident_workflow_id,
        "event_type_raw":       raw_event_type,
        "event_type_resolved":  normalized_type,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Datadog
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/datadog/webhook", status_code=200)
async def receive_datadog_alert(
    request:                   Request,
    background_tasks:          BackgroundTasks,
    x_datadog_webhook_secret:  Optional[str] = Header(None, alias="X-Datadog-Webhook-Secret"),
):
    """
    Receive a Datadog monitor alert webhook.

    Configure in Datadog: Integrations → Webhooks → Add
    URL: https://<your-platform>/api/connectors/datadog/webhook

    Recovery events (alert_transition = Recovered) are silently dropped.
    """
    config = _load_config("datadog")
    _validate_secret(config, x_datadog_webhook_secret, "datadog")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    logger.info("Datadog webhook received: title=%s", payload.get("title", "?"))

    from agentic_os.connectors.datadog.alert_ingest import parse_datadog_alert
    fields = parse_datadog_alert(payload, config)

    if fields is None:
        return {"accepted": False, "reason": "recovery_event_skipped"}

    db: Session = SessionLocal()
    try:
        return await _ingest_event(fields, background_tasks, db, config=config)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Dynatrace
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/dynatrace/webhook", status_code=200)
async def receive_dynatrace_alert(
    request:                    Request,
    background_tasks:           BackgroundTasks,
    x_dynatrace_webhook_secret: Optional[str] = Header(None, alias="X-Dynatrace-Webhook-Secret"),
):
    """
    Receive a Dynatrace problem notification webhook.

    Configure in Dynatrace: Settings → Integrations → Problem notifications
    → Add notification → Custom integration
    URL: https://<your-platform>/api/connectors/dynatrace/webhook

    RESOLVED problems are silently dropped.
    """
    config = _load_config("dynatrace")
    _validate_secret(config, x_dynatrace_webhook_secret, "dynatrace")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    logger.info(
        "Dynatrace webhook received: problemId=%s state=%s",
        payload.get("problemId", "?"),
        payload.get("state", "?"),
    )

    from agentic_os.connectors.dynatrace.alert_ingest import parse_dynatrace_alert
    fields = parse_dynatrace_alert(payload, config)

    if fields is None:
        return {"accepted": False, "reason": "resolved_problem_skipped"}

    db: Session = SessionLocal()
    try:
        return await _ingest_event(fields, background_tasks, db, config=config)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Prometheus / Alertmanager
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/prometheus/webhook", status_code=200)
async def receive_prometheus_alerts(
    request:                     Request,
    background_tasks:            BackgroundTasks,
    x_prometheus_webhook_secret: Optional[str] = Header(None, alias="X-Prometheus-Webhook-Secret"),
):
    """
    Receive Prometheus Alertmanager grouped alerts.

    Configure in alertmanager.yml:
      receivers:
        - name: platform-webhook
          webhook_configs:
            - url: https://<your-platform>/api/connectors/prometheus/webhook

    One request may contain multiple firing alerts (sent as a batch).
    Each firing alert becomes an independent monitoring event.
    Resolved alerts are silently dropped.

    Returns a list of results, one per ingested event.
    """
    config = _load_config("prometheus")
    _validate_secret(config, x_prometheus_webhook_secret, "prometheus")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    alert_count = len(payload.get("alerts", []))
    logger.info("Prometheus webhook received: %d alerts, status=%s", alert_count, payload.get("status", "?"))

    from agentic_os.connectors.prometheus.alert_ingest import parse_prometheus_alerts
    all_fields = parse_prometheus_alerts(payload, config)

    if not all_fields:
        return {"accepted": False, "reason": "no_firing_alerts", "results": []}

    db: Session = SessionLocal()
    results = []
    try:
        for fields in all_fields:
            r = await _ingest_event(fields, background_tasks, db, config=config)
            results.append(r)
    finally:
        db.close()

    return {
        "accepted": True,
        "count":    len(results),
        "results":  results,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PagerDuty
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/pagerduty/webhook", status_code=200)
async def receive_pagerduty_alert(
    request:                    Request,
    background_tasks:           BackgroundTasks,
    x_pagerduty_webhook_secret: Optional[str] = Header(None, alias="X-PagerDuty-Webhook-Secret"),
):
    """
    Receive a PagerDuty incident webhook (v2 or v3 format).

    Configure in PagerDuty: Integrations → Generic Webhooks (v3)
    URL: https://<your-platform>/api/connectors/pagerduty/webhook
    Events: incident.triggered, incident.acknowledged

    Resolved incidents are silently dropped.
    """
    config = _load_config("pagerduty")
    _validate_secret(config, x_pagerduty_webhook_secret, "pagerduty")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    ev_type = (
        (payload.get("event") or {}).get("event_type")
        or ((payload.get("messages") or [{}])[0]).get("type")
        or "?"
    )
    logger.info("PagerDuty webhook received: event_type=%s", ev_type)

    from agentic_os.connectors.pagerduty.alert_ingest import parse_pagerduty_alert
    fields = parse_pagerduty_alert(payload, config)

    if fields is None:
        return {"accepted": False, "reason": "resolved_incident_skipped"}

    db: Session = SessionLocal()
    try:
        return await _ingest_event(fields, background_tasks, db, config=config)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Zabbix
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/zabbix/webhook", status_code=200)
async def receive_zabbix_alert(
    request:                  Request,
    background_tasks:         BackgroundTasks,
    x_zabbix_webhook_secret:  Optional[str] = Header(None, alias="X-Zabbix-Webhook-Secret"),
):
    """
    Receive a Zabbix trigger problem notification.

    Configure in Zabbix: Administration → Media types → Create media type
    Type: Webhook
    URL: (not needed — body goes here)
    Parameters: event_id, trigger_name, trigger_severity, trigger_status,
                host_name, item_name, item_value, event_tags, trigger_url

    URL for the action: https://<your-platform>/api/connectors/zabbix/webhook

    RESOLVED triggers are silently dropped.
    """
    config = _load_config("zabbix")
    _validate_secret(config, x_zabbix_webhook_secret, "zabbix")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    logger.info(
        "Zabbix webhook received: trigger=%s host=%s severity=%s",
        payload.get("trigger_name", "?"),
        payload.get("host_name", "?"),
        payload.get("trigger_severity", "?"),
    )

    from agentic_os.connectors.zabbix.alert_ingest import parse_zabbix_alert
    fields = parse_zabbix_alert(payload, config)

    if fields is None:
        return {"accepted": False, "reason": "resolved_trigger_skipped"}

    db: Session = SessionLocal()
    try:
        return await _ingest_event(fields, background_tasks, db, config=config)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Grafana Unified Alerting
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/grafana/webhook", status_code=200)
async def receive_grafana_alerts(
    request:                  Request,
    background_tasks:         BackgroundTasks,
    x_grafana_webhook_secret: Optional[str] = Header(None, alias="X-Grafana-Webhook-Secret"),
):
    """
    Receive Grafana Unified Alerting grouped alerts.

    Configure in Grafana: Alerting → Contact points → Add contact point → Webhook
    URL: https://<your-platform>/api/connectors/grafana/webhook
    (Optional) Set "Authorization header" to match webhook_secret in connector config.

    One request may contain multiple firing alerts — each becomes an independent
    monitoring event. Resolved alerts are silently dropped.

    Returns a list of results, one per ingested event.
    """
    config = _load_config("grafana")
    _validate_secret(config, x_grafana_webhook_secret, "grafana")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    alert_count = len(payload.get("alerts", []))
    logger.info(
        "Grafana webhook received: %d alerts status=%s title=%s",
        alert_count, payload.get("status", "?"), payload.get("title", "?"),
    )

    from agentic_os.connectors.grafana.alert_ingest import parse_grafana_alerts
    all_fields = parse_grafana_alerts(payload, config)

    if not all_fields:
        return {"accepted": False, "reason": "no_firing_alerts", "results": []}

    db: Session = SessionLocal()
    results = []
    try:
        for fields in all_fields:
            r = await _ingest_event(fields, background_tasks, db, config=config)
            results.append(r)
    finally:
        db.close()

    return {
        "accepted": True,
        "count":    len(results),
        "results":  results,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Generic / custom event source
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connectors/generic/webhook", status_code=200)
async def receive_generic_event(
    request:           Request,
    background_tasks:  BackgroundTasks,
    x_webhook_secret:  Optional[str] = Header(None, alias="X-Webhook-Secret"),
):
    """
    Receive a single event from any custom source.

    Required payload fields:
      source         — origin system name (e.g. "my-app", "cron-monitor")
      event_type     — what happened (e.g. "high_error_rate", "job_failed")
      resource_name  — affected resource (e.g. "payment-service")
      severity       — "info" | "warning" | "critical"

    Optional:
      signal_value, signal_threshold, title, description, process,
      environment, metadata (any additional key-value context)

    Auth: send the connector's webhook_secret in the X-Webhook-Secret header.
    Operators may also hard-code default_source, default_event_type, and
    default_criticality in the connector config to simplify minimal payloads.
    """
    config = _load_config("generic")
    _validate_secret(config, x_webhook_secret, "generic")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    logger.info(
        "Generic webhook received: source=%s event_type=%s resource=%s",
        payload.get("source", "?"),
        payload.get("event_type", "?"),
        payload.get("resource_name", "?"),
    )

    from agentic_os.connectors.generic.alert_ingest import parse_generic_event
    fields = parse_generic_event(payload, config)

    if fields is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing required fields. Payload must include: "
                "source, event_type, resource_name, severity "
                "(or configure defaults in the connector config)."
            ),
        )

    db: Session = SessionLocal()
    try:
        return await _ingest_event(fields, background_tasks, db, config=config)
    finally:
        db.close()
