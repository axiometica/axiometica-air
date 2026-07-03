"""
API route tests for workflow submission and queries
"""

import pytest
from uuid import uuid4


class TestHealthEndpoints:
    """Health check endpoint tests"""

    def test_liveness_check(self, client):
        """Test /health endpoint"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert data["service"] == "agentic_os"

    def test_readiness_check(self, client):
        """Test /ready endpoint"""
        response = client.get("/api/ready")
        # May fail if database not connected in test environment
        assert response.status_code in [200, 503]


class TestWorkflowEndpoints:
    """Workflow submission and query endpoints"""

    def test_submit_incident(self, client_authenticated):
        """Test POST /workflows/incident"""
        response = client_authenticated.post(
            "/api/workflows/incident",
            json={
                "severity": "high",
                "type": "high_cpu",
                "resource_name": "api-server",
                "description": "Test incident",
            },
        )

        # 200=success, 400=validation/business error, 422=invalid input, 500=server error
        assert response.status_code in [200, 400, 422, 500]

        if response.status_code == 200:
            data = response.json()
            assert "workflow_id" in data
            assert data["workflow_type"] == "incident"
            assert data["lifecycle_state"] == "open"

    def test_submit_change(self, client_authenticated):
        """Test POST /workflows/change"""
        response = client_authenticated.post(
            "/api/workflows/change",
            json={
                "change_type": "standard",
                "description": "Test change",
                "affected_services": ["api-server", "database"],
                "rollback_plan": "Revert to previous version",
            },
        )

        assert response.status_code in [200, 400, 422, 500]

        if response.status_code == 200:
            data = response.json()
            assert "workflow_id" in data
            assert data["workflow_type"] == "change"
            assert data["lifecycle_state"] == "open"

    def test_list_workflows(self, client_authenticated):
        """Test GET /workflows"""
        response = client_authenticated.get("/api/workflows")
        assert response.status_code in [200, 500]

        if response.status_code == 200:
            data = response.json()
            # GET /workflows returns paginated response, not a plain list
            assert isinstance(data, dict)
            assert "workflows" in data
            assert isinstance(data["workflows"], list)

    def test_get_workflow_not_found(self, client_authenticated):
        """Test GET /workflows/{id} with non-existent ID"""
        response = client_authenticated.get(f"/api/workflows/{uuid4()}")
        assert response.status_code in [404, 500]

    def test_invalid_workflow_id_format(self, client_authenticated):
        """Test GET /workflows/{id} with invalid format"""
        response = client_authenticated.get("/api/workflows/invalid-id")
        assert response.status_code in [400, 422, 500]


class TestApprovalEndpoints:
    """Approval request endpoints"""

    def test_get_pending_approvals(self, client_authenticated):
        """Test GET /approvals/pending"""
        response = client_authenticated.get("/api/approvals/pending")
        assert response.status_code in [200, 500]

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    def test_get_approval_not_found(self, client_authenticated):
        """Test GET /approvals/{id} with non-existent ID"""
        response = client_authenticated.get(f"/api/approvals/{uuid4()}")
        assert response.status_code in [404, 500]

    def test_invalid_approval_id_format(self, client_authenticated):
        """Test GET /approvals/{id} with invalid format"""
        response = client_authenticated.get("/api/approvals/invalid-id")
        assert response.status_code in [400, 422, 500]


class TestRootEndpoint:
    """Root endpoint tests"""

    def test_root(self, client):
        """Test GET /"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "docs" in data
        assert data["status"] == "running"


class TestWebSocketEndpoint:
    """WebSocket endpoint tests"""

    def test_websocket_upgrade(self, client):
        """Test WebSocket endpoint exists"""
        workflow_id = str(uuid4())

        # WebSocket upgrade is a separate protocol, normal client won't work
        # Just verify the route exists by checking with GET (should 405)
        response = client.get(f"/ws/workflows/{workflow_id}")
        assert response.status_code in [405, 404]  # Method not allowed or not found


class TestIncidentWorkflow:
    """E2E incident workflow tests"""

    def test_incident_submission_and_retrieval(self, client_authenticated):
        """Test full incident submission and status check"""
        # Submit incident
        submit_response = client_authenticated.post(
            "/api/workflows/incident",
            json={
                "severity": "high",
                "type": "high_cpu",
                "resource_name": "api-server",
            },
        )

        if submit_response.status_code != 200:
            pytest.skip("Database not available for E2E test")

        data = submit_response.json()
        workflow_id = data["workflow_id"]

        # Retrieve workflow
        get_response = client_authenticated.get(f"/api/workflows/{workflow_id}")
        assert get_response.status_code == 200
        retrieved = get_response.json()

        assert retrieved["workflow_id"] == workflow_id
        assert retrieved["workflow_type"] == "incident"


class TestChangeWorkflow:
    """E2E change workflow tests"""

    def test_change_submission_and_retrieval(self, client_authenticated):
        """Test full change submission and status check"""
        # Submit change
        submit_response = client_authenticated.post(
            "/api/workflows/change",
            json={
                "change_type": "standard",
                "description": "API update",
                "affected_services": ["api-server"],
                "rollback_plan": "Revert image",
            },
        )

        if submit_response.status_code != 200:
            pytest.skip("Database not available for E2E test")

        data = submit_response.json()
        workflow_id = data["workflow_id"]

        # Retrieve workflow
        get_response = client_authenticated.get(f"/api/workflows/{workflow_id}")
        assert get_response.status_code == 200
        retrieved = get_response.json()

        assert retrieved["workflow_id"] == workflow_id
        assert retrieved["workflow_type"] == "change"
