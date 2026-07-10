"""
Unit test conftest — no database required.

Overrides the session-level DB fixtures defined in the parent conftest so
that pure-Python unit tests (no Postgres, no watcher, no Docker) can run
locally without any infrastructure.
"""

import pytest


@pytest.fixture(scope="session")
def test_engine():
    """No-op override: unit tests don't touch the database."""
    return None


@pytest.fixture(autouse=True)
def clean_workflow_states(test_engine):
    """No-op override: unit tests don't need table cleanup."""
    yield


@pytest.fixture(autouse=True)
def reset_incident_seq(test_engine):
    """No-op override: unit tests don't use the incident sequence."""
    yield
