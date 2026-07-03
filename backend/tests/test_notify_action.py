"""
Tests for ToolRegistryAgent._execute_notify_action — the versatile
escalate/acknowledge/resolve/message dispatcher shared by the notify,
alert_escalate, alert_update, and send_alert runbook actions.

Covers: team resolution (found/enabled vs not-found/disabled -> default
fallback), per-action channel-type filtering, legacy tool-name aliasing via
_execute_alert_action, and multi-channel fan-out aggregation.

SessionLocal is imported locally inside the method body, so patching the
source module's attribute (agentic_os.db.database.SessionLocal) is what
actually takes effect — same pattern as test_alert_escalate_action.py.
_resolve_notify_team is patched directly to isolate channel-dispatch logic
from the team lookup, which is exercised separately in
test_notification_teams_api.py / test_resolve_notify_team_unit below.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentic_os.agents.incident_agents import ToolRegistryAgent
from agentic_os.db.models import NotificationTeamModel


def _fake_team(**overrides):
    base = dict(
        name="Network On-Call",
        pagerduty_routing_key=None,
        slack_channel=None,
        email_recipients=None,
        webhook_url=None,
        webhook_secret=None,
        enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_session():
    fake_db = MagicMock()
    fake_session_local = MagicMock(return_value=fake_db)
    return patch("agentic_os.db.database.SessionLocal", fake_session_local)


class TestIncidentContextPrefix:
    """args["incident_number"]/["incident_title"] are auto-injected upstream (the
    runbook executor, not the runbook author) — _execute_notify_action must always
    prepend them to the message, degrading field-by-field rather than silently
    sending an unattributed bare message."""

    def test_prefixes_with_both_number_and_title(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "Service is now responsive",
                 "incident_number": "INC0046", "incident_title": "High CPU on prod-web-01"},
                "prod-web-01", "exec_ctx1",
            )

        sent_text = mock_slack.call_args[0][0]
        assert sent_text == "INC0046 - High CPU on prod-web-01 - Service is now responsive"

    def test_degrades_to_number_only(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "Service is now responsive", "incident_number": "INC0046"},
                "prod-web-01", "exec_ctx2",
            )

        assert mock_slack.call_args[0][0] == "INC0046 - Service is now responsive"

    def test_degrades_to_title_only(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "Service is now responsive", "incident_title": "High CPU on prod-web-01"},
                "prod-web-01", "exec_ctx3",
            )

        assert mock_slack.call_args[0][0] == "High CPU on prod-web-01 - Service is now responsive"

    def test_explicit_marker_when_neither_available(self):
        """e.g. a manual Test Run in the editor with no real incident behind it —
        still sends (never silently no-ops), but says plainly that it has no context."""
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            ToolRegistryAgent._execute_notify_action(
                "message", {"team": "Network On-Call", "message": "Service is now responsive"},
                "prod-web-01", "exec_ctx4",
            )

        assert mock_slack.call_args[0][0] == "[no incident context] - Service is now responsive"

    def test_webhook_payload_gets_structured_incident_fields(self):
        team = _fake_team(webhook_url="https://example.com/hooks/notify")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.webhook.outbound_client.OutboundWebhookClient.send_sync") as mock_webhook:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "Service is now responsive",
                 "incident_number": "INC0046", "incident_title": "High CPU on prod-web-01"},
                "prod-web-01", "exec_ctx5",
            )

        payload = mock_webhook.call_args[0][0]
        assert payload["incident_number"] == "INC0046"
        assert payload["incident_title"] == "High CPU on prod-web-01"
        assert payload["message"] == "INC0046 - High CPU on prod-web-01 - Service is now responsive"

    def test_names_the_runbook_when_available(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "Service is now responsive",
                 "incident_number": "INC0046", "runbook_name": "High CPU Remediation"},
                "prod-web-01", "exec_ctx6",
            )

        assert mock_slack.call_args[0][0] == "INC0046 - High CPU Remediation - Service is now responsive"

    def test_webhook_payload_gets_runbook_name_field(self):
        team = _fake_team(webhook_url="https://example.com/hooks/notify")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.webhook.outbound_client.OutboundWebhookClient.send_sync") as mock_webhook:

            ToolRegistryAgent._execute_notify_action(
                "message",
                {"team": "Network On-Call", "message": "test", "runbook_name": "High CPU Remediation"},
                "prod-web-01", "exec_ctx7",
            )

        assert mock_webhook.call_args[0][0]["runbook_name"] == "High CPU Remediation"


class TestResolveNotifyTeam:
    """Real DB lookups — no mocking needed since this is a plain query + enabled check."""

    def test_returns_none_when_no_name_given(self, db):
        assert ToolRegistryAgent._resolve_notify_team(None, db) is None

    def test_returns_none_when_team_not_found(self, db):
        assert ToolRegistryAgent._resolve_notify_team("Nonexistent Team", db) is None

    def test_returns_team_on_case_insensitive_match(self, db):
        db.add(NotificationTeamModel(name="Resolve Team Case Test", enabled=True))
        db.commit()
        team = ToolRegistryAgent._resolve_notify_team("resolve team case test", db)
        assert team is not None
        assert team.name == "Resolve Team Case Test"

    def test_returns_none_when_team_disabled(self, db):
        db.add(NotificationTeamModel(name="Disabled Team", enabled=False))
        db.commit()
        assert ToolRegistryAgent._resolve_notify_team("Disabled Team", db) is None


class TestEscalateWithTeam:
    def test_escalate_fans_out_to_all_team_channels(self):
        team = _fake_team(
            pagerduty_routing_key="R0123", slack_channel="#net-oncall",
            email_recipients="a@x.com,b@x.com", webhook_url="https://x.example.com",
        )
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.trigger_sync",
                   return_value={"status": "success", "dedup_key": "INC1"}) as mock_pd, \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack, \
             patch("agentic_os.services.email_service.EmailService.send_incident_notification", return_value=True) as mock_email, \
             patch("agentic_os.connectors.webhook.outbound_client.OutboundWebhookClient.send_sync", return_value={"status_code": 200}) as mock_webhook:

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"team": "Network On-Call", "message": "High CPU"}, "prod-web-01", "exec1",
            )

        assert result["success"] is True
        mock_pd.assert_called_once()
        mock_slack.assert_called_once()
        assert mock_slack.call_args[1]["channel"] == "#net-oncall"
        mock_email.assert_called_once()
        mock_webhook.assert_called_once()

    def test_escalate_with_team_does_not_touch_global_defaults(self):
        """A resolved team's (partial) channel set replaces, not adds to, the defaults."""
        team = _fake_team(slack_channel="#net-oncall")  # no pagerduty/email/webhook on this team
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.api.routes.connectors.get_pagerduty_client") as mock_get_pd, \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"team": "Network On-Call", "message": "test"}, "prod-web-01", "exec1",
            )

        assert result["success"] is True
        mock_get_pd.assert_not_called()   # never falls back to default PagerDuty just because team has no PD key
        mock_slack.assert_called_once()


