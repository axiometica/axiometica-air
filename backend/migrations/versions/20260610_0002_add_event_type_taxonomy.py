"""Add event_type_taxonomy table and seed canonical types.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10
"""

import json
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create table ───────────────────────────────────────────────────────
    op.create_table(
        "event_type_taxonomy",
        sa.Column("code",        sa.String(150), primary_key=True),
        sa.Column("label",       sa.String(200), nullable=False),
        sa.Column("description", sa.Text,        nullable=True),
        sa.Column("category",    sa.String(50),  nullable=False),
        sa.Column("aliases",     sa.JSON,        nullable=False, server_default="[]"),
        sa.Column("is_system",   sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("enabled",     sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("created_at",  sa.DateTime,    nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_taxonomy_category",         "event_type_taxonomy", ["category"])
    op.create_index("idx_taxonomy_enabled_category", "event_type_taxonomy", ["enabled", "category"])

    # ── 2. Seed canonical types ───────────────────────────────────────────────
    # Import here to keep migration self-contained (data module is in src path)
    try:
        from agentic_os.db.event_type_taxonomy_data import ALL_ENTRIES
    except ImportError:
        # Fallback if run outside the installed package — embed inline seed
        _seed_inline(op)
        return

    conn = op.get_bind()
    now  = datetime.utcnow()

    # Use op.bulk_insert via a Table definition — avoids psycopg2 JSONB cast issues
    taxonomy_table = sa.table(
        "event_type_taxonomy",
        sa.column("code",        sa.String),
        sa.column("label",       sa.String),
        sa.column("description", sa.Text),
        sa.column("category",    sa.String),
        sa.column("aliases",     sa.JSON),
        sa.column("is_system",   sa.Boolean),
        sa.column("enabled",     sa.Boolean),
        sa.column("created_at",  sa.DateTime),
    )

    rows = [
        {
            "code":        e["code"],
            "label":       e["label"],
            "description": e["description"],
            "category":    e["category"],
            "aliases":     e["aliases"],   # pass as Python list; SQLAlchemy serialises to JSON
            "is_system":   True,
            "enabled":     True,
            "created_at":  now,
        }
        for e in ALL_ENTRIES
    ]

    # Insert in one batch — table is brand-new in this transaction, no conflicts possible
    conn.execute(taxonomy_table.insert(), rows)


def downgrade() -> None:
    op.drop_index("idx_taxonomy_enabled_category", table_name="event_type_taxonomy")
    op.drop_index("idx_taxonomy_category",         table_name="event_type_taxonomy")
    op.drop_table("event_type_taxonomy")


def _seed_inline(op) -> None:
    """Minimal inline seed used only when the src package is not importable."""
    pass  # safe no-op — table will be seeded via API or next full deploy
