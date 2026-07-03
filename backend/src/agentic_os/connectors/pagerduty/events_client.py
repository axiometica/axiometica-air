"""
PagerDuty Events API v2 client — outbound (trigger/acknowledge/resolve).

This is the other half of the PagerDuty connector: alert_ingest.py parses
PagerDuty's *inbound* webhooks (PagerDuty -> platform). This module pushes
events the other way (platform -> PagerDuty) so the platform can actually
page an on-call engineer, which is PagerDuty's whole purpose — the previous
state of this integration only ever consumed PagerDuty as an alert source.

Both sync and async entry points are provided: the runbook tool-action
dispatcher (ToolRegistryAgent) runs synchronously, while the Celery
auto-resolve hook runs in an async-bridged worker context. Both share the
same payload construction so the two paths can't drift apart.

API docs: https://developer.pagerduty.com/docs/ZG9jOjExMDI5NTgw-events-api-v2-overview

Usage:
    client = PagerDutyEventsClient(routing_key)
    client.trigger_sync(summary="High CPU on api-1", severity="critical", dedup_key="INC0042")
    await client.trigger(summary="High CPU on api-1", severity="critical", dedup_key="INC0042")
    await client.resolve(dedup_key="INC0042")
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_EVENTS_API_URL = "https://events.pagerduty.com/v2/enqueue"

# PagerDuty only accepts these four severities on the Events API v2.
_VALID_SEVERITIES = {"critical", "error", "warning", "info"}


class PagerDutyEventsError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"PagerDuty Events API error {status_code}: {detail}")


class PagerDutyEventsClient:
    """Thin wrapper around PagerDuty's Events API v2 (POST /v2/enqueue)."""

    def __init__(self, routing_key: str, source: str = "axiometica-air"):
        self.routing_key = routing_key
        self.source = source

    @staticmethod
    def _normalise_severity(severity: Optional[str]) -> str:
        sev = (severity or "warning").lower().strip()
        return sev if sev in _VALID_SEVERITIES else "warning"

    def _trigger_payload(
        self,
        summary: str,
        severity: Optional[str],
        dedup_key: Optional[str],
        source: Optional[str],
        custom_details: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "routing_key":  self.routing_key,
            "event_action": "trigger",
            "payload": {
                "summary":  summary[:1024],
                "source":   source or self.source,
                "severity": self._normalise_severity(severity),
            },
        }
        if dedup_key:
            payload["dedup_key"] = dedup_key
        if custom_details:
            payload["payload"]["custom_details"] = custom_details
        return payload

    def _state_change_payload(self, event_action: str, dedup_key: str) -> dict[str, Any]:
        return {
            "routing_key":  self.routing_key,
            "event_action": event_action,
            "dedup_key":    dedup_key,
        }

    # ── Async (Celery / async route handlers) ────────────────────────────────

    async def _enqueue(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_EVENTS_API_URL, json=payload)
            if resp.status_code not in (200, 202):
                raise PagerDutyEventsError(resp.status_code, resp.text)
            return resp.json()

    async def trigger(
        self,
        summary: str,
        severity: Optional[str] = None,
        dedup_key: Optional[str] = None,
        source: Optional[str] = None,
        custom_details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Open (or update, if dedup_key matches an existing one) a PagerDuty incident."""
        result = await self._enqueue(self._trigger_payload(summary, severity, dedup_key, source, custom_details))
        logger.info("[PagerDuty] triggered dedup_key=%s status=%s", result.get("dedup_key"), result.get("status"))
        return result

    async def acknowledge(self, dedup_key: str) -> dict[str, Any]:
        result = await self._enqueue(self._state_change_payload("acknowledge", dedup_key))
        logger.info("[PagerDuty] acknowledged dedup_key=%s", dedup_key)
        return result

    async def resolve(self, dedup_key: str) -> dict[str, Any]:
        result = await self._enqueue(self._state_change_payload("resolve", dedup_key))
        logger.info("[PagerDuty] resolved dedup_key=%s", dedup_key)
        return result

    # ── Sync (the tool-action dispatcher runs synchronously) ─────────────────

    def _enqueue_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_EVENTS_API_URL, json=payload)
            if resp.status_code not in (200, 202):
                raise PagerDutyEventsError(resp.status_code, resp.text)
            return resp.json()

    def trigger_sync(
        self,
        summary: str,
        severity: Optional[str] = None,
        dedup_key: Optional[str] = None,
        source: Optional[str] = None,
        custom_details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        result = self._enqueue_sync(self._trigger_payload(summary, severity, dedup_key, source, custom_details))
        logger.info("[PagerDuty] triggered dedup_key=%s status=%s", result.get("dedup_key"), result.get("status"))
        return result

    def acknowledge_sync(self, dedup_key: str) -> dict[str, Any]:
        result = self._enqueue_sync(self._state_change_payload("acknowledge", dedup_key))
        logger.info("[PagerDuty] acknowledged dedup_key=%s", dedup_key)
        return result

    def resolve_sync(self, dedup_key: str) -> dict[str, Any]:
        result = self._enqueue_sync(self._state_change_payload("resolve", dedup_key))
        logger.info("[PagerDuty] resolved dedup_key=%s", dedup_key)
        return result
