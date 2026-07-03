"""Add target_locks table for per-target remediation concurrency control.

Multiple incidents can target the same infrastructure resource concurrently
(e.g. two storm-correlated incidents on the same host). Nothing today stops
two ToolRegistryAgent runs from executing mutating steps on the same target
at once — one incident's restart_service can race a second incident's
process_kill on the exact same container. Diagnostics (blast_radius=1,
read-only) are unaffected; only the mutating step-execution loop acquires
a lease here.

This is a lease, not a hard mutex: if a worker dies mid-remediation while
holding the row, expires_at (~15 min, longer than the 5-min per-step
timeout) lets a periodic sweep (cleanup_expired_target_locks in
tasks/celery_app.py) reclaim it rather than deadlocking the target forever.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_locks",
        sa.Column("target_id", sa.String(255), primary_key=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_target_locks_expires_at", "target_locks", ["expires_at"])
    op.create_index("idx_target_locks_incident_id", "target_locks", ["incident_id"])


def downgrade() -> None:
    op.drop_index("idx_target_locks_incident_id", table_name="target_locks")
    op.drop_index("idx_target_locks_expires_at", table_name="target_locks")
    op.drop_table("target_locks")
