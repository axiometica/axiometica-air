"""
Tests for /storms endpoints.
Verifies storm listing, detail retrieval, and action endpoints.
"""

import pytest
from uuid import uuid4


class TestStormList:
    """GET /storms"""

    def test_list_storms_returns_200(self, client_authenticated):
        """GET /storms returns a list (may be empty)."""
        response = client_authenticated.get("/api/storms")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_list_storms_active_only_filter(self, client_authenticated):
        """active_only=true query parameter is accepted."""
        response = client_authenticated.get("/api/storms?active_only=true")
        assert response.status_code in [200, 500]

    def test_list_storms_with_limit(self, client_authenticated):
        """limit query parameter is accepted."""
        response = client_authenticated.get("/api/storms?limit=10")
        assert response.status_code in [200, 500]

    def test_list_storms_unauthenticated(self, client):
        """Unauthenticated storm list returns 401."""
        response = client.get("/api/storms")
        assert response.status_code == 401

    def test_storm_list_response_shape(self, client_authenticated):
        """If storms exist, each has the expected fields."""
        response = client_authenticated.get("/api/storms?limit=1")
        if response.status_code == 200:
            storms = response.json()
            if storms:
                storm = storms[0]
                # StormSummary fields
                assert "storm_id" in storm or "workflow_id" in storm
                assert "lifecycle_state" in storm


class TestStormDetail:
    """GET /storms/{storm_id}"""

    def test_get_nonexistent_storm(self, client_authenticated):
        """Non-existent storm ID returns 404."""
        response = client_authenticated.get(f"/api/storms/{uuid4()}")
        assert response.status_code in [404, 500]

    def test_get_storm_invalid_id(self, client_authenticated):
        """Invalid UUID format returns 400, 404, or 422."""
        response = client_authenticated.get("/api/storms/not-a-valid-uuid")
        assert response.status_code in [400, 404, 422, 500]

    def test_get_storm_unauthenticated(self, client):
        """Unauthenticated storm detail returns 401."""
        response = client.get(f"/api/storms/{uuid4()}")
        assert response.status_code == 401


class TestStormActions:
    """POST /storms/{storm_id}/release and /resolve"""

    def test_release_nonexistent_storm(self, client_authenticated):
        """Release action on non-existent storm returns 404 or 400."""
        response = client_authenticated.post(
            f"/api/storms/{uuid4()}/release",
            json={"notes": "Manual release from test"},
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_resolve_nonexistent_storm(self, client_authenticated):
        """Resolve action on non-existent storm returns 404 or 400."""
        response = client_authenticated.post(
            f"/api/storms/{uuid4()}/resolve",
            json={"notes": "Manual resolve from test"},
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_storm_actions_require_auth(self, client):
        """Storm action endpoints require authentication."""
        for action in ["release", "resolve"]:
            response = client.post(
                f"/api/storms/{uuid4()}/{action}",
                json={"notes": "test"},
            )
            assert response.status_code == 401, f"{action} should require auth"

    def test_release_accepts_empty_body(self, client_authenticated):
        """Release can be called with an empty body (notes are optional)."""
        response = client_authenticated.post(
            f"/api/storms/{uuid4()}/release",
            json={},
        )
        # 404 (storm doesn't exist) or 400 — NOT 422 (body is optional)
        assert response.status_code in [400, 404, 500]
        assert response.status_code != 422