class TestSlackChannelSpecificFailure:
    """Slack being configured at all (bot token present) is a different failure
    mode than a specific channel rejecting the post (bot not invited, channel
    archived/renamed, etc.) — these used to collapse into the same generic
    "Slack: not configured" message regardless of which one actually happened."""

    def test_channel_rejection_surfaces_specific_reason_not_generic_not_configured(self):
        from slack_sdk.errors import SlackApiError

        team = _fake_team(slack_channel="#infrateam")
        fake_slack_client = MagicMock()
        fake_slack_client.chat_postMessage.side_effect = SlackApiError(
            message="request failed", response={"ok": False, "error": "not_in_channel"},
        )

        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._slack_client", return_value=fake_slack_client):

            result = ToolRegistryAgent._execute_notify_action(
                "message", {"team": "infraTeam", "message": "test"}, "prod-web-01", "exec_slack_err",
            )

        assert result["success"] is False
        assert "not_in_channel" in result["message"]
        assert "#infrateam" in result["message"]
        assert "not configured" not in result["message"]


class TestEscalateExceptionHandling:
    def test_pagerduty_exception_returns_clean_failure(self):
        team = _fake_team(pagerduty_routing_key="R0123")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.trigger_sync",
                   side_effect=RuntimeError("PagerDuty Events API error 400: bad routing key")):

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"team": "Network On-Call"}, "prod-web-01", "exec_err",
            )

        assert result["success"] is False
        assert "PagerDuty" in result["message"]


