"""
Tests for the PagerDuty outbound routing_key: encrypted clear/keep/replace
semantics on save, routing_key_set surfaced (never the raw key) on read, and
get_pagerduty_client() returning a usable client only when one is actually
configured.

connectors.py's routes open their own SessionLocal() rather than using
FastAPI's injected session (same pattern as splunk_webhook.py), so they
bypass the `db`/`client` fixtures' test-DB override unless patched directly.
"""
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from agentic_os.db.models import ConnectorConfigModel
from agentic_os.security.crypto import decrypt_if_encrypted, encrypt, is_encrypted


def _patched_session(test_engine):
    return patch(
        "agentic_os.api.routes.connectors.SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=test_engine),
    )


class TestSaveRoutingKey:
    def test_save_sets_and_encrypts_routing_key(self, client_authenticated, db, test_engine):
        with _patched_session(test_engine):
            resp = client_authenticated.post(
                "/api/connectors/pagerduty/alert-config",
                json={
                    "default_criticality": "warning",
                    "default_event_type": "unknown",
                    "enabled": True,
                    "allow_auto_remediation": False,
                    "routing_key": "R0123456789ABCDEF",
                },
            )
        assert resp.status_code == 200

        db.expire_all()
        cfg = db.query(ConnectorConfigModel).filter_by(id="pagerduty").first()
        assert cfg is not None
        stored = cfg.config_json["routing_key"]
        assert is_encrypted(stored)
        assert decrypt_if_encrypted(stored) == "R0123456789ABCDEF"

    def test_save_with_blank_routing_key_keeps_existing(self, client_authenticated, db, test_engine):
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True,
            config_json={"routing_key": encrypt("existing-key")},
        ))
        db.commit()

        with _patched_session(test_engine):
            resp = client_authenticated.post(
                "/api/connectors/pagerduty/alert-config",
                json={
                    "default_criticality": "warning",
                    "default_event_type": "unknown",
                    "enabled": True,
                    "allow_auto_remediation": False,
                },
            )
        assert resp.status_code == 200

        db.expire_all()
        cfg = db.query(ConnectorConfigModel).filter_by(id="pagerduty").first()
        assert decrypt_if_encrypted(cfg.config_json["routing_key"]) == "existing-key"

    def test_save_with_dash_clears_routing_key(self, client_authenticated, db, test_engine):
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True,
            config_json={"routing_key": encrypt("existing-key")},
        ))
        db.commit()

        with _patched_session(test_engine):
            resp = client_authenticated.post(
                "/api/connectors/pagerduty/alert-config",
                json={
                    "default_criticality": "warning",
                    "default_event_type": "unknown",
                    "enabled": True,
                    "allow_auto_remediation": False,
                    "routing_key": "-",
                },
            )
        assert resp.status_code == 200

        db.expire_all()
        cfg = db.query(ConnectorConfigModel).filter_by(id="pagerduty").first()
        assert "routing_key" not in cfg.config_json


class TestGetConnectorExposesOnlySetFlag:
    def test_routing_key_set_true_never_leaks_raw_key(self, client_authenticated, db, test_engine):
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True,
            config_json={"routing_key": encrypt("super-secret-key")},
        ))
        db.commit()

        with _patched_session(test_engine):
            resp = client_authenticated.get("/api/connectors/pagerduty")
        assert resp.status_code == 200
        body = resp.json()
        assert body["routing_key_set"] is True
        assert "super-secret-key" not in resp.text
        assert "routing_key" not in body

    def test_routing_key_set_false_when_unconfigured(self, client_authenticated, db, test_engine):
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True, config_json={},
        ))
        db.commit()

        with _patched_session(test_engine):
            resp = client_authenticated.get("/api/connectors/pagerduty")
        assert resp.status_code == 200
        assert resp.json()["routing_key_set"] is False


class TestGetPagerdutyClient:
    def test_returns_none_when_not_configured(self, db):
        from agentic_os.api.routes.connectors import get_pagerduty_client
        assert get_pagerduty_client(db) is None

    def test_returns_none_when_disabled(self, db):
        from agentic_os.api.routes.connectors import get_pagerduty_client
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=False,
            config_json={"routing_key": encrypt("a-key")},
        ))
        db.commit()
        assert get_pagerduty_client(db) is None

    def test_returns_none_when_no_routing_key(self, db):
        from agentic_os.api.routes.connectors import get_pagerduty_client
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True, config_json={},
        ))
        db.commit()
        assert get_pagerduty_client(db) is None

    def test_returns_client_with_decrypted_routing_key(self, db):
        from agentic_os.api.routes.connectors import get_pagerduty_client
        db.merge(ConnectorConfigModel(
            id="pagerduty", display_name="PagerDuty", enabled=True,
            config_json={"routing_key": encrypt("R0123456789ABCDEF")},
        ))
        db.commit()
        result = get_pagerduty_client(db)
        assert result is not None
        assert result.routing_key == "R0123456789ABCDEF"
