#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Agentic Platform — comprehensive backup script
#
# Backs up everything that is NOT already covered by the postgres_backup
# sidecar container (which runs pg_dump daily inside docker-compose and keeps
# 7 daily / 4 weekly / 6 monthly copies in the postgres_backups volume):
#
#   1. Neo4j CMDB         — full graph export via APOC (online, no downtime)
#   2. Watcher config     — backend/.state/watcher_config.json
#   3. nginx TLS certs    — nginx/certs/*.pem  (skip if self-signed/ephemeral)
#   4. PostgreSQL         — optional on-demand dump (see --postgres flag)
#                           Useful for off-site uploads or pre-migration snapshots.
#
# The .env file intentionally NOT backed up here — it contains plaintext
# secrets.  Store it in a secrets manager (AWS Secrets Manager, HashiCorp
# Vault, Bitwarden, etc.) or keep an encrypted copy in a secure location.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#   ./scripts/backup.sh                         # Neo4j + config + certs
#   ./scripts/backup.sh --postgres              # also dump PostgreSQL
#   ./scripts/backup.sh --s3 s3://bucket/path/ # upload archive to S3/GCS
#   ./scripts/backup.sh --prune-days 14        # keep 14 days locally
#   ./scripts/backup.sh --skip-neo4j           # skip Neo4j (e.g. not running)
#
# ── Restore cheat-sheet ──────────────────────────────────────────────────────
#   Neo4j CMDB:
#     gunzip -c backups/neo4j/cmdb_20260101_023000.cypher.gz \
#       | docker exec -i agentic_os_neo4j cypher-shell \
#           -u neo4j -p "$NEO4J_PASSWORD" --database neo4j
#
#   Watcher config:
#     cp backups/config/watcher_config_20260101_023000.json \
#        backend/.state/watcher_config.json
#     docker compose restart watcher
#
#   PostgreSQL (if --postgres was used):
#     gunzip -c backups/postgres/agentic_os_20260101_023000.sql.gz \
#       | docker exec -i agentic_os_postgres psql \
#           -U postgres -d agentic_os
#
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load .env for passwords (never commit .env to git)
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +o allexport
fi

# ── Defaults (override via env or CLI flags) ──────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
PRUNE_DAYS="${PRUNE_DAYS:-7}"
S3_DEST="${S3_DEST:-}"
DO_POSTGRES=false
SKIP_NEO4J=false

NEO4J_CONTAINER="${NEO4J_CONTAINER:-agentic_os_neo4j}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-agentic_os_neo4j}"
NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-agentic_os_postgres}"
POSTGRES_DB="${POSTGRES_DB:-agentic_os}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-agentic_os}"

# ── Parse CLI flags ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --postgres)         DO_POSTGRES=true;      shift ;;
        --skip-neo4j)       SKIP_NEO4J=true;       shift ;;
        --prune-days)       PRUNE_DAYS="$2";       shift 2 ;;
        --s3)               S3_DEST="$2";          shift 2 ;;
        --backup-dir)       BACKUP_DIR="$2";       shift 2 ;;
        --help|-h)
            sed -n '/^# ── Usage/,/^# ──[^─]/p' "$0" | sed 's/^# //'
            exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
ERRORS=0

log()  { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"; }
warn() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] WARN: $*" >&2; }
err()  { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] ERROR: $*" >&2; ((ERRORS++)) || true; }

check_min_size() {
    local path="$1" min_bytes="${2:-1024}"
    local actual
    actual="$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path" 2>/dev/null || echo 0)"
    if [[ "$actual" -lt "$min_bytes" ]]; then
        err "$(basename "$path") is suspiciously small ($actual bytes). Possible empty export."
        return 1
    fi
}

s3_upload() {
    local src="$1" dest="$2"
    if command -v aws >/dev/null 2>&1; then
        aws s3 cp "$src" "$dest" --storage-class STANDARD_IA --only-show-errors
    elif command -v s3cmd >/dev/null 2>&1; then
        s3cmd put "$src" "$dest"
    else
        warn "S3 upload requested but neither 'aws' nor 's3cmd' found — skipping upload of $(basename "$src")"
    fi
}

# ── Directory setup ────────────────────────────────────────────────────────────
mkdir -p \
    "$BACKUP_DIR/neo4j" \
    "$BACKUP_DIR/config" \
    "$BACKUP_DIR/certs" \
    "$BACKUP_DIR/postgres"

