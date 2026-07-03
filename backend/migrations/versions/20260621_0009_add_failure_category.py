"""Add failure_category to remediation_outcomes and runbook_step_outcomes.

Classifies a failure's root cause (tool_error, target_not_found,
precondition_unmet, timeout, permission_denied, partial_completion, unknown)
so Platform Intelligence can distinguish "this runbook is badly written"
from "this runbook is fine but fed stale CMDB data" in its rationale text.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "remediation_outcomes",
        sa.Column("failure_category", sa.String(30), nullable=True),
    )
    op.add_column(
        "runbook_step_outcomes",
        sa.Column("failure_category", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runbook_step_outcomes", "failure_category")
    op.drop_column("remediation_outcomes", "failure_category")
