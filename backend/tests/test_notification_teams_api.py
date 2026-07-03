"""
Tests for the Notification Teams CRUD API — a standalone registry of named
teams (PagerDuty routing key / Slack channel / email recipients / webhook),
looked up by name to route notify/alert_escalate/alert_update/send_alert
actions instead of always hitting the global defaults.

Unlike connectors.py's routes, notification_teams.py uses FastAPI's injected
session (Depends(get_session)), so the standard `client_authenticated`/`db`
fixtures see the same data without any SessionLocal patching.
"""
from agentic_os.db.models import NotificationTeamModel


class TestCreateNotificationTeam:
    def test_create_with_all_channels(self, client_authenticated, db):
        resp = client_authenticated.post("/api/notification-teams", json={
            "name": "Network On-Call",
            "pagerduty_routing_key": "R0123456789ABCDEF",
            "slack_channel": "#network-oncall",
            "email_recipients": "net-oncall@example.com, net-lead@example.com",
            "webhook_url": "https://example.com/hooks/notify",
            "webhook_secret": "shh",
            "enabled": True,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Network On-Call"
        assert body["pagerduty_routing_key_set"] is True
        assert body["webhook_secret_set"] is True
        assert body["slack_channel"] == "#network-oncall"
        assert "pagerduty_routing_key" not in body
        assert "webhook_secret" not in body

        db.expire_all()
        row = db.query(NotificationTeamModel).filter_by(name="Network On-Call").first()
        assert row is not None
        assert row.pagerduty_routing_key == "R0123456789ABCDEF"

    def test_create_with_no_channels_is_allowed(self, client_authenticated):
        resp = client_authenticated.post("/api/notification-teams", json={"name": "Empty Team"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["pagerduty_routing_key_set"] is False
        assert body["slack_channel"] is None

    def test_create_duplicate_name_rejected(self, client_authenticated):
        client_authenticated.post("/api/notification-teams", json={"name": "Dup Team"})
        resp = client_authenticated.post("/api/notification-teams", json={"name": "dup team"})
        assert resp.status_code == 409


class TestListNotificationTeams:
    def test_list_returns_created_teams(self, client_authenticated):
        client_authenticated.post("/api/notification-teams", json={"name": "Team A"})
        client_authenticated.post("/api/notification-teams", json={"name": "Team B"})
        resp = client_authenticated.get("/api/notification-teams")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "Team A" in names
        assert "Team B" in names


class TestUpdateNotificationTeam:
    def test_update_keeps_secret_when_blank(self, client_authenticated, db):
        created = client_authenticated.post("/api/notification-teams", json={
            "name": "Keep Test", "pagerduty_routing_key": "original-key",
        }).json()

        resp = client_authenticated.put(f"/api/notification-teams/{created['team_id']}", json={
            "slack_channel": "#new-channel",
        })
        assert resp.status_code == 200
        assert resp.json()["pagerduty_routing_key_set"] is True
        assert resp.json()["slack_channel"] == "#new-channel"

        db.expire_all()
        row = db.query(NotificationTeamModel).filter_by(name="Keep Test").first()
        assert row.pagerduty_routing_key == "original-key"

    def test_update_replaces_secret_when_value_given(self, client_authenticated, db):
        created = client_authenticated.post("/api/notification-teams", json={
            "name": "Replace Test", "pagerduty_routing_key": "old-key",
        }).json()

        resp = client_authenticated.put(f"/api/notification-teams/{created['team_id']}", json={
            "pagerduty_routing_key": "new-key",
        })
        assert resp.status_code == 200

        db.expire_all()
        row = db.query(NotificationTeamModel).filter_by(name="Replace Test").first()
        assert row.pagerduty_routing_key == "new-key"

    def test_update_clears_secret_with_dash(self, client_authenticated, db):
        created = client_authenticated.post("/api/notification-teams", json={
            "name": "Clear Test", "webhook_url": "https://x.example.com", "webhook_secret": "secret123",
        }).json()

        resp = client_authenticated.put(f"/api/notification-teams/{created['team_id']}", json={
            "webhook_secret": "-",
        })
        assert resp.status_code == 200
        assert resp.json()["webhook_secret_set"] is False

        db.expire_all()
        row = db.query(NotificationTeamModel).filter_by(name="Clear Test").first()
        assert row.webhook_secret is None

    def test_update_nonexistent_team_404s(self, client_authenticated):
        resp = client_authenticated.put(
            "/api/notification-teams/00000000-0000-0000-0000-000000000000",
            json={"slack_channel": "#x"},
        )
        assert resp.status_code == 404


class TestDeleteNotificationTeam:
    def test_delete_removes_team(self, client_authenticated, db):
        created = client_authenticated.post("/api/notification-teams", json={"name": "Delete Me"}).json()
        resp = client_authenticated.delete(f"/api/notification-teams/{created['team_id']}")
        assert resp.status_code == 204

        db.expire_all()
        assert db.query(NotificationTeamModel).filter_by(name="Delete Me").first() is None

    def test_delete_nonexistent_team_404s(self, client_authenticated):
        resp = client_authenticated.delete("/api/notification-teams/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
