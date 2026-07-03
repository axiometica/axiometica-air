"""Add auto-apply columns to optimization_recommendations table.

Platform Intelligence recommendations can now earn auto-apply trust:
after 3 consecutive accepted+applied+verified-improved cycles for the
same parameter, new recommendations for that parameter apply themselves
(status=auto_applied) instead of waiting for manual review.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_recommendations",
        sa.Column("auto_apply_eligible", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column(
        "optimization_recommendations",
        sa.Column("auto_apply_threshold_met_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "optimization_recommendations",
        sa.Column("outcome_verified_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "optimization_recommendations",
        sa.Column("outcome_improved", sa.Boolean, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_recommendations", "outcome_improved")
    op.drop_column("optimization_recommendations", "outcome_verified_at")
    op.drop_column("optimization_recommendations", "auto_apply_threshold_met_at")
    op.drop_column("optimization_recommendations", "auto_apply_eligible")
