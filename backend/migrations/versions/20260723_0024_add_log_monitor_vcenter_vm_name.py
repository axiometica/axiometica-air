"""Add vm_name column to log_monitor_configs for vCenter source type

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "log_monitor_configs",
        sa.Column("vm_name", sa.String(200), nullable=False, server_default=""),
    )


def downgrade():
    op.drop_column("log_monitor_configs", "vm_name")
