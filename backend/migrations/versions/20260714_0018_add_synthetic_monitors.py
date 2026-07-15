"""Add synthetic_monitors table

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "synthetic_monitors",
        sa.Column("id",              sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name",            sa.String(200), nullable=False),
        sa.Column("har_filename",    sa.String(500), nullable=True),
        sa.Column("script",          sa.Text, nullable=True),
        sa.Column("credentials_enc", sa.Text, nullable=True),
        sa.Column("schedule_mins",   sa.Integer, nullable=False, server_default="60"),
        sa.Column("enabled",         sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_run_at",     sa.DateTime, nullable=True),
        sa.Column("last_status",     sa.String(20), nullable=True),
        sa.Column("last_output",     sa.Text, nullable=True),
        sa.Column("created_at",      sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",      sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_synthetic_monitors_name",    "synthetic_monitors", ["name"],    unique=True)
    op.create_index("idx_synthetic_monitors_enabled", "synthetic_monitors", ["enabled"], unique=False)


def downgrade():
    op.drop_index("idx_synthetic_monitors_enabled", table_name="synthetic_monitors")
    op.drop_index("idx_synthetic_monitors_name",    table_name="synthetic_monitors")
    op.drop_table("synthetic_monitors")
