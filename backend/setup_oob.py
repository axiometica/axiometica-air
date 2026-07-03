#!/usr/bin/env python3
"""
Out-of-Box Setup Script — Agentic Platform v1.2.0
==================================================

Initialises a fresh installation with all required database tables, seed data,
and service-level configuration.  Idempotent — safe to re-run on an existing
installation; nothing is overwritten unless explicitly commented as such.

Execution order
---------------
1.  Database tables          (SQLAlchemy init_db)
2.  SQL migrations           (incident enumeration sequence, new columns, settings seed)
3.  Approved remediation actions
4.  Risk-assessment weights
5.  Default governance policies
6.  Platform settings / watcher thresholds
7.  Neo4j CMDB structure + container seed data
8.  Watcher service config file
9.  Workflow definition YAML loaders (smoke-check)
10. Event-type taxonomy (210 canonical types across 9 domains)

Note: the runbook library is no longer seeded here.  It is owned entirely by
`agentic_os.db.runbooks_seed` (self-healing upsert, run automatically on every
backend startup — see main.py).  The legacy SQL files under backend/seeds/
(runbooks.sql, common_runbooks.sql) are superseded; common_runbooks.sql in
particular used to run *after* the Python seed and could overwrite corrected
runbook data with stale content, so it was removed from this flow.

Usage
-----
  # Inside Docker (recommended):
  docker exec -it agentic_os_backend python /app/setup_oob.py

  # Local venv:
  cd backend && source venv/bin/activate && python setup_oob.py
"""

import os
import sys
import logging
import subprocess
from pathlib import Path

# ── path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "versions"
SCRIPTS_DIR    = Path(__file__).parent / "scripts"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pg_exec(sql: str, db=None) -> bool:
    """Execute raw SQL against the configured PostgreSQL database."""
    try:
        if db is None:
            from agentic_os.db.database import SessionLocal
            db = SessionLocal()
            close_after = True
        else:
            close_after = False
        from sqlalchemy import text
        db.execute(text(sql))
        db.commit()
        if close_after:
            db.close()
        return True
    except Exception as exc:
        logger.error(f"SQL execution failed: {exc}")
        return False


