#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Axiometica AIR — Production Install / Validation Script
# ════════════════════════════════════════════════════════════════════════════
#
# Deploys or validates the platform using the production compose override:
#   docker-compose.yml  +  docker-compose.prod.yml
#
# What this does:
#   1.  Prerequisite checks (docker, .env file)
#   2.  Build all images (nginx bakes React SPA; backend bakes src)
#   3.  Start all services (frontend scaled to 0 — nginx serves SPA)
#   4.  Wait for PostgreSQL and backend to be healthy
#   5.  Apply all SQL migrations (idempotent)
#   6.  Run out-of-box seed (runbooks, policies, actions, settings)
#   7.  Seed Neo4j CMDB graph
#   8.  Validate all services and core functionality
#   9.  Print summary
#
# Usage:
#   chmod +x install-prod.sh
#   ./install-prod.sh                     # full fresh install
#   ./install-prod.sh --validate-only     # skip build/start, just validate
#   ./install-prod.sh --skip-build        # start + seed without rebuilding
#
# ════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'
RED='\033[0;31m';   BOLD='\033[1m';    NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓  $*${NC}"; }
info() { echo -e "${BLUE}  ▸  $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${NC}"; }
err()  { echo -e "${RED}  ✗  $*${NC}"; exit 1; }
hdr()  { echo -e "\n${BOLD}${BLUE}═══  $*${NC}"; }
pass() { echo -e "${GREEN}  ✔  $*${NC}"; }
fail() { echo -e "${RED}  ✘  $*${NC}"; FAILURES=$((FAILURES+1)); }

FAILURES=0

# ── auto-detect compose command ───────────────────────────────────────────────
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  err "Neither 'docker compose' nor 'docker-compose' found. Install Docker first."
fi

COMPOSE_CMD="$DC -f docker-compose.yml -f docker-compose.prod.yml"

# ── argument parsing ──────────────────────────────────────────────────────────
VALIDATE_ONLY=false
SKIP_BUILD=false

for arg in "$@"; do
  case "$arg" in
    --validate-only) VALIDATE_ONLY=true ;;
    --skip-build)    SKIP_BUILD=true    ;;
    -h|--help)
      echo ""
      echo "  Usage: $0 [--validate-only|--skip-build]"
      echo ""
      echo "  (no args)         Full production install: build → start → seed → validate"
      echo "  --validate-only   Only run health/function checks (services must already be up)"
      echo "  --skip-build      Start + seed without rebuilding images"
      echo ""
      exit 0
      ;;
  esac
done

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       Axiometica AIR — Production Install / Validate       ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
hdr "Step 1  —  Prerequisites"

command -v docker >/dev/null 2>&1 || err "Docker not found. Install Docker Engine first."
ok "Docker: $(docker --version | cut -d' ' -f3 | tr -d ',')"
ok "Compose: $($DC version --short 2>/dev/null || $DC version | head -1)"

[[ -f ".env" ]] || err ".env file not found. Copy .env.example and set your values, or run ./install.sh first to generate it."
ok ".env file found"

[[ -f "docker-compose.prod.yml" ]] || err "docker-compose.prod.yml not found"
ok "docker-compose.prod.yml found"

