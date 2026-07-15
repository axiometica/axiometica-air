"""
FastAPI application for Axiometica AIR.
REST API for workflow submission, status tracking, and approvals.
"""

import asyncio
import logging
import logging.config
import os
from fastapi import FastAPI, Depends, HTTPException, WebSocket
from agentic_os.api.auth import get_current_principal, require_role
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

# ── Structured JSON logging ───────────────────────────────────────────────────
# When LOG_FORMAT=json (the default in production), every log line is a JSON
# object with timestamp, level, logger, and message — easy to ingest into
# Datadog, Splunk, or CloudWatch Logs.  Set LOG_FORMAT=text for human-readable
# output during local development.
_log_format = os.getenv("LOG_FORMAT", "json").lower()
_log_level  = os.getenv("LOG_LEVEL",  "INFO").upper()

if _log_format == "json":
    try:
        from pythonjsonlogger import jsonlogger  # type: ignore[import]

        class _AgenticJsonFormatter(jsonlogger.JsonFormatter):
            """Adds service name and environment to every log record."""

            def add_fields(self, log_record, record, message_dict):  # type: ignore[override]
                super().add_fields(log_record, record, message_dict)
                log_record.setdefault("service", "agentic-platform")
                log_record.setdefault("environment", os.getenv("ENVIRONMENT", "production"))

        _formatter = _AgenticJsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        _handler = logging.StreamHandler()
        _handler.setFormatter(_formatter)
        logging.root.handlers = [_handler]
        logging.root.setLevel(_log_level)
    except ImportError:
        # python-json-logger not installed — fall back to standard text format
        logging.basicConfig(
            level=_log_level,
            format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        )
else:
    logging.basicConfig(
        level=_log_level,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )

# ── Sentry error tracking (Fix 9) ────────────────────────────────────────────
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.getenv("ENVIRONMENT", "production"),
            traces_sample_rate=0.1,
            profiles_sample_rate=0.1,
            # Explicit, not relying on the SDK default — request bodies/headers
            # (which can include connector passwords, Slack tokens, etc. on
            # save endpoints) must never be captured in breadcrumbs.
            send_default_pii=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
        )
        logging.getLogger(__name__).info("✓ Sentry error tracking enabled")
    except ImportError:
        logging.getLogger(__name__).warning("sentry-sdk not installed — Sentry disabled")

# Rate limiting is done via per-route RateLimit dependencies (see api/rate_limit.py)

from agentic_os.db.database import SessionLocal, init_db
from agentic_os.bus.postgres_bus import PostgresEventBus
from agentic_os.core.workflow_engine import WorkflowEngine
from agentic_os.agents.registry import register_all_agents
from agentic_os.api.routes import workflows, approvals, health, metrics, admin, runbooks, approved_actions, monitoring_events, risk_config, llm_settings, policies, governance, ml_endpoints, monitoring_checks, log_monitors
from agentic_os.api.routes import admin_logs as admin_logs_route
from agentic_os.api.routes import cmdb_routes
from agentic_os.api.routes import platform_settings
from agentic_os.api.routes import connectors
from agentic_os.api.routes import notification_teams
from agentic_os.api.routes import auth as auth_routes
from agentic_os.api.routes import splunk_webhook
from agentic_os.api.routes import alert_webhooks
from agentic_os.api.routes import storms
from agentic_os.api.routes import event_types as event_types_routes
from agentic_os.api.routes import platform_intelligence
from agentic_os.api.routes import chat as chat_routes
from agentic_os.api.routes import slack_webhook
from agentic_os.api.routes import synthetics as synthetics_routes
from agentic_os.api.ws import websocket_endpoint, global_events_endpoint
from agentic_os.services.neo4j_init import seed_neo4j_database

