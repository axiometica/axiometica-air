"""
Generic webhook payload parser.

Accepts any JSON payload that conforms to a simple documented schema.
Designed for internal services, custom scripts, or any source that doesn't
have a dedicated connector.

Expected payload shape:

  Required:
    source         str   — origin system name (e.g. "my-app", "cron-monitor")
    event_type     str   — what happened (e.g. "high_error_rate", "job_failed")
    resource_name  str   — affected resource (e.g. "payment-service", "db-primary")
    severity       str   — "info" | "warning" | "critical"  (alias: "raw_criticality")

  Optional:
    signal_value     float  — actual metric reading
    signal_threshold float  — threshold that was breached
    title            str    — short human-readable summary
    description      str    — detailed message
    process          str    — subprocess / job name involved (alias: "anomaly_process")
    environment      str    — e.g. "production", "staging"
    metadata         dict   — any additional context (stored in raw_payload)

Example:
  {
    "source":           "payment-service",
    "event_type":       "high_error_rate",
    "resource_name":    "payment-api-prod",
    "severity":         "critical",
    "signal_value":     95.2,
    "signal_threshold": 80.0,
    "title":            "Error rate above 80% threshold",
    "description":      "5xx responses have spiked in the last 5 minutes",
    "process":          "checkout-handler",
    "environment":      "production",
    "metadata":         {"region": "us-east-1", "deploy": "v1.4.2"}
  }
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high":     "critical",
    "error":    "critical",
    "severe":   "critical",
    "fatal":    "critical",
    "warning":  "warning",
    "warn":     "warning",
    "medium":   "warning",
    "info":     "info",
    "low":      "info",
    "notice":   "info",
    "ok":       "info",
}


def _normalise_criticality(raw: str, fallback: str = "warning") -> str:
    return _SEVERITY_MAP.get((raw or "").lower().strip(), fallback)


def parse_generic_event(payload: dict[str, Any], config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a generic webhook payload into a MonitoringEvent field-value dict.

    Returns None if required fields are missing, so the caller can 400-reject
    gracefully rather than ingesting a broken event.

    Args:
        payload: Full JSON body POSTed by the sender
        config:  Connector config_json from DB
                 (keys: default_criticality, default_source, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
        — or None if required fields are absent.
    """
    # Required fields — fall back to config defaults so operators can hard-code
    # source/type at the connector level for simple integrations.
    source = (
        payload.get("source")
        or config.get("default_source")
        or ""
    )
    event_type = (
        payload.get("event_type")
        or config.get("default_event_type")
        or ""
    )
    resource_name = (
        payload.get("resource_name")
        or payload.get("resource")
        or payload.get("host")
        or payload.get("service")
        or ""
    )
    severity_raw = (
        payload.get("severity")
        or payload.get("raw_criticality")
        or payload.get("priority")
        or config.get("default_criticality")
        or ""
    )

    missing = [f for f, v in [
        ("source", source), ("event_type", event_type),
        ("resource_name", resource_name), ("severity", severity_raw),
    ] if not v]

    if missing:
        logger.warning("Generic webhook: missing required fields: %s", missing)
        return None

    raw_criticality = _normalise_criticality(severity_raw)

    # Optional numeric signals
    signal_value: Optional[float] = None
    for k in ("signal_value", "value", "metric_value", "current_value"):
        raw = payload.get(k)
        if raw is not None:
            try:
                signal_value = float(raw)
                break
            except (TypeError, ValueError):
                pass

    signal_threshold: Optional[float] = None
    for k in ("signal_threshold", "threshold", "limit"):
        raw = payload.get(k)
        if raw is not None:
            try:
                signal_threshold = float(raw)
                break
            except (TypeError, ValueError):
                pass

    anomaly_process: Optional[str] = (
        payload.get("process")
        or payload.get("anomaly_process")
        or payload.get("job")
        or None
    )

    raw_payload: dict[str, Any] = {
        **payload,
        "title":       payload.get("title") or event_type,
        "summary":     payload.get("title") or "",
        "description": payload.get("description") or "",
        "_generic_webhook": True,
    }

    logger.info(
        "Generic webhook parsed: source=%s event_type=%s resource=%s criticality=%s",
        source, event_type, resource_name, raw_criticality,
    )

    return {
        "source":           source,
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     signal_value,
        "signal_threshold": signal_threshold,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
