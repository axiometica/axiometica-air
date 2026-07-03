"""Add resume_retry_count to workflow_states.

Today, if an approval is decided (lifecycle_state flips to 'approved'
synchronously) but resume_workflow_task never actually fires — a broker
hiccup, a backend restart at the wrong moment, a worker crash — the
incident is stuck with no operator-facing recovery action. awaiting_manual
has Retry/Resolve Manually buttons; 'approved' has nothing.

resume_retry_count tracks how many times the safety-net sweep
(resume_stuck_approvals in tasks/celery_app.py) has auto-re-fired the resume
task for a given incident still stuck in 'approved'. Once it hits the cap,
the sweep stops retrying and escalates to awaiting_manual instead, so a
human eventually sees it either way.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_states",
        sa.Column("resume_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_states", "resume_retry_count")
