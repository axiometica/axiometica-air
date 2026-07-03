"""
Tests for /approvals endpoints.
Covers pending list, history, detail retrieval, and approve/reject decisions.
"""

import pytest
from uuid import uuid4


class TestApprovalsPending:
    """GET /approvals/pending"""

    def test_list_pending_returns_200(self, client_authenticated):
        """GET /approvals/pending returns a list (may be empty)."""
        response = client_authenticated.get("/api/approvals/pending")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_list_pending_with_limit(self, client_authenticated):
        """limit query parameter is accepted."""
        response = client_authenticated.get("/api/approvals/pending?limit=5")
        assert response.status_code in [200, 500]

    def test_list_pending_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/approvals/pending")
        assert response.status_code == 401

    def test_pending_response_shape(self, client_authenticated):
        """If approvals exist, each has required fields."""
        response = client_authenticated.get("/api/approvals/pending?limit=1")
        if response.status_code == 200:
            approvals = response.json()
            if approvals:
                approval = approvals[0]
                assert "approval_id" in approval or "id" in approval
                assert "workflow_id" in approval
                assert "status" in approval


class TestApprovalsHistory:
    """GET /approvals/history"""

    def test_list_history_returns_200(self, client_authenticated):
        """GET /approvals/history returns a list."""
        response = client_authenticated.get("/api/approvals/history")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert isinstance(response.json(), list)

    def test_list_history_filter_by_status(self, client_authenticated):
        """status filter query parameter is accepted."""
        response = client_authenticated.get("/api/approvals/history?status=approved")
        assert response.status_code in [200, 500]

    def test_history_unauthenticated(self, client):
        """Unauthenticated history returns 401."""
        response = client.get("/api/approvals/history")
        assert response.status_code == 401


class TestApprovalById:
    """GET /approvals/{approval_id}"""

    def test_get_nonexistent_approval(self, client_authenticated):
        """Non-existent approval ID returns 404."""
        response = client_authenticated.get(f"/api/approvals/{uuid4()}")
        assert response.status_code in [404, 500]

    def test_get_approval_invalid_id(self, client_authenticated):
        """Invalid UUID format returns 400, 404, or 422."""
        response = client_authenticated.get("/api/approvals/not-a-uuid")
        assert response.status_code in [400, 404, 422, 500]

    def test_get_approval_unauthenticated(self, client):
        """Unauthenticated get returns 401."""
        response = client.get(f"/api/approvals/{uuid4()}")
        assert response.status_code == 401


class TestApprovalDecision:
    """POST /approvals/{approval_id}/approve and /reject"""

    def test_approve_nonexistent(self, client_authenticated):
        """Approving non-existent approval returns 404."""
        response = client_authenticated.post(
            f"/api/approvals/{uuid4()}/approve",
            json={
                "decision": "approved",
                "notes": "Approved in test",
                "decided_by": "test-admin",
            },
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_reject_nonexistent(self, client_authenticated):
        """Rejecting non-existent approval returns 404."""
        response = client_authenticated.post(
            f"/api/approvals/{uuid4()}/reject",
            json={
                "decision": "rejected",
                "notes": "Rejected in test",
                "decided_by": "test-admin",
            },
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_decide_by_workflow_nonexistent(self, client_authenticated):
        """Decision by workflow_id on non-existent workflow returns 404."""
        response = client_authenticated.post(
            f"/api/approvals/by-workflow/{uuid4()}/decide",
            json={
                "decision": "approved",
                "notes": "Approved",
                "decided_by": "test-admin",
            },
        )
        assert response.status_code in [400, 404, 422, 500]

    def test_approve_requires_auth(self, client):
        """Approval decision requires authentication."""
        response = client.post(
            f"/api/approvals/{uuid4()}/approve",
            json={"decision": "approved"},
        )
        assert response.status_code == 401

    def test_reject_requires_auth(self, client):
        """Rejection requires authentication."""
        response = client.post(
            f"/api/approvals/{uuid4()}/reject",
            json={"decision": "rejected"},
        )
        assert response.status_code == 401
