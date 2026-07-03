"""
Prometheus Alertmanager webhook payload parser.

Alertmanager posts a grouped alert batch when alerts fire or resolve.
We ingest each *firing* alert as a separate monitoring event and skip
resolved ones (Alertmanager sends status="resolved" for cleared alerts).

Typical payload (Alertmanager webhook receiver):
{
  "version": "4",
  "groupKey": "{}:{alertname='HighCPU'}",
  "status": "firing",           # firing | resolved
  "receiver": "platform-webhook",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighCPUUsage",
        "instance":  "prod-web-01:9100",
        "job":       "node",
        "severity":  "warning",
        "env":       "prod",
        "service":   "api"
      },
      "annotations": {
        "summary":     "CPU usage is above 80%",
        "description": "prod-web-01 CPU is at 92%"
      },
      "generatorURL": "http://prometheus:9090/...",
      "fingerprint":  "abc123"
    }
  ],
  "groupLabels":  {"alertname": "HighCPUUsage"},
  "commonLabels": {"job": "node"},
  "externalURL":  "http://alertmanager:9093"
}

We return a list of parsed events (one per firing alert).
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Severity mapping ──────────────────────────────────────────────────────────

_PROM_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "page":     "critical",
    "high":     "critical",
    "severe":   "critical",
    "warning":  "warning",
    "warn":     "warning",
    "medium":   "warning",
    "info":     "info",
    "low":      "info",
    "none":     "info",
}


def _normalise_criticality(severity: str, fallback: str = "warning") -> str:
    return _PROM_SEVERITY_MAP.get(severity.lower().strip(), fallback)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _extract_host(instance: str) -> str:
    """Strip port from 'host:port' Prometheus instance label."""
    if instance and ":" in instance:
        return instance.rsplit(":", 1)[0]
    return instance or "unknown"


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_prometheus_alerts(payload: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse a Prometheus Alertmanager webhook body into a list of
    MonitoringEvent field-value dicts (one per firing alert).

    Resolved alerts are silently skipped.

    Args:
        payload: Full JSON body POSTed by Alertmanager
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        List of dicts, each with keys: source, event_type, resource_name,
        raw_criticality, signal_value, signal_threshold, anomaly_process,
        raw_payload
    """
    results: list[dict[str, Any]] = []

    for alert in payload.get("alerts", []):
        if (alert.get("status") or "").lower() != "firing":
            logger.debug("Prometheus webhook: skipping non-firing alert (status=%s)", alert.get("status"))
            continue

        labels      = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        # ── event_type ────────────────────────────────────────────────────────
        # 1. labels.event_type
        # 2. labels.alertname → slug
        # 3. config default
        alertname  = labels.get("alertname", "")
        event_type = (
            labels.get("event_type")
            or (_slugify(alertname) if alertname else None)
            or config.get("default_event_type", "unknown")
        )

        # ── resource_name ─────────────────────────────────────────────────────
        # 1. labels.host / labels.hostname
        # 2. labels.instance (strip port)
        # 3. labels.node
        # 4. "unknown"
        resource_name = (
            labels.get("host")
            or labels.get("hostname")
            or _extract_host(labels.get("instance", ""))
            or labels.get("node")
            or "unknown"
        )

        # ── criticality ───────────────────────────────────────────────────────
        sev_raw         = labels.get("severity") or labels.get("priority") or ""
        raw_criticality = _normalise_criticality(
            sev_raw,
            fallback=config.get("default_criticality", "warning"),
        )

        # ── signal metrics ────────────────────────────────────────────────────
        signal_value: Optional[float] = None
        signal_threshold: Optional[float] = None

        for k in ("value", "current_value", "metric_value"):
            v = labels.get(k) or annotations.get(k)
            if v:
                try:
                    signal_value = float(v)
                    break
                except (TypeError, ValueError):
                    pass

        for k in ("threshold", "limit", "for"):
            v = labels.get(k) or annotations.get(k)
            if v:
                try:
                    signal_threshold = float(v)
                    break
                except (TypeError, ValueError):
                    pass

        # ── anomaly process ───────────────────────────────────────────────────
        anomaly_process: Optional[str] = (
            labels.get("process")
            or labels.get("service")
            or labels.get("job")
            or None
        )

        # ── raw payload ───────────────────────────────────────────────────────
        raw_payload: dict[str, Any] = {
            **alert,
            "_prom_groupKey":    payload.get("groupKey"),
            "_prom_receiver":    payload.get("receiver"),
            "_prom_externalURL": payload.get("externalURL"),
            "_prom_version":     payload.get("version"),
        }

        logger.info(
            "Prometheus alert parsed: event_type=%s resource=%s criticality=%s fingerprint=%s",
            event_type, resource_name, raw_criticality, alert.get("fingerprint"),
        )

        results.append({
            "source":           "prometheus",
            "event_type":       event_type,
            "resource_name":    resource_name,
            "raw_criticality":  raw_criticality,
            "signal_value":     signal_value,
            "signal_threshold": signal_threshold,
            "anomaly_process":  anomaly_process,
            "raw_payload":      raw_payload,
        })

    return results
