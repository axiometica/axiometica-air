"""
Unit tests for OutboundWebhookClient — the generic outbound channel type for
notification teams that just want a plain HTTP POST destination.
"""
from unittest.mock import MagicMock, patch

import pytest

from agentic_os.connectors.webhook.outbound_client import OutboundWebhookClient, OutboundWebhookError


def _fake_response(status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error detail"
    return resp


class TestOutboundWebhookClient:
    def test_send_sync_posts_payload_without_secret(self):
        client = OutboundWebhookClient("https://example.com/hooks/notify")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()

            client.send_sync({"event": "escalate", "message": "High CPU"})

            instance.post.assert_called_once()
            url = instance.post.call_args[0][0]
            kwargs = instance.post.call_args[1]
            assert url == "https://example.com/hooks/notify"
            assert kwargs["json"] == {"event": "escalate", "message": "High CPU"}
            assert kwargs["headers"] == {}

    def test_send_sync_includes_secret_header_when_set(self):
        client = OutboundWebhookClient("https://example.com/hooks/notify", secret="shh")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()

            client.send_sync({"event": "message"})

            kwargs = instance.post.call_args[1]
            assert kwargs["headers"] == {"X-Webhook-Secret": "shh"}

    def test_send_sync_raises_on_error_status(self):
        client = OutboundWebhookClient("https://example.com/hooks/notify")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response(status_code=500)

            with pytest.raises(OutboundWebhookError) as exc_info:
                client.send_sync({"event": "escalate"})
            assert exc_info.value.status_code == 500

    @pytest.mark.parametrize("status", [200, 201, 202, 204])
    def test_send_sync_accepts_all_2xx_success_codes(self, status):
        client = OutboundWebhookClient("https://example.com/hooks/notify")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response(status_code=status)
            result = client.send_sync({"event": "escalate"})
            assert result["status_code"] == status