log "════════════════════════════════════════════════════════"
log " Agentic Platform backup — $TIMESTAMP"
log "════════════════════════════════════════════════════════"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Neo4j CMDB — export full graph via APOC
# ─────────────────────────────────────────────────────────────────────────────
# Uses APOC's apoc.export.cypher.all() which is an ONLINE export (no downtime).
# The output is a Cypher script that recreates all nodes, relationships, and
# constraints.  It is written to Neo4j's import directory inside the container,
# then copied out and compressed.
#
# Prerequisites: APOC plugin must be installed (it is, per docker-compose.yml).
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_NEO4J" == "false" ]]; then
    log "── Neo4j CMDB export ────────────────────────────────────"
    NEO4J_OUT="$BACKUP_DIR/neo4j/cmdb_${TIMESTAMP}.cypher.gz"
    CONTAINER_EXPORT_PATH="/var/lib/neo4j/import/backup_${TIMESTAMP}.cypher"

    if ! docker ps --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
        warn "Container '$NEO4J_CONTAINER' is not running — skipping Neo4j backup"
    else
        # Run APOC export inside the container.
        # apoc.export.cypher.all writes a .cypher file to the import directory,
        # which is the only directory writable by the neo4j user for file ops.
        docker exec "$NEO4J_CONTAINER" \
            cypher-shell \
                -u "$NEO4J_USER" \
                -p "$NEO4J_PASSWORD" \
                --database "$NEO4J_DATABASE" \
                --format plain \
                "CALL apoc.export.cypher.all('backup_${TIMESTAMP}.cypher', {
                    format: 'cypher-shell',
                    useOptimizations: {type: 'UNWIND_BATCH', unwindBatchSize: 100}
                }) YIELD nodes, relationships, time
                RETURN nodes AS nodes_exported,
                       relationships AS rels_exported,
                       time AS duration_ms" \
            2>&1 | grep -v "^$" || {
                err "Neo4j APOC export command failed"
            }

        # Copy the exported file out of the container and compress it
        if docker exec "$NEO4J_CONTAINER" test -f "$CONTAINER_EXPORT_PATH" 2>/dev/null; then
            docker cp "${NEO4J_CONTAINER}:${CONTAINER_EXPORT_PATH}" - \
                | gzip --best > "$NEO4J_OUT"

            # Clean up the temp file inside the container
            docker exec "$NEO4J_CONTAINER" rm -f "$CONTAINER_EXPORT_PATH"

            check_min_size "$NEO4J_OUT" 512
            FILESIZE="$(du -sh "$NEO4J_OUT" | cut -f1)"
            log "Neo4j export complete: $NEO4J_OUT ($FILESIZE)"

            [[ -n "$S3_DEST" ]] && s3_upload "$NEO4J_OUT" "${S3_DEST%/}/neo4j/$(basename "$NEO4J_OUT")"
        else
            err "Neo4j export file not found in container at $CONTAINER_EXPORT_PATH"
        fi
    fi
else
    log "── Neo4j CMDB export — SKIPPED (--skip-neo4j) ──────────"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. Watcher configuration
# ─────────────────────────────────────────────────────────────────────────────
# watcher_config.json holds all watcher thresholds (CPU/memory/disk/syscall
# limits, monitored containers, cooldown windows, etc.).  It is modified at
# runtime via the API; losing it means reconfiguring the watcher from scratch.
#
# watcher_status.json is transient operational state — not backed up.
# ─────────────────────────────────────────────────────────────────────────────
log "── Watcher configuration ────────────────────────────────"
WATCHER_SRC="$PROJECT_ROOT/backend/.state/watcher_config.json"
WATCHER_OUT="$BACKUP_DIR/config/watcher_config_${TIMESTAMP}.json"

if [[ -f "$WATCHER_SRC" ]]; then
    cp "$WATCHER_SRC" "$WATCHER_OUT"
    check_min_size "$WATCHER_OUT" 10
    log "Watcher config backed up: $WATCHER_OUT"
    [[ -n "$S3_DEST" ]] && s3_upload "$WATCHER_OUT" "${S3_DEST%/}/config/$(basename "$WATCHER_OUT")"
else
    warn "Watcher config not found at $WATCHER_SRC — has the watcher run at least once?"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. nginx TLS certificates
# ─────────────────────────────────────────────────────────────────────────────
# Self-signed certs are regenerated on container start — no backup needed.
# CA-signed certs (Let's Encrypt, company CA) must be backed up because they
# cannot be regenerated without proof-of-domain; loss means TLS outage until
# the certificate is re-issued (up to hours for some CAs).
#
# The backup is skipped automatically if the cert is self-signed.
# ─────────────────────────────────────────────────────────────────────────────
log "── nginx TLS certificates ───────────────────────────────"
CERTS_DIR="$PROJECT_ROOT/nginx/certs"
CERT_FILE="$CERTS_DIR/cert.pem"

