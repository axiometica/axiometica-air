#!/usr/bin/env bash
# ============================================================
# Axiometica AIR — Installation Script  (Linux / macOS)
# ============================================================
# Version: v1.2.0
#
# Installs the platform in Docker Compose mode (recommended)
# or local venv mode (advanced).
#
# Usage:
#   chmod +x install.sh
#   ./install.sh              # Docker mode (default, recommended)
#   ./install.sh --docker     # Docker mode (explicit)
#   ./install.sh --local      # Local venv mode (advanced)
#   ./install.sh --help       # Show this help
# ============================================================

set -euo pipefail

# ── colours ─────────────────────────────────────────────────
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'
RED='\033[0;31m';   BOLD='\033[1m';    NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓  $*${NC}"; }
info() { echo -e "${BLUE}  ▸  $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${NC}"; }
err()  { echo -e "${RED}  ✗  $*${NC}"; }
hdr()  { echo -e "\n${BOLD}${BLUE}═══  $*${NC}"; }

# ── detect this host's reachable address (for the "Open in your
#    browser" summary) — tries the GCP metadata server first, then
#    falls back to the primary local IP. Empty string if neither works.
_detect_server_ip() {
  local ip
  ip=$(curl -sf -m 2 -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" 2>/dev/null || true)
  if [ -z "$ip" ]; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  echo "$ip"
}

# ── auto-detect docker compose command ───────────────────────
# Supports both the modern plugin (docker compose) and the
# legacy standalone binary (docker-compose) used on many Linux servers.
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  DC="docker compose"   # will fail later with a clear Docker error
fi

# ── parse arguments ──────────────────────────────────────────
MODE="docker"
for arg in "$@"; do
  case "$arg" in
    --docker) MODE="docker" ;;
    --local)  MODE="local"  ;;
    -h|--help)
      echo ""
      echo "  Usage: $0 [--docker|--local]"
      echo ""
      echo "  --docker   Docker Compose mode (default, recommended)"
      echo "             Requires: Docker Desktop 4.x+"
      echo ""
      echo "  --local    Local venv + npm mode (advanced)"
      echo "             Requires: Python 3.11+, Node.js 18+, PostgreSQL 15+, Redis 7+"
      echo ""
      exit 0
      ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║          Agentic Platform  v1.0.0  —  Installer             ║${NC}"
echo -e "${BOLD}║                    (${MODE} mode)                            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ════════════════════════════════════════════════════════════
# DOCKER MODE  (default, recommended)
# ════════════════════════════════════════════════════════════

