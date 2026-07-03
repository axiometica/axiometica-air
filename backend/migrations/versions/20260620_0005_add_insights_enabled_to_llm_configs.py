"""Add insights_enabled column to llm_configs table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_configs",
        sa.Column(
            "insights_enabled",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_configs", "insights_enabled")
