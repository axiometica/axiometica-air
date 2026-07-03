"""
Regression test for a second class of secrets-encryption bug: several
read paths fetched connector secrets directly from config_json without
decrypting them, even though connectors.py's own helpers (_get_snow_push,
_require_creds) were already fixed. Found via a real ServiceNow auto-push
failing with 401 once the password was actually encrypted in the DB.

Covers:
  - IncidentPush.auto_push_if_configured (ServiceNow auto-create/update)
  - alert_webhooks._validate_secret (Datadog/Dynatrace/Prometheus/etc.)
  - splunk_webhook's X-Splunk-Webhook-Token check
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from agentic_os.core.models import WorkflowType, LifecycleState
from agentic_os.db.models import ConnectorConfigModel, WorkflowStateModel
from agentic_os.security.crypto import encrypt


class TestServiceNowAutoPushDecryption:
    @pytest.mark.asyncio
    async def test_auto_push_uses_decrypted_password(self, db):
        from agentic_os.connectors.servicenow.incident_push import IncidentPush

        plaintext_password = "hunter2-real-password"
        db.merge(ConnectorConfigModel(
            id="servicenow",
            display_name="ServiceNow",
            enabled=True,
            config_json={
                "base_url": "https://example.service-now.com",
                "username": "api_user",
                "password": encrypt(plaintext_password),
                "incident_sync": {"enabled": True, "auto_create": True},
            },
        ))
        workflow_id = uuid4()
        db.add(WorkflowStateModel(
            workflow_id=workflow_id,
            workflow_type=WorkflowType.INCIDENT.value,
            lifecycle_state=LifecycleState.OPEN.value,
            severity="high",
            title="Test Incident",
            context={"alert_payload": {"type": "high_cpu"}},
        ))
        db.commit()

        captured = {}

        def _fake_client(base_url, username, password):
            captured["password"] = password
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = False
            mock_client.create_record = AsyncMock(return_value={"sys_id": "abc", "number": "INC0001"})
            return mock_client

        with patch("agentic_os.connectors.servicenow.incident_push.ServiceNowClient", side_effect=_fake_client):
            with patch.object(IncidentPush, "_lookup_ci", AsyncMock(return_value=None)):
                result = await IncidentPush.auto_push_if_configured(
                    db_session=db,
                    workflow_id=str(workflow_id),
                    trigger_event="created",
                    new_lifecycle_state="open",
                )

        assert captured["password"] == plaintext_password
        assert result["status"] == "created"


class TestAlertWebhookSecretDecryption:
    def test_validate_secret_accepts_plaintext_matching_encrypted_stored_value(self):
        from agentic_os.api.routes.alert_webhooks import _validate_secret

        real_secret = "datadog-shared-secret-123"
        config = {"webhook_secret": encrypt(real_secret)}

        # Must not raise — the provided plaintext matches once decrypted
        _validate_secret(config, real_secret, "datadog")

    def test_validate_secret_rejects_wrong_value(self):
        from agentic_os.api.routes.alert_webhooks import _validate_secret
        from fastapi import HTTPException

        config = {"webhook_secret": encrypt("real-secret")}
        with pytest.raises(HTTPException) as exc_info:
            _validate_secret(config, "wrong-value", "datadog")
        assert exc_info.value.status_code == 401


class TestSplunkWebhookSecretDecryption:
    """splunk_webhook.py's route opens its own raw SessionLocal() rather than
    using FastAPI's injected session, so it bypasses the test DB override the
    `db`/`client` fixtures rely on — it must be patched directly to see data
    written through the `db` fixture."""

    def test_splunk_webhook_accepts_plaintext_token_matching_encrypted_stored_value(self, client, db, test_engine):
        real_secret = "splunk-shared-token-456"
        db.merge(ConnectorConfigModel(
            id="splunk",
            display_name="Splunk",
            enabled=True,
            config_json={
                "base_url": "https://splunk.example.com:8089",
                "token": encrypt("unrelated-api-token"),
                "webhook_secret": encrypt(real_secret),
                "allow_auto_remediation": False,
                "allow_storm_detection": True,
            },
        ))
        db.commit()

        test_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        with patch("agentic_os.api.routes.splunk_webhook.SessionLocal", test_session_factory):
            response = client.post(
                "/api/connectors/splunk/webhook",
                headers={"X-Splunk-Webhook-Token": real_secret},
                json={"search_name": "test search", "host": "test-host"},
            )
        # 401 would mean the secret comparison failed (the bug); anything else
        # means auth passed and it moved on to payload parsing/qualification.
        assert response.status_code != 401

    def test_splunk_webhook_rejects_wrong_token(self, client, db, test_engine):
        db.merge(ConnectorConfigModel(
            id="splunk",
            display_name="Splunk",
            enabled=True,
            config_json={
                "base_url": "https://splunk.example.com:8089",
                "webhook_secret": encrypt("real-secret"),
                "allow_auto_remediation": False,
                "allow_storm_detection": True,
            },
        ))
        db.commit()

        test_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        with patch("agentic_os.api.routes.splunk_webhook.SessionLocal", test_session_factory):
            response = client.post(
                "/api/connectors/splunk/webhook",
                headers={"X-Splunk-Webhook-Token": "wrong-token"},
                json={"search_name": "test search", "host": "test-host"},
            )
        assert response.status_code == 401