install_docker_mode() {

  # ── prerequisites ────────────────────────────────────────
  hdr "Step 1 of 10  —  Checking prerequisites"

  if ! command -v docker >/dev/null 2>&1; then
    err "Docker not found."
    echo ""
    echo "  Please install Docker Desktop from:"
    echo "    https://www.docker.com/products/docker-desktop/"
    echo ""
    echo "  After installing, start Docker Desktop and re-run this script."
    exit 1
  fi
  ok "Docker: $(docker --version | cut -d' ' -f3 | tr -d ',')"

  if ! $DC version >/dev/null 2>&1; then
    err "Docker Compose v2 not found. Please update Docker Desktop to version 4.x or later."
    exit 1
  fi
  ok "Docker Compose: $($DC version --short)"

  if ! docker info >/dev/null 2>&1; then
    err "Docker is not running. Please start Docker Desktop and try again."
    exit 1
  fi
  ok "Docker daemon is running"

  command -v curl >/dev/null 2>&1 && ok "curl available" || warn "curl not found — health-check waits may be skipped"

  # ── configuration wizard ─────────────────────────────────
  hdr "Step 2 of 9  —  Configuration"

  if [ -f ".env" ]; then
    ok ".env already exists — skipping (delete it to re-run setup)"
  else
    echo ""
    echo -e "  ${BOLD}Quick configuration — press ENTER to accept defaults.${NC}"
    echo -e "  ${YELLOW}  All security tokens are auto-generated; you only need to${NC}"
    echo -e "  ${YELLOW}  supply API keys if you want AI-powered summaries.${NC}"
    echo ""

    # ── generate secure random tokens ──────────────────────
    _gen_secret() {
      openssl rand -hex 32 2>/dev/null || \
        python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
        date +%s | sha256sum | cut -c1-64
    }
    JWT_SECRET=$(_gen_secret)
    WATCHER_KEY=$(_gen_secret)
    PG_PASS=$(_gen_secret | cut -c1-16)
    REDIS_PASS=$(_gen_secret | cut -c1-16)
    NEO4J_PASS=$(_gen_secret | cut -c1-16)
    # Fernet key = 32 random bytes, urlsafe-base64-encoded — generated the same
    # way Fernet.generate_key() does, so it's valid without needing the
    # `cryptography` package installed on the host running this script.
    SECRET_ENCRYPTION_KEY=$(python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null || \
      openssl rand -base64 32 2>/dev/null | tr '+/' '-_' )

    # ── LLM API keys (optional) ────────────────────────────
    read -rp "  OpenAI API key (sk-…)     [leave blank to skip]: " OPENAI_KEY
    OPENAI_KEY="${OPENAI_KEY:-}"

    read -rp "  Anthropic API key (sk-ant-…) [leave blank to skip]: " ANTHROPIC_KEY
    ANTHROPIC_KEY="${ANTHROPIC_KEY:-}"

    # ── Admin account password ──────────────────────────────
    echo ""
    read -rsp "  Admin password [Admin@1234!]: " ADMIN_PASS
    echo ""
    ADMIN_PASS="${ADMIN_PASS:-Admin@1234!}"

    # ── Flower (task monitor) password ─────────────────────
    echo ""
    read -rsp "  Flower monitoring password [changeme]: " FLOWER_PASS
    echo ""
    FLOWER_PASS="${FLOWER_PASS:-changeme}"

    cat > .env << ENVEOF
# ── Agentic Platform  v1.0.0  —  Environment Configuration ──
# Generated by install.sh on $(date -u '+%Y-%m-%d %H:%M UTC')
#
# Edit this file to change passwords, API keys, or thresholds.
# Re-run install.sh only if you need to reset to defaults.

# ── LLM Providers (optional — enables AI-generated summaries) ──
OPENAI_API_KEY=${OPENAI_KEY}
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}

# ── Security (auto-generated — do not share) ─────────────────
JWT_SECRET=${JWT_SECRET}
JWT_EXPIRY_HOURS=8
WATCHER_API_KEY=${WATCHER_KEY}
# Encrypts connector/Slack/SMTP credentials and LLM API keys at rest.
# BACK THIS UP somewhere other than this file (e.g. a password manager) —
# if it's lost, every encrypted secret becomes permanently unrecoverable.
SECRET_ENCRYPTION_KEY=${SECRET_ENCRYPTION_KEY}
ENVIRONMENT=production

# ── Admin account ──────────────────────────────────────────────
ADMIN_EMAIL=admin@platform.local
ADMIN_INITIAL_PASSWORD=${ADMIN_PASS}

# ── Database credentials (auto-generated) ────────────────────
POSTGRES_USER=postgres
POSTGRES_DB=agentic_os
POSTGRES_PASSWORD=${PG_PASS}

# ── Service Credentials ───────────────────────────────────────
NEO4J_BOLT_URL=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=${NEO4J_PASS}

FLOWER_USER=admin
FLOWER_PASSWORD=${FLOWER_PASS}

# ── Redis credentials (auto-generated) ───────────────────────
REDIS_PASSWORD=${REDIS_PASS}

