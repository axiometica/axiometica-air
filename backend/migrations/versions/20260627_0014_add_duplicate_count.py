"""Add duplicate_count to workflow_states.

The watcher intentionally clears its own tracking once an incident reaches
awaiting_manual (operator has taken ownership). The backend's resource-level
dedup then silently re-links the next recurrence to that same dormant
incident — correct behavior, but with zero visibility that the underlying
condition is still actively recurring while a human hasn't looked yet.

duplicate_count is incremented (in the monitoring-events dedup branch) only
when a recurrence is validated as an exact repeat of the same condition from
the same source — not just any event sharing the resource.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_states",
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_states", "duplicate_count")
