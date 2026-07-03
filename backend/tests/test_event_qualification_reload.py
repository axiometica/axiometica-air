"""
Regression test for the qualification-weights cache bug.

EventQualificationService is a process-lifetime singleton that loaded its
weights once and never again — saving new qualification config via
PUT/reset /risk-config had zero effect until the backend process restarted.
Verifies both the underlying reload mechanism and that the API endpoints
actually trigger it.
"""
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from agentic_os.db.repositories import RiskWeightConfigRepository
from agentic_os.services.event_qualification import EventQualificationService


class TestReloadWeights:
    def test_reload_picks_up_changed_db_value(self, db):
        repo = RiskWeightConfigRepository(db)
        repo.create_or_update("default", {"event_type_multipliers": {"x.y.z": 1.0}})

        service = EventQualificationService(db_session=db)
        assert service.weights["event_type_multipliers"]["x.y.z"] == 1.0

        repo.create_or_update("default", {"event_type_multipliers": {"x.y.z": 3.0}})
        service.reload_weights()
        assert service.weights["event_type_multipliers"]["x.y.z"] == 3.0

    def test_reload_with_no_session_opens_its_own(self, db, test_engine):
        """The real singleton is constructed with db_session=None — reload_weights()
        must still be able to read the DB rather than silently no-op.

        reload_weights() opens its own SessionLocal() rather than reusing the
        test's session-on-a-connection, so it must be pointed at the test
        database explicitly — otherwise it reads whatever DATABASE_URL the
        app is configured with, which in production is correct but in this
        test harness is a different database than the `db` fixture writes to.
        """
        repo = RiskWeightConfigRepository(db)
        repo.create_or_update("default", {"event_type_multipliers": {"a.b.c": 2.5}})

        test_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        with patch("agentic_os.db.database.SessionLocal", test_session_factory):
            service = EventQualificationService(db_session=None)
            service.reload_weights()

        assert service.weights["event_type_multipliers"]["a.b.c"] == 2.5
        # Must not leak a permanently-open session back onto the instance
        assert service.db_session is None


class TestRiskConfigEndpointsTriggerReload:
    def test_put_default_config_triggers_reload(self, client_authenticated):
        with patch("agentic_os.api.routes.risk_config.reload_qualification_service") as mock_reload:
            response = client_authenticated.put(
                "/api/risk-config?config_key=default",
                json={"weights": {"event_type_multipliers": {"infrastructure.compute.cpu_high": 1.5}}},
            )
            assert response.status_code == 200
            mock_reload.assert_called_once()

    def test_put_non_default_config_does_not_trigger_reload(self, client_authenticated):
        """Only the 'default' config is actually read by the live qualification service."""
        with patch("agentic_os.api.routes.risk_config.reload_qualification_service") as mock_reload:
            response = client_authenticated.put(
                "/api/risk-config?config_key=some_other_profile",
                json={"weights": {"event_type_multipliers": {"x.y.z": 1.0}}},
            )
            assert response.status_code == 200
            mock_reload.assert_not_called()

    def test_reset_default_config_triggers_reload(self, client_authenticated):
        with patch("agentic_os.api.routes.risk_config.reload_qualification_service") as mock_reload:
            response = client_authenticated.post("/api/risk-config/reset?config_key=default")
            assert response.status_code == 200
            mock_reload.assert_called_once()