# ── Platform ──────────────────────────────────────────────────
DEBUG=false
LOG_LEVEL=INFO
ALLOWED_ORIGINS=http://localhost:3000,https://localhost
ENVEOF
    ok ".env created with auto-generated security tokens"
  fi

  # ── read back admin credentials for the final summary (covers
  #    both the "just created" and "pre-existing .env" code paths) ──
  ADMIN_EMAIL_SHOW=$(grep -m1 '^ADMIN_EMAIL=' .env 2>/dev/null | cut -d= -f2-)
  ADMIN_PASS_SHOW=$(grep -m1 '^ADMIN_INITIAL_PASSWORD=' .env 2>/dev/null | cut -d= -f2-)
  ADMIN_EMAIL_SHOW="${ADMIN_EMAIL_SHOW:-admin@platform.local}"
  ADMIN_PASS_SHOW="${ADMIN_PASS_SHOW:-Admin@1234!}"

  # ── build images ─────────────────────────────────────────
  hdr "Step 3 of 9  —  Building Docker images"
  info "Building all service images — this takes 3–5 minutes on first run…"
  $DC build --parallel
  ok "All images built"

  # ── start services ────────────────────────────────────────
  hdr "Step 4 of 9  —  Starting services"
  info "Starting all platform services in the background…"
  $DC up -d
  ok "Services started"

  # ── wait for PostgreSQL ───────────────────────────────────
  hdr "Step 5 of 9  —  Waiting for PostgreSQL"
  info "Waiting for the database to accept connections…"
  for i in $(seq 1 60); do
    if $DC exec -T postgres pg_isready -U postgres -q 2>/dev/null; then
      ok "PostgreSQL ready (took ${i}s)"
      break
    fi
    if [ "$i" -eq 60 ]; then
      err "PostgreSQL did not become ready after 60 seconds."
      echo "  Check logs with:  $DC logs postgres"
      exit 1
    fi
    sleep 1
  done

  # ── wait for backend API ──────────────────────────────────
  hdr "Step 6 of 9  —  Waiting for backend API"
  info "Waiting for the API to start (compiling on first run)…"
  for i in $(seq 1 120); do
    if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
      ok "Backend API ready (took ${i}s)"
      break
    fi
    if [ "$i" -eq 120 ]; then
      err "Backend API did not start after 120 seconds."
      echo "  Check logs with:  $DC logs backend"
      exit 1
    fi
    sleep 1
  done

  # ── database migrations ───────────────────────────────────
  hdr "Step 7 of 9  —  Applying database migrations"
  info "Applying schema migrations…"
  MIGRATION_COUNT=0
  for migration_file in $(ls -1 backend/migrations/versions/*.sql 2>/dev/null | sort); do
    info "  → $(basename "$migration_file")"
    $DC exec -T postgres psql -U postgres agentic_os \
      -f /dev/stdin < "$migration_file" 2>&1 \
      | grep -v "^$" | grep -v "NOTICE" | sed 's/^/      /' || true
    MIGRATION_COUNT=$((MIGRATION_COUNT + 1))
  done
  ok "${MIGRATION_COUNT} migration(s) applied"

  # ── validate and create missing schema objects ───────────
  hdr "Step 7a of 9  —  Validating database schema"
  info "Checking for required sequences and columns…"

  # Check and create incident_seq
  if ! $DC exec -T postgres psql -U postgres agentic_os \
    -tc "SELECT 1 FROM information_schema.sequences WHERE sequence_name='incident_seq';" 2>/dev/null | grep -q 1; then
    info "  → Creating incident_seq sequence…"
    $DC exec -T postgres psql -U postgres -d agentic_os \
      -c "CREATE SEQUENCE incident_seq START 1;" 2>&1 | sed 's/^/      /' || true
  else
    ok "  incident_seq already exists"
  fi

  # Check and create storm_id column
  if ! $DC exec -T postgres psql -U postgres agentic_os \
    -tc "SELECT column_name FROM information_schema.columns WHERE table_name='workflow_states' AND column_name='storm_id';" 2>/dev/null | grep -q storm_id; then
    info "  → Creating storm_id and storm_detected_at columns…"
    $DC exec -T postgres psql -U postgres -d agentic_os << 'SQL_BLOCK' 2>&1 | sed 's/^/      /' || true
ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_id UUID REFERENCES workflow_states(workflow_id) ON DELETE SET NULL;
ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS storm_detected_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_workflow_states_storm_id ON workflow_states(storm_id);
SQL_BLOCK
  else
    ok "  storm columns already exist"
  fi

  # Check and create incident_number_str column
  if ! $DC exec -T postgres psql -U postgres agentic_os \
    -tc "SELECT column_name FROM information_schema.columns WHERE table_name='workflow_states' AND column_name='incident_number_str';" 2>/dev/null | grep -q incident_number_str; then
    info "  → Creating incident_number_str column…"
    $DC exec -T postgres psql -U postgres -d agentic_os \
      -c "ALTER TABLE workflow_states ADD COLUMN IF NOT EXISTS incident_number_str VARCHAR(20);" 2>&1 | sed 's/^/      /' || true
    $DC exec -T postgres psql -U postgres -d agentic_os \
      -c "CREATE INDEX IF NOT EXISTS idx_workflow_states_incident_number ON workflow_states(incident_number_str) WHERE incident_number_str IS NOT NULL;" 2>&1 | sed 's/^/      /' || true
  else
    ok "  incident_number_str already exists"
  fi

  ok "Schema validation complete"

  # ── seed database ─────────────────────────────────────────
  # ── seed database ─────────────────────────────────────────
  hdr "Step 8 of 10  —  Seeding platform data"
  info "Running out-of-box setup (runbooks, policies, settings, approved actions)…"
  $DC exec -T backend python /app/setup_oob.py
  ok "Platform data seeded"

  # ── seed Neo4j CMDB ───────────────────────────────────────
  hdr "Step 10 of 10  —  Initialising CMDB graph"
  info "Waiting for Neo4j graph database…"
  for i in $(seq 1 45); do
    if $DC exec -T neo4j cypher-shell -u neo4j -p agentic_os_neo4j "RETURN 1;" >/dev/null 2>&1; then
      ok "Neo4j ready (took ${i}s)"
      break
    fi
    if [ "$i" -eq 45 ]; then
      warn "Neo4j did not respond after 45s — CMDB graph seed skipped"
      warn "Run manually later:  $DC exec neo4j cypher-shell …"
      print_docker_summary; return
    fi
    sleep 1
  done

  if $DC exec -T neo4j cypher-shell -u neo4j -p agentic_os_neo4j "RETURN 1;" >/dev/null 2>&1; then
    if [ -f "backend/scripts/neo4j_seed.cypher" ]; then
      info "Seeding CMDB service topology graph…"
      $DC exec -T neo4j cypher-shell -u neo4j -p agentic_os_neo4j \
        < backend/scripts/neo4j_seed.cypher 2>&1 \
        | grep -v "^$" | sed 's/^/    /' || true
      ok "CMDB graph seeded"
    fi
  fi

  print_docker_summary
}


# ════════════════════════════════════════════════════════════
# LOCAL MODE  (venv + npm — advanced)
# ════════════════════════════════════════════════════════════

install_local_mode() {

  hdr "Step 1 of 7  —  Checking prerequisites"
  command -v python3 >/dev/null 2>&1 || { err "Python 3.11+ required.  https://www.python.org/downloads/"; exit 1; }
  ok "Python: $(python3 --version)"
  command -v node >/dev/null 2>&1 || { err "Node.js 18+ required.  https://nodejs.org/"; exit 1; }
  ok "Node.js: $(node --version)"
  command -v npm >/dev/null 2>&1 || { err "npm not found."; exit 1; }
  ok "npm: $(npm --version)"
  command -v psql >/dev/null 2>&1 || { err "PostgreSQL client (psql) required.  https://www.postgresql.org/download/"; exit 1; }
  ok "PostgreSQL: $(psql --version)"

  hdr "Step 2 of 7  —  Python virtual environment"
  cd backend
  if [ ! -d "venv" ]; then
    info "Creating virtual environment…"
    python3 -m venv venv
  fi
  source venv/bin/activate
  info "Installing Python dependencies…"
  pip install --upgrade pip -q
  pip install -r requirements.txt -q
  ok "Backend dependencies installed"

  if [ ! -f ".env" ]; then
    _gen_secret() {
      openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))"
    }
    _gen_fernet_key() {
      python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null || \
        openssl rand -base64 32 2>/dev/null | tr '+/' '-_'
    }
    cat > .env << ENVEOF
DATABASE_URL=postgresql://postgres:agentic_os@localhost:5432/agentic_os
SQL_ECHO=false
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
NEO4J_BOLT_URL=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=$(_gen_secret | cut -c1-16)
JWT_SECRET=$(_gen_secret)
WATCHER_API_KEY=$(_gen_secret)
# Encrypts connector/Slack/SMTP credentials and LLM API keys at rest.
# BACK THIS UP somewhere other than this file — if it's lost, every
# encrypted secret becomes permanently unrecoverable.
SECRET_ENCRYPTION_KEY=$(_gen_fernet_key)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEBUG=false
LOG_LEVEL=INFO
ENVEOF
    ok "backend/.env created"
  fi
  cd ..

  hdr "Step 3 of 7  —  Frontend"
  cd frontend
  info "Installing Node.js dependencies…"
  npm install -q
  if [ ! -f ".env.local" ]; then
    printf 'VITE_API_BASE_URL=http://localhost:8000\nVITE_WS_BASE_URL=ws://localhost:8000\n' > .env.local
    ok ".env.local created"
  fi
  cd ..

  hdr "Step 4 of 7  —  PostgreSQL database"
  DBEXISTS=$(psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='agentic_os';" 2>/dev/null | tr -d '[:space:]' || echo "")
  if [ "$DBEXISTS" != "1" ]; then
    info "Creating database agentic_os…"
    psql -U postgres -c "CREATE DATABASE agentic_os OWNER postgres;"
    psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'agentic_os';"
    ok "Database created"
  else
    ok "Database already exists"
  fi

  hdr "Step 5 of 7  —  Database tables"
  cd backend
  source venv/bin/activate
  info "Initialising schema…"
  python -c "from agentic_os.db.database import init_db; init_db()"
  ok "Tables initialised"

  hdr "Step 6 of 7  —  SQL migrations"
  for migration in $(ls -1 migrations/versions/*.sql 2>/dev/null | sort); do
    info "  → $(basename "$migration")"
    psql "postgresql://postgres:agentic_os@localhost:5432/agentic_os" \
      -f "$migration" 2>&1 | grep -v "^$" | sed 's/^/      /' || true
  done
  ok "Migrations applied"

  hdr "Step 7 of 7  —  Platform seed data"
  info "Running out-of-box setup…"
  python setup_oob.py
  cd ..

  print_local_summary
}


# ════════════════════════════════════════════════════════════
# Summary printers
# ════════════════════════════════════════════════════════════

print_docker_summary() {
  local server_ip
  server_ip=$(_detect_server_ip)

  echo ""
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}${BOLD}║              Installation Complete!                          ║${NC}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BOLD}Running services:${NC}"
  $DC ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null | sed 's/^/    /'
  echo ""
  echo -e "  ${BOLD}Open in your browser:${NC}"
  echo -e "    ${GREEN}Frontend    →  http://localhost:3000${NC}      (main application)"
  echo    "    API Docs    →  http://localhost:8000/docs  (interactive API reference)"
  echo    "    Task Queue  →  http://localhost:5555       (Celery Flower monitor)"
  echo    "    CMDB Graph  →  http://localhost:7474       (Neo4j Browser)"
  echo    "    Frontend (HTTPS)  →  https://localhost      (if a reverse proxy/TLS is configured)"
  if [ -n "$server_ip" ] && [ "$server_ip" != "127.0.0.1" ]; then
    echo ""
    echo -e "  ${BOLD}Or, from another machine (this server's address):${NC}"
    echo -e "    ${GREEN}Frontend    →  http://${server_ip}:3000${NC}"
    echo    "    API Docs    →  http://${server_ip}:8000/docs"
    echo    "    Task Queue  →  http://${server_ip}:5555"
    echo    "    CMDB Graph  →  http://${server_ip}:7474"
  fi
  echo ""
  echo -e "  ${BOLD}Default login:${NC}  ${ADMIN_EMAIL_SHOW:-admin@platform.local} / ${ADMIN_PASS_SHOW:-Admin@1234!}  (change after first login)"
  echo ""
  echo -e "  ${BOLD}Useful commands:${NC}"
  echo    "    $DC logs -f backend         # view backend logs"
  echo    "    $DC logs -f celery_worker   # view agent pipeline logs"
  echo    "    $DC restart celery_worker   # reload after config changes"
  echo    "    $DC down                    # stop all services"
  echo    "    $DC down -v                 # stop and delete all data"
  echo ""
  echo -e "  ${BOLD}Documentation:${NC}  README.md  ·  docs/QUICKSTART.md  ·  INSTALL.md"
  echo ""
}

print_local_summary() {
  echo ""
  echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}${BOLD}║        Installation Complete!  (local mode)                 ║${NC}"
  echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "  ${BOLD}Start in three terminals:${NC}"
  echo ""
  echo    "    Terminal 1 — Backend API:"
  echo    "      cd backend && source venv/bin/activate"
  echo    "      uvicorn agentic_os.main:app --reload --host 0.0.0.0 --port 8000"
  echo ""
  echo    "    Terminal 2 — Agent worker:"
  echo    "      cd backend && source venv/bin/activate"
  echo    "      celery -A agentic_os.tasks.celery_app worker --loglevel=info"
  echo ""
  echo    "    Terminal 3 — Frontend:"
  echo    "      cd frontend && npm run dev"
  echo ""
  echo -e "  ${BOLD}Open in your browser:${NC}  http://localhost:3000"
  echo ""
}


# ════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════

case "$MODE" in
  docker) install_docker_mode ;;
  local)  install_local_mode  ;;
esac
