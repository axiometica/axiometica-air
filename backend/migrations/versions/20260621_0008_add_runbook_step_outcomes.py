"""Add runbook_step_outcomes table.

Persists per-step execution results (which was previously computed by
ToolRegistryAgent and then discarded after the abort/continue decision)
so Platform Intelligence can identify which specific step in a runbook is
brittle, rather than only seeing whole-runbook success/failure rates.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runbook_step_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_states.workflow_id", ondelete="CASCADE"), nullable=False),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("step_name", sa.String(255), nullable=True),
        sa.Column("step_type", sa.String(50), nullable=True),
        sa.Column("tool", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_runbook_step_outcomes_runbook_step",
        "runbook_step_outcomes",
        ["runbook_id", "step_index"],
    )
    op.create_index(
        "idx_runbook_step_outcomes_workflow",
        "runbook_step_outcomes",
        ["workflow_id"],
    )
    op.create_index(
        "idx_runbook_step_outcomes_created",
        "runbook_step_outcomes",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_runbook_step_outcomes_created", table_name="runbook_step_outcomes")
    op.drop_index("idx_runbook_step_outcomes_workflow", table_name="runbook_step_outcomes")
    op.drop_index("idx_runbook_step_outcomes_runbook_step", table_name="runbook_step_outcomes")
    op.drop_table("runbook_step_outcomes")
