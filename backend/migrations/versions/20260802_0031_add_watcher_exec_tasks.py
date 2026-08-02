"""Add watcher_exec_tasks table and dispatch_mode column

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "watcher_exec_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("watcher_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("command", sa.Text, nullable=False),
        sa.Column("target", sa.String(255), nullable=False, server_default=""),
        sa.Column("mode", sa.String(20), nullable=False, server_default="host"),
        sa.Column("timeout", sa.Integer, nullable=False, server_default="30"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("result_success", sa.Boolean, nullable=True),
        sa.Column("result_stdout", sa.Text, nullable=True),
        sa.Column("result_stderr", sa.Text, nullable=True),
        sa.Column("result_returncode", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "idx_watcher_exec_tasks_watcher_status",
        "watcher_exec_tasks",
        ["watcher_id", "status"],
    )
    op.create_index(
        "idx_watcher_exec_tasks_workflow",
        "watcher_exec_tasks",
        ["workflow_id", "step_index"],
    )

    op.add_column(
        "watcher_registrations",
        sa.Column("dispatch_mode", sa.String(10), nullable=False, server_default="push"),
    )


def downgrade():
    op.drop_column("watcher_registrations", "dispatch_mode")
    op.drop_table("watcher_exec_tasks")
