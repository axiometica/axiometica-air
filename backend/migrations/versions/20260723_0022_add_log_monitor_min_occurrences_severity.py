"""Add min_occurrences and severity columns to log_monitor_configs

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "log_monitor_configs",
        sa.Column("min_occurrences", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "log_monitor_configs",
        sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
    )


def downgrade():
    op.drop_column("log_monitor_configs", "min_occurrences")
    op.drop_column("log_monitor_configs", "severity")
