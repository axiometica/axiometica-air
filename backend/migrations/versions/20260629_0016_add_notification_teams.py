"""Add notification_teams table for standalone team-based alert routing.

PagerDuty escalation/notification only had a single global routing key and
a single global Slack channel — every alert_escalate/alert_update/send_alert
call went to the same destination regardless of which team actually owns
the affected resource. notification_teams lets an admin define named teams
with their own PagerDuty routing key / Slack channel / email recipients /
webhook URL, looked up by name via a `team` arg on the notify action.
Deliberately standalone (no ServiceNow/CMDB dependency) — v1 routes only on
an explicit `team` arg, falling back to the existing global defaults.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_teams",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("pagerduty_routing_key", sa.String(500), nullable=True),
        sa.Column("slack_channel", sa.String(100), nullable=True),
        sa.Column("email_recipients", sa.Text(), nullable=True),
        sa.Column("webhook_url", sa.String(500), nullable=True),
        sa.Column("webhook_secret", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_notification_team_name", "notification_teams", ["name"])


def downgrade() -> None:
    op.drop_index("idx_notification_team_name", table_name="notification_teams")
    op.drop_table("notification_teams")
