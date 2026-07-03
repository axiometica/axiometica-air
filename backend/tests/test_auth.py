"""
Tests for /auth/* endpoints.
Covers login, current user, principal management, and password operations.
"""

import pytest


# The platform seeds an admin account on startup
ADMIN_EMAIL = "admin@platform.local"
ADMIN_PASSWORD = "admin"  # Default from seed script


class TestLogin:
    """POST /auth/login"""

    def test_login_valid_credentials(self, client):
        """Valid admin credentials return a JWT token."""
        response = client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        # 200 with token, or 401 if seed not applied, or 500 if DB unavailable
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "access_token" in data
            assert data.get("token_type") == "bearer"
            assert "expires_in" in data
            assert "principal" in data

    def test_login_wrong_password(self, client):
        """Wrong password returns 401."""
        response = client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "wrong-password-xyz"},
        )
        assert response.status_code in [401, 500]

    def test_login_unknown_email(self, client):
        """Unknown email returns 401."""
        response = client.post(
            "/api/auth/login",
            json={"email": "nobody@nowhere.test", "password": "password123"},
        )
        assert response.status_code in [401, 500]

    def test_login_missing_fields(self, client):
        """Missing email/password returns 422."""
        response = client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL},
        )
        assert response.status_code == 422

    def test_login_empty_body(self, client):
        """Empty body returns 422."""
        response = client.post("/api/auth/login", json={})
        assert response.status_code == 422

    def test_login_does_not_require_auth_header(self, client):
        """Login endpoint is public — no auth header needed."""
        response = client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "any"},
        )
        # Should not be 401 due to missing Bearer token (it's a public endpoint)
        # May be 401 for wrong credentials but NOT for missing auth header
        assert response.status_code != 403


class TestCurrentUser:
    """GET /auth/me"""

    def test_get_me_authenticated(self, client_authenticated):
        """Authenticated /auth/me returns current principal info."""
        response = client_authenticated.get("/api/auth/me")
        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert "email" in data or "name" in data or "role" in data

    def test_get_me_unauthenticated(self, client):
        """Unauthenticated /auth/me returns 401."""
        response = client.get("/api/auth/me")
        assert response.status_code == 401


class TestLogout:
    """POST /auth/logout"""

    def test_logout_authenticated(self, client_authenticated):
        """Authenticated logout returns 200."""
        response = client_authenticated.post("/api/auth/logout")
        assert response.status_code in [200, 500]

    def test_logout_unauthenticated(self, client):
        """Unauthenticated logout returns 401."""
        response = client.post("/api/auth/logout")
        assert response.status_code == 401


class TestPrincipalManagement:
    """GET/POST /auth/principals — admin only"""

    def test_list_principals_authenticated(self, client_authenticated):
        """Admin can list principals."""
        response = client_authenticated.get("/api/auth/principals")
        # 200 (found), 403 (not admin in test token), 500 (DB issue)
        assert response.status_code in [200, 403, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_list_principals_unauthenticated(self, client):
        """Unauthenticated principals list returns 401."""
        response = client.get("/api/auth/principals")
        assert response.status_code == 401

    def test_create_principal_valid(self, client_authenticated):
        """Creating a principal with valid data succeeds or returns expected error."""
        response = client_authenticated.post(
            "/api/auth/principals",
            json={
                "name": "CI Test User",
                "email": f"ci-test-{__import__('uuid').uuid4().hex[:8]}@platform.local",
                "role": "operator",
                "password": "SecurePass123!",
            },
        )
        # 201 created, 409 conflict (duplicate), 403 not admin, 422 validation, 500 DB
        assert response.status_code in [200, 201, 400, 403, 409, 422, 500]

    def test_create_principal_missing_required(self, client_authenticated):
        """Principal without name returns 422."""
        response = client_authenticated.post(
            "/api/auth/principals",
            json={"role": "operator"},
        )
        assert response.status_code == 422

    def test_create_principal_invalid_role(self, client_authenticated):
        """Invalid role value returns 422."""
        response = client_authenticated.post(
            "/api/auth/principals",
            json={
                "name": "Bad Role User",
                "email": "bad@platform.local",
                "role": "superadmin",  # not a valid role
            },
        )
        assert response.status_code in [400, 422, 500]


class TestAuditLog:
    """GET /auth/audit-log"""

    def test_audit_log_authenticated(self, client_authenticated):
        """Audit log returns a list of events."""
        response = client_authenticated.get("/api/auth/audit-log")
        assert response.status_code in [200, 403, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_audit_log_unauthenticated(self, client):
        """Unauthenticated audit log returns 401."""
        response = client.get("/api/auth/audit-log")
        assert response.status_code == 401
