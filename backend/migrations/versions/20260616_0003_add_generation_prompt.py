"""Add generation_prompt column to runbooks table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runbooks",
        sa.Column("generation_prompt", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runbooks", "generation_prompt")
