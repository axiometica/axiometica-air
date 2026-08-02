"""Add per-watcher settings JSONB column to watcher_registrations.

Revision ID: 0029
Revises: 0028
"""

from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "watcher_registrations",
        sa.Column("settings", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("watcher_registrations", "settings")
