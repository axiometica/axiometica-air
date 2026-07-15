"""Add pages_json to synthetic_monitors

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("synthetic_monitors", sa.Column("pages_json", sa.Text, nullable=True))


def downgrade():
    op.drop_column("synthetic_monitors", "pages_json")
