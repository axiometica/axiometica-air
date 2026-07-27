"""Add llm_usage table for per-day per-principal cost tracking

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-27

Feeds the demo LLM cap system. Written for every LLM call by every
principal; queryable for spend reporting. Small — one row per (principal,
day, model) — so unbounded growth isn't a concern at the demo scale.

Not demo-specific: schema exists on every install (migrations aren't
opt-in). Only WRITTEN when demo_mode's maybe_record_usage fires, and
that only fires for demo principals. Non-demo installs get an empty
table that never grows.
"""

import sqlalchemy as sa
from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_usage",
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("usage_date",   sa.Date(),      nullable=False),
        sa.Column("model",        sa.String(80),  nullable=False),
        sa.Column("input_tokens",  sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd",     sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("call_count",   sa.Integer(),   nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("principal_id", "usage_date", "model"),
    )
    op.create_index("idx_llm_usage_date", "llm_usage", ["usage_date"])
    op.create_index("idx_llm_usage_principal", "llm_usage", ["principal_id"])


def downgrade():
    op.drop_index("idx_llm_usage_principal", table_name="llm_usage")
    op.drop_index("idx_llm_usage_date", table_name="llm_usage")
    op.drop_table("llm_usage")
