"""Add ssh_credentials table

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ssh_credentials",
        sa.Column("id",           sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name",         sa.String(100), nullable=False),
        sa.Column("host_pattern", sa.String(255), nullable=False),
        sa.Column("username",     sa.String(100), nullable=False, server_default="root"),
        sa.Column("private_key",  sa.String(16000), nullable=False),
        sa.Column("port",         sa.Integer, nullable=False, server_default="22"),
        sa.Column("description",  sa.Text, nullable=True),
        sa.Column("enabled",      sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at",   sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",   sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_ssh_credentials_name",    "ssh_credentials", ["name"],    unique=True)
    op.create_index("idx_ssh_credentials_pattern", "ssh_credentials", ["host_pattern"], unique=False)
    op.create_index("idx_ssh_credentials_enabled", "ssh_credentials", ["enabled"], unique=False)


def downgrade():
    op.drop_index("idx_ssh_credentials_enabled", table_name="ssh_credentials")
    op.drop_index("idx_ssh_credentials_pattern", table_name="ssh_credentials")
    op.drop_index("idx_ssh_credentials_name",    table_name="ssh_credentials")
    op.drop_table("ssh_credentials")
