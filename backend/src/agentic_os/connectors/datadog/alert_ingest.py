"""
Datadog alert webhook payload parser.

Datadog posts to a webhook when a monitor alert transitions to
ALERT, WARNING, NO DATA, or RECOVERED.

Typical payload (Datadog webhook integration):
{
  "id":                123456,
  "title":             "[Triggered on {hostname}] CPU is too high",
  "body":              "Average CPU over 5 min is 92% (threshold: 80%)",
  "hostname":          "prod-web-01",
  "alert_type":        "warning",          # metric_alert_monitor | warning | error | info | success
  "alert_metric":      "system.cpu.user",
  "alert_transition":  "Triggered",        # Triggered | Recovered | No Data
  "alert_query":       "avg:system.cpu.user{*}",
  "alert_cycle_key":   "abc123",
  "priority":          "normal",           # normal | low
  "tags":              "env:prod,service:api,host:prod-web-01",
  "date":              1666666666,
  "last_updated":      "2023-01-01 00:00:00",
  "aggreg_key":        "abc123"
}

Field-mapping priority is documented inline.
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Criticality mapping ───────────────────────────────────────────────────────

_DD_ALERT_TYPES: dict[str, str] = {
    "error":   "critical",
    "warning": "warning",
    "info":    "info",
    "success": "info",
    "metric_alert_monitor": "warning",
    "service_check":        "warning",
    "query_alert_monitor":  "warning",
    "event_alert":          "warning",
}

# Datadog alert_transition → filter recovered alerts (we only care about fires)
_RECOVERY_TRANSITIONS = {"recovered", "recovery", "ok", "resolved"}


def _normalise_criticality(alert_type: str, fallback: str = "warning") -> str:
    return _DD_ALERT_TYPES.get(alert_type.lower().strip(), fallback)


def _parse_tags(tags_str: str) -> dict[str, str]:
    """Parse Datadog tag string 'env:prod,service:api' into a dict."""
    result: dict[str, str] = {}
    for tag in tags_str.split(","):
        tag = tag.strip()
        if ":" in tag:
            k, _, v = tag.partition(":")
            result[k.strip()] = v.strip()
        elif tag:
            result[tag] = "true"
    return result


def _slugify(text: str) -> str:
    text = text.lower().strip()
    # Strip Datadog status prefix like "[Triggered on hostname] "
    text = re.sub(r"^\[.*?\]\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_datadog_alert(payload: dict[str, Any], config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a Datadog webhook body into MonitoringEvent field values.

    Returns None if the alert is a recovery/resolution event (we skip those).

    Args:
        payload: Full JSON body POSTed by Datadog
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
        — or None for recovery alerts.
    """
    # ── Filter out recovery transitions ──────────────────────────────────────
    transition = (payload.get("alert_transition") or "").lower()
    if transition in _RECOVERY_TRANSITIONS:
        logger.info("Datadog webhook: skipping recovery event (transition=%s)", transition)
        return None

    tags_str = payload.get("tags", "")
    tags      = _parse_tags(tags_str) if tags_str else {}

    # ── event_type ────────────────────────────────────────────────────────────
    # 1. tags.event_type
    # 2. alert_metric slug   (system.cpu.user → system_cpu_user)
    # 3. slugified title
    # 4. config default
    metric      = payload.get("alert_metric", "")
    metric_slug = re.sub(r"[^a-z0-9]+", "_", metric.lower()).strip("_") if metric else ""
    event_type  = (
        tags.get("event_type")
        or metric_slug
        or _slugify(payload.get("title", ""))
        or config.get("default_event_type", "unknown")
    )

    # ── resource_name ─────────────────────────────────────────────────────────
    # 1. tags.host / tags.hostname
    # 2. payload.hostname
    # 3. tags.service
    # 4. "unknown"
    resource_name = (
        tags.get("host")
        or tags.get("hostname")
        or payload.get("hostname", "")
        or tags.get("service")
        or "unknown"
    )

    # ── criticality ───────────────────────────────────────────────────────────
    alert_type     = payload.get("alert_type", "")
    raw_criticality = _normalise_criticality(
        alert_type,
        fallback=config.get("default_criticality", "warning"),
    )

    # ── signal metrics ────────────────────────────────────────────────────────
    # Datadog embeds these in tags: threshold:<value> or as top-level keys
    signal_value: Optional[float]    = None
    signal_threshold: Optional[float] = None

    raw_value = tags.get("value") or tags.get("metric_value")
    if raw_value:
        try:
            signal_value = float(raw_value)
        except (TypeError, ValueError):
            pass

    raw_thresh = tags.get("threshold")
    if raw_thresh:
        try:
            signal_threshold = float(raw_thresh)
        except (TypeError, ValueError):
            pass

    # ── anomaly process ───────────────────────────────────────────────────────
    anomaly_process: Optional[str] = tags.get("process") or tags.get("service") or None

    # ── raw payload ───────────────────────────────────────────────────────────
    raw_payload: dict[str, Any] = {
        **payload,
        "_dd_tags":            tags,
        "_dd_alert_type":      alert_type,
        "_dd_alert_metric":    metric,
        "_dd_alert_transition": transition,
    }

    logger.info(
        "Datadog alert parsed: event_type=%s resource=%s criticality=%s",
        event_type, resource_name, raw_criticality,
    )

    return {
        "source":           "datadog",
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     signal_value,
        "signal_threshold": signal_threshold,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