logger = logging.getLogger(__name__)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup and shutdown logic"""
    # Startup
    logger.info("Starting up Agentic OS Platform...")

    # Initialise in-memory log buffer (captures all subsequent log output)
    from agentic_os.api.routes.admin_logs import init_log_buffer
    init_log_buffer()

    # Initialize database schema
    init_db()
    logger.info("✓ Database initialized")

    # Seed approved actions catalog (full upsert on every startup)
    try:
        from agentic_os.db.repositories import ApprovedActionRepository
        _seed_db = SessionLocal()
        try:
            seeded = ApprovedActionRepository(_seed_db).seed_defaults()
            _seed_db.commit()
        finally:
            _seed_db.close()
        logger.info(f"✓ Approved actions catalog: {seeded} action(s) inserted/updated")
    except Exception as e:
        logger.error(f"✖ Approved actions seed FAILED — runbooks will not execute correctly: {e}", exc_info=True)

    # Seed risk weight configuration (no-op if already populated)
    try:
        from agentic_os.db.risk_weights_seed import seed_risk_weights
        _seed_db = SessionLocal()
        seeded = seed_risk_weights(_seed_db)
        _seed_db.close()
        if seeded:
            logger.info("✓ Risk weight configuration seeded with defaults")
        else:
            logger.info("✓ Risk weight configuration already exists")
    except Exception as e:
        logger.warning(f"⚠ Risk weight seed failed: {e}")

    # Seed platform settings (watcher thresholds, etc.) — no-op if already seeded
    try:
        from agentic_os.api.routes.platform_settings import (
            seed_watcher_defaults, seed_storm_defaults,
            seed_general_defaults, seed_smtp_defaults, seed_slack_defaults,
            seed_platform_intelligence_defaults,
        )
        _seed_db = SessionLocal()
        seed_watcher_defaults(_seed_db)
        seed_storm_defaults(_seed_db)
        seed_general_defaults(_seed_db)
        seed_smtp_defaults(_seed_db)
        seed_slack_defaults(_seed_db)
        seed_platform_intelligence_defaults(_seed_db)
        _seed_db.close()
        logger.info("✓ Platform settings seeded (watcher + storm + general + smtp + slack + platform_intelligence)")
    except Exception as e:
        logger.warning(f"⚠ Platform settings seed failed: {e}")

    # Apply all pending schema migrations via Alembic
    # The initial migration (revision 0001) applies add_principals.sql and
    # add_principal_audit.sql — previously executed manually here.
    # All migrations are idempotent; safe to run on every startup.
    try:
        from alembic import command as _alembic_cmd
        from alembic.config import Config as _AlembicConfig
        _alembic_cfg = _AlembicConfig("/app/alembic.ini")
        _alembic_cfg.set_main_option(
            "sqlalchemy.url",
            os.getenv("DATABASE_URL", "postgresql://postgres:agentic_os@postgres:5432/agentic_os"),
        )
        _alembic_cmd.upgrade(_alembic_cfg, "head")
        logger.info("✓ Database migrations applied (Alembic upgrade head)")
    except Exception as e:
        logger.warning(f"⚠ Alembic migration failed: {e}")

    # Seed default principals (admin, operator, viewer, watcher_bot) — no-op if already seeded
    try:
        from agentic_os.db.seed_principals import seed_principals
        _seed_db = SessionLocal()
        seed_principals(_seed_db)
        _seed_db.close()
        logger.info("✓ Principals seeded")
    except Exception as e:
        logger.warning(f"⚠ Principals seed failed: {e}")

    # ── Security check: default admin password warning ────────────────────────
    # Warn if ADMIN_INITIAL_PASSWORD has not been overridden.  The default
    # credentials are publicly known — any deployment that skips this step is
    # immediately vulnerable.
    _admin_pw_env = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    if _admin_pw_env in ("", "admin", "Admin@1234!"):
        logger.warning(
            "⚠ SECURITY: Admin account is using default credentials. "
            "Change the password immediately via Settings → User Management, "
            "or set ADMIN_INITIAL_PASSWORD to a unique value before first deployment."
        )

    # Upsert runbooks from Python seed data (idempotent — always runs).
    # Corrects enabled=False and platform=NULL left by SQL-seeded rows,
    # and inserts any runbooks added since the last deployment.
    try:
        from agentic_os.db.runbooks_seed import seed_runbooks
        _seed_db = SessionLocal()
        changed = seed_runbooks(_seed_db)
        _seed_db.close()
        if changed:
            logger.info(f"✓ Runbooks catalog updated: {changed} runbook(s) inserted/updated")
        else:
            logger.info("✓ Runbooks catalog up to date")
    except Exception as e:
        logger.warning(f"⚠ Runbooks seed failed: {e}")

    # Initialize event bus
    _db_url = os.getenv("DATABASE_URL", "postgresql://postgres:agentic_os@postgres:5432/agentic_os")
    event_bus = PostgresEventBus(_db_url)
    await event_bus.connect()
    logger.info("✓ Event bus connected")

    # Initialize Neo4j CMDB with seed data
    try:
        seed_neo4j_database()
        logger.info("✓ Neo4j CMDB initialized")
    except Exception as e:
        logger.warning(f"⚠ Neo4j CMDB initialization failed: {e}")

    # Initialize workflow engine
    db = SessionLocal()
    engine = WorkflowEngine(event_bus, db)
    register_all_agents(engine)
    logger.info("✓ Workflow engine initialized with 23 agents (including RunbookGeneratorAgent)")

    # Store in app state for routes to access
    app.state.event_bus = event_bus
    app.state.engine = engine
    app.state.db = db

    # Start Slack Socket Mode listener (no-op if tokens not configured)
    _socket_task = asyncio.create_task(slack_webhook.start_socket_mode())
    logger.info("✓ Slack Socket Mode task started")

    yield

    # Shutdown
    logger.info("Shutting down Agentic OS Platform...")

    _socket_task.cancel()
    try:
        await _socket_task
    except asyncio.CancelledError:
        pass
    logger.info("✓ Slack Socket Mode listener stopped")

    await event_bus.disconnect()
    logger.info("✓ Event bus disconnected")
    db.close()
    logger.info("✓ Workflow engine DB session closed")


# Create FastAPI app
app = FastAPI(
    title="Agentic Platform",
    description="AI-driven IT operations platform for autonomous incident detection, triage, and remediation",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── Request ID middleware ─────────────────────────────────────────────────────
# Stamps every request/response with X-Request-ID so log lines from the same
# request can be correlated across the backend, Celery worker, and nginx.
import uuid as _uuid_mod
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _StarletteRequest
from starlette.responses import Response as _StarletteResponse


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: _StarletteRequest, call_next):
        req_id = request.headers.get("X-Request-ID") or str(_uuid_mod.uuid4())
        request.state.request_id = req_id
        response: _StarletteResponse = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


app.add_middleware(RequestIDMiddleware)

# Add CORS middleware
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000,http://localhost:3001,http://localhost:7892")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Route-level auth dependencies ───────────────────────────────────────────
# _open    : no auth (health checks, login endpoints)
# _any     : any valid JWT or API key (viewer, operator, itom_admin, admin, automation)
# _itom_up : itom_admin or admin (configuration & knowledge-base routes)
# _admin   : admin only (platform management, LLM keys, user admin)
_any    = [Depends(get_current_principal)]
_itom_up = [Depends(require_role("admin", "itom_admin"))]
_admin  = [Depends(require_role("admin"))]

# Include routers
# Public — no token required
app.include_router(health.router,                        prefix="/api", tags=["Health"])
app.include_router(auth_routes.router,                   prefix="/api", tags=["Auth"])
app.include_router(splunk_webhook.router,                prefix="/api", tags=["Splunk Webhook"])
app.include_router(alert_webhooks.router,                prefix="/api", tags=["Alert Webhooks"])
# Slack webhooks — auth is via Slack signing-secret HMAC, not JWT
app.include_router(slack_webhook.router,                 prefix="/api", tags=["Slack ChatOps"])
# Read-only platform config — public so internal services (watcher, etc.) can poll without a key
app.include_router(platform_settings.public_router,      prefix="/api", tags=["Platform Settings"])
# Watcher registration endpoint — public for self-registration bootstrap
app.include_router(monitoring_checks.public_router,      prefix="/api", tags=["Monitoring - Public"])

# Any authenticated principal (viewer / operator / itom_admin / admin / automation)
app.include_router(workflows.router,          prefix="/api", tags=["Workflows"],          dependencies=_any)
app.include_router(approvals.router,          prefix="/api", tags=["Approvals"],          dependencies=_any)
app.include_router(metrics.router,            prefix="/api", tags=["Metrics"],            dependencies=_any)
app.include_router(monitoring_events.router,  prefix="/api", tags=["Monitoring Events"],  dependencies=_any)
app.include_router(monitoring_checks.router,  prefix="/api", tags=["Monitoring Checks"],  dependencies=_any)
app.include_router(log_monitors.router,       prefix="/api", tags=["Log Monitors"],       dependencies=_any)
app.include_router(synthetics_routes.router,  prefix="/api", tags=["Synthetics"],          dependencies=_any)
app.include_router(storms.router,             prefix="/api", tags=["Storms"],              dependencies=_any)
app.include_router(platform_intelligence.router, prefix="/api", tags=["Platform Intelligence"], dependencies=_itom_up)
app.include_router(chat_routes.router,            prefix="/api", tags=["Operator Chat"],         dependencies=_any)
app.include_router(cmdb_routes.router,        prefix="/api", tags=["CMDB"],               dependencies=_any)
app.include_router(ml_endpoints.router,       prefix="/api", tags=["ML/AI"],              dependencies=_any)
app.include_router(platform_settings.router,  prefix="/api", tags=["Platform Settings"],  dependencies=_any)

# ITOM Admin and above — operational configuration
app.include_router(event_types_routes.router, prefix="/api", tags=["Event Type Taxonomy"], dependencies=_any)
app.include_router(runbooks.read_router,   prefix="/api", tags=["Runbooks"],          dependencies=_any)
app.include_router(runbooks.router,        prefix="/api", tags=["Runbooks"],          dependencies=_itom_up)
app.include_router(approved_actions.router,prefix="/api", tags=["Approved Actions"],  dependencies=_itom_up)
app.include_router(risk_config.router,     prefix="/api", tags=["Risk Config"],       dependencies=_itom_up)
app.include_router(policies.router,        prefix="/api", tags=["Policies"],          dependencies=_itom_up)
app.include_router(governance.router,      prefix="/api", tags=["Governance Policies"],dependencies=_itom_up)
app.include_router(connectors.router,      prefix="/api", tags=["Connectors"],        dependencies=_itom_up)
app.include_router(notification_teams.router, prefix="/api", tags=["Notification Teams"], dependencies=_itom_up)

# Admin only — platform management
app.include_router(admin.router,           prefix="/api/admin", tags=["Admin"],        dependencies=_admin)
app.include_router(admin_logs_route.router,                     tags=["Admin Logs"],   dependencies=_admin)
app.include_router(llm_settings.router,                         tags=["LLM Settings"], dependencies=_admin)

# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API is running"""
    return {
        "message": "Agentic OS Platform v2.0",
        "docs": "/api/docs",
        "status": "running",
    }


# Compatibility redirect for root-level /health (old clients, health check scripts)
@app.get("/health", tags=["Health"])
async def health_compat():
    """Compatibility endpoint — redirects to /api/health for old clients"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/health", status_code=307)


# WebSocket endpoint for real-time updates
@app.websocket("/ws/workflows/{workflow_id}")
async def ws_workflow_updates(workflow_id: str, websocket: WebSocket):
    """
    WebSocket endpoint for real-time workflow status updates.

    Connect with: ws://localhost:8000/ws/workflows/{workflow_id}

    Messages received:
    - workflow_update: Status changed (lifecycle, severity, risk_score, etc.)
    - workflow_completed: Workflow reached terminal state
    """
    await websocket_endpoint(workflow_id, websocket)


# Global event feed — broadcasts incident + approval changes to all connected tabs
@app.websocket("/ws/events")
async def ws_global_events(websocket: WebSocket):
    """
    Global incident / approval event feed.

    Every connected browser tab shares a single 1-second DB poll.
    Messages pushed:
      - incident_created  : new incident appeared
      - incident_updated  : lifecycle_state / severity changed
      - approval_requested: new pending approval arrived
    """
    await global_events_endpoint(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
