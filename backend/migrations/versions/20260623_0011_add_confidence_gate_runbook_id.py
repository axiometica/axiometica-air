"""Add confidence_gate_runbook_id to policies.

The confidence gate (confidence_gate_threshold / confidence_gate_min_runs)
previously checked whichever runbook the 4-pass lookup cascade happened to
resolve for the incident's event_type/service/platform at execution time —
not a runbook the policy author actually chose. Multiple runbooks can match
the same event_type/service, so this coupled "which runbook runs" to "is the
gate trustworthy" in a way the operator had no control over.

confidence_gate_runbook_id lets a policy pin the gate to one specific,
named runbook. NULL preserves the existing cascade-lookup behaviour.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policies",
        sa.Column(
            "confidence_gate_runbook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runbooks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("policies", "confidence_gate_runbook_id")
