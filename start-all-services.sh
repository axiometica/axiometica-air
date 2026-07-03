#!/bin/bash

# Agentic Platform - Complete Service Startup Script
# Starts all essential services in proper dependency order

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo "Agentic Platform - Service Startup Script"
echo "=========================================="
echo "Project Directory: $PROJECT_DIR"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

wait_for_health() {
    local service=$1
    local max_attempts=$2
    local attempt=0

    log_info "Waiting for $service to become healthy..."

    while [ $attempt -lt $max_attempts ]; do
        status=$(docker ps --filter "name=$service" --format "{{.Status}}" 2>/dev/null || echo "")

        if [[ $status == *"healthy"* ]]; then
            log_success "$service is healthy"
            return 0
        elif [[ $status == *"Up"* ]]; then
            echo -n "."
        fi

        ((attempt++))
        sleep 2
    done

    if [[ -n "$status" ]]; then
        log_warning "$service status: $status (may still be starting)"
        return 0
    else
        log_error "$service failed to start"
        return 1
    fi
}

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    log_error "docker-compose is not installed or not in PATH"
    exit 1
fi

# Stop any existing containers
log_info "Cleaning up any existing containers..."
docker-compose down 2>/dev/null || true
sleep 2

echo ""
echo "=========================================="
echo "LAYER 1: Core Databases"
echo "=========================================="

log_info "Starting PostgreSQL, Redis, and Neo4j..."
docker-compose up -d postgres redis neo4j

wait_for_health "agentic_os_postgres" 40
wait_for_health "agentic_os_redis" 30
wait_for_health "agentic_os_neo4j" 50

echo ""
echo "=========================================="
echo "LAYER 2: Backend Application"
echo "=========================================="

log_info "Starting Backend API..."
docker-compose up -d backend

wait_for_health "agentic_os_backend" 60

echo ""
echo "=========================================="
echo "LAYER 3: Workers, Scheduler & Queue"
echo "=========================================="

log_info "Starting Celery Worker, Celery Default Worker, Celery Beat, and Flower..."
docker-compose up -d celery_worker celery_default_worker celery_beat flower

wait_for_health "agentic_os_celery_worker" 30
wait_for_health "agentic_os_celery_default_worker" 30
wait_for_health "agentic_os_celery_beat" 15
wait_for_health "agentic_os_flower" 30

echo ""
echo "=========================================="
echo "LAYER 4: Monitoring & Infrastructure"
echo "=========================================="

log_info "Starting Sentinel and Watcher..."
docker-compose up -d sentinel watcher

wait_for_health "sentinel_senses" 30
wait_for_health "watcher_brain" 30

echo ""
echo "=========================================="
echo "LAYER 5: Frontend"
echo "=========================================="

log_info "Starting Frontend..."
docker-compose up -d frontend

wait_for_health "agentic_os_frontend" 30

# Add nginx to the mix
log_info "Starting Nginx..."
docker-compose up -d nginx

wait_for_health "agentic_os_nginx" 20

echo ""
echo "=========================================="
echo "Final Status Check"
echo "=========================================="
echo ""

# Get all service status
docker-compose ps

echo ""
echo "=========================================="
echo "Service Access Points"
echo "=========================================="
echo ""
log_info "Frontend (via Nginx): http://localhost"
log_info "Frontend (via Vite): http://localhost:3000"
log_info "Backend API: http://localhost:8000/api"
log_info "API Health: http://localhost:8000/api/health"
log_info "API Readiness: http://localhost:8000/api/ready"
log_info "Flower (Celery): http://localhost:5555"
log_info "Neo4j Browser: http://localhost:7474"

echo ""
echo "=========================================="
echo "Startup Complete!"
echo "=========================================="
echo ""

# Check API readiness
log_info "Checking API readiness..."
sleep 2

READY_STATUS=$(curl -s http://localhost:8000/api/ready 2>&1 | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
if [ "$READY_STATUS" = "healthy" ]; then
    log_success "API is HEALTHY ✓"
elif [ "$READY_STATUS" = "degraded" ]; then
    log_warning "API is DEGRADED (check Neo4j connection)"
else
    log_warning "Could not determine API status"
fi

echo ""
echo "Startup process finished. Monitor the services with:"
echo "  docker-compose logs -f"
echo ""
echo "Stop all services with:"
echo "  docker-compose down"
echo ""
