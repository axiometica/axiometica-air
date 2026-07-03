"""
Splunk alert ingestion webhook — public endpoint (no JWT required).

Splunk posts here when a saved search fires an alert action.
We parse the payload, run it through the standard event qualification
pipeline, and let the normal dedup / incident-creation / SN-sync /
LLM-summary machinery handle the rest.

Configure Splunk to POST to:
    POST /api/connectors/splunk/webhook
    Header:  X-Splunk-Webhook-Token: <webhook_secret>   (if configured)
"""
import logging
import os
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

# When WEBHOOK_REQUIRE_SECRET=true, reject requests when no secret is configured.
_REQUIRE_SECRET = os.getenv("WEBHOOK_REQUIRE_SECRET", "").lower() in ("1", "true", "yes")
from sqlalchemy.orm import Session

from agentic_os.db.database import SessionLocal
from agentic_os.db.models import ConnectorConfigModel
from agentic_os.connectors.splunk.alert_ingest import parse_splunk_alert

# Reuse the full submission logic from the monitoring events route
from agentic_os.api.routes.monitoring_events import (
    MonitoringEventSubmit,
    submit_monitoring_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Splunk Webhook"])


@router.post("/connectors/splunk/webhook", status_code=200)
async def receive_splunk_alert(
    request: Request,
    background_tasks: BackgroundTasks,
    x_splunk_webhook_token: str | None = Header(None, alias="X-Splunk-Webhook-Token"),
):
    """
    Receive an alert from Splunk's custom alert action webhook.

    The event is parsed and submitted through the standard monitoring event
    qualification pipeline — deduplication, incident creation, incident
    numbering, ServiceNow sync, and LLM summary generation all apply.

    Splunk setup:
    1.  In Splunk: Settings → Alert Actions → Webhook → Add your URL
        URL: https://<your-platform>/api/connectors/splunk/webhook
    2.  If webhook_secret is configured: set the custom header
        X-Splunk-Webhook-Token: <your_secret>
    3.  Recommended SPL fields to include in your search results:
        event_type (e.g. "high_cpu"), host, severity, threshold

    Returns:
        {accepted, event_id, qualified, score, incident_workflow_id}
    """
    db: Session = SessionLocal()
    try:
        # ── Load and validate connector config ────────────────────────────────
        cfg = db.query(ConnectorConfigModel).filter_by(id="splunk").first()
        if not cfg or not cfg.enabled:
            raise HTTPException(
                status_code=503,
                detail="Splunk connector is not configured or is disabled",
            )

        config: dict = cfg.config_json or {}

        # ── Webhook-secret validation ─────────────────────────────────────────
        from agentic_os.security.crypto import decrypt_if_encrypted

        webhook_secret = decrypt_if_encrypted(config.get("webhook_secret") or "") or None
        if not webhook_secret:
            if _REQUIRE_SECRET:
                logger.error(
                    "Splunk webhook: rejected — no webhook_secret configured and "
                    "WEBHOOK_REQUIRE_SECRET is enabled"
                )
                raise HTTPException(
                    status_code=401,
                    detail="Webhook secret is required but not configured for this connector",
                )
            logger.warning(
                "Splunk webhook: no webhook_secret configured — accepting unauthenticated "
                "request. Set WEBHOOK_REQUIRE_SECRET=true to enforce strict mode."
            )
        elif not x_splunk_webhook_token or x_splunk_webhook_token != webhook_secret:
            logger.warning("Splunk webhook: bad or missing X-Splunk-Webhook-Token")
            raise HTTPException(status_code=401, detail="Invalid webhook token")

        # ── Parse body ────────────────────────────────────────────────────────
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON")

        if not payload:
            raise HTTPException(status_code=400, detail="Empty payload")

        logger.info("Splunk webhook received: search=%s", payload.get("search_name", "?"))

        # ── Map Splunk fields → MonitoringEventSubmit ─────────────────────────
        fields = parse_splunk_alert(payload, config)

        event = MonitoringEventSubmit(
            source=fields["source"],
            event_type=fields["event_type"],
            resource_name=fields["resource_name"],
            raw_criticality=fields["raw_criticality"],
            signal_value=fields.get("signal_value"),
            signal_threshold=fields.get("signal_threshold"),
            anomaly_process=fields.get("anomaly_process"),
            raw_payload=fields.get("raw_payload", {}),
        )

        # ── Submit through the standard pipeline ──────────────────────────────
        # This gives us: dedup, incident numbering, SN sync, LLM summary
        result = await submit_monitoring_event(
            event=event,
            background_tasks=background_tasks,
            db=db,
        )

        return {
            "accepted":             True,
            "event_id":             result.event_id,
            "qualified":            result.qualified_as_incident,
            "score":                result.qualification_score,
            "incident_workflow_id": result.incident_workflow_id,
        }

    finally:
        db.close()
