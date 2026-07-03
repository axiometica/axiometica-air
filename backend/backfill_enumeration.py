#!/usr/bin/env python3
"""
Backfill incident numbers for existing incidents that don't have one.
Also fixes 'Unknown Incident' titles using the alert_payload data.
Run once after applying the enumeration migration.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sqlalchemy import create_engine, text
from agentic_os.db.database import DATABASE_URL

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1) Ensure sequence exists
    conn.execute(text("CREATE SEQUENCE IF NOT EXISTS incident_seq START 1 INCREMENT 1"))
    conn.commit()

    # 2) Find all incidents missing an INC number, ordered by creation date
    rows = conn.execute(text("""
        SELECT workflow_id, title, context
        FROM workflow_states
        WHERE workflow_type = 'incident'
          AND incident_number IS NULL
        ORDER BY created_at ASC
    """)).fetchall()

    print(f"Found {len(rows)} incidents without enumeration")

    for row in rows:
        wf_id  = row[0]
        title  = row[1]
        ctx    = row[2] or {}

        # Get next sequence number
        num = conn.execute(text("SELECT nextval('incident_seq')")).scalar()
        inc_str = f"INC{num:04d}"

        # Fix title if it's null or 'Unknown Incident'
        if not title or title == 'Unknown Incident':
            alert = ctx.get('alert_payload', {})
            event_type    = alert.get('type', '')
            resource_name = alert.get('resource_name', '')
            # Check new context schema path too
            sentinel = ctx.get('sentinel', {})
            if not event_type and sentinel:
                event_type = sentinel.get('anomaly_type', '')
                resource_name = resource_name or (sentinel.get('alert_payload', {}) or {}).get('resource_name', '')
            if event_type and resource_name:
                title = f"{event_type.replace('_', ' ').title()} on {resource_name}"
            elif event_type:
                title = event_type.replace('_', ' ').title()
            else:
                title = f"Incident {inc_str}"

        conn.execute(text("""
            UPDATE workflow_states
            SET incident_number = :num,
                incident_number_str = :inc_str,
                title = :title
            WHERE workflow_id = :wf_id
        """), {"num": int(num), "inc_str": inc_str, "title": title, "wf_id": str(wf_id)})

        print(f"  {inc_str} -> {wf_id} | {title}")

    conn.commit()
    print(f"\nDone. Assigned {len(rows)} incident numbers.")
