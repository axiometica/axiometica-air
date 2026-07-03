"""Background Celery task for platform data backup.

Covers:
  1. PostgreSQL  — pg_dump piped to gzip (postgresql-client is in the image)
  2. Neo4j CMDB  — plain-Cypher export via bolt driver (no APOC plugin needed)
  3. Watcher config — backend/.state/watcher_config.json (read-only mount)

Results are written to /app/backups/{postgres,neo4j,config}/ inside the
celery_worker container, which is bind-mounted to ./backups on the host.

Status is persisted in platform_settings so the Admin panel can surface
last-run time and outcome without polling.
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from agentic_os.tasks.celery_app import app

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
BACKUP_ROOT = Path(os.getenv("BACKUP_DIR", "/app/backups"))
WATCHER_CONFIG_SRC = Path(os.getenv("WATCHER_CONFIG_PATH", "/app/.state/watcher_config.json"))

# ── Helpers ────────────────────────────────────────────────────────────────────

def _set_status(db, status: str, message: str = "") -> None:
    """Write backup status + timestamp into platform_settings."""
    from agentic_os.db.models import PlatformSettingModel

    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {
        "general.last_backup_status":  status,
        "general.last_backup_message": message[:500],  # cap length
    }
    if status in ("ok", "error"):
        updates["general.last_backup_at"] = now_iso

    for key, value in updates.items():
        row = db.get(PlatformSettingModel, key)
        if row is None:
            row = PlatformSettingModel(
                key=key,
                category="general",
                value_type="str",
                label=key.split(".")[-1].replace("_", " ").title(),
                description="",
            )
            db.add(row)
        row.value = value
    db.commit()


def _prune(directory: Path, retention_days: int) -> None:
    """Delete files older than retention_days in directory."""
    cutoff = time.time() - retention_days * 86_400
    pruned = 0
    for p in directory.iterdir():
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink()
            pruned += 1
    if pruned:
        logger.info("Pruned %d files older than %d days from %s", pruned, retention_days, directory)


# ── Sub-tasks ──────────────────────────────────────────────────────────────────

def _backup_postgres(dest_dir: Path, ts: str) -> str:
    """Run pg_dump and compress the output.  Returns path of created file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"agentic_os_{ts}.sql.gz"

    db_url = os.getenv("DATABASE_URL", "")
    # Parse postgresql://user:pass@host:port/dbname
    # pg_dump accepts the full URL via --dbname=
    env = {**os.environ, "PGPASSWORD": ""}

    # Extract password separately so it doesn't appear in the process list
    if db_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            env["PGPASSWORD"] = parsed.password or ""
            pg_host = parsed.hostname or "postgres"
            pg_port = str(parsed.port or 5432)
            pg_user = parsed.username or "postgres"
            pg_db   = (parsed.path or "/agentic_os").lstrip("/")
        except Exception:
            pg_host, pg_port, pg_user, pg_db = "postgres", "5432", "postgres", "agentic_os"
    else:
        pg_host, pg_port, pg_user, pg_db = "postgres", "5432", "postgres", "agentic_os"

    cmd = [
        "pg_dump",
        f"--host={pg_host}",
        f"--port={pg_port}",
        f"--username={pg_user}",
        f"--dbname={pg_db}",
        "--format=plain",
        "--no-owner",
        "--no-acl",
    ]

    with gzip.open(out_path, "wb", compresslevel=9) as gz_file:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=300,
        )
        if result.returncode != 0:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump failed: {result.stderr.decode()[:400]}")
        gz_file.write(result.stdout)

    size_kb = out_path.stat().st_size // 1024
    if size_kb < 5:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump output suspiciously small ({size_kb} KB) — possible empty export")

    logger.info("PostgreSQL backup: %s (%d KB)", out_path.name, size_kb)
    return str(out_path)