if [[ ! -f "$CERT_FILE" ]]; then
    warn "No cert.pem found in $CERTS_DIR — nginx may not have started yet; skipping"
elif openssl x509 -in "$CERT_FILE" -noout -issuer 2>/dev/null \
        | grep -qi "issuer.*CN.*=.*$HOSTNAME\|self.signed\|self-signed\|localhost"; then
    log "Certificate appears self-signed — skipping cert backup (regenerated on restart)"
else
    CERTS_OUT="$BACKUP_DIR/certs/nginx_certs_${TIMESTAMP}.tar.gz"
    tar -czf "$CERTS_OUT" -C "$CERTS_DIR" .
    check_min_size "$CERTS_OUT" 512
    log "TLS certs backed up: $CERTS_OUT"
    [[ -n "$S3_DEST" ]] && s3_upload "$CERTS_OUT" "${S3_DEST%/}/certs/$(basename "$CERTS_OUT")"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. PostgreSQL — on-demand dump (optional, --postgres flag)
# ─────────────────────────────────────────────────────────────────────────────
# The postgres_backup sidecar handles SCHEDULED daily backups automatically.
# This section is for:
#   • Pre-migration / pre-upgrade snapshots
#   • Off-site uploads that bypass the named volume
#   • Manual ad-hoc backups triggered by this script
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$DO_POSTGRES" == "true" ]]; then
    log "── PostgreSQL on-demand dump ─────────────────────────────"
    PG_OUT="$BACKUP_DIR/postgres/${POSTGRES_DB}_${TIMESTAMP}.sql.gz"

    command -v pg_dump >/dev/null 2>&1 || {
        # Fall back to running pg_dump inside the postgres container
        log "pg_dump not found locally — using docker exec fallback"
        if docker ps --format '{{.Names}}' | grep -q "^${POSTGRES_CONTAINER}$"; then
            docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$POSTGRES_CONTAINER" \
                pg_dump \
                    --username="$POSTGRES_USER" \
                    --dbname="$POSTGRES_DB" \
                    --format=plain \
                    --no-owner \
                    --no-acl \
                | gzip --best > "$PG_OUT"

            check_min_size "$PG_OUT" 10240
            FILESIZE="$(du -sh "$PG_OUT" | cut -f1)"
            log "PostgreSQL dump complete: $PG_OUT ($FILESIZE)"
            [[ -n "$S3_DEST" ]] && s3_upload "$PG_OUT" "${S3_DEST%/}/postgres/$(basename "$PG_OUT")"
        else
            err "Container '$POSTGRES_CONTAINER' not running — cannot dump PostgreSQL"
        fi
    } && {
        export PGPASSWORD="$POSTGRES_PASSWORD"
        pg_dump \
            --host=localhost \
            --port="${POSTGRES_PORT:-5432}" \
            --username="$POSTGRES_USER" \
            --dbname="$POSTGRES_DB" \
            --format=plain \
            --no-owner \
            --no-acl \
            | gzip --best > "$PG_OUT"
        unset PGPASSWORD

        check_min_size "$PG_OUT" 10240
        FILESIZE="$(du -sh "$PG_OUT" | cut -f1)"
        log "PostgreSQL dump complete: $PG_OUT ($FILESIZE)"
        [[ -n "$S3_DEST" ]] && s3_upload "$PG_OUT" "${S3_DEST%/}/postgres/$(basename "$PG_OUT")"
    }
else
    log "── PostgreSQL — handled by sidecar (use --postgres for on-demand dump)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Prune old local backups
# ─────────────────────────────────────────────────────────────────────────────
log "── Pruning backups older than ${PRUNE_DAYS} days ────────────────"
for subdir in neo4j config certs postgres; do
    find "$BACKUP_DIR/$subdir" \
        -maxdepth 1 \
        -type f \
        -mtime "+${PRUNE_DAYS}" \
        -print \
        -delete 2>/dev/null || true
done

# ─────────────────────────────────────────────────────────────────────────────
# 6. Summary
# ─────────────────────────────────────────────────────────────────────────────
log "════════════════════════════════════════════════════════"
if [[ "$ERRORS" -eq 0 ]]; then
    log " Backup complete — no errors"
else
    log " Backup finished with $ERRORS error(s) — review output above"
fi
log "════════════════════════════════════════════════════════"

[[ "$ERRORS" -eq 0 ]]   # exit non-zero if any step failed (triggers cron mail)
