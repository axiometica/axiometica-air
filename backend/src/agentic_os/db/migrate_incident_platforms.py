"""
Migration script to add platform field to existing incidents' context.

This script updates incidents that were created before the platform serialization
fix was applied. It re-derives the platform from the resource type and updates
the incident's context with the platform field.

Usage:
    python -c "from agentic_os.db.migrate_incident_platforms import run_migration; run_migration()"
"""

import json
import logging
import sys
from sqlalchemy.orm import Session
from sqlalchemy import text

from agentic_os.db.database import SessionLocal
from agentic_os.agents.incident_agents import _derive_platform

logger = logging.getLogger(__name__)


def migrate_incident_platforms(db: Session) -> int:
    """
    Update all incidents that are missing the platform field in their context.

    Returns:
        Number of incidents updated
    """
    updated_count = 0
    skipped_count = 0

    try:
        # Find all incidents with context that have cmdb
        rows = db.execute(text("""
            SELECT
                workflow_id,
                incident_number_str,
                context
            FROM workflow_states
            WHERE CAST(workflow_type AS TEXT) = 'incident'
            AND   context IS NOT NULL
        """)).fetchall()

        print(f"[MIGRATE] Checking {len(rows)} incidents...")

        for row in rows:
            workflow_id, inc_number, context = row

            if not isinstance(context, dict):
                continue

            # Check if context has cmdb and if platform is missing
            if 'cmdb' not in context:
                continue

            cmdb = context['cmdb']
            if not isinstance(cmdb, dict):
                continue

            # Skip if platform already exists
            if 'platform' in cmdb and cmdb['platform']:
                skipped_count += 1
                continue

            # Derive platform from resource_info
            resource_info = cmdb.get('resource_info', {})
            if not isinstance(resource_info, dict):
                continue

            resource_type = resource_info.get('type', 'service')
            platform = _derive_platform(resource_type)

            # Update context with platform
            cmdb['platform'] = platform
            context['cmdb'] = cmdb

            # Serialize to JSON
            context_json = json.dumps(context, default=str)

            # Persist back to database
            db.execute(text(f"""
                UPDATE workflow_states
                SET context = CAST(:context_json AS jsonb)
                WHERE workflow_id = :workflow_id
            """), {
                'context_json': context_json,
                'workflow_id': workflow_id
            })

            updated_count += 1
            print(f"  [+] {inc_number}: platform={platform} (resource_type={resource_type})")

        if updated_count > 0:
            db.commit()
            print(f"\n[MIGRATE] Successfully updated {updated_count} incidents")
        else:
            print(f"\n[MIGRATE] No incidents needed updating ({skipped_count} already had platform)")

        return updated_count

    except Exception as exc:
        logger.error(f"[MIGRATE] Migration failed: {exc}")
        db.rollback()
        print(f"\n[ERROR] Migration failed: {exc}", file=sys.stderr)
        raise


def run_migration():
    """Run the migration against the database."""
    db = SessionLocal()
    try:
        count = migrate_incident_platforms(db)
        return count
    finally:
        db.close()


if __name__ == "__main__":
    count = run_migration()
    sys.exit(0 if count >= 0 else 1)
