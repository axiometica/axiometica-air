"""Add platform_intel_runs table.

Persists one row per TuningAgent analysis pass (scheduled, manual, or force
refresh) — incidents_analysed, which path produced the recommendations
(llm/rules/healthy/suppressed), the raw LLM response (for a "what did the
model actually see and say" debug view), and a JSONB snapshot of computed
KPIs. Without this, every run's reasoning was discarded the moment the
response was returned, and there was no way to plot a KPI trend over time
or audit why a given cycle behaved a certain way (e.g. confirm LLM vs rules
fired) without grepping backend logs.

kpis is JSONB rather than fixed columns so new KPIs can be added later
without another migration each time.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_intel_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("period_days", sa.Integer, nullable=False),
        # "scheduled" | "manual" | "force_refresh"
        sa.Column("trigger", sa.String(20), nullable=False),
        # "llm" | "rules" | "healthy" | "suppressed" | "insufficient_data"
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("incidents_analysed", sa.Integer, nullable=False, default=0),
        sa.Column("recommendations_generated", sa.Integer, nullable=False, default=0),
        sa.Column("recommendations_skipped", sa.Integer, nullable=False, default=0),
        sa.Column("llm_raw_response", sa.Text, nullable=True),
        sa.Column("kpis", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index(
        "idx_platform_intel_runs_created",
        "platform_intel_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_platform_intel_runs_created", table_name="platform_intel_runs")
    op.drop_table("platform_intel_runs")
