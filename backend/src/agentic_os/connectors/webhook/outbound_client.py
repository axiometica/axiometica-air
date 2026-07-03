"""
Generic outbound webhook client — for notification teams that want a plain
HTTP POST destination (chat tools, custom integrations, etc.) rather than
one of the named connector types (PagerDuty, Slack, email).

Usage:
    client = OutboundWebhookClient(url, secret="optional-shared-secret")
    client.send_sync({"event": "escalate", "message": "...", "severity": "critical"})
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class OutboundWebhookError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"Outbound webhook error {status_code}: {detail}")


class OutboundWebhookClient:
    """POSTs a JSON payload to a user-configured URL, with an optional shared secret header."""

    def __init__(self, url: str, secret: Optional[str] = None):
        self.url = url
        self.secret = secret

    def send_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-Webhook-Secret": self.secret} if self.secret else {}
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(self.url, json=payload, headers=headers)
            if resp.status_code not in (200, 201, 202, 204):
                raise OutboundWebhookError(resp.status_code, resp.text)
            return {"status_code": resp.status_code}
