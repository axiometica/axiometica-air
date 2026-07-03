#!/bin/bash
# ============================================================================
# Axiometica AIR - GCP Fresh Install Script with Backup & Restore
# ============================================================================
# This script performs a complete fresh installation of Axiometica AIR on GCP
# with automatic backup and restore functionality
# ============================================================================

set -e  # Exit on error

# Set this if you're migrating from a legacy install at a non-standard
# absolute path (e.g. LEGACY_INSTALL_DIR="/home/my-old-hostname"). Leave
# blank if your previous install only ever lived under $HOME.
LEGACY_INSTALL_DIR="${LEGACY_INSTALL_DIR:-}"

echo "============================================================================"
echo "Axiometica AIR - GCP Fresh Install with Backup & Restore"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ============================================================================
# STEP 1: Create Backup
# ============================================================================
info "STEP 1: Creating backup of current system..."

BACKUP_DIR="$HOME/axiometica-backups"
BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/backup_$BACKUP_TIMESTAMP"

mkdir -p "$BACKUP_DIR"

if [ -d "$LEGACY_INSTALL_DIR" ] || [ -d "$HOME/agentic-platformi-v2" ]; then
  info "Backing up current installation..."

  # Backup PostgreSQL database
  info "Backing up PostgreSQL database..."
  docker-compose -f $LEGACY_INSTALL_DIR/docker-compose.yml exec -T postgres \
    pg_dump -U postgres agentic_os > "$BACKUP_PATH/database_backup.sql" 2>/dev/null || \
    docker-compose -f $HOME/agentic-platformi-v2/docker-compose.yml exec -T postgres \
    pg_dump -U postgres agentic_os > "$BACKUP_PATH/database_backup.sql" || true

  if [ -f "$BACKUP_PATH/database_backup.sql" ]; then
    success "Database backup created: $BACKUP_PATH/database_backup.sql"
  else
    warn "Could not backup database (may not be running)"
  fi

  # Backup .env file
  if [ -f "$HOME/agentic-platformi-v2/.env" ]; then
    cp "$HOME/agentic-platformi-v2/.env" "$BACKUP_PATH/.env"
    success ".env file backed up"
  fi

  # Backup docker-compose overrides
  if [ -f "$HOME/agentic-platformi-v2/docker-compose.override.yml" ]; then
    cp "$HOME/agentic-platformi-v2/docker-compose.override.yml" "$BACKUP_PATH/"
    success "docker-compose.override.yml backed up"
  fi
else
  warn "No existing installation found to back up"
fi

echo ""

# ============================================================================
# STEP 2: Stop and Remove Containers
# ============================================================================
info "STEP 2: Stopping and removing all containers..."

# Find and remove old installations
for OLD_DIR in "$LEGACY_INSTALL_DIR" "$HOME/agentic-platformi-v2" "$HOME/agentic-platform"; do
  if [ -d "$OLD_DIR" ]; then
    info "Stopping services in $OLD_DIR..."
    cd "$OLD_DIR" && docker-compose down -v 2>/dev/null || true
    success "Containers stopped: $OLD_DIR"
  fi
done

# Remove all axiometica/agentic images
info "Removing Docker images..."
docker image rm $(docker images -q 'agentic*' 2>/dev/null) 2>/dev/null || true
docker image rm $(docker images -q 'axiometica*' 2>/dev/null) || true
success "Docker images cleaned up"

echo ""

# ============================================================================
# STEP 3: Remove Old Directories
# ============================================================================
info "STEP 3: Removing old directories..."

for OLD_DIR in "$LEGACY_INSTALL_DIR" "$HOME/agentic-platformi-v2" "$HOME/agentic-platform"; do
  if [ -d "$OLD_DIR" ]; then
    warn "Removing $OLD_DIR..."
    rm -rf "$OLD_DIR"
    success "Removed: $OLD_DIR"
  fi
done

echo ""

# ============================================================================
# STEP 4: Clone Fresh Repository
# ============================================================================
info "STEP 4: Cloning fresh Axiometica AIR repository..."

INSTALL_DIR="$HOME/axiometica-air"
mkdir -p "$HOME"

if [ -d "$INSTALL_DIR" ]; then
  warn "Directory $INSTALL_DIR already exists, removing..."
  rm -rf "$INSTALL_DIR"
