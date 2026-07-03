"""
Zabbix problem webhook payload parser.

Zabbix can call a webhook script (media type) when a trigger fires.
The payload is typically a flat JSON object defined by the admin.

Common fields (using Zabbix macros in the webhook media type body):
{
  "event_id":         "12345",
  "trigger_id":       "67890",
  "trigger_name":     "High CPU utilization on {HOST.NAME}",
  "trigger_severity": "WARNING",     # NOT CLASSIFIED | INFO | WARNING | AVERAGE | HIGH | DISASTER
  "trigger_status":   "PROBLEM",     # PROBLEM | RESOLVED | UPDATE
  "trigger_url":      "http://zabbix/...",
  "host_name":        "prod-web-01",
  "host_ip":          "10.0.0.1",
  "item_name":        "CPU utilization",
  "item_value":       "87.5%",
  "event_date":       "2022.01.02",
  "event_time":       "00:00:00",
  "event_tags":       "env:prod,service:api",
  "problem_duration": "00:05:00",
  "event_update_message": ""
}

Zabbix severity ladder:
  NOT CLASSIFIED → info
  INFO           → info
  WARNING        → warning
  AVERAGE        → warning
  HIGH           → critical
  DISASTER       → critical
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Severity mapping ──────────────────────────────────────────────────────────

_ZBX_SEVERITY_MAP: dict[str, str] = {
    "not classified": "info",
    "not_classified": "info",
    "information":    "info",
    "info":           "info",
    "warning":        "warning",
    "average":        "warning",
    "high":           "critical",
    "disaster":       "critical",
}


def _normalise_criticality(severity: str, fallback: str = "warning") -> str:
    return _ZBX_SEVERITY_MAP.get(severity.lower().strip(), fallback)


def _slugify(text: str) -> str:
    """Turn a Zabbix trigger name into an event_type slug.

    'High CPU utilization on {HOST.NAME}' → 'high_cpu_utilization'
    """
    # Strip Zabbix macro references like {HOST.NAME}
    text = re.sub(r"\{[^}]+\}", "", text)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _parse_tags(tags_str: str) -> dict[str, str]:
    """Parse 'env:prod,service:api' tag string into a dict."""
    result: dict[str, str] = {}
    for tag in tags_str.split(","):
        tag = tag.strip()
        if ":" in tag:
            k, _, v = tag.partition(":")
            result[k.strip()] = v.strip()
        elif tag:
            result[tag] = "true"
    return result


def _parse_item_value(raw: str) -> Optional[float]:
    """Parse '87.5%' or '1.23 GB' into a float (strips units)."""
    if not raw:
        return None
    match = re.search(r"[\d.]+", raw)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_zabbix_alert(payload: dict[str, Any], config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a Zabbix webhook body into MonitoringEvent field values.

    Returns None for RESOLVED events.

    Args:
        payload: Flat JSON dict sent by Zabbix media type webhook
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
        — or None for resolved triggers.
    """
    status = (payload.get("trigger_status") or payload.get("status") or "").upper()
    if status in ("RESOLVED", "OK", "RECOVERY"):
        logger.info("Zabbix webhook: skipping resolved event (status=%s)", status)
        return None

    trigger_name = payload.get("trigger_name") or payload.get("name") or ""
    tags_str     = payload.get("event_tags") or payload.get("tags") or ""
    tags         = _parse_tags(tags_str) if tags_str else {}

    # ── event_type ────────────────────────────────────────────────────────────
    # 1. tags.event_type
    # 2. slugified trigger name
    # 3. config default
    event_type = (
        tags.get("event_type")
        or (_slugify(trigger_name) if trigger_name else None)
        or config.get("default_event_type", "unknown")
    )

    # ── resource_name ─────────────────────────────────────────────────────────
    resource_name = (
        payload.get("host_name")
        or payload.get("hostname")
        or payload.get("host")
        or tags.get("host")
        or "unknown"
    )

    # ── criticality ───────────────────────────────────────────────────────────
    sev_raw         = payload.get("trigger_severity") or payload.get("severity") or ""
    raw_criticality = _normalise_criticality(
        sev_raw,
        fallback=config.get("default_criticality", "warning"),
    )

    # ── signal metrics ────────────────────────────────────────────────────────
    signal_value: Optional[float]    = _parse_item_value(payload.get("item_value", ""))
    signal_threshold: Optional[float] = None

    # ── anomaly process ───────────────────────────────────────────────────────
    anomaly_process: Optional[str] = (
        payload.get("item_name")
        or tags.get("process")
        or tags.get("service")
        or None
    )

    # ── raw payload ───────────────────────────────────────────────────────────
    raw_payload: dict[str, Any] = {
        **payload,
        "_zbx_tags":        tags,
        "_zbx_trigger_id":  payload.get("trigger_id"),
        "_zbx_event_id":    payload.get("event_id"),
        "_zbx_status":      status,
    }

    logger.info(
        "Zabbix alert parsed: event_type=%s resource=%s criticality=%s",
        event_type, resource_name, raw_criticality,
    )

    return {
        "source":           "zabbix",
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     signal_value,
        "signal_threshold": signal_threshold,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
