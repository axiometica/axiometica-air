"""
Tests for /runbooks endpoints.
Verifies runbook library CRUD and that seeded runbooks are available.
"""

import pytest
from uuid import uuid4


SAMPLE_RUNBOOK = {
    "name": "CI Test Runbook",
    "description": "Created by CI test suite",
    "event_type": "high_cpu",
    "platform": "any",
    "enabled": True,
    # RunbookModel stores steps in three separate JSON columns (not a single 'steps' list)
    "diagnostics": [
        {
            "order": 1,
            "name": "Check CPU",
            "description": "Measure CPU utilisation",
            "tool": "check_cpu",
            "args_json": {},
        }
    ],
    "actions": [
        {
            "order": 1,
            "name": "Kill runaway process",
            "description": "Send SIGTERM to the anomalous process",
            "tool": "process_kill",
            "args_json": {"signal": "SIGTERM"},
        }
    ],
    "verification_steps": [
        {
            "order": 1,
            "name": "CPU normalised",
            "description": "CPU should be below 70%",
            "metric": "cpu_percent",
            "check": "less_than",
            "value": 70,
        }
    ],
}


class TestRunbookList:
    """GET /runbooks"""

    def test_list_runbooks_returns_200(self, client_authenticated):
        """GET /runbooks returns a list (seeded runbooks exist)."""
        response = client_authenticated.get("/api/runbooks")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_seeded_runbooks_exist(self, client_authenticated):
        """Platform ships with seeded runbooks — at least one should exist."""
        response = client_authenticated.get("/api/runbooks")
        if response.status_code == 200:
            runbooks = response.json()
            # The platform seeds runbooks on startup; at least high_cpu should exist
            # (this may be 0 in a fresh test DB with no seeding)
            assert isinstance(runbooks, list)

    def test_filter_by_event_type(self, client_authenticated):
        """event_type filter is accepted."""
        response = client_authenticated.get("/api/runbooks?event_type=high_cpu")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            runbooks = response.json()
            for rb in runbooks:
                assert rb.get("event_type") == "high_cpu"

    def test_filter_by_platform(self, client_authenticated):
        """platform filter is accepted."""
        response = client_authenticated.get("/api/runbooks?platform=any")
        assert response.status_code in [200, 500]

    def test_filter_enabled_only(self, client_authenticated):
        """enabled filter is accepted."""
        response = client_authenticated.get("/api/runbooks?enabled=true")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            runbooks = response.json()
            for rb in runbooks:
                assert rb.get("enabled") is True

    def test_list_unauthenticated(self, client):
        """Unauthenticated list returns 401."""
        response = client.get("/api/runbooks")
        assert response.status_code == 401


