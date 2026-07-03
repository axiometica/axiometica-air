"""Add draft/publish workflow + version history to runbooks and policies.

Today, editing a runbook or policy takes effect immediately for the very
next matching incident — there's no staging gate and no way to see prior
state or undo a bad edit. This is most dangerous on the policy side, since
PolicyModel.requires_manual_approval is the actual approval gate for
incident remediation; a bad edit there fails silently rather than loudly.

Adds status/published_at/has_unpublished_changes/draft_snapshot to both
tables (PUT now writes draft_snapshot only; a separate publish step copies
it onto the live columns) plus runbook_versions/policy_versions, written
once per publish. `enabled` on both tables is deliberately left out of this
workflow — it stays an instant kill-switch.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, singular in (("runbooks", "runbook"), ("policies", "policy")):
        op.add_column(table, sa.Column("status", sa.String(20), nullable=False, server_default="draft"))
        op.add_column(table, sa.Column("published_at", sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column("has_unpublished_changes", sa.Boolean(), nullable=False, server_default="false"))
        op.add_column(table, sa.Column("draft_snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=True))
        op.create_index(f"idx_{singular}_status", table, ["status"])

    op.create_table(
        "runbook_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("runbook_id", "version", name="uq_runbook_version"),
    )
    op.create_index("idx_runbook_version_runbook_id", "runbook_versions", ["runbook_id"])

    op.create_table(
        "policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("policies.policy_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_version"),
    )
    op.create_index("idx_policy_version_policy_id", "policy_versions", ["policy_id"])

    # Every existing row is "live" today — backfill so nothing currently
    # matched by incident execution silently disappears after this migration.
    op.execute("UPDATE runbooks SET status = 'published', published_at = updated_at, has_unpublished_changes = false")
    op.execute("UPDATE policies SET status = 'published', published_at = updated_at, has_unpublished_changes = false")


def downgrade() -> None:
    op.drop_index("idx_policy_version_policy_id", table_name="policy_versions")
    op.drop_table("policy_versions")
    op.drop_index("idx_runbook_version_runbook_id", table_name="runbook_versions")
    op.drop_table("runbook_versions")

    for table, singular in (("runbooks", "runbook"), ("policies", "policy")):
        op.drop_index(f"idx_{singular}_status", table_name=table)
        op.drop_column(table, "draft_snapshot")
        op.drop_column(table, "has_unpublished_changes")
        op.drop_column(table, "published_at")
        op.drop_column(table, "status")
