"""Add source and container columns to log_monitor_configs for docker logs polling

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "log_monitor_configs",
        sa.Column("source", sa.String(20), nullable=False, server_default="file"),
    )
    op.add_column(
        "log_monitor_configs",
        sa.Column("container", sa.String(200), nullable=False, server_default=""),
    )


def downgrade():
    op.drop_column("log_monitor_configs", "source")
    op.drop_column("log_monitor_configs", "container")