# Check required env vars
for var in NEO4J_PASSWORD POSTGRES_PASSWORD JWT_SECRET WATCHER_API_KEY; do
  val=$(grep "^${var}=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  if [[ -z "$val" ]]; then
    warn "${var} not set in .env — run ./install.sh to generate all secrets"
  else
    ok "${var} is set"
  fi
done

if $VALIDATE_ONLY; then
  echo ""
  info "Skipping build/start (--validate-only)"
  echo ""
  # Jump straight to validation
  goto_validate=true
else
  goto_validate=false
fi

# ── Step 1b: Create host directories with correct ownership ──────────────────
# ./backups is a bind-mount.  The container runs as appuser (UID 1001).
# If the host directory is owned by root the container cannot write, so we
# create and chown here before Docker ever sees the mount.
hdr "Step 1b —  Preparing host directories"
mkdir -p backups/postgres backups/neo4j backups/config
# chown only if we're root or have sudo; warn otherwise so the operator knows.
if [[ $EUID -eq 0 ]]; then
  chown -R 1001:1001 backups/
  ok "backups/ created and ownership set to UID 1001 (appuser)"
elif sudo -n chown -R 1001:1001 backups/ 2>/dev/null; then
  ok "backups/ created and ownership set to UID 1001 (appuser, via sudo)"
else
  warn "Could not chown backups/ to UID 1001 — run: sudo chown -R 1001:1001 backups/"
  warn "Backups will fail with [Errno 13] Permission denied until this is done."
fi

# ── Step 2: Build ─────────────────────────────────────────────────────────────
if ! $VALIDATE_ONLY; then
  hdr "Step 2  —  Building images (production)"
  info "This builds nginx with the React SPA baked in (multi-stage)..."
  info "Using: $COMPOSE_CMD"

  if $SKIP_BUILD; then
    info "Skipping image build (--skip-build)"
  else
    $COMPOSE_CMD build --parallel
    ok "All images built"
  fi

  # ── Step 3: Start services ──────────────────────────────────────────────────
  hdr "Step 3  —  Starting services"
  info "Starting in production mode (frontend scaled to 0)..."

  $COMPOSE_CMD up -d --scale frontend=0
  ok "Services started"

  # ── Step 4: Wait for PostgreSQL ─────────────────────────────────────────────
  hdr "Step 4  —  Waiting for PostgreSQL"
  RETRIES=30
  until $DC exec -T postgres pg_isready -U postgres -q 2>/dev/null; do
    RETRIES=$((RETRIES-1))
    [[ $RETRIES -le 0 ]] && err "PostgreSQL did not become ready after 30s. Check: $DC logs postgres"
    info "Waiting for PostgreSQL... ($RETRIES retries left)"
    sleep 2
  done
  ok "PostgreSQL is ready"

  # ── Step 5: Wait for backend API ────────────────────────────────────────────
  hdr "Step 5  —  Waiting for backend API"
  RETRIES=30
  until $DC exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" \
    >/dev/null 2>&1; do
    RETRIES=$((RETRIES-1))
    [[ $RETRIES -le 0 ]] && err "Backend API did not become ready. Check: $DC logs backend"
    info "Waiting for backend... ($RETRIES retries left)"
    sleep 3
  done
  ok "Backend API is ready"

  # ── Step 6: Apply SQL migrations ────────────────────────────────────────────
  hdr "Step 6  —  Applying SQL migrations"
  MIGRATION_COUNT=0
  for migration_file in $(ls -1 backend/migrations/versions/*.sql 2>/dev/null | sort); do
    info "  → $(basename "$migration_file")"
    $DC exec -T postgres psql -U postgres agentic_os \
      -f /dev/stdin < "$migration_file" 2>&1 \
      | grep -v "^$" | grep -Ev "NOTICE|already exists" | sed 's/^/      /' || true
    MIGRATION_COUNT=$((MIGRATION_COUNT+1))
  done
  ok "${MIGRATION_COUNT} migration(s) applied"

  # ── Step 7: Out-of-box seed ─────────────────────────────────────────────────
  hdr "Step 7  —  Seeding platform data"
  info "Running out-of-box setup (runbooks, policies, actions, settings)..."
  $DC exec -T backend python /app/setup_oob.py 2>&1 | grep -E "INFO|WARN|ERROR|✓|✗" | sed 's/^/  /' || true
  ok "Platform data seeded"

  # ── Step 8: Seed Neo4j CMDB ─────────────────────────────────────────────────
  hdr "Step 8  —  Seeding Neo4j CMDB"
  NEO4J_PASS=$(grep "^NEO4J_PASSWORD=" .env | cut -d= -f2- | tr -d '"' || echo "agentic_os_neo4j")
  RETRIES=15
  until $DC exec -T neo4j cypher-shell -u neo4j -p "${NEO4J_PASS}" \
    "RETURN 1;" >/dev/null 2>&1; do
    RETRIES=$((RETRIES-1))
    [[ $RETRIES -le 0 ]] && { warn "Neo4j not ready — skipping CMDB seed"; break; }
    info "Waiting for Neo4j... ($RETRIES retries left)"
    sleep 3
  done

  if [[ $RETRIES -gt 0 ]]; then
    if [[ -f "backend/scripts/neo4j_seed.cypher" ]]; then
      $DC exec -T neo4j cypher-shell -u neo4j -p "${NEO4J_PASS}" \
        < backend/scripts/neo4j_seed.cypher 2>&1 | sed 's/^/  /' || true
      ok "CMDB graph seeded"
    else
      warn "backend/scripts/neo4j_seed.cypher not found — skipping"
    fi
  fi
fi  # end: not --validate-only

# ════════════════════════════════════════════════════════════════════════════
# Validation — always runs
# ════════════════════════════════════════════════════════════════════════════
hdr "Validation  —  Service health checks"

NEO4J_PASS=$(grep "^NEO4J_PASSWORD=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "agentic_os_neo4j")
PG_PASS=$(grep "^POSTGRES_PASSWORD=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "agentic_os")

echo ""
echo "  Checking container status..."
echo ""

# 1. All containers running
for svc in postgres redis neo4j backend celery_worker watcher_brain nginx; do
  state=$($DC ps --format "{{.Status}}" "$svc" 2>/dev/null | head -1 || true)
  if echo "$state" | grep -qi "up\|running\|healthy"; then
    pass "$svc  →  $state"
  else
    fail "$svc  →  ${state:-not running}"
  fi
done

echo ""
echo "  Checking connectivity..."
echo ""

# 2. PostgreSQL reachable
if $DC exec -T postgres psql -U postgres -d agentic_os -c "SELECT 1;" >/dev/null 2>&1; then
  pass "PostgreSQL — remote auth OK"
else
  fail "PostgreSQL — remote auth FAILED"
fi

# 3. Incident sequence exists
if $DC exec -T postgres psql -U postgres -d agentic_os \
  -tc "SELECT 1 FROM information_schema.sequences WHERE sequence_name='incident_seq';" \
  2>/dev/null | grep -q 1; then
  pass "PostgreSQL — incident_seq exists"
else
  fail "PostgreSQL — incident_seq MISSING (run migrations)"
fi

# 4. Storm sequence exists
if $DC exec -T postgres psql -U postgres -d agentic_os \
  -tc "SELECT 1 FROM information_schema.sequences WHERE sequence_name='storm_seq';" \
  2>/dev/null | grep -q 1; then
  pass "PostgreSQL — storm_seq exists"
else
  fail "PostgreSQL — storm_seq MISSING (run v1_1_0 migration)"
fi

# 5. DB trigger exists
if $DC exec -T postgres psql -U postgres -d agentic_os \
  -tc "SELECT 1 FROM information_schema.triggers WHERE trigger_name='trg_workflow_human_id_insert';" \
  2>/dev/null | grep -q 1; then
  pass "PostgreSQL — INC/STRM trigger exists"
else
  fail "PostgreSQL — INC/STRM trigger MISSING (run v1_1_0 migration)"
fi

# 6. Storm columns exist
STORM_COLS=$($DC exec -T postgres psql -U postgres -d agentic_os \
  -tc "SELECT COUNT(*) FROM information_schema.columns \
       WHERE table_name='workflow_states' \
       AND column_name IN ('is_storm_parent','storm_number','storm_number_str','storm_id','storm_detected_at');" \
  2>/dev/null | tr -d ' ' || echo "0")
if [[ "$STORM_COLS" == "5" ]]; then
  pass "PostgreSQL — all storm columns exist (5/5)"
else
  fail "PostgreSQL — storm columns incomplete (${STORM_COLS}/5 found)"
fi

# 7. Neo4j reachable
if $DC exec -T neo4j cypher-shell -u neo4j -p "${NEO4J_PASS}" \
  "RETURN 1 AS ok;" >/dev/null 2>&1; then
  pass "Neo4j — auth OK"
else
  fail "Neo4j — auth FAILED"
fi

# 8. CMDB has nodes
CI_COUNT=$($DC exec -T neo4j cypher-shell -u neo4j -p "${NEO4J_PASS}" \
  "MATCH (n:ConfigurationItem) RETURN COUNT(n) AS n;" 2>/dev/null \
  | grep -o '[0-9]*' | head -1 || echo "0")
if [[ "${CI_COUNT:-0}" -gt 0 ]]; then
  pass "Neo4j CMDB — ${CI_COUNT} ConfigurationItem nodes"
else
  fail "Neo4j CMDB — 0 nodes (watcher may need a poll cycle, or seed failed)"
fi

# 9. Backend health endpoint
if $DC exec -T backend python -c \
  "import urllib.request; r=urllib.request.urlopen('http://localhost:8000/api/health'); print(r.status)" \
  2>/dev/null | grep -q "200"; then
  pass "Backend API — /api/health 200 OK"
else
  fail "Backend API — /api/health unreachable"
fi

# 10. No auth errors in logs (last 100 lines)
AUTH_ERRORS=$($DC logs backend --tail 100 2>/dev/null | grep -ci "unauthorized\|authentication.*fail\|rate.*limit" || echo 0)
if [[ "$AUTH_ERRORS" -eq 0 ]]; then
  pass "Backend logs — no auth errors"
else
  fail "Backend logs — ${AUTH_ERRORS} auth error(s) detected (check: $DC logs backend)"
fi

# 11. Neo4j connectivity from all services (check env vars)
for svc in celery_worker celery_beat watcher_brain; do
  if $DC exec -T "$svc" env 2>/dev/null | grep -q "NEO4J_PASSWORD"; then
    pass "${svc} — NEO4J_PASSWORD env var set"
  else
    fail "${svc} — NEO4J_PASSWORD NOT SET (CMDB writes will fail)"
  fi
done

# 12. Nginx serving (prod mode)
NGINX_STATUS=$($DC exec -T nginx wget -qO- --server-response \
  http://localhost/api/health 2>&1 | grep "HTTP/" | awk '{print $2}' || echo "?")
if [[ "$NGINX_STATUS" == "200" ]]; then
  pass "Nginx — proxying /api/health → backend OK"
else
  fail "Nginx — /api/health returned: ${NGINX_STATUS:-unreachable}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
if [[ $FAILURES -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}  ✅  ALL CHECKS PASSED — Production deployment is healthy${NC}"
else
  echo -e "${RED}${BOLD}  ❌  ${FAILURES} CHECK(S) FAILED — Review output above${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Print access details
NGINX_HTTP=$(grep "^ALLOWED_ORIGINS=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "https://localhost")
echo -e "  ${BOLD}Platform URL:${NC}    ${NGINX_HTTP}"
echo -e "  ${BOLD}Neo4j Browser:${NC}   http://$(hostname -I | awk '{print $1}'):7474"
echo -e "  ${BOLD}Flower:${NC}          http://$(hostname -I | awk '{print $1}'):5555"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    ${BLUE}$DC logs -f backend${NC}          # follow backend logs"
echo -e "    ${BLUE}$DC logs -f watcher_brain${NC}    # follow watcher logs"
echo -e "    ${BLUE}$DC ps${NC}                        # container status"
echo ""

[[ $FAILURES -gt 0 ]] && exit 1
exit 0
