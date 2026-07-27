"""Extend principals_role_check to allow 'demo'

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-27

The principals table has a CHECK constraint limiting `role` to the five
original values. Demo mode adds a sixth ('demo'), so the constraint
needs to be rebuilt to include it. Drop-then-recreate is the standard
Postgres pattern for changing a CHECK constraint's IN-list.
"""

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE principals DROP CONSTRAINT IF EXISTS principals_role_check")
    op.execute(
        "ALTER TABLE principals ADD CONSTRAINT principals_role_check "
        "CHECK (role IN ('admin','itom_admin','operator','viewer','automation','demo'))"
    )


def downgrade():
    op.execute("ALTER TABLE principals DROP CONSTRAINT IF EXISTS principals_role_check")
    op.execute(
        "ALTER TABLE principals ADD CONSTRAINT principals_role_check "
        "CHECK (role IN ('admin','itom_admin','operator','viewer','automation'))"
    )
