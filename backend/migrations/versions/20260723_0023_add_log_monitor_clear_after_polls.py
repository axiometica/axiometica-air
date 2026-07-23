"""Add clear_after_polls column to log_monitor_configs

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "log_monitor_configs",
        sa.Column("clear_after_polls", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade():
    op.drop_column("log_monitor_configs", "clear_after_polls")