class TestEscalateWithDefaults:
    def test_escalate_falls_back_to_defaults_when_no_team_given(self):
        fake_pd = MagicMock()
        fake_pd.trigger_sync.return_value = {"status": "success", "dedup_key": "INC2"}
        with _patch_session(), \
             patch("agentic_os.api.routes.connectors.get_pagerduty_client", return_value=fake_pd), \
             patch("agentic_os.services.notifications._post_slack", return_value=False), \
             patch("agentic_os.services.email_service.EmailService.is_configured", return_value=False):

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"message": "test"}, "prod-web-01", "exec2",
            )

        assert result["success"] is True
        fake_pd.trigger_sync.assert_called_once()

    def test_escalate_falls_back_to_defaults_when_team_unknown(self):
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=None), \
             patch("agentic_os.api.routes.connectors.get_pagerduty_client", return_value=None), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack, \
             patch("agentic_os.services.email_service.EmailService.is_configured", return_value=False):

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"team": "Nonexistent", "message": "test"}, "prod-web-01", "exec3",
            )

        assert result["success"] is True
        mock_slack.assert_called_once()
        assert mock_slack.call_args[1]["channel"] is None   # uses configured default channel
        # Unknown team name is surfaced in the result, not just server logs —
        # otherwise a typo silently falls back to defaults with no visible clue.
        assert "Nonexistent" in result["message"] and "not found" in result["message"]

    def test_fails_cleanly_when_nothing_configured(self):
        # use_slack defaults to True in the no-team path even when Slack isn't
        # actually configured, so this is "attempted and failed" rather than
        # "nothing attempted" — the assertion checks the resulting failure text.
        def _fake_post_slack(text, inc_number=None, channel=None, error_out=None):
            if error_out is not None:
                error_out.append("Slack is not enabled or has no bot token configured")
            return False

        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=None), \
             patch("agentic_os.api.routes.connectors.get_pagerduty_client", return_value=None), \
             patch("agentic_os.services.notifications._post_slack", side_effect=_fake_post_slack), \
             patch("agentic_os.services.email_service.EmailService.is_configured", return_value=False):

            result = ToolRegistryAgent._execute_notify_action(
                "escalate", {"message": "test"}, "prod-web-01", "exec4",
            )

        assert result["success"] is False
        assert "Slack" in result["message"] and "not enabled" in result["message"]


class TestAcknowledgeResolveOnlyTouchPagerDuty:
    def test_resolve_ignores_slack_even_if_configured(self):
        team = _fake_team(pagerduty_routing_key="R0123", slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.resolve_sync",
                   return_value={"status": "success"}) as mock_resolve, \
             patch("agentic_os.services.notifications._post_slack") as mock_slack:

            result = ToolRegistryAgent._execute_notify_action(
                "resolve", {"team": "Network On-Call", "dedup_key": "INC42"}, "prod-web-01", "exec5",
            )

        assert result["success"] is True
        mock_resolve.assert_called_once_with(dedup_key="INC42")
        mock_slack.assert_not_called()

    def test_resolve_without_dedup_key_fails_cleanly(self):
        team = _fake_team(pagerduty_routing_key="R0123")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team):
            result = ToolRegistryAgent._execute_notify_action(
                "resolve", {"team": "Network On-Call"}, "prod-web-01", "exec6",
            )
        assert result["success"] is False
        assert "dedup_key" in result["message"]

    def test_acknowledge_with_no_pagerduty_channel_fails_cleanly(self):
        team = _fake_team(slack_channel="#net-oncall")   # no pagerduty key on this team
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team):
            result = ToolRegistryAgent._execute_notify_action(
                "acknowledge", {"team": "Network On-Call", "dedup_key": "INC42"}, "prod-web-01", "exec7",
            )
        assert result["success"] is False
        assert "nothing to acknowledge" in result["message"]


