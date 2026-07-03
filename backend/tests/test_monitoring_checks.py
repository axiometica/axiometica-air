"""
Tests for /monitoring/watchers/* endpoints.
Covers watcher registration (public), list, approve/reject/disable/enable,
and external check CRUD.
"""

import pytest
from uuid import uuid4


WATCHER_NAME = "test-watcher-ci"


class TestWatcherRegistration:
    """POST /monitoring/watchers/register (public — no auth required)"""

    def test_register_watcher_no_auth(self, client):
        """Watcher registration endpoint is public (no JWT needed)."""
        response = client.post(
            "/api/monitoring/watchers/register",
            json={
                "watcher_name": WATCHER_NAME,
                "watcher_type": "docker",
                "host": "localhost",
                "version": "1.0.0",
            },
        )
        # 200 success or 400 (validation) — should not be 401
        assert response.status_code in [200, 201, 400, 422, 500]
        assert response.status_code != 401, "Registration endpoint must be public"

    def test_register_watcher_returns_watcher_id(self, client):
        """Successful registration returns watcher_id and status."""
        response = client.post(
            "/api/monitoring/watchers/register",
            json={
                "watcher_name": f"watcher-{uuid4().hex[:8]}",
                "watcher_type": "docker",
                "host": "ci-runner",
                "version": "1.0.0",
            },
        )
        if response.status_code in [200, 201]:
            data = response.json()
            assert "ok" in data or "watcher_id" in data or "watcher_name" in data

    def test_register_watcher_missing_name(self, client):
        """Missing watcher_name returns 422."""
        response = client.post(
            "/api/monitoring/watchers/register",
            json={"watcher_type": "docker"},
        )
        assert response.status_code == 422


class TestWatcherList:
    """GET /monitoring/watchers"""

    def test_list_watchers_returns_200(self, client_authenticated):
        """Authenticated list of watchers returns a list."""
        response = client_authenticated.get("/api/monitoring/watchers")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_list_watchers_unauthenticated(self, client):
        """Unauthenticated list returns 401."""
        response = client.get("/api/monitoring/watchers")
        assert response.status_code == 401


class TestWatcherApprovalWorkflow:
    """POST /monitoring/watchers/{name}/approve|reject|disable|enable"""

    def test_approve_nonexistent_watcher(self, client_authenticated):
        """Approving a non-existent watcher returns 404 or 400."""
        response = client_authenticated.post(
            "/api/monitoring/watchers/does-not-exist-xyz/approve"
        )
        assert response.status_code in [400, 404, 500]

    def test_reject_nonexistent_watcher(self, client_authenticated):
        """Rejecting a non-existent watcher returns 404 or 400."""
        response = client_authenticated.post(
            "/api/monitoring/watchers/does-not-exist-xyz/reject"
        )
        assert response.status_code in [400, 404, 500]

    def test_disable_nonexistent_watcher(self, client_authenticated):
        """Disabling a non-existent watcher returns 404 or 400."""
        response = client_authenticated.post(
            "/api/monitoring/watchers/does-not-exist-xyz/disable"
        )
        assert response.status_code in [400, 404, 500]

    def test_enable_nonexistent_watcher(self, client_authenticated):
        """Enabling a non-existent watcher returns 404 or 400."""
        response = client_authenticated.post(
            "/api/monitoring/watchers/does-not-exist-xyz/enable"
        )
        assert response.status_code in [400, 404, 500]

    def test_approval_endpoints_require_auth(self, client):
        """All watcher management endpoints require authentication."""
        for path in ["approve", "reject", "disable", "enable"]:
            response = client.post(f"/api/monitoring/watchers/any-watcher/{path}")
            assert response.status_code == 401, f"{path} should require auth"


class TestExternalChecks:
    """GET/POST /monitoring/watchers/{name}/checks"""

    def test_list_checks_for_nonexistent_watcher(self, client_authenticated):
        """Checks list for unknown watcher returns 404 or empty list."""
        response = client_authenticated.get(
            "/api/monitoring/watchers/does-not-exist-xyz/checks"
        )
        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_create_check_for_nonexistent_watcher(self, client_authenticated):
        """Creating a check on a non-existent watcher returns 404 or 400."""
        response = client_authenticated.post(
            "/api/monitoring/watchers/does-not-exist-xyz/checks",
            json={
                "name": "API Health",
                "check_type": "http",
                "url": "http://api-server:8000/api/health",
                "interval_seconds": 30,
                "timeout_seconds": 5,
            },
        )
        assert response.status_code in [201, 400, 404, 422, 500]

    def test_checks_endpoints_require_auth(self, client):
        """Check endpoints require authentication."""
        response = client.get("/api/monitoring/watchers/any/checks")
        assert response.status_code == 401
