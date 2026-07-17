"""Add default_severity to event_type_taxonomy, backfill watcher-native types

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Backfill matches *current* watcher_service.py behavior exactly (criticality_map
# + the two previously-hardcoded types) — this migration makes it configurable,
# it does not change any default. Everything else in the taxonomy stays NULL.
_BACKFILL = [
    ("infrastructure.compute.syscall_intensity_high", "critical"),
    ("infrastructure.compute.cpu_high",                "warning"),
    ("infrastructure.compute.memory_high",             "critical"),
    ("infrastructure.storage.disk_full",               "critical"),
    ("application.availability.health_check_failing",  "warning"),
    ("application.performance.latency_high",           "warning"),
    ("application.performance.error_rate_high",         "info"),
    ("application.availability.service_unresponsive",  "critical"),
    ("network.tls.certificate_expiring",                "warning"),
    ("synthetic.transaction.failed",                   "critical"),
]


def upgrade():
    op.add_column(
        "event_type_taxonomy",
        sa.Column("default_severity", sa.String(20), nullable=True),
    )
    conn = op.get_bind()
    for code, severity in _BACKFILL:
        conn.execute(
            sa.text(
                "UPDATE event_type_taxonomy SET default_severity = :severity WHERE code = :code"
            ),
            {"severity": severity, "code": code},
        )


def downgrade():
    op.drop_column("event_type_taxonomy", "default_severity")
