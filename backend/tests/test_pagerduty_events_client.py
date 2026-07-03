"""
Unit tests for PagerDutyEventsClient (Events API v2 wrapper) — the outbound
half of the PagerDuty connector. No network calls: httpx.Client/AsyncClient
are mocked, asserting the exact payload shape PagerDuty's API expects.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_os.connectors.pagerduty.events_client import (
    PagerDutyEventsClient,
    PagerDutyEventsError,
    _EVENTS_API_URL,
)


def _fake_response(status_code=202, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error detail"
    resp.json.return_value = json_body or {"status": "success", "dedup_key": "abc123"}
    return resp


class TestSeverityNormalisation:
    def test_valid_severities_pass_through(self):
        for sev in ("critical", "error", "warning", "info"):
            assert PagerDutyEventsClient._normalise_severity(sev) == sev

    def test_invalid_severity_defaults_to_warning(self):
        assert PagerDutyEventsClient._normalise_severity("high") == "warning"

    def test_none_defaults_to_warning(self):
        assert PagerDutyEventsClient._normalise_severity(None) == "warning"

    def test_case_insensitive(self):
        assert PagerDutyEventsClient._normalise_severity("CRITICAL") == "critical"


class TestSyncTrigger:
    def test_trigger_sync_posts_correct_payload(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()

            result = client.trigger_sync(
                summary="High CPU on api-1", severity="critical", dedup_key="INC0042",
                custom_details={"team": "platform"},
            )

            assert result["dedup_key"] == "abc123"
            instance.post.assert_called_once()
            url, kwargs = instance.post.call_args[0][0], instance.post.call_args[1]
            assert url == _EVENTS_API_URL
            payload = kwargs["json"]
            assert payload["routing_key"] == "test-routing-key"
            assert payload["event_action"] == "trigger"
            assert payload["dedup_key"] == "INC0042"
            assert payload["payload"]["summary"] == "High CPU on api-1"
            assert payload["payload"]["severity"] == "critical"
            assert payload["payload"]["custom_details"] == {"team": "platform"}

    def test_trigger_sync_omits_dedup_key_when_not_given(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()
            client.trigger_sync(summary="Something happened")
            payload = instance.post.call_args[1]["json"]
            assert "dedup_key" not in payload

    def test_trigger_sync_raises_on_error_status(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response(status_code=400)
            with pytest.raises(PagerDutyEventsError) as exc_info:
                client.trigger_sync(summary="bad request")
            assert exc_info.value.status_code == 400


class TestSyncStateChange:
    def test_resolve_sync_posts_correct_payload(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()
            client.resolve_sync(dedup_key="INC0042")
            payload = instance.post.call_args[1]["json"]
            assert payload == {
                "routing_key": "test-routing-key",
                "event_action": "resolve",
                "dedup_key": "INC0042",
            }

    def test_acknowledge_sync_posts_correct_payload(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = _fake_response()
            client.acknowledge_sync(dedup_key="INC0042")
            payload = instance.post.call_args[1]["json"]
            assert payload["event_action"] == "acknowledge"


class TestAsyncVariants:
    @pytest.mark.asyncio
    async def test_trigger_posts_correct_payload(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_fake_response())
            result = await client.trigger(summary="High CPU", severity="critical", dedup_key="INC0099")
            assert result["dedup_key"] == "abc123"
            payload = instance.post.call_args[1]["json"]
            assert payload["event_action"] == "trigger"
            assert payload["dedup_key"] == "INC0099"

    @pytest.mark.asyncio
    async def test_resolve_posts_correct_payload(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_fake_response())
            await client.resolve(dedup_key="INC0099")
            payload = instance.post.call_args[1]["json"]
            assert payload["event_action"] == "resolve"

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self):
        client = PagerDutyEventsClient("test-routing-key")
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_fake_response(status_code=500))
            with pytest.raises(PagerDutyEventsError):
                await client.trigger(summary="boom")
