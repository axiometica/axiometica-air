"""
Pytest configuration and fixtures for Agentic OS tests.
Sets up test database and session management.
Uses PostgreSQL (provided by CI/CD) instead of SQLite for UUID type compatibility.
"""

import pytest
import sys
import os
import json
import base64
import hmac
import hashlib
import uuid as _uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# Add src to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agentic_os.db.models import Base
from agentic_os.main import app


# Test database URL - uses PostgreSQL (same as production)
# In CI/CD: Provided by GitHub Actions workflow (TEST_DATABASE_URL env var)
# Locally: Can be overridden with TEST_DATABASE_URL env var
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:testpass@localhost:5432/agentic_os_test"
)

# JWT configuration — must match what the application uses for token validation
# In CI: JWT_SECRET env var is set by the workflow
# Locally: falls back to the application's own dev default so tokens are accepted
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production-openssl-rand-hex-32")


def _generate_jwt_token():
    """Generate a valid JWT token for testing with all required claims."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip('=')
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "sub": "test-user-id",
            "name": "Test User",
            "email": "test@platform.local",
            "role": "admin",
            "jti": str(_uuid.uuid4()),
            "exp": int((datetime.utcnow() + timedelta(hours=8)).timestamp())
        }).encode()
    ).decode().rstrip('=')

    message = f"{header}.{payload}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(JWT_SECRET.encode(), message, hashlib.sha256).digest()
    ).decode().rstrip('=')

    return f"{header}.{payload}.{signature}"


@pytest.fixture(scope="session")
def test_engine():
    """Create a test database engine using PostgreSQL.

    PostgreSQL is required (not SQLite) because the models use PostgreSQL-specific
    types like UUID which SQLite cannot compile.
    """
    engine = create_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )

    # Create all tables from SQLAlchemy models
    Base.metadata.create_all(bind=engine)

    # Apply schema objects added by SQL migrations (not in SQLAlchemy models).
    # Each statement is idempotent — safe to run against an existing database.
    with engine.begin() as conn:

        # ── add_incident_enumeration.sql ────────────────────────────────────
        conn.execute(text(
            "CREATE SEQUENCE IF NOT EXISTS incident_seq START 1 INCREMENT 1"
        ))
        conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='workflow_states' AND column_name='incident_number'
                ) THEN
                    ALTER TABLE workflow_states
                        ADD COLUMN incident_number INTEGER UNIQUE NULL,
                        ADD COLUMN incident_number_str VARCHAR(20) UNIQUE NULL;
                END IF;
            END $$
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_incident_number "
            "ON workflow_states(incident_number)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_incident_number_str "
            "ON workflow_states(incident_number_str)"
        ))

        # ── add_storm_hold_state.sql ────────────────────────────────────────
        # storm_id:          FK linking a child incident to its storm parent
        # storm_detected_at: timestamp when the storm cluster was first detected
        conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='workflow_states' AND column_name='storm_id'
                ) THEN
                    ALTER TABLE workflow_states
                        ADD COLUMN storm_id UUID
                            REFERENCES workflow_states(workflow_id) ON DELETE SET NULL;
                END IF;
            END $$
        """))
        conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='workflow_states' AND column_name='storm_detected_at'
                ) THEN
                    ALTER TABLE workflow_states
                        ADD COLUMN storm_detected_at TIMESTAMP;
                END IF;
            END $$
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_workflow_states_storm_id "
            "ON workflow_states(storm_id)"
        ))

    yield engine

    # Cleanup after all tests complete
    Base.metadata.drop_all(bind=engine)


# ─────────────────────────────────────────────────────────────────────────────
# Per-test cleanup fixtures (autouse — run for every test automatically)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_workflow_states(test_engine):
    """Truncate workflow_states (and all dependent tables) before each test.

    Uses TRUNCATE … CASCADE so that child tables that reference workflow_states
    via foreign keys (monitoring_events, approvals, agent_executions,
    incident_notes, …) are also cleared automatically.  A plain DELETE would
    fail with ForeignKeyViolation whenever a monitoring event or approval was
    created in the previous test.

    Why BEFORE (not after): if a test fails mid-run its cleanup wouldn't have
    executed; cleaning before guarantees every test starts with a fresh slate.
    """
    with test_engine.begin() as conn:
        conn.execute(text("TRUNCATE workflow_states CASCADE"))
    yield


@pytest.fixture(autouse=True)
def reset_incident_seq(test_engine):
    """Reset incident_seq to 1 before every test.

    PostgreSQL sequences are non-transactional — they never roll back.
    Without this, test 2 would see INC0002 instead of INC0001 because
    test 1 already advanced the sequence.
    """
    try:
        with test_engine.begin() as conn:
            conn.execute(text("ALTER SEQUENCE incident_seq RESTART WITH 1"))
    except Exception:
        pass  # Safe to skip if sequence doesn't exist yet
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Database session fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db(test_engine) -> Session:
    """Create a database session for a test.

    Service code (e.g. EnumerationService) calls session.commit() freely.
    Data lands in the real DB — that is intentional. Cleanup is handled by
    clean_workflow_states (which runs before the NEXT test).

    The FastAPI app's get_session dependency is overridden to this session so
    that API calls made via TestClient see the same test data without requiring
    a separate commit.
    """
    from agentic_os.db.database import get_session

    connection = test_engine.connect()
    session = sessionmaker(autocommit=False, autoflush=False, bind=connection)()

    # Share this session with the FastAPI app so TestClient API calls see test data
    app.dependency_overrides[get_session] = lambda: session

    yield session

    # Teardown: restore original dependency, close session
    app.dependency_overrides.pop(get_session, None)
    try:
        session.rollback()
    except Exception:
        pass
    session.close()
    connection.close()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def client(db):
    """Unauthenticated FastAPI TestClient. Use client_authenticated for protected endpoints.

    Depends on `db` so every request made through this client (and therefore
    through client_authenticated, which wraps it) is routed to the isolated
    TEST_DATABASE_URL via the get_session override — not the real dev database.
    Without this, every test that hits an endpoint writes straight into
    whatever DATABASE_URL the app is actually configured with.
    """
    return TestClient(app)


class AuthenticatedTestClient:
    """Wrapper that automatically adds JWT authentication headers to every request."""

    def __init__(self, test_client):
        self._client = test_client

    def _auth(self, kwargs):
        if "headers" not in kwargs or kwargs.get("headers") is None:
            kwargs["headers"] = {}
        kwargs["headers"]["Authorization"] = f"Bearer {_generate_jwt_token()}"
        return kwargs

    def get(self, url, **kwargs):
        return self._client.get(url, **self._auth(kwargs))

    def post(self, url, **kwargs):
        return self._client.post(url, **self._auth(kwargs))

    def put(self, url, **kwargs):
        return self._client.put(url, **self._auth(kwargs))

    def delete(self, url, **kwargs):
        return self._client.delete(url, **self._auth(kwargs))

    def patch(self, url, **kwargs):
        return self._client.patch(url, **self._auth(kwargs))

    def head(self, url, **kwargs):
        return self._client.head(url, **self._auth(kwargs))

    def options(self, url, **kwargs):
        return self._client.options(url, **self._auth(kwargs))

    def request(self, method, url, **kwargs):
        return self._client.request(method, url, **self._auth(kwargs))


@pytest.fixture(scope="function")
def client_authenticated(client):
    """Authenticated TestClient — all requests automatically include a valid JWT token."""
    return AuthenticatedTestClient(client)
