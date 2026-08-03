"""Add watcher_targets table and discovery_auto_approve column

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watcher_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("watcher_id", UUID(as_uuid=True), sa.ForeignKey("watcher_registrations.watcher_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="22"),
        sa.Column("credential_name", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("cidr_group", sa.String(50), nullable=True),
        sa.Column("auto_approve", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_probe_at", sa.DateTime, nullable=True),
        sa.Column("last_connected_at", sa.DateTime, nullable=True),
        sa.Column("probe_error", sa.String(500), nullable=True),
        sa.Column("matched_credential", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("watcher_id", "host", "port", name="uq_watcher_target_host_port"),
    )
    op.create_index("idx_watcher_targets_watcher_status", "watcher_targets", ["watcher_id", "status"])

    op.add_column(
        "watcher_registrations",
        sa.Column("discovery_auto_approve", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("watcher_registrations", "discovery_auto_approve")
    op.drop_index("idx_watcher_targets_watcher_status", table_name="watcher_targets")
    op.drop_table("watcher_targets")
