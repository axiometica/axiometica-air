"""
Grafana Unified Alerting webhook payload parser.

Grafana posts a grouped alert batch when alerts fire or resolve — the same
envelope format as Prometheus Alertmanager, with these additions:
  • values          dict  — actual metric values keyed by variable name (e.g. {"A": 92.5})
  • valueString     str   — human-readable metric summary
  • dashboardURL    str   — link to the originating dashboard
  • panelURL        str   — link to the originating panel
  • silenceURL      str   — link to silence this alert in Grafana
  • grafana_folder  label — folder the alert rule lives in
  • orgId           int   — Grafana org
  • title           str   — top-level convenience summary (e.g. "[FIRING:1] HighCPU (critical)")

Configure in Grafana: Alerting → Contact points → Add contact point → Webhook
  URL: https://<your-platform>/api/connectors/grafana/webhook
  Method: POST
  Optional "Authorization header" → set to "Bearer <webhook_secret>"
  (or leave blank and configure webhook_secret in the connector config)

Resolved alerts (status="resolved") are silently dropped; only firing alerts
become monitoring events.
"""
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Severity mapping ──────────────────────────────────────────────────────────

_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high":     "critical",
    "error":    "critical",
    "severe":   "critical",
    "page":     "critical",
    "warning":  "warning",
    "warn":     "warning",
    "medium":   "warning",
    "info":     "info",
    "low":      "info",
    "none":     "info",
    "ok":       "info",
}


def _normalise_criticality(severity: str, fallback: str = "warning") -> str:
    return _SEVERITY_MAP.get(severity.lower().strip(), fallback)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _extract_host(instance: str) -> str:
    """Strip port from 'host:port' Prometheus-style instance label."""
    if instance and ":" in instance:
        return instance.rsplit(":", 1)[0]
    return instance or ""


def _extract_signal_value(alert: dict[str, Any]) -> Optional[float]:
    """
    Extract a numeric signal value from Grafana's values dict or annotations.

    Grafana sends values as {"A": 92.5, "B": 0.0} — take the first numeric entry.
    """
    values = alert.get("values") or {}
    for v in values.values():
        try:
            return float(v)
        except (TypeError, ValueError):
            continue

    labels      = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    for k in ("value", "current_value", "metric_value"):
        raw = labels.get(k) or annotations.get(k)
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_grafana_alerts(payload: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse a Grafana Unified Alerting webhook body into a list of
    MonitoringEvent field-value dicts (one per firing alert).

    Resolved alerts are silently skipped.

    Args:
        payload: Full JSON body POSTed by Grafana
        config:  Connector config_json from DB
                 (keys: default_criticality, default_event_type)

    Returns:
        List of dicts, each with keys: source, event_type, resource_name,
        raw_criticality, signal_value, signal_threshold, anomaly_process,
        raw_payload
    """
    results: list[dict[str, Any]] = []

    for alert in payload.get("alerts", []):
        if (alert.get("status") or "").lower() not in ("firing", "alerting"):
            logger.debug(
                "Grafana webhook: skipping non-firing alert (status=%s fingerprint=%s)",
                alert.get("status"), alert.get("fingerprint"),
            )
            continue

        labels      = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        # ── event_type ────────────────────────────────────────────────────────
        # 1. labels.event_type  (operator override)
        # 2. labels.alertname   → slug
        # 3. config default
        alertname  = labels.get("alertname", "")
        event_type = (
            labels.get("event_type")
            or (_slugify(alertname) if alertname else None)
            or config.get("default_event_type", "unknown")
        )

        # ── resource_name ─────────────────────────────────────────────────────
        # 1. labels.host / labels.hostname / labels.node
        # 2. labels.instance (strip port)
        # 3. labels.service
        # 4. "unknown"
        resource_name = (
            labels.get("host")
            or labels.get("hostname")
            or labels.get("node")
            or _extract_host(labels.get("instance", ""))
            or labels.get("service")
            or "unknown"
        )

        # ── criticality ───────────────────────────────────────────────────────
        sev_raw         = labels.get("severity") or labels.get("priority") or ""
        raw_criticality = _normalise_criticality(
            sev_raw,
            fallback=config.get("default_criticality", "warning"),
        )

        # ── signal value ──────────────────────────────────────────────────────
        signal_value    = _extract_signal_value(alert)
        signal_threshold: Optional[float] = None
        for k in ("threshold", "limit"):
            raw = labels.get(k) or annotations.get(k)
            if raw:
                try:
                    signal_threshold = float(raw)
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

        # ── raw payload — preserve all Grafana context ────────────────────────
        raw_payload: dict[str, Any] = {
            **alert,
            "_grafana_groupKey":    payload.get("groupKey"),
            "_grafana_receiver":    payload.get("receiver"),
            "_grafana_externalURL": payload.get("externalURL"),
            "_grafana_orgId":       payload.get("orgId"),
            "_grafana_title":       payload.get("title"),
            "_grafana_version":     payload.get("version"),
            "title":    annotations.get("summary") or payload.get("title") or alertname,
            "summary":  annotations.get("summary") or "",
            "description": annotations.get("description") or alert.get("valueString") or "",
        }

        logger.info(
            "Grafana alert parsed: event_type=%s resource=%s criticality=%s fingerprint=%s value=%s",
            event_type, resource_name, raw_criticality, alert.get("fingerprint"), signal_value,
        )

        results.append({
            "source":           "grafana",
            "event_type":       event_type,
            "resource_name":    resource_name,
            "raw_criticality":  raw_criticality,
            "signal_value":     signal_value,
            "signal_threshold": signal_threshold,
            "anomaly_process":  anomaly_process,
            "raw_payload":      raw_payload,
        })

    return results
