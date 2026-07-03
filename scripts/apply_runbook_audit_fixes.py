"""
One-off: push corrected source_steps for the 5 runbooks fixed in the
output_capture/decision-condition audit (commit 8088148) into the database.

Why this is needed: seed_runbooks()'s upsert deliberately never overwrites an
existing row's source_steps once it's non-null (db/runbooks_seed.py — "Seed
source_steps only when the DB has none — never overwrite user edits"). Since
these 5 runbooks were already seeded with non-null source_steps before this
fix, a plain `git pull` + restart will NOT pick up the graph-level corrections
on its own — only the flat legacy diagnostics/actions/verification_steps
arrays auto-update on restart. This script explicitly pushes the corrected
source_steps (and flat arrays, for completeness) for just these 5 runbooks,
matched by id. Nothing else in the database is touched.

Run inside the backend container:
    docker cp scripts/apply_runbook_audit_fixes.py agentic_os_backend:/tmp/apply_runbook_audit_fixes.py
    docker exec agentic_os_backend python3 /tmp/apply_runbook_audit_fixes.py
"""
from agentic_os.db.database import SessionLocal
from agentic_os.db.models import RunbookModel
from agentic_os.db.runbooks_seed_data import RUNBOOKS
import uuid

FIXED_RUNBOOK_NAMES = {
    "High Syscall Intensity — Process Termination",
    "High Latency — Diagnose and Reduce Load",
    "Log Error Detected — Diagnose and Recover",
    "Web Service Health Check and Remediation",
    "Service Unresponsive — Check Status, Restart, Validate",
}

db = SessionLocal()
updated = 0
for rb in RUNBOOKS:
    if rb["name"] not in FIXED_RUNBOOK_NAMES:
        continue
    row = db.query(RunbookModel).filter_by(id=uuid.UUID(rb["id"])).first()
    if not row:
        print(f"SKIP (not in DB): {rb['name']}")
        continue
    row.source_steps = rb["source_steps"]
    row.diagnostics = rb.get("diagnostics", [])
    row.actions = rb.get("actions", [])
    row.verification_steps = rb.get("verification_steps", [])
    updated += 1
    print(f"Updated: {rb['name']}")

db.commit()
print(f"\n{updated} runbook(s) updated.")
db.close()
