"""add watcher_name to synthetic_monitors

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "synthetic_monitors",
        sa.Column("watcher_name", sa.String(100), nullable=False, server_default="watcher_brain"),
    )
    op.create_index("idx_synthetic_monitors_watcher", "synthetic_monitors", ["watcher_name"])
    op.create_index(
        "idx_synthetic_monitors_watcher_name",
        "synthetic_monitors",
        ["watcher_name", "name"],
        unique=True,
    )
    # Drop the old global unique constraint on name
    op.drop_index("ix_synthetic_monitors_name", table_name="synthetic_monitors")
    op.create_index("ix_synthetic_monitors_name", "synthetic_monitors", ["name"])


def downgrade() -> None:
    op.drop_index("idx_synthetic_monitors_watcher_name", table_name="synthetic_monitors")
    op.drop_index("idx_synthetic_monitors_watcher", table_name="synthetic_monitors")
    op.drop_column("synthetic_monitors", "watcher_name")
    op.drop_index("ix_synthetic_monitors_name", table_name="synthetic_monitors")
    op.create_index("ix_synthetic_monitors_name", "synthetic_monitors", ["name"], unique=True)
