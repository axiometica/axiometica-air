"""
PagerDuty webhook v3 payload parser.

PagerDuty fires webhook events for incident lifecycle transitions.
We ingest triggered/acknowledged alerts and skip resolved ones.

Typical payload (PagerDuty webhook v3 — single event):
{
  "event": {
    "id":          "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "event_type":  "incident.triggered",   # incident.triggered | incident.acknowledged | incident.resolved
    "occurred_at": "2022-01-02T00:00:00Z",
    "data": {
      "id":              "Q14VH0BKIKKJKXX",
      "type":            "incident",
      "summary":         "Example Incident",
      "status":          "triggered",      # triggered | acknowledged | resolved
      "incident_number": 4,
      "title":           "High CPU on prod-web-01",
      "urgency":         "high",           # high | low
      "service": {
        "id":      "PIJ90N7",
        "summary": "My Application Service"
      },
      "priority": {
        "id":   "P1",
        "name": "P1"
      },
      "assignees": [],
      "html_url": "https://acmeinc.pagerduty.com/incidents/..."
    },
    "agent": {"type": "user_reference", "summary": "John Doe"}
  }
}

Some older PagerDuty integrations send a "messages" array instead:
{
  "messages": [
    {
      "type":     "incident.trigger",
      "incident": {
        "incident_number": 4,
        "summary": "High CPU",
        "status": "triggered",
        "urgency": "high",
        "service": {"summary": "My Service"},
        "created_on": "2022-01-02T00:00:00Z"
      }
    }
  ]
}

We handle both v2 (messages) and v3 (event) formats.
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Urgency / priority mapping ────────────────────────────────────────────────

_PD_URGENCY_MAP: dict[str, str] = {
    "high":     "critical",
    "low":      "warning",
    "critical": "critical",
    "p1":       "critical",
    "p2":       "critical",
    "p3":       "warning",
    "p4":       "info",
    "p5":       "info",
}

# Event types that signal an active problem
_ACTIVE_EVENT_TYPES = {
    "incident.triggered",
    "incident.trigger",    # v2 alias
    "incident.acknowledged",
    "incident.acknowledge",
}

# Resolved states to skip
_RESOLVED_EVENT_TYPES = {
    "incident.resolved",
    "incident.resolve",
    "incident.unacknowledged",
}


def _normalise_criticality(urgency: str, priority_name: str, fallback: str = "warning") -> str:
    """Map PagerDuty urgency + priority name to a normalised criticality."""
    if priority_name:
        mapped = _PD_URGENCY_MAP.get(priority_name.lower().strip())
        if mapped:
            return mapped
    return _PD_URGENCY_MAP.get(urgency.lower().strip(), fallback)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


# ── Unified incident extractor ────────────────────────────────────────────────

def _extract_incident(payload: dict) -> Optional[tuple[str, dict]]:
    """
    Normalise v2 and v3 PagerDuty payloads.

    Returns (event_type_str, incident_dict) or None if unrecognised.
    """
    # v3 format
    if "event" in payload:
        ev      = payload["event"]
        ev_type = ev.get("event_type", "")
        inc     = ev.get("data", {})
        return ev_type, inc

    # v2 format (messages array)
    messages = payload.get("messages", [])
    if messages:
        msg     = messages[0]  # process first message
        ev_type = msg.get("type", "")
        inc     = msg.get("incident", {})
        return ev_type, inc

    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_pagerduty_alert(payload: dict[str, Any], config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a PagerDuty webhook v2/v3 body into MonitoringEvent field values.

    Returns None for resolved events.

    Args:
        payload: Full JSON body POSTed by PagerDuty
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
        — or None for resolved incidents.
    """
    extracted = _extract_incident(payload)
    if not extracted:
        logger.warning("PagerDuty webhook: unrecognised payload structure")
        return None

    ev_type_raw, incident = extracted
    ev_type_lower = ev_type_raw.lower()

    if ev_type_lower in _RESOLVED_EVENT_TYPES:
        logger.info("PagerDuty webhook: skipping resolved event (type=%s)", ev_type_raw)
        return None

    # ── event_type ────────────────────────────────────────────────────────────
    # 1. Slugified incident title
    # 2. config default
    title      = incident.get("title") or incident.get("summary") or ""
    event_type = _slugify(title) if title else config.get("default_event_type", "unknown")

    # ── resource_name ─────────────────────────────────────────────────────────
    # 1. Service name (usually the monitored system)
    # 2. PagerDuty incident number as fallback
    service = incident.get("service") or {}
    svc_name = (
        (service.get("summary") or service.get("name") or "").strip()
        or None
    )
    inc_number = incident.get("incident_number")
    resource_name = svc_name or (f"pd-incident-{inc_number}" if inc_number else "unknown")

    # ── criticality ───────────────────────────────────────────────────────────
    urgency      = incident.get("urgency", "")
    priority     = incident.get("priority") or {}
    priority_name = priority.get("name", "")
    raw_criticality = _normalise_criticality(
        urgency,
        priority_name,
        fallback=config.get("default_criticality", "warning"),
    )

    # ── anomaly process ───────────────────────────────────────────────────────
    # We use the PD service name as a proxy for the affected process
    anomaly_process: Optional[str] = svc_name

    # ── raw payload ───────────────────────────────────────────────────────────
    raw_payload: dict[str, Any] = {
        **payload,
        "_pd_event_type":    ev_type_raw,
        "_pd_incident_id":   incident.get("id"),
        "_pd_html_url":      incident.get("html_url"),
        "_pd_status":        incident.get("status"),
    }

    logger.info(
        "PagerDuty alert parsed: event_type=%s resource=%s criticality=%s",
        event_type, resource_name, raw_criticality,
    )

    return {
        "source":           "pagerduty",
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     None,
        "signal_threshold": None,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