def _cypher_literal(value) -> str:
    """Render a Python value as a Cypher literal (property values only —
    Neo4j properties are scalars or lists of scalars, never nested maps)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_cypher_literal(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _cypher_props(props: dict) -> str:
    if not props:
        return "{}"
    return "{" + ", ".join(f"{k}: {_cypher_literal(v)}" for k, v in props.items()) + "}"


def _backup_neo4j(dest_dir: Path, ts: str) -> str:
    """Export full Neo4j graph as plain Cypher CREATE statements.

    Built from MATCH queries over the bolt driver rather than
    apoc.export.cypher.all — the APOC plugin's only use in this codebase was
    this export, and loading it adds real weight (jar extraction + procedure
    scanning) to every Neo4j container startup for a feature that runs once a
    day. Nodes get a temporary `_backup_idx` property so relationships can be
    rewired by index on restore; the property (and its backing index) is
    stripped at the end of the generated script.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"cmdb_{ts}.cypher.gz"

    uri      = os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687")
    user     = os.getenv("NEO4J_USER",     "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise RuntimeError("neo4j Python driver not installed — cannot back up Neo4j")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database="neo4j") as session:
            nodes = list(session.run(
                "MATCH (n) RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props"
            ))
            rels = list(session.run(
                "MATCH (a)-[r]->(b) RETURN id(a) AS a, type(r) AS type, properties(r) AS props, id(b) AS b"
            ))
    finally:
        driver.close()

    if not nodes:
        logger.warning("Neo4j export found no nodes — graph may be empty")

    statements: list[str] = [
        "CREATE INDEX backup_idx_tmp IF NOT EXISTS FOR (n:`__BackupTmp__`) ON (n._backup_idx);",
    ]
    for rec in nodes:
        labels = ":".join(rec["labels"]) if rec["labels"] else ""
        props = dict(rec["props"] or {})
        props["_backup_idx"] = rec["id"]
        label_clause = f":{labels}:`__BackupTmp__`" if labels else ":`__BackupTmp__`"
        statements.append(f"CREATE (n{label_clause} {_cypher_props(props)});")
    for rec in rels:
        props = _cypher_props(dict(rec["props"] or {}))
        statements.append(
            f"MATCH (a {{_backup_idx: {rec['a']}}}), (b {{_backup_idx: {rec['b']}}}) "
            f"CREATE (a)-[:{rec['type']} {props}]->(b);"
        )
    statements.append("DROP INDEX backup_idx_tmp IF EXISTS;")
    statements.append("MATCH (n) WHERE n._backup_idx IS NOT NULL REMOVE n._backup_idx, n:`__BackupTmp__`;")

    combined = "\n".join(statements)
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as gz_file:
        gz_file.write(combined)

    size_kb = out_path.stat().st_size // 1024
    logger.info("Neo4j backup: %s (%d KB, %d chars uncompressed)", out_path.name, size_kb, len(combined))
    return str(out_path)


def _backup_watcher_config(dest_dir: Path, ts: str) -> str | None:
    """Copy watcher_config.json if the file is mounted.  Returns path or None."""
    if not WATCHER_CONFIG_SRC.exists():
        logger.warning("Watcher config not found at %s — skipping", WATCHER_CONFIG_SRC)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"watcher_config_{ts}.json"
    shutil.copy2(WATCHER_CONFIG_SRC, out_path)
    logger.info("Watcher config backup: %s", out_path.name)
    return str(out_path)


# ── Celery task ────────────────────────────────────────────────────────────────

@app.task(
    name="backup.run",
    bind=True,
    max_retries=0,              # Don't retry backups automatically
    time_limit=1800,            # Hard limit: 30 minutes
    soft_time_limit=1500,       # Soft limit: 25 minutes
    queue="default",
)
def run_backup_task(self, retention_days: int = 7) -> dict:
    """Run a full platform backup.

    Writes files to /app/backups/ and prunes old ones.
    Updates general.last_backup_at / general.last_backup_status in the DB.
    """
    from agentic_os.db.database import SessionLocal

    db = SessionLocal()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results: dict[str, str | None] = {}
    errors: list[str] = []

    try:
        _set_status(db, "in_progress", "Backup started")

        # ── 1. PostgreSQL ──────────────────────────────────────────────────────
        try:
            results["postgres"] = _backup_postgres(BACKUP_ROOT / "postgres", ts)
        except Exception as exc:
            logger.error("PostgreSQL backup failed: %s", exc)
            errors.append(f"PostgreSQL: {exc}")
            results["postgres"] = None

        # ── 2. Neo4j CMDB ──────────────────────────────────────────────────────
        try:
            results["neo4j"] = _backup_neo4j(BACKUP_ROOT / "neo4j", ts)
        except Exception as exc:
            logger.error("Neo4j backup failed: %s", exc)
            errors.append(f"Neo4j: {exc}")
            results["neo4j"] = None

        # ── 3. Watcher config ──────────────────────────────────────────────────
        try:
            results["watcher_config"] = _backup_watcher_config(BACKUP_ROOT / "config", ts)
        except Exception as exc:
            logger.error("Watcher config backup failed: %s", exc)
            errors.append(f"Watcher config: {exc}")
            results["watcher_config"] = None

        # ── 4. Prune old backups ───────────────────────────────────────────────
        for subdir in ("postgres", "neo4j", "config"):
            d = BACKUP_ROOT / subdir
            if d.exists():
                try:
                    _prune(d, retention_days)
                except Exception as exc:
                    logger.warning("Prune failed for %s: %s", subdir, exc)

        # ── 5. Persist status ──────────────────────────────────────────────────
        if errors:
            msg = "; ".join(errors)
            _set_status(db, "error", msg)
            logger.error("Backup completed with errors: %s", msg)
        else:
            _set_status(db, "ok", "All stores backed up successfully")
            logger.info("Backup completed successfully")

        return {
            "status": "error" if errors else "ok",
            "timestamp": ts,
            "errors": errors,
            "files": results,
        }

    except Exception as exc:
        _set_status(db, "error", str(exc)[:500])
        logger.exception("Backup task failed unexpectedly: %s", exc)
        raise
    finally:
        db.close()