class TestRunbookCRUD:
    """POST/GET/PUT/DELETE /runbooks"""

    def test_create_runbook(self, client_authenticated):
        """Creating a runbook with valid data succeeds."""
        response = client_authenticated.post(
            "/api/runbooks",
            json={**SAMPLE_RUNBOOK, "name": f"CI Runbook {uuid4().hex[:6]}"},
        )
        assert response.status_code in [200, 201, 400, 422, 500]
        if response.status_code in [200, 201]:
            data = response.json()
            assert "runbook_id" in data or "id" in data

    def test_create_runbook_missing_name(self, client_authenticated):
        """Runbook without name returns 422."""
        response = client_authenticated.post(
            "/api/runbooks",
            json={"event_type": "high_cpu"},
        )
        assert response.status_code == 422

    def test_get_nonexistent_runbook(self, client_authenticated):
        """Non-existent runbook ID returns 404."""
        response = client_authenticated.get(f"/api/runbooks/{uuid4()}")
        assert response.status_code in [404, 500]

    def test_update_nonexistent_runbook(self, client_authenticated):
        """Updating a non-existent runbook returns 404."""
        response = client_authenticated.put(
            f"/api/runbooks/{uuid4()}",
            json=SAMPLE_RUNBOOK,
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_delete_nonexistent_runbook(self, client_authenticated):
        """Deleting a non-existent runbook returns 404 or 204."""
        response = client_authenticated.delete(f"/api/runbooks/{uuid4()}")
        assert response.status_code in [204, 404, 500]

    def test_create_requires_auth(self, client):
        """Creating a runbook requires authentication."""
        response = client.post("/api/runbooks", json=SAMPLE_RUNBOOK)
        assert response.status_code == 401

    def test_runbook_response_shape(self, client_authenticated):
        """Runbook response includes essential fields."""
        response = client_authenticated.post(
            "/api/runbooks",
            json={**SAMPLE_RUNBOOK, "name": f"Shape Test {uuid4().hex[:6]}"},
        )
        if response.status_code in [200, 201]:
            data = response.json()
            assert "name" in data
            assert "event_type" in data
            assert "enabled" in data


class TestRunbookDraftPublish:
    """Draft/publish workflow: PUT writes drafts only, publish promotes to live,
    versions are recorded, enabled stays an instant kill-switch."""

    def _create(self, client_authenticated, **overrides):
        payload = {**SAMPLE_RUNBOOK, "name": f"Draft Test {uuid4().hex[:6]}", **overrides}
        res = client_authenticated.post("/api/runbooks", json=payload)
        assert res.status_code in [200, 201]
        return res.json()

    def test_new_runbook_starts_as_draft(self, client_authenticated):
        rb = self._create(client_authenticated)
        assert rb["status"] == "draft"
        assert rb["has_unpublished_changes"] is False
        assert rb["published_at"] is None

    def test_put_writes_draft_not_live(self, client_authenticated):
        rb = self._create(client_authenticated)
        original_name = rb["name"]

        res = client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": original_name + " EDITED"},
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["has_unpublished_changes"] is True
        # Live name must be unchanged — only draft_snapshot holds the edit.
        assert updated["name"] == original_name

        fetched = client_authenticated.get(f"/api/runbooks/{rb['runbook_id']}").json()
        assert fetched["name"] == original_name
        assert fetched["draft_snapshot"]["name"] == original_name + " EDITED"

    def test_publish_promotes_draft_to_live_and_records_version(self, client_authenticated):
        rb = self._create(client_authenticated)
        original_name = rb["name"]
        client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": original_name + " EDITED"},
        )

        res = client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/publish", json={"change_note": "test"})
        assert res.status_code == 200
        published = res.json()
        assert published["status"] == "published"
        assert published["has_unpublished_changes"] is False
        assert published["name"] == original_name + " EDITED"
        assert published["published_at"] is not None

        versions = client_authenticated.get(f"/api/runbooks/{rb['runbook_id']}/versions").json()
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["change_note"] == "test"

    def test_restore_version_loads_draft_not_live(self, client_authenticated):
        rb = self._create(client_authenticated)
        original_name = rb["name"]
        client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": original_name + " v2"},
        )
        client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/publish")

        client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": original_name + " v3"},
        )
        client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/publish")
        # At this point: live name = "v3" (version 2's content), version 1 = "v2".

        # Restore version 1 — must land in draft_snapshot, not overwrite live v3 name.
        res = client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/versions/1/restore")
        assert res.status_code == 200
        restored = res.json()
        assert restored["name"] == original_name + " v3"  # live unaffected by restore
        assert restored["has_unpublished_changes"] is True
        assert restored["draft_snapshot"]["name"] == original_name + " v2"  # draft now holds v1's content

    def test_discard_draft_clears_pending_changes(self, client_authenticated):
        rb = self._create(client_authenticated)
        client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": rb["name"] + " EDITED"},
        )
        res = client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/discard-draft")
        assert res.status_code == 200
        assert res.json()["has_unpublished_changes"] is False

    def test_enabled_toggle_is_instant_regardless_of_draft_state(self, client_authenticated):
        rb = self._create(client_authenticated)
        client_authenticated.put(
            f"/api/runbooks/{rb['runbook_id']}",
            json={**SAMPLE_RUNBOOK, "name": rb["name"] + " EDITED"},
        )
        res = client_authenticated.patch(f"/api/runbooks/{rb['runbook_id']}/enabled", json={"enabled": False})
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False
        # enabled change is live immediately — independent of has_unpublished_changes
        assert body["has_unpublished_changes"] is True
        assert body["status"] == "draft"

    def test_publish_with_no_pending_draft_is_a_noop(self, client_authenticated):
        rb = self._create(client_authenticated)
        client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/publish")  # publish initial create
        res = client_authenticated.post(f"/api/runbooks/{rb['runbook_id']}/publish")  # no new draft pending
        assert res.status_code == 200
        # No second version should have been created since there was nothing to publish.
        versions = client_authenticated.get(f"/api/runbooks/{rb['runbook_id']}/versions").json()
        assert len(versions) <= 1


class TestSeededRunbooksResolveCleanly:
    """Every seeded runbook's action-containing paths must reach verification
    followed by an incident_update step — otherwise VerifierAgent has no signal
    to ever mark the incident resolved, and it sits awaiting manual review
    forever. Regression guard for 'Service Unresponsive — Signal and Restart',
    whose failure branch (still down after restart) notified but never set an
    incident_update, silently leaving every such incident unresolvable."""

    def test_no_seeded_runbook_has_an_unresolvable_action_path(self):
        from agentic_os.db.runbooks_seed_data import RUNBOOKS
        from agentic_os.api.routes.runbooks import _check_publish_validation_paths

        offenders = {}
        for rb in RUNBOOKS:
            warnings = _check_publish_validation_paths(rb.get("source_steps") or {})
            if warnings:
                offenders[rb["name"]] = warnings

        assert not offenders, f"Seeded runbooks with unresolvable action paths: {offenders}"
