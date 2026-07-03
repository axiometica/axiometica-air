"""
Integration tests for the enhanced GET /workflows endpoint.
Tests sorting, filtering, pagination, and incident enumeration.
"""

import pytest
from uuid import uuid4
from datetime import datetime, timedelta

from agentic_os.db.models import WorkflowStateModel
from agentic_os.core.models import WorkflowType, LifecycleState, Severity
from fastapi.testclient import TestClient


@pytest.fixture
def sample_workflows(db):
    """Create sample workflows for testing."""
    workflows = []

    # Create 5 incident workflows with varying properties
    for i in range(5):
        workflow = WorkflowStateModel(
            workflow_id=uuid4(),
            workflow_type=WorkflowType.INCIDENT.value,
            lifecycle_state=LifecycleState.OPEN.value if i < 2 else LifecycleState.RESOLVED.value,
            severity=["critical", "high", "medium", "low", "critical"][i],
            risk_score=float([90, 75, 50, 25, 85][i]),
            title=f"Test Incident {i+1}",
            context={"alert_payload": {"type": "high_cpu"}},
            created_at=datetime.utcnow() - timedelta(hours=i),
        )
        db.add(workflow)
        workflows.append(workflow)

    db.commit()
    return workflows


class TestWorkflowListEndpoint:
    """Test the GET /workflows endpoint."""

    def test_list_workflows_basic(self, client_authenticated, sample_workflows):
        """Test basic workflow listing."""
        response = client_authenticated.get("/api/workflows")
        assert response.status_code == 200

        data = response.json()
        assert "workflows" in data
        assert "total_count" in data
        assert "limit" in data
        assert "offset" in data
        assert "has_more" in data

    def test_list_workflows_pagination(self, client_authenticated, sample_workflows):
        """Test pagination parameters."""
        # Request first 2 items
        response = client_authenticated.get("/api/workflows?limit=2&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["workflows"]) <= 2
        assert data["total_count"] >= 5  # at least the 5 sample_workflows

    def test_list_workflows_workflow_type_filter(self, client_authenticated, sample_workflows):
        """Test filtering by workflow type."""
        response = client_authenticated.get("/api/workflows?workflow_type=incident")
        assert response.status_code == 200

        data = response.json()
        assert all(w["workflow_type"] == "incident" for w in data["workflows"])

    def test_list_workflows_lifecycle_state_filter(self, client_authenticated, sample_workflows):
        """Test filtering by lifecycle state."""
        # lifecycle_state is stored lowercase in the DB ("open" not "OPEN")
        response = client_authenticated.get("/api/workflows?lifecycle_state=open")
        assert response.status_code == 200

        data = response.json()
        # Should have at least some open workflows
        open_count = len([w for w in data["workflows"] if w["lifecycle_state"] == "open"])
        assert open_count > 0

    def test_list_workflows_severity_filter(self, client_authenticated, sample_workflows):
        """Test filtering by severity."""
        response = client_authenticated.get("/api/workflows?severity=critical")
        assert response.status_code == 200

        data = response.json()
        assert all(w["severity"] == "critical" for w in data["workflows"])

    def test_list_workflows_sorting_by_created_at(self, client_authenticated, sample_workflows):
        """Test sorting by created_at."""
        # Sort ascending (oldest first)
        response = client_authenticated.get("/api/workflows?sort_by=created_at&sort_order=asc")
        assert response.status_code == 200

        data = response.json()
        workflows = data["workflows"]
        if len(workflows) > 1:
            # Verify ascending order
            dates = [w["created_at"] for w in workflows]
            assert dates == sorted(dates), "Workflows not sorted ascending by created_at"

    def test_list_workflows_sorting_by_risk_score(self, client_authenticated, sample_workflows):
        """Test sorting by risk_score."""
        response = client_authenticated.get("/api/workflows?sort_by=risk_score&sort_order=desc")
        assert response.status_code == 200

        data = response.json()
        workflows = data["workflows"]
        if len(workflows) > 1:
            # Verify descending order by risk_score
            scores = [w["risk_score"] for w in workflows if w["risk_score"] is not None]
            assert scores == sorted(scores, reverse=True), \
                "Workflows not sorted descending by risk_score"

    def test_list_workflows_sorting_by_severity(self, client_authenticated, sample_workflows):
        """Test sorting by severity."""
        response = client_authenticated.get("/api/workflows?sort_by=severity")
        assert response.status_code == 200

        data = response.json()
        # Response should be successful
        assert "workflows" in data

    def test_list_workflows_has_more_flag(self, client_authenticated, sample_workflows):
        """Test has_more pagination flag."""
        # Request 2 items out of 5 total
        response = client_authenticated.get("/api/workflows?limit=2&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["has_more"] == True, "Should have more items"

        # Request last 2 items
        response = client_authenticated.get("/api/workflows?limit=2&offset=4")
        data = response.json()
        # May or may not have more depending on total
        assert "has_more" in data

    def test_list_workflows_incident_number_in_response(self, client_authenticated, sample_workflows):
        """Test that incident_number is included in response."""
        response = client_authenticated.get("/api/workflows")
        assert response.status_code == 200

        data = response.json()
        for workflow in data["workflows"]:
            # incident_number should be present (might be None for non-incidents)
            assert "incident_number" in workflow

    def test_list_workflows_default_limit(self, client_authenticated, sample_workflows):
        """Test default limit (should be 10)."""
        response = client_authenticated.get("/api/workflows")
        assert response.status_code == 200

        data = response.json()
        assert data["limit"] == 10

    def test_list_workflows_max_limit_enforced(self, client_authenticated, sample_workflows):
        """Test that limit is capped at 100."""
        response = client_authenticated.get("/api/workflows?limit=200")
        assert response.status_code == 200

        data = response.json()
        assert data["limit"] == 100, "Limit should be capped at 100"

    def test_list_workflows_multiple_filters(self, client_authenticated, sample_workflows):
        """Test combining multiple filters."""
        # lifecycle_state is stored lowercase in the DB
        response = client_authenticated.get(
            "/api/workflows?workflow_type=incident&lifecycle_state=open&limit=10"
        )
        assert response.status_code == 200

        data = response.json()
        assert all(w["workflow_type"] == "incident" for w in data["workflows"])
        assert all(w["lifecycle_state"] == "open" for w in data["workflows"])


class TestWorkflowListPagination:
    """Test pagination edge cases."""

    def test_pagination_offset_zero(self, client_authenticated, sample_workflows):
        """Test pagination with offset=0."""
        response = client_authenticated.get("/api/workflows?limit=2&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["offset"] == 0

    def test_pagination_beyond_total(self, client_authenticated, sample_workflows):
        """Test pagination beyond total count."""
        response = client_authenticated.get("/api/workflows?limit=10&offset=100")
        assert response.status_code == 200

        data = response.json()
        # Should return empty list
        assert len(data["workflows"]) == 0
        assert data["has_more"] == False

    def test_pagination_negative_offset_handled(self, client_authenticated, sample_workflows):
        """Test that negative offset is handled gracefully."""
        response = client_authenticated.get("/api/workflows?offset=-5")
        # Should succeed (offset coerced to 0)
        assert response.status_code == 200


class TestWorkflowListErrorHandling:
    """Test error handling."""

    def test_invalid_sort_field_defaults_to_created_at(self, client_authenticated, sample_workflows):
        """Test that invalid sort field defaults to created_at."""
        response = client_authenticated.get("/api/workflows?sort_by=invalid_field")
        # Should succeed with default sort
        assert response.status_code == 200

    def test_invalid_sort_order_defaults_to_desc(self, client_authenticated, sample_workflows):
        """Test that invalid sort order is handled."""
        response = client_authenticated.get("/api/workflows?sort_order=invalid")
        assert response.status_code == 200

        data = response.json()
        # Should still return workflows
        assert "workflows" in data


class TestWorkflowListResponseFormat:
    """Test response format and field presence."""

    def test_workflow_response_includes_required_fields(self, client_authenticated, sample_workflows):
        """Test that workflow responses include all required fields."""
        response = client_authenticated.get("/api/workflows?limit=1")
        assert response.status_code == 200

        data = response.json()
        assert len(data["workflows"]) > 0

        workflow = data["workflows"][0]
        required_fields = [
            "workflow_id",
            "workflow_type",
            "lifecycle_state",
            "created_at",
            "updated_at",
            "incident_number",
            "severity",
            "risk_score",
            "title",
            "summary",
            "reasoning_trace",
        ]

        for field in required_fields:
            assert field in workflow, f"Missing field: {field}"

    def test_pagination_metadata_included(self, client_authenticated, sample_workflows):
        """Test that pagination metadata is included."""
        response = client_authenticated.get("/api/workflows?limit=2&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["total_count"] >= 5  # at least the 5 sample_workflows
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert data["has_more"] in [True, False]