def _run_sql_file(path: Path, label: str) -> bool:
    """Execute a .sql file via psql or SQLAlchemy."""
    if not path.exists():
        logger.warning(f"⚠️  SQL file not found, skipping: {path}")
        return True

    # Prefer psql if available (handles multi-statement files & sequences cleanly)
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:agentic_os@localhost:5432/agentic_os",
    )
    try:
        import subprocess
        result = subprocess.run(
            ["psql", db_url, "-f", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info(f"✅ {label} applied via psql")
            return True
        # psql not available or failed — fall through to SQLAlchemy
        logger.debug(f"psql returned {result.returncode}: {result.stderr[:200]}")
    except FileNotFoundError:
        pass  # psql not in PATH

    # Fallback: split on semicolons and execute each statement
    try:
        from agentic_os.db.database import SessionLocal
        from sqlalchemy import text
        sql_text = path.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql_text.split(";") if s.strip()]
        db = SessionLocal()
        for stmt in statements:
            if stmt.startswith("--") or not stmt:
                continue
            try:
                db.execute(text(stmt))
            except Exception as stmt_err:
                # Log but keep going — many statements are idempotent "IF NOT EXISTS"
                logger.debug(f"Statement skipped ({stmt_err}): {stmt[:80]}")
        db.commit()
        db.close()
        logger.info(f"✅ {label} applied via SQLAlchemy")
        return True
    except Exception as exc:
        logger.error(f"❌ Failed to apply {label}: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Database tables
# ─────────────────────────────────────────────────────────────────────────────

def setup_database() -> bool:
    logger.info("── Step 1: Initialising database tables ──")
    try:
        from agentic_os.db.database import init_db
        init_db()
        logger.info("✅ Database tables initialised")
        return True
    except Exception as exc:
        logger.error(f"❌ Database init failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — SQL migrations
# ─────────────────────────────────────────────────────────────────────────────

def run_migrations() -> bool:
    logger.info("── Step 2: Applying SQL migrations ──")

    # Known labels for well-known files; any new file uses its filename as the label.
    known_labels: dict[str, str] = {
        "add_event_condition_state.sql":       "Event condition state dedup table (qualified TTL)",
        "add_execution_mode_to_actions.sql":   "Execution mode column on approved actions",
        "add_external_check_fields.sql":       "External check container_name/service_name fields",
        "add_incident_enumeration.sql":        "Incident enumeration sequence (INC0001…)",
        "add_incident_lifecycle_v2.sql":       "Incident lifecycle v2 state machine",
        "add_ml_tables.sql":                   "ML model tables",
        "add_policy_confidence_gate.sql":      "Policy confidence gate columns (v1.2.0)",
        "add_principal_audit.sql":             "Principal audit log table",
        "add_principals.sql":                  "Principals / RBAC tables",
        "add_runbook_source.sql":              "Runbook source tracking column",
        "add_runbook_source_steps.sql":        "Runbook source steps columns",
        "add_state_history.sql":               "Workflow state history table",
        "add_storm_hold_state.sql":            "Storm hold state on workflows",
        "add_updated_at_index.sql":            "Performance index on updated_at",
        "add_workflow_state_columns.sql":      "Workflow state columns",
        "populate_action_commands.sql":        "Approved action command variants seed",
        "seed_platform_settings.sql":          "Platform / watcher settings defaults",
        "v1_1_0_schema_foundations.sql":       "v1.1.0 schema foundations",
    }

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        logger.info("ℹ️  No SQL migration files found in %s — skipping", MIGRATIONS_DIR)
        return True

    all_ok = True
    for path in sql_files:
        label = known_labels.get(path.name, path.name)
        logger.info("  → %s", path.name)
        ok = _run_sql_file(path, label)
        if not ok:
            all_ok = False
    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Approved remediation actions
# ─────────────────────────────────────────────────────────────────────────────

def seed_approved_actions() -> bool:
    logger.info("── Step 3: Seeding approved remediation actions ──")
    try:
        from agentic_os.db.approved_actions_seed import APPROVED_ACTIONS
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import ApprovedActionModel

        db = SessionLocal()
        try:
            existing = db.query(ApprovedActionModel).count()
            if existing > 0:
                logger.info(f"✅ Approved actions already seeded ({existing} found) — skipping")
                return True

            added = 0
            for action in APPROVED_ACTIONS:
                obj = ApprovedActionModel(
                    tool_name          = action["tool_name"],
                    name               = action["name"],
                    description        = action.get("description"),
                    category           = action["category"],
                    blast_radius       = action.get("blast_radius", 1),
                    requires_approval  = action.get("requires_approval", False),
                    enabled            = True,
                    command            = action.get("command"),
                    command_variants   = action.get("command_variants"),
                    parameters         = action.get("parameters", []),
                    process_rules      = action.get("process_rules"),
                )
                db.add(obj)
                added += 1

            db.commit()
            logger.info(f"✅ Approved actions seeded: {added} actions inserted")
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"❌ Approved actions seed failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Risk-assessment weights
# ─────────────────────────────────────────────────────────────────────────────

def seed_risk_weights() -> bool:
    logger.info("── Step 4: Seeding risk-assessment weights ──")
    try:
        from agentic_os.db.risk_weights_seed import seed_risk_weights as _seed
        from agentic_os.db.database import SessionLocal

        db = SessionLocal()
        try:
            _seed(db)  # seed_risk_weights requires a db_session argument
            logger.info("✅ Risk weights seeded")
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"❌ Risk weights seed failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Default governance policies
# ─────────────────────────────────────────────────────────────────────────────

def setup_default_policies() -> bool:
    logger.info("── Step 5: Creating default governance policies ──")
    try:
        from agentic_os.db.database import SessionLocal
        from agentic_os.db.models import PolicyModel

        db = SessionLocal()
        existing = db.query(PolicyModel).count()
        if existing > 0:
            logger.info(f"✅ Policies already exist ({existing} found), skipping")
            db.close()
            return True

        default_policies = [
            {
                "name": "High CPU — Auto-Restart (Low Risk)",
                "description": "Automatically restart service on sustained high CPU when blast radius is minimal.",
                "priority": 10,
                "rules": {"anomaly_types": ["high_cpu"], "min_severity": "medium"},
                "approved_actions": ["restart_service", "process_kill"],
                "requires_approval": False,
            },
            {
                "name": "High Syscall Intensity — Process Kill",
                "description": "Kill the offending process on high syscall intensity without manual approval.",
                "priority": 8,
                "rules": {"anomaly_types": ["high_syscall_intensity"], "min_severity": "medium"},
                "approved_actions": ["process_kill"],
                "requires_approval": False,
            },
            {
                "name": "Disk Full — Require CAB Approval",
                "description": "Escalate disk-full incidents to the CAB for manual remediation decision.",
                "priority": 5,
                "rules": {"anomaly_types": ["disk_full"], "min_severity": "high"},
                "approved_actions": [],
                "requires_approval": True,
            },
            {
                "name": "Service Unresponsive — CAB Restart",
                "description": "Require CAB approval before restarting an unresponsive service. Confidence gate: once the runbook reaches 90% confidence over 10+ successful runs it self-heals without approval.",
                "priority": 3,
                "rules": {"anomaly_types": ["service_unresponsive", "service_down"], "min_severity": "high"},
                "approved_actions": ["restart_service"],
                "requires_approval": True,
                "confidence_gate_threshold": 0.90,
                "confidence_gate_min_runs": 10,
            },
            {
                "name": "Critical Incident — Full Approval",
                "description": "All critical-severity incidents require manual approval regardless of type.",
                "priority": 1,
                "rules": {"min_severity": "critical"},
                "approved_actions": ["restart_service", "scale_pods", "process_kill"],
                "requires_approval": True,
            },
        ]

        for p in default_policies:
            db.add(PolicyModel(
                name=p["name"],
                description=p["description"],
                approval_priority=p["priority"],
                rules=p["rules"],
                approved_actions=p["approved_actions"],
                requires_manual_approval=p["requires_approval"],
                constraints={},
                confidence_gate_threshold=p.get("confidence_gate_threshold"),
                confidence_gate_min_runs=p.get("confidence_gate_min_runs"),
            ))

        db.commit()
        logger.info(f"✅ Created {len(default_policies)} default governance policies")
        db.close()
        return True
    except Exception as exc:
        logger.warning(f"⚠️  Default policies skipped: {exc}")
        return True  # Non-fatal


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Platform / watcher settings
# ─────────────────────────────────────────────────────────────────────────────

def seed_platform_settings() -> bool:
    """Ensure watcher threshold settings exist with defaults (idempotent).

    Belt-and-suspenders: seed_platform_settings.sql (Step 2 migration) inserts
    the same rows via raw SQL.  This Python path handles environments where psql
    is not available or the SQL migration was not run (e.g. local-mode without
    PostgreSQL on PATH).  ON CONFLICT DO NOTHING means running both is harmless.
    """
    logger.info("── Step 6: Seeding platform / watcher settings ──")
    try:
        from agentic_os.db.database import SessionLocal
        from sqlalchemy import text

        defaults = {
            "watcher.poll_interval":            ("20",    "int",   "watcher", "Poll Interval (s)",            "How often the watcher samples Docker stats (seconds)."),
            "watcher.cooldown_seconds":         ("30",    "int",   "watcher", "Incident Cooldown (s)",         "Minimum gap between incidents for the same resource (seconds)."),
            "watcher.min_consecutive_polls":    ("2",     "int",   "watcher", "Min Consecutive Polls",         "Consecutive threshold breaches before an incident is created."),
            "watcher.cpu_threshold":            ("90.0",  "float", "watcher", "CPU Alert Threshold (%)",       "CPU % above which a high_cpu event is generated."),
            "watcher.memory_threshold":         ("90.0",  "float", "watcher", "Memory Alert Threshold (%)",    "Memory % above which a high_memory event is generated."),
            "watcher.disk_threshold":           ("90.0",  "float", "watcher", "Disk Alert Threshold (%)",      "Disk % above which a disk_full event is generated."),
            "watcher.syscall_threshold":        ("9000",  "int",   "watcher", "Syscall Anomaly Threshold",     "Syscall rate (per sec) above which high_syscall_intensity fires."),
            "watcher.connection_threshold":     ("1000",  "int",   "watcher", "Network Connection Threshold",  "Open TCP connections above which a network_anomaly fires."),
            "watcher.discovery_enabled":        ("true",  "bool",  "watcher", "CMDB Discovery Enabled",        "Auto-register new containers in Neo4j CMDB."),
            "watcher.discovery_interval_polls": ("60",    "int",   "watcher", "Discovery Interval (polls)",    "Poll cycles between full container discovery scans."),
        }

        db = SessionLocal()
        inserted = 0
        for key, (value, vtype, cat, label, desc) in defaults.items():
            result = db.execute(
                text("""
                    INSERT INTO platform_settings (key, value, value_type, category, label, description, updated_at)
                    VALUES (:key, :value, :vtype, :cat, :label, :desc, NOW())
                    ON CONFLICT (key) DO NOTHING
                """),
                {"key": key, "value": value, "vtype": vtype, "cat": cat, "label": label, "desc": desc},
            )
            inserted += result.rowcount
        db.commit()
        db.close()
        logger.info(f"✅ Platform settings: {inserted} defaults inserted (existing values preserved)")
        return True
    except Exception as exc:
        logger.warning(f"⚠️  Platform settings seed skipped: {exc}")
        return True  # Non-fatal


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Neo4j CMDB
# ─────────────────────────────────────────────────────────────────────────────

def setup_neo4j() -> bool:
    logger.info("── Step 7: Initialising Neo4j CMDB ──")

    # 8a — Python init (constraints, indexes, and CMDB seed via Python path)
    try:
        from agentic_os.services.neo4j_init import seed_neo4j_database
        seed_neo4j_database()
        logger.info("✅ Neo4j schema constraints and CMDB seed applied")
    except Exception as exc:
        logger.warning(f"⚠️  Neo4j schema init skipped: {exc}")

    # 8b — Cypher seed file (container topology + DEPENDS_ON edges)
    cypher_file = SCRIPTS_DIR / "neo4j_seed.cypher"
    if cypher_file.exists():
        neo4j_host = os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_pass = os.getenv("NEO4J_PASSWORD", "agentic_os_neo4j")

        # Prefer cypher-shell if available
        try:
            result = subprocess.run(
                [
                    "cypher-shell",
                    "-a", neo4j_host,
                    "-u", neo4j_user,
                    "-p", neo4j_pass,
                    "--file", str(cypher_file),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("✅ Neo4j CMDB seeded via cypher-shell")
                return True
            logger.debug(f"cypher-shell failed: {result.stderr[:200]}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # Fall through to docker exec approach

        # Fallback: docker exec into the neo4j container
        try:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", "agentic_os_neo4j",
                    "cypher-shell", "-u", neo4j_user, "-p", neo4j_pass,
                ],
                input=cypher_file.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("✅ Neo4j CMDB seeded via docker exec")
                return True
            logger.warning(f"⚠️  Neo4j seed via docker exec failed: {result.stderr[:200]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning(f"⚠️  Neo4j seed skipped (docker not available): {exc}")
    else:
        logger.info("ℹ️  neo4j_seed.cypher not found — seeding skipped")

    # 8c — Python seed_cmdb.py fallback
    seed_script = Path(__file__).parent / "seed_cmdb.py"
    if seed_script.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(seed_script)],
                capture_output=True, text=True,
                cwd=str(Path(__file__).parent),
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("✅ Neo4j CMDB seeded via seed_cmdb.py")
                return True
            logger.warning(f"⚠️  seed_cmdb.py returned {result.returncode}: {result.stderr[:200]}")
        except Exception as exc:
            logger.warning(f"⚠️  seed_cmdb.py failed: {exc}")

    return True  # Neo4j is optional — don't fail the whole setup


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — Watcher config file
# ─────────────────────────────────────────────────────────────────────────────

def setup_watcher_config() -> bool:
    logger.info("── Step 8: Writing watcher service config file ──")
    try:
        import json
        config_dir = Path(__file__).parent / "config"
        config_dir.mkdir(exist_ok=True)

        config_file = config_dir / "watcher_config.json"
        if config_file.exists():
            logger.info("✅ watcher_config.json already exists — not overwritten")
            return True

        config = {
            "monitoring": {
                "enabled": True,
                "poll_interval": 20,
                "batch_size": 50,
                "retention_days": 7,
            },
            "thresholds": {
                "cpu_percent":       90.0,
                "memory_percent":    90.0,
                "disk_percent":      90.0,
                "syscall_rate":      9000,
                "connections":       1000,
                "cooldown_seconds":  30,
                "min_consecutive":   2,
            },
            "event_sources": [
                {"name": "prometheus", "enabled": False, "url": "http://prometheus:9090"},
                {"name": "elastic",    "enabled": False, "url": "http://elasticsearch:9200", "index": "logs-*"},
                {"name": "splunk",     "enabled": False, "url": "http://splunk:8089"},
            ],
            "qualification": {
                "enabled": True,
                "min_confidence": 25.0,
                "min_score": 50.0,
            },
            "enrichment": {
                "enabled": True,
                "cmdb_lookup": True,
                "historical_incidents": True,
                "service_dependencies": True,
            },
        }
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
        logger.info(f"✅ watcher_config.json created at {config_file}")
        return True
    except Exception as exc:
        logger.warning(f"⚠️  Watcher config creation skipped: {exc}")
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — Workflow definition YAML smoke-check
# ─────────────────────────────────────────────────────────────────────────────


def verify_workflow_definitions() -> bool:
    logger.info("── Step 9: Verifying workflow definition YAMLs ──")
    try:
        from agentic_os.core.definitions import WorkflowDefinitionLoader
        from agentic_os.core.models import WorkflowType
        loader = WorkflowDefinitionLoader()
        definitions = []
        for wf_type in (WorkflowType.INCIDENT, WorkflowType.CHANGE):
            try:
                defn = loader.load_definition(wf_type)
                if defn:
                    definitions.append(defn)
            except Exception:
                pass
        logger.info(f"✅ {len(definitions)} workflow definition(s) loaded OK")
        return True
    except Exception as exc:
        logger.warning(f"⚠️  Workflow definition check skipped: {exc}")
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 — Event-type taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def seed_event_type_taxonomy() -> bool:
    logger.info("── Step 10: Seeding event-type taxonomy ──")
    try:
        import json as _json
        from agentic_os.db.event_type_taxonomy_data import ALL_ENTRIES
        from agentic_os.db.database import SessionLocal
        from sqlalchemy import text
        from datetime import datetime, timezone

        db = SessionLocal()
        try:
            inserted = 0
            now = datetime.now(timezone.utc)
            for entry in ALL_ENTRIES:
                result = db.execute(
                    text("""
                        INSERT INTO event_type_taxonomy
                            (code, label, description, category, aliases,
                             is_system, enabled, created_at)
                        VALUES
                            (:code, :label, :description, :category, :aliases,
                             true, true, :created_at)
                        ON CONFLICT (code) DO NOTHING
                    """),
                    {
                        "code":        entry["code"],
                        "label":       entry["label"],
                        "description": entry.get("description", ""),
                        "category":    entry["category"],
                        "aliases":     _json.dumps(entry.get("aliases", [])),
                        "created_at":  now,
                    },
                )
                inserted += result.rowcount
            db.commit()
            logger.info(f"✅ Event-type taxonomy: {inserted} entries inserted (existing preserved)")
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"⚠️  Event-type taxonomy seed skipped: {exc}")
        return True  # Non-fatal


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: dict) -> bool:
    print("\n" + "=" * 62)
    print("🎉  Agentic Platform — Out-of-Box Setup Summary")
    print("=" * 62)

    steps = [
        ("Database tables",        results.get("database",  False)),
        ("SQL migrations",         results.get("migrations", False)),
        ("Approved actions",       results.get("actions",   False)),
        ("Risk weights",           results.get("risk",      False)),
        ("Governance policies",    results.get("policies",  False)),
        ("Platform settings",      results.get("settings",  False)),
        ("Neo4j CMDB",             results.get("neo4j",     False)),
        ("Watcher config file",    results.get("watcher",   False)),
        ("Workflow definitions",   results.get("workflows", False)),
        ("Event-type taxonomy",    results.get("taxonomy",  False)),
    ]

    all_ok = True
    for label, ok in steps:
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {label}")
        if not ok:
            all_ok = False

    print("\n" + "=" * 62)
    if all_ok:
        print("✨  Platform is ready!\n")
        print("  Docker Compose (recommended):")
        print("    docker compose up -d")
        print("    cd frontend && npm install && npm run dev\n")
        print("  Endpoints:")
        print("    Frontend  →  http://localhost:3000")
        print("    API       →  http://localhost:8000")
        print("    API Docs  →  http://localhost:8000/docs")
        print("    Celery    →  http://localhost:5555")
        print("    Neo4j     →  http://localhost:7474  (neo4j / agentic_os_neo4j)")
    else:
        print("⚠️   Some steps failed — review the log above for details.")
    print("=" * 62 + "\n")
    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "=" * 62)
    print("🚀  Agentic Platform v1.2.0 — Out-of-Box Setup")
    print("=" * 62 + "\n")

    results: dict = {}

    # Step 1: tables must succeed — everything else depends on it
    results["database"] = setup_database()
    if not results["database"]:
        print("\n❌  Database init failed — cannot continue.\n")
        return 1

    # Steps 2–10 are all idempotent; failures are warnings, not fatal
    results["migrations"] = run_migrations()
    results["actions"]    = seed_approved_actions()
    results["risk"]       = seed_risk_weights()
    results["policies"]   = setup_default_policies()
    results["settings"]   = seed_platform_settings()
    results["neo4j"]      = setup_neo4j()
    results["watcher"]    = setup_watcher_config()
    results["workflows"]  = verify_workflow_definitions()
    results["taxonomy"]   = seed_event_type_taxonomy()

    ok = print_summary(results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
