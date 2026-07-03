"""
Runbook seeding — idempotent upsert pipeline.

Called on every backend startup. Uses the Python dict data from
runbooks_seed_data.py and upserts each runbook by its UUID primary key:
  • If the runbook does not exist → INSERT.
  • If it already exists → UPDATE all mutable fields (name, description,
    diagnostics, actions, verification_steps, platform, enabled, confidence,
    blast_radius). This ensures SQL-seeded rows (which had enabled=False and
    platform=NULL) are corrected on next startup without manual intervention.

The only fields never overwritten are:
  • created_at (keeps original creation timestamp)
  • execution statistics (total_executions, successful_executions, etc.)
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def seed_runbooks(db: Session) -> int:
    """
    Upsert all runbooks from runbooks_seed_data.RUNBOOKS.

    Returns the number of rows inserted or updated.
    """
    from agentic_os.db.models import RunbookModel
    from agentic_os.db.runbooks_seed_data import RUNBOOKS

    now = datetime.utcnow()
    inserted = 0
    updated  = 0

    for rb in RUNBOOKS:
        rb_id = uuid.UUID(rb["id"])

        existing = db.query(RunbookModel).filter_by(id=rb_id).first()

        if existing is None:
            # ── INSERT ──────────────────────────────────────────────────
            row = RunbookModel(
                id                  = rb_id,
                name                = rb["name"],
                description         = rb.get("description", ""),
                event_type          = rb["event_type"],
                service             = rb.get("service"),
                environment         = rb.get("environment"),
                platform            = rb.get("platform", "any"),
                diagnostics         = rb.get("diagnostics", []),
                actions             = rb.get("actions", []),
                verification_steps  = rb.get("verification_steps", []),
                source_steps        = rb.get("source_steps"),
                confidence          = rb.get("confidence", 0.80),
                blast_radius        = rb.get("blast_radius", 1),
                enabled             = rb.get("enabled", True),
                is_seeded           = True,
                # Seeded/OOB runbooks are trusted defaults — published immediately,
                # never sitting in draft waiting on a manual publish click.
                status                   = "published",
                published_at             = now,
                has_unpublished_changes  = False,
                created_at          = now,
                updated_at          = now,
            )
            db.add(row)
            inserted += 1
            logger.info(f"[SEED] Runbook inserted: {rb['name']} ({rb['event_type']})")

        else:
            # ── UPDATE mutable fields, preserve stats & created_at ──────
            changed = False

            def _set(attr, val):
                nonlocal changed
                if getattr(existing, attr) != val:
                    setattr(existing, attr, val)
                    changed = True

            _set("name",               rb["name"])
            _set("description",        rb.get("description", ""))
            _set("event_type",         rb["event_type"])
            _set("service",            rb.get("service"))
            _set("environment",        rb.get("environment"))
            _set("platform",           rb.get("platform", "any"))
            _set("diagnostics",        rb.get("diagnostics", []))
            _set("actions",            rb.get("actions", []))
            _set("verification_steps", rb.get("verification_steps", []))
            _set("confidence",         rb.get("confidence", 0.80))
            _set("blast_radius",       rb.get("blast_radius", 1))
            _set("enabled",            rb.get("enabled", True))
            _set("is_seeded",          True)
            # Self-heal: seeded runbooks must always be published, never stuck in draft
            _set("status",                  "published")
            _set("has_unpublished_changes", False)
            if existing.published_at is None:
                existing.published_at = now
                changed = True
            # Seed source_steps only when the DB has none — never overwrite user edits
            if rb.get("source_steps") and existing.source_steps is None:
                existing.source_steps = rb["source_steps"]
                changed = True

            if changed:
                existing.updated_at = now
                updated += 1
                logger.info(
                    f"[SEED] Runbook updated: {rb['name']} ({rb['event_type']}) "
                    f"— platform={rb.get('platform','any')} enabled={rb.get('enabled',True)}"
                )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[SEED] Runbook seed commit failed: {e}")
        raise

    total = inserted + updated
    if total:
        logger.info(
            f"[SEED] Runbook seed complete: {inserted} inserted, {updated} updated "
            f"({len(RUNBOOKS)} runbooks total in catalog)"
        )
    else:
        logger.info(
            f"[SEED] Runbooks up to date: {len(RUNBOOKS)} runbooks, no changes needed"
        )

    return total