class TestMessageSkipsPagerDuty:
    def test_message_only_touches_slack_email_webhook(self):
        team = _fake_team(pagerduty_routing_key="R0123", slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.trigger_sync") as mock_pd, \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            result = ToolRegistryAgent._execute_notify_action(
                "message", {"team": "Network On-Call", "message": "FYI"}, "prod-web-01", "exec8",
            )

        assert result["success"] is True
        mock_pd.assert_not_called()
        mock_slack.assert_called_once()

    def test_message_fails_cleanly_when_only_pagerduty_resolved(self):
        team = _fake_team(pagerduty_routing_key="R0123")   # only PagerDuty configured
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team):
            result = ToolRegistryAgent._execute_notify_action(
                "message", {"team": "Network On-Call", "message": "FYI"}, "prod-web-01", "exec9",
            )
        assert result["success"] is False


class TestLegacyAliasMapping:
    def test_alert_escalate_maps_to_escalate(self):
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=None), \
             patch("agentic_os.api.routes.connectors.get_pagerduty_client") as mock_get_pd, \
             patch("agentic_os.services.notifications._post_slack", return_value=True), \
             patch("agentic_os.services.email_service.EmailService.is_configured", return_value=False):
            fake_pd = MagicMock()
            fake_pd.trigger_sync.return_value = {"status": "success", "dedup_key": "x"}
            mock_get_pd.return_value = fake_pd

            ToolRegistryAgent._execute_alert_action("alert_escalate", {"severity": "critical"}, "target", "exec10")
            fake_pd.trigger_sync.assert_called_once()

    def test_alert_update_resolved_status_maps_to_resolve(self):
        team = _fake_team(pagerduty_routing_key="R0123")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.resolve_sync",
                   return_value={"status": "success"}) as mock_resolve, \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.acknowledge_sync") as mock_ack:

            ToolRegistryAgent._execute_alert_action(
                "alert_update", {"team": "Network On-Call", "status": "resolved", "alert_id": "INC1"}, "target", "exec11",
            )
        mock_resolve.assert_called_once()
        mock_ack.assert_not_called()

    def test_alert_update_acknowledged_status_maps_to_acknowledge(self):
        team = _fake_team(pagerduty_routing_key="R0123")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.connectors.pagerduty.events_client.PagerDutyEventsClient.acknowledge_sync",
                   return_value={"status": "success"}) as mock_ack:

            ToolRegistryAgent._execute_alert_action(
                "alert_update", {"team": "Network On-Call", "status": "acknowledged", "alert_id": "INC1"}, "target", "exec12",
            )
        mock_ack.assert_called_once()

    def test_send_alert_maps_to_message(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            result = ToolRegistryAgent._execute_alert_action(
                "send_alert", {"team": "Network On-Call", "message": "done"}, "target", "exec13",
            )
        assert result["success"] is True
        mock_slack.assert_called_once()

    def test_notify_uses_explicit_action_arg(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            result = ToolRegistryAgent._execute_alert_action(
                "notify", {"team": "Network On-Call", "action": "message", "message": "hi"}, "target", "exec14",
            )
        assert result["success"] is True
        mock_slack.assert_called_once()


class TestDispatchFromExecuteTool:
    def test_execute_tool_impl_routes_notify_to_action_handler(self):
        team = _fake_team(slack_channel="#net-oncall")
        with _patch_session(), \
             patch.object(ToolRegistryAgent, "_resolve_notify_team", return_value=team), \
             patch("agentic_os.services.notifications._post_slack", return_value=True) as mock_slack:

            result = ToolRegistryAgent._execute_tool_impl(
                "notify", {"team": "Network On-Call", "action": "message", "message": "hi"}, container="sentinel_senses",
            )

        assert result["success"] is True
        mock_slack.assert_called_once()
