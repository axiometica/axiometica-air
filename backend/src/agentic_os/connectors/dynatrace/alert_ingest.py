"""
Dynatrace problem notification webhook parser.

Dynatrace posts a problem notification when it opens, updates, or closes
a problem. We ingest OPEN/UPDATE events and skip RESOLVED ones.

Typical payload (Dynatrace custom integration webhook):
{
  "title":            "High CPU on HOST-abc123",
  "problemUrl":       "https://abc123.live.dynatrace.com/...",
  "problemId":        "P-12345",
  "problemImpact":    "INFRASTRUCTURE",   # APPLICATION | SERVICE | INFRASTRUCTURE
  "problemSeverity":  "PERFORMANCE",      # AVAILABILITY | ERROR | PERFORMANCE | RESOURCE_CONTENTION
  "state":            "OPEN",             # OPEN | RESOLVED
  "affectedEntities": [
    {"id": "HOST-abc123", "type": "HOST"}
  ],
  "tagsOfAffectedEntities": [
    {"context": "CONTEXTLESS", "key": "env",  "value": "prod"},
    {"context": "CONTEXTLESS", "key": "team", "value": "platform"}
  ],
  "servicesAffected": [],
  "impactedEntities": []
}

Field-mapping priority is documented inline.
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Severity mapping ──────────────────────────────────────────────────────────

_DT_SEVERITY_MAP: dict[str, str] = {
    "availability":          "critical",
    "error":                 "critical",
    "performance":           "warning",
    "resource_contention":   "warning",
    "custom_alert":          "info",
}

_DT_IMPACT_MAP: dict[str, str] = {
    "application":    "critical",
    "service":        "warning",
    "infrastructure": "warning",
    "environment":    "info",
}

# States that represent an active problem worth creating an incident for
_ACTIVE_STATES = {"open", "resolved_manually", "acknowledged"}


def _normalise_criticality(severity: str, impact: str, fallback: str = "warning") -> str:
    """Combine Dynatrace severity + impact into a normalised criticality level."""
    sev = _DT_SEVERITY_MAP.get(severity.lower().strip())
    if sev == "critical":
        return "critical"
    imp = _DT_IMPACT_MAP.get(impact.lower().strip())
    return sev or imp or fallback


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _extract_tags(tags_list: list[dict]) -> dict[str, str]:
    """Flatten Dynatrace tag list into a simple key→value dict."""
    result: dict[str, str] = {}
    for tag in tags_list or []:
        k = tag.get("key", "")
        v = tag.get("value") or "true"
        if k:
            result[k] = v
    return result


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_dynatrace_alert(payload: dict[str, Any], config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a Dynatrace problem notification into MonitoringEvent field values.

    Returns a condition_cleared event for RESOLVED state so the platform auto-closes
    the matching incident, mirroring how Dynatrace natively signals problem resolution.

    Args:
        payload: Full JSON body POSTed by Dynatrace
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
        — or None for closed/resolved problems.
    """
    state = (payload.get("state") or "").upper()
    if state == "RESOLVED":
        # Extract resource name using the same priority as OPEN events so the
        # condition_cleared signal targets the right incident.
        _tags_resolved  = _extract_tags(payload.get("tagsOfAffectedEntities", []))
        _affected       = payload.get("affectedEntities", [])
        _entity         = _affected[0] if _affected else {}
        _resource_name  = (
            _entity.get("name") or _entity.get("id")
            or _tags_resolved.get("host") or _tags_resolved.get("hostname")
            or "unknown"
        )
        logger.info(
            "Dynatrace webhook: RESOLVED → condition_cleared for resource=%s (id=%s)",
            _resource_name, payload.get("problemId"),
        )
        return {
            "source":           "dynatrace",
            "event_type":       "condition_cleared",
            "resource_name":    _resource_name,
            "raw_criticality":  "info",
            "signal_value":     None,
            "signal_threshold": None,
            "anomaly_process":  None,
            "raw_payload":      {**payload, "_dt_state": "RESOLVED"},
        }

    severity  = payload.get("problemSeverity", "")
    impact    = payload.get("problemImpact", "")
    tags_list = payload.get("tagsOfAffectedEntities", [])
    tags      = _extract_tags(tags_list)

    # ── event_type ────────────────────────────────────────────────────────────
    # 1. tags.event_type
    # 2. severity slug (performance → performance_issue)
    # 3. slugified title
    # 4. config default
    sev_slug   = _slugify(f"{severity}_problem") if severity else ""
    event_type = (
        tags.get("event_type")
        or sev_slug
        or _slugify(payload.get("title", ""))
        or config.get("default_event_type", "unknown")
    )

    # ── resource_name ─────────────────────────────────────────────────────────
    # 1. First affected entity id (e.g. "HOST-abc123")
    # 2. tags.host
    # 3. "unknown"
    affected   = payload.get("affectedEntities", [])
    entity_name: Optional[str] = None
    if affected:
        entity = affected[0]
        entity_name = entity.get("name") or entity.get("id") or None

    resource_name = (
        entity_name
        or tags.get("host")
        or tags.get("hostname")
        or "unknown"
    )

    # ── criticality ───────────────────────────────────────────────────────────
    raw_criticality = _normalise_criticality(
        severity,
        impact,
        fallback=config.get("default_criticality", "warning"),
    )

    # ── anomaly process ───────────────────────────────────────────────────────
    # Use service name from tags or first service name
    anomaly_process: Optional[str] = (
        tags.get("service")
        or tags.get("application")
        or None
    )

    # ── service_url ───────────────────────────────────────────────────────────
    # Tag key=service_url (or key=url / key=endpoint_url) carries the HTTP
    # endpoint being monitored.  Stored as a top-level raw_payload key so the
    # _extra spread in monitoring_events.py passes it into alert_payload, where
    # the runbook executor resolves {service_url} in step templates.
    service_url: Optional[str] = (
        tags.get("service_url")
        or tags.get("url")
        or tags.get("endpoint_url")
        or None
    )

    # ── raw payload ───────────────────────────────────────────────────────────
    raw_payload: dict[str, Any] = {
        **payload,
        "_dt_tags":         tags,
        "_dt_problem_id":   payload.get("problemId"),
        "_dt_problem_url":  payload.get("problemUrl"),
        "_dt_state":        state,
        **({"service_url": service_url} if service_url else {}),
    }

    logger.info(
        "Dynatrace alert parsed: event_type=%s resource=%s criticality=%s",
        event_type, resource_name, raw_criticality,
    )

    return {
        "source":           "dynatrace",
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     None,
        "signal_threshold": None,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
