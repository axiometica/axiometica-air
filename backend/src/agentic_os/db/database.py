"""
Database initialization and session management.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import json
import os
from pathlib import Path

from agentic_os.db.models import Base

# Get database URL from environment or use default
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:agentic_os@localhost:5432/agentic_os"
)

# Connection pool — Phase 1 optimization for 3-instance backend scaling.
# Tuned for concurrent webhook ingestion and query bursts.
# Defaults: pool_size=20, overflow=10, timeout=30, recycle=3600.
# Override per environment via DB_POOL_* env vars (no rebuild required).
_pool_size     = int(os.getenv("DB_POOL_SIZE",     "20"))
_pool_overflow = int(os.getenv("DB_POOL_OVERFLOW", "10"))
_pool_timeout  = int(os.getenv("DB_POOL_TIMEOUT",  "30"))

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=_pool_size,
    max_overflow=_pool_overflow,
    pool_timeout=_pool_timeout,
    pool_recycle=3600,  # Recycle connections every hour (prevent idle disconnect)
    pool_pre_ping=True,  # Verify connection is alive before use
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    future=True,
    # Fail safe (stringify) instead of crashing the whole session + cascading
    # into PendingRollbackError when a JSON column gets a stray non-serializable
    # value (e.g. a neo4j.time.DateTime that slipped past an upstream sanitizer).
    json_serializer=lambda obj: json.dumps(obj, default=str),
)

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)


def get_session():
    """Get database session - generator for FastAPI dependency injection"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # Rollback on any exception so the connection is returned to the pool
        # in a clean state. Without this, a failed transaction poisons the
        # connection and every subsequent query in the same session gets
        # "current transaction is aborted".
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Initialize database - create all tables and apply lightweight column migrations."""
    # GeneratedRunbookModel and RemediationOutcomeModel are now part of the main
    # Base (models.py) — the old generated_runbooks_model.py is retired.
    # create_all() creates every table registered under Base including those models.
    Base.metadata.create_all(bind=engine)

    # Lightweight column migrations — safe to run on every startup.
    # ADD COLUMN IF NOT EXISTS is a no-op when the column already exists.
    _RUNBOOK_MIGRATIONS = [
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS total_executions      INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS successful_executions INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS failed_executions     INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS success_rate          FLOAT",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS recent_outcomes       JSON DEFAULT '[]'",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS confidence_trend      VARCHAR(10)",
        "ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS last_executed_at      TIMESTAMP",
    ]

    # Watcher registration approval workflow columns.
    # Existing rows default to 'approved' so pre-existing watchers keep working.
    _WATCHER_MIGRATIONS = [
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS registration_status VARCHAR(20) NOT NULL DEFAULT 'approved'",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS nginx_url     VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS kill_api_url  VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS approved_at   TIMESTAMP",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS approved_by   VARCHAR(100) NOT NULL DEFAULT ''",
    ]

    # Watcher environment & adapter columns
    _WATCHER_ENV_MIGRATIONS = [
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS environment     VARCHAR(50)  NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS adapter_mode    VARCHAR(20)  NOT NULL DEFAULT 'docker'",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS targets          JSON",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS watcher_version  VARCHAR(50)",
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS metrics_history  JSON",
    ]

    # Watcher sysid migration — promotes watcher_id UUID to primary key.
    # Safe to re-run: each statement is idempotent or caught by try/except.
    # Existing rows receive a stable UUID from gen_random_uuid().
    _WATCHER_SYSID_MIGRATIONS = [
        # 1. Add watcher_id column if it doesn't exist yet
        "ALTER TABLE watcher_registrations ADD COLUMN IF NOT EXISTS watcher_id UUID",
        # 2. Back-fill any NULL rows (new installs already have it from CREATE TABLE)
        "UPDATE watcher_registrations SET watcher_id = gen_random_uuid() WHERE watcher_id IS NULL",
        # 3. Enforce NOT NULL now that all rows have a value
        "ALTER TABLE watcher_registrations ALTER COLUMN watcher_id SET NOT NULL",
        # 4. Drop the old watcher_name primary key (IF EXISTS is safe on repeat runs)
        "ALTER TABLE watcher_registrations DROP CONSTRAINT IF EXISTS watcher_registrations_pkey",
        # 5. Promote watcher_id as the new primary key (no-op on repeat: caught by except)
        "ALTER TABLE watcher_registrations ADD PRIMARY KEY (watcher_id)",
        # 6. Unique index on watcher_name — replaces PK uniqueness guarantee
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_watcher_registrations_name ON watcher_registrations (watcher_name)",
    ]

    # Approved actions — add columns introduced since initial schema
    _ACTION_MIGRATIONS = [
        # command_variants stores per-adapter shell commands as JSON
        "ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS command_variants JSON",
        # output_fields: schema-driven output-extraction rules (regex/JSONPath), replacing hardcoded parsing
        "ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS output_fields JSON DEFAULT '[]'",
        # is_builtin: true for tools seeded from approved_actions_seed.py — locks output_fields editing
        "ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN NOT NULL DEFAULT FALSE",
    ]

    # ── Run each migration in its own autocommit connection ───────────────────
    # CRITICAL: All migrations must be isolated in separate transactions.
    # PostgreSQL puts a connection into an aborted-transaction state on any error,
    # causing every subsequent statement in the same transaction to fail with
    # "current transaction is aborted". Using separate connections means one
    # failed migration (e.g. column already exists) never prevents later ones.
    import logging as _logging
    _mig_log = _logging.getLogger(__name__)

    all_migrations = (
        _RUNBOOK_MIGRATIONS
        + _WATCHER_MIGRATIONS
        + _WATCHER_ENV_MIGRATIONS
        + _WATCHER_SYSID_MIGRATIONS
        + _ACTION_MIGRATIONS
    )
    for sql in all_migrations:
        try:
            with engine.connect() as conn:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
        except Exception as _mig_err:
            _mig_log.debug(f"Migration no-op (already applied or benign): {_mig_err!r}")

    # ── tool_name column: must be done in dependency order ───────────────────
    # Step 1: add column (nullable, so existing rows don't violate NOT NULL).
    # Step 2: back-fill from name. Step 3: enforce constraints.
    # Each step is a separate transaction so a failure doesn't roll back others.
    _tool_name_steps = [
        "ALTER TABLE approved_actions ADD COLUMN IF NOT EXISTS tool_name VARCHAR(100)",
        (
            "UPDATE approved_actions "
            "SET tool_name = LOWER(REGEXP_REPLACE(TRIM(name), '[^a-zA-Z0-9]+', '_', 'g')) "
            "WHERE tool_name IS NULL OR tool_name = ''"
        ),
        "ALTER TABLE approved_actions ALTER COLUMN tool_name SET NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_approved_actions_tool_name ON approved_actions (tool_name)",
    ]
    for sql in _tool_name_steps:
        try:
            with engine.connect() as conn:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
        except Exception as _mig_err:
            _mig_log.debug(f"tool_name migration no-op: {_mig_err!r}")

    # ── v1.1.0 schema foundations — sequences, storm columns, INC/STRM trigger ─
    # These are run inline (not via SQL migration files) so they are guaranteed
    # to exist before the first incident is ever created — regardless of whether
    # the external SQL migration scripts ran and regardless of startup order.

    _V110_COLUMN_MIGRATIONS = [
        # Incident enumeration sequence (idempotent)
        "CREATE SEQUENCE IF NOT EXISTS incident_seq START 1 INCREMENT 1",
        # Storm sequence + columns
        "CREATE SEQUENCE IF NOT EXISTS storm_seq START 1 INCREMENT 1",
        "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS is_storm_parent  BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_number      INTEGER",
        "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_number_str  VARCHAR(20)",
        "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_id          UUID REFERENCES workflow_states(workflow_id) ON DELETE SET NULL",
        "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_detected_at TIMESTAMP",
        # Indexes
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_storm_number ON workflow_states(storm_number) WHERE storm_number IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_storm_number_str ON workflow_states(storm_number_str) WHERE storm_number_str IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_workflow_states_storm_id ON workflow_states(storm_id) WHERE storm_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_workflow_storm_parent ON workflow_states(is_storm_parent, created_at DESC) WHERE is_storm_parent = TRUE",
        # Backfill is_storm_parent from context JSONB for existing rows
        "UPDATE workflow_states SET is_storm_parent = TRUE WHERE workflow_type = 'incident' AND is_storm_parent = FALSE AND context->>'is_storm_parent' = 'true'",
    ]
    for sql in _V110_COLUMN_MIGRATIONS:
        try:
            with engine.connect() as conn:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
        except Exception as _mig_err:
            _mig_log.debug(f"v1.1.0 column migration no-op: {_mig_err!r}")

    # ── INC / STRM trigger function (CREATE OR REPLACE — always up to date) ───
    # This trigger guarantees incident_number_str (INC0001…) and storm_number_str
    # (STRM0001…) are assigned at INSERT/UPDATE time — the Python EnumerationService
    # reads the value back but no longer calls nextval() itself.
    _TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION assign_workflow_human_id()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.workflow_type = 'incident' AND NEW.incident_number IS NULL THEN
            NEW.incident_number     := nextval('incident_seq');
            NEW.incident_number_str := 'INC' || LPAD(NEW.incident_number::text, 4, '0');
        END IF;
        IF NEW.is_storm_parent = TRUE AND NEW.storm_number IS NULL THEN
            NEW.storm_number     := nextval('storm_seq');
            NEW.storm_number_str := 'STRM' || LPAD(NEW.storm_number::text, 4, '0');
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.is_storm_parent = TRUE
           AND (OLD.is_storm_parent IS DISTINCT FROM TRUE)
           AND NEW.storm_number IS NULL THEN
            NEW.storm_number     := nextval('storm_seq');
            NEW.storm_number_str := 'STRM' || LPAD(NEW.storm_number::text, 4, '0');
        END IF;
        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$func$
"""
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text(_TRIGGER_SQL))
            conn.execute(__import__("sqlalchemy").text(
                "DROP TRIGGER IF EXISTS trg_workflow_human_id_insert ON workflow_states"
            ))
            conn.execute(__import__("sqlalchemy").text(
                "CREATE TRIGGER trg_workflow_human_id_insert "
                "BEFORE INSERT ON workflow_states "
                "FOR EACH ROW EXECUTE FUNCTION assign_workflow_human_id()"
            ))
            conn.execute(__import__("sqlalchemy").text(
                "DROP TRIGGER IF EXISTS trg_workflow_human_id_update ON workflow_states"
            ))
            conn.execute(__import__("sqlalchemy").text(
                "CREATE TRIGGER trg_workflow_human_id_update "
                "BEFORE UPDATE OF is_storm_parent ON workflow_states "
                "FOR EACH ROW WHEN (NEW.is_storm_parent = TRUE AND OLD.is_storm_parent IS DISTINCT FROM TRUE) "
                "EXECUTE FUNCTION assign_workflow_human_id()"
            ))
            conn.commit()
        _mig_log.info("✓ INC/STRM trigger installed")
    except Exception as _trig_err:
        _mig_log.warning(f"Trigger install failed: {_trig_err!r}")

    # ── Seed event-type taxonomy (idempotent upsert) ─────────────────────────
    # Runs on every startup so entries added to event_type_taxonomy_data.py are
    # picked up without a manual migration. ON CONFLICT DO NOTHING preserves any
    # operator customisations (label/description edits, enabled flag).
    try:
        from agentic_os.db.event_type_taxonomy_data import ALL_ENTRIES
        import json as _json
        _seed_sql = __import__("sqlalchemy").text("""
            INSERT INTO event_type_taxonomy
                (code, label, description, category, aliases, is_system, enabled, created_at)
            VALUES
                (:code, :label, :description, :category, :aliases, :is_system, TRUE, NOW())
            ON CONFLICT (code) DO NOTHING
        """)
        _seeded = 0
        with engine.connect() as _conn:
            for _entry in ALL_ENTRIES:
                result = _conn.execute(_seed_sql, {
                    "code":        _entry["code"],
                    "label":       _entry["label"],
                    "description": _entry.get("description", ""),
                    "category":    _entry["category"],
                    "aliases":     _json.dumps(_entry.get("aliases", [])),
                    "is_system":   _entry.get("is_system", True),
                })
                _seeded += result.rowcount
            _conn.commit()
        if _seeded:
            _mig_log.info(f"✓ Event-type taxonomy: seeded {_seeded} new entries")
        else:
            _mig_log.debug("Event-type taxonomy: all entries already present")
    except Exception as _tax_err:
        _mig_log.warning(f"Taxonomy seed failed (non-fatal): {_tax_err!r}")

    print("✓ Database initialized")


async def get_db_async():
    """Async database session generator (for FastAPI dependency injection)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Enable UUID support in PostgreSQL
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Enable PostgreSQL UUID extension on connection"""
    cursor = dbapi_conn.cursor()
    cursor.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    cursor.close()