fi

cd "$HOME"
git clone https://github.com/axiometica/axiometica-air.git
success "Repository cloned: $INSTALL_DIR"

cd "$INSTALL_DIR"
success "Changed to: $(pwd)"

echo ""

# ============================================================================
# STEP 5: Restore Configuration
# ============================================================================
info "STEP 5: Restoring configuration..."

if [ -f "$BACKUP_PATH/.env" ]; then
  cp "$BACKUP_PATH/.env" "$INSTALL_DIR/.env"
  success ".env restored"
else
  warn "No .env backup found. Using template..."
  # Copy example if it exists
  if [ -f "backend/.env.example" ]; then
    cp backend/.env.example .env
    info "Using .env.example as template"
  fi
fi

if [ -f "$BACKUP_PATH/docker-compose.override.yml" ]; then
  cp "$BACKUP_PATH/docker-compose.override.yml" "$INSTALL_DIR/"
  success "docker-compose.override.yml restored"
fi

echo ""

# ============================================================================
# STEP 5.5: Prepare backup directories with correct ownership
# ============================================================================
info "STEP 5.5: Preparing backup directories..."

# The backend/celery containers run as appuser (UID 1001). The git clone
# creates these dirs owned by the current user, which causes [Errno 13]
# Permission denied on the first backup run. Fix it before Docker starts.
mkdir -p "$INSTALL_DIR/backups/postgres" "$INSTALL_DIR/backups/neo4j" "$INSTALL_DIR/backups/config"
if [[ $EUID -eq 0 ]]; then
  chown -R 1001:1001 "$INSTALL_DIR/backups/"
  success "Backup directories created and ownership set to UID 1001 (appuser)"
elif sudo -n chown -R 1001:1001 "$INSTALL_DIR/backups/" 2>/dev/null; then
  success "Backup directories created and ownership set to UID 1001 (appuser, via sudo)"
else
  warn "Could not chown backup dirs to UID 1001 — run: sudo chown -R 1001:1001 $INSTALL_DIR/backups/"
  warn "Backups will fail with [Errno 13] Permission denied until this is done."
fi

echo ""

# ============================================================================
# STEP 6: Start Services
# ============================================================================
info "STEP 6: Starting Axiometica AIR services..."

cd "$INSTALL_DIR"
docker-compose up -d

info "Waiting for services to start (30 seconds)..."
sleep 30

# Check if services are running
info "Checking service status..."
docker-compose ps

echo ""

# ============================================================================
# STEP 7: Restore Database (Optional)
# ============================================================================
if [ -f "$BACKUP_PATH/database_backup.sql" ]; then
  read -p "Do you want to restore the database from backup? (y/n) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    info "Restoring database..."

    # Wait for PostgreSQL to be ready
    info "Waiting for PostgreSQL to be ready..."
    sleep 10

    # Restore the backup
    docker-compose exec -T postgres psql -U postgres agentic_os < "$BACKUP_PATH/database_backup.sql"
    success "Database restored from: $BACKUP_PATH/database_backup.sql"
  fi
fi

echo ""

# ============================================================================
# STEP 8: Verify Installation
# ============================================================================
info "STEP 8: Verifying installation..."

# Check API health
info "Checking API health..."
if curl -s http://localhost:8000/api/health > /dev/null; then
  success "API is healthy at http://localhost:8000/api/health"
else
  warn "API health check failed, it may still be starting up"
fi

# List running containers
info "Running containers:"
docker-compose ps

echo ""

# ============================================================================
# Summary
# ============================================================================
success "============================================================================"
success "Fresh Installation Complete!"
success "============================================================================"
echo ""
echo "Backup Location: $BACKUP_PATH"
echo "Installation Directory: $INSTALL_DIR"
echo ""
echo "Access URLs:"
echo "  - Frontend: http://localhost:3000"
echo "  - API: http://localhost:8000"
echo "  - API Docs: http://localhost:8000/docs"
echo "  - Celery Flower: http://localhost:5555"
echo "  - Neo4j: http://localhost:7474"
echo ""
echo "Next Steps:"
echo "  1. Access http://localhost:3000"
echo "  2. Log in with: admin@platform.local / admin"
echo "  3. Change default passwords in Settings"
echo ""
success "Installation Ready!"
echo ""
