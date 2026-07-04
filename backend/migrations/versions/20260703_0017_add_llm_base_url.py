"""Add base_url column to llm_configs for local LLM providers (Ollama).

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_configs",
        sa.Column("base_url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_configs", "base_url")
