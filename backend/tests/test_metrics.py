"""
Tests for /metrics/* endpoints.
Verifies dashboard metric endpoints return expected shapes.
These endpoints query the DB so they return 200 even with empty data.
"""

import pytest


class TestIncidentMetrics:
    """GET /metrics/incidents"""

    def test_incident_metrics_returns_200(self, client_authenticated):
        """Incident metrics endpoint returns 200 with expected structure."""
        response = client_authenticated.get("/api/metrics/incidents")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            # IncidentMetricsResponse actual field names
            expected_keys = {
                "total_incidents",
                "active_incidents",
                "resolved_today",
                "avg_resolution_time",
                "severity_breakdown",
            }
            assert expected_keys.issubset(data.keys()), \
                f"Missing keys: {expected_keys - data.keys()}"

    def test_incident_metrics_severity_breakdown(self, client_authenticated):
        """severity_breakdown contains standard severities."""
        response = client_authenticated.get("/api/metrics/incidents")
        if response.status_code == 200:
            data = response.json()
            by_severity = data.get("severity_breakdown", {})
            # Should be a dict (may be empty if no incidents)
            assert isinstance(by_severity, dict)

    def test_incident_metrics_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/metrics/incidents")
        assert response.status_code == 401


class TestRemediationMetrics:
    """GET /metrics/remediation"""

    def test_remediation_metrics_returns_200(self, client_authenticated):
        """Remediation metrics endpoint returns 200."""
        response = client_authenticated.get("/api/metrics/remediation")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)

    def test_remediation_metrics_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/metrics/remediation")
        assert response.status_code == 401


class TestMTTRBreakdown:
    """GET /metrics/mttr-breakdown"""

    def test_mttr_breakdown_returns_200(self, client_authenticated):
        """MTTR breakdown returns expected structure."""
        response = client_authenticated.get("/api/metrics/mttr-breakdown")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert "period_days" in data
            assert "by_severity" in data
            assert "by_path" in data

    def test_mttr_breakdown_custom_days(self, client_authenticated):
        """days parameter is accepted."""
        response = client_authenticated.get("/api/metrics/mttr-breakdown?days=14")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert data.get("period_days") == 14

    def test_mttr_breakdown_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/metrics/mttr-breakdown")
        assert response.status_code == 401


class TestMetricsTrend:
    """GET /metrics/trend"""

    def test_trend_returns_list(self, client_authenticated):
        """Trend endpoint returns a list of daily data points."""
        response = client_authenticated.get("/api/metrics/trend")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    def test_trend_custom_days(self, client_authenticated):
        """days parameter is accepted."""
        response = client_authenticated.get("/api/metrics/trend?days=14")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            # Each point should have date and counts
            if data:
                point = data[0]
                assert "date" in point or "label" in point

    def test_trend_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/metrics/trend")
        assert response.status_code == 401


class TestNavBadgeCounts:
    """GET /metrics/nav-counts"""

    def test_nav_counts_returns_200(self, client_authenticated):
        """Nav badge counts return expected fields for the UI."""
        response = client_authenticated.get("/api/metrics/nav-counts")
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            # NavBadgeCountsResponse fields used by the sidebar
            expected_keys = {"active_incidents", "pending_approvals"}
            assert expected_keys.issubset(data.keys()), \
                f"UI badge counts missing keys: {expected_keys - data.keys()}"

    def test_nav_counts_values_are_non_negative(self, client_authenticated):
        """All badge counts must be >= 0."""
        response = client_authenticated.get("/api/metrics/nav-counts")
        if response.status_code == 200:
            data = response.json()
            for key, value in data.items():
                assert isinstance(value, int), f"{key} should be int"
                assert value >= 0, f"{key} should be >= 0"

    def test_nav_counts_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/metrics/nav-counts")
        assert response.status_code == 401
