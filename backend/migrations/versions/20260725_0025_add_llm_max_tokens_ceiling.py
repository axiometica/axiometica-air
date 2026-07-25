"""Add max_tokens_ceiling column to llm_configs

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "llm_configs",
        sa.Column(
            "max_tokens_ceiling",
            sa.Integer(),
            nullable=False,
            server_default="4000",
        ),
    )


def downgrade():
    op.drop_column("llm_configs", "max_tokens_ceiling")
