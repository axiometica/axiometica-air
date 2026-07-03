"""Initial schema — principals + audit log

Consolidates the two SQL migration files that were previously applied manually
in main.py's lifespan function.  All statements are idempotent (CREATE TABLE IF
NOT EXISTS, CREATE INDEX IF NOT EXISTS) so this revision is safe to run against
an existing database that was created before Alembic was introduced.

For databases that already have these tables and indexes, stamp them at head
instead of re-running:
    alembic stamp head

Revision ID: 0001
Revises: None
Create Date: 2026-01-01 00:00:00.000000
"""

import os

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Ordered list of SQL files to apply (relative to this file's directory)
# Order matters: dependencies must be applied first
_SQL_FILES = [
    "add_principals.sql",
    "add_principal_audit.sql",
    "add_incident_enumeration.sql",
    "add_incident_lifecycle_v2.sql",
    "add_external_check_fields.sql",
    "add_ml_tables.sql",
    "add_state_history.sql",
    "add_workflow_state_columns.sql",
    "add_runbook_source.sql",
    "add_storm_hold_state.sql",
    "populate_action_commands.sql",
    "seed_platform_settings.sql",
    "add_updated_at_index.sql",
    "20260603_0001_add_performance_indexes.sql",
]

# Files containing CREATE INDEX CONCURRENTLY. Postgres refuses to run this
# statement inside a transaction block — including the implicit block it
# wraps around a multi-statement string sent in one round-trip, even when
# the connection is otherwise in autocommit mode. So each statement in these
# files must be sent as its own round-trip with the connection taken out of
# the migration's ambient transaction via autocommit_block().
_CONCURRENT_INDEX_FILES = {
    "add_updated_at_index.sql",
    "20260603_0001_add_performance_indexes.sql",
}


def _split_statements(sql_text: str) -> list[str]:
    """Split a SQL file into individual statements, dropping comment-only lines."""
    code_lines = [
        line for line in sql_text.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    return [s.strip() for s in "\n".join(code_lines).split(";") if s.strip()]


def upgrade() -> None:
    versions_dir = os.path.dirname(__file__)
    conn = op.get_bind()
    for sql_file in _SQL_FILES:
        sql_path = os.path.join(versions_dir, sql_file)
        with open(sql_path) as fh:
            sql_text = fh.read()
        if sql_file in _CONCURRENT_INDEX_FILES:
            with op.get_context().autocommit_block():
                for statement in _split_statements(sql_text):
                    conn.execute(sa.text(statement))
        else:
            conn.execute(sa.text(sql_text))


def downgrade() -> None:
    # Dropping the principals and audit tables would destroy all user accounts.
    # A full downgrade is intentionally not implemented — restore from backup.
    raise NotImplementedError(
        "Downgrade from initial_schema is not supported. Restore from a database backup."
    )
