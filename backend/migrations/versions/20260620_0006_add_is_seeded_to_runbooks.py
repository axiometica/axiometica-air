"""Add is_seeded column to runbooks table.

Seeded runbooks ship with the platform and cannot be deleted.
Existing rows default to False; the seeding pipeline sets them to True
on the next startup via the normal upsert pass.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runbooks",
        sa.Column(
            "is_seeded",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("runbooks", "is_seeded")
