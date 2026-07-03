"""
Splunk alert webhook payload parser.

Converts a Splunk custom alert action POST body into the fields expected by
MonitoringEventRepository.create() / the qualification pipeline.

Splunk alert payload format (custom alert action webhook):
{
  "search_name": "High CPU on prod-servers",
  "result": {
    "host":       "prod-web-01",
    "event_type": "high_cpu",       # optional — user-defined SPL field
    "severity":   "critical",       # optional
    "cpu_pct":    "95.4",
    "threshold":  "80",
    "process":    "nginx"
  },
  "results_link": "https://splunk.example.com/...",
  "sid":          "rt_scheduler__admin__...",
  "owner":        "admin",
  "app":          "search"
}

Field-mapping priority for each target field is documented inline.
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Turn a human-readable search name into an event_type slug.

    'High CPU Usage' → 'high_cpu_usage'
    """
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _first_numeric(result: dict, *keys: str) -> Optional[float]:
    """Return the first parseable float found among the given keys."""
    for k in keys:
        v = result.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


# Normalise non-standard criticality strings to info / warning / critical
_CRIT_ALIASES: dict[str, str] = {
    "high":     "critical",
    "severe":   "critical",
    "error":    "critical",
    "critical": "critical",
    "medium":   "warning",
    "med":      "warning",
    "warn":     "warning",
    "warning":  "warning",
    "low":      "info",
    "normal":   "info",
    "info":     "info",
    "informational": "info",
}


def _normalise_criticality(raw: str, fallback: str = "warning") -> str:
    normalised = _CRIT_ALIASES.get(raw.lower().strip())
    if normalised:
        return normalised
    logger.debug("Unknown Splunk criticality '%s', using fallback '%s'", raw, fallback)
    return fallback if fallback in ("info", "warning", "critical") else "warning"


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_splunk_alert(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """
    Parse a Splunk alert webhook body into MonitoringEvent field values.

    Args:
        payload: Full JSON body POSTed by Splunk
        config:  Splunk connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        Dict with keys: source, event_type, resource_name, raw_criticality,
        signal_value, signal_threshold, anomaly_process, raw_payload
    """
    result      = payload.get("result") or {}
    search_name = payload.get("search_name", "")

    # ── event_type ────────────────────────────────────────────────────────────
    # 1. result.event_type field (user-defined in the search)
    # 2. result.event_category
    # 3. Slugified search name
    # 4. Config default
    event_type = (
        result.get("event_type")
        or result.get("event_category")
        or (_slugify(search_name) if search_name else None)
        or config.get("default_event_type", "unknown")
    )

    # ── resource_name ─────────────────────────────────────────────────────────
    resource_name = (
        result.get("host")
        or result.get("resource")
        or result.get("hostname")
        or result.get("dest")
        or "unknown"
    )

    # ── criticality ───────────────────────────────────────────────────────────
    raw_crit_str = (
        result.get("severity")
        or result.get("criticality")
        or result.get("alert_level")
        or config.get("default_criticality", "warning")
    )
    raw_criticality = _normalise_criticality(
        raw_crit_str,
        fallback=config.get("default_criticality", "warning"),
    )

    # ── signal metrics ────────────────────────────────────────────────────────
    signal_value = _first_numeric(
        result,
        "signal_value", "value", "count",
        "cpu_pct", "mem_pct", "disk_pct",
        "cpu_percent", "memory_percent",
    )
    signal_threshold = _first_numeric(
        result,
        "signal_threshold", "threshold", "limit", "max",
    )

    # ── anomaly process ───────────────────────────────────────────────────────
    anomaly_process: Optional[str] = (
        result.get("process")
        or result.get("anomaly_process")
        or result.get("process_name")
        or None
    )

    # ── raw payload — attach Splunk metadata for audit trail ──────────────────
    raw_payload: dict[str, Any] = {
        **result,
        "_splunk_search_name":   search_name,
        "_splunk_sid":           payload.get("sid"),
        "_splunk_results_link":  payload.get("results_link"),
        "_splunk_owner":         payload.get("owner"),
        "_splunk_app":           payload.get("app"),
    }
    # Remove None values from splunk metadata
    raw_payload = {k: v for k, v in raw_payload.items() if v is not None}

    logger.info(
        "Splunk alert parsed: event_type=%s resource=%s criticality=%s",
        event_type, resource_name, raw_criticality,
    )

    return {
        "source":           "splunk",
        "event_type":       event_type,
        "resource_name":    resource_name,
        "raw_criticality":  raw_criticality,
        "signal_value":     signal_value,
        "signal_threshold": signal_threshold,
        "anomaly_process":  anomaly_process,
        "raw_payload":      raw_payload,
    }
