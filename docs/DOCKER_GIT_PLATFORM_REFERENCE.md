# Docker, Git & Platform Management Reference Guide

**Axiometica AIR v1.1.2 — Command Reference**
**Last Updated**: 2026-06-07

---

## Table of Contents

1. [Docker Compose Operations](#docker-compose-operations)
2. [Building Docker Images](#building-docker-images)
3. [Viewing Logs](#viewing-logs)
4. [Running Processes in Containers](#running-processes-in-containers)
5. [Git Operations](#git-operations)
6. [Configuration Management](#configuration-management)
7. [Diagnostics & Troubleshooting](#diagnostics--troubleshooting)
8. [Restarting Services](#restarting-services)
9. [Database Management](#database-management)
10. [Performance Monitoring](#performance-monitoring)

---

## Docker Compose Operations

### Starting Services

```bash
# Start all services in background
docker-compose up -d

# Start specific service(s)
docker-compose up -d backend
docker-compose up -d frontend
docker-compose up -d postgres redis neo4j

# Start with verbose logging
docker-compose up

# Start with specific compose file
docker-compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

### Stopping Services

```bash
# Stop all services (keeps containers)
docker-compose stop

# Stop specific service
docker-compose stop backend

# Stop all services and remove containers
docker-compose down

# Stop and remove everything including volumes (⚠️ DANGEROUS - deletes data)
docker-compose down -v
```

### Checking Service Status

```bash
# Show running containers
docker-compose ps

# Show detailed status with resource usage
docker-compose ps --all

# Check specific service status
docker ps | grep backend

# List all containers (including stopped)
docker-compose ps -a
```

### Example Output
```
NAME                COMMAND                  SERVICE      STATUS      PORTS
agentic_os-backend-1
              uvicorn main:app --host 0.0.0.0 --port 8000    backend      Up (healthy)   0.0.0.0:8000->8000/tcp
agentic_os-frontend-1
              npm run dev                      frontend     Up           0.0.0.0:3000->3000/tcp
agentic_os-postgres-1
              postgres                        postgres     Up           0.0.0.0:5432->5432/tcp
agentic_os-redis-1
              redis-server                    redis        Up           0.0.0.0:6379->6379/tcp
agentic_os-neo4j-1
              /sbin/tini -- /docker-entrypoint.sh neo4j          neo4j        Up           7474->7474/tcp, 7687->7687/tcp
```

### Restarting Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart backend
docker-compose restart postgres

# Restart with immediate stop (no grace period)
docker-compose restart -t 0

# Gracefully restart with 30-second timeout
docker-compose restart -t 30
```

---

## Building Docker Images

### Building Images Locally

```bash
# Build backend image
docker build -t agentic_os-backend:latest ./backend

# Build with specific tag
docker build -t agentic_os-backend:1.0 ./backend

# Build with build arguments
docker build \
  --build-arg PYTHON_VERSION=3.11 \
  -t agentic_os-backend:latest \
  ./backend

# Build multiple images
docker build -t agentic_os-backend:latest ./backend
docker build -t agentic_os-frontend:latest ./frontend

# Build with no cache (fresh build)
docker build --no-cache -t agentic_os-backend:latest ./backend
```

### Using Docker Compose to Build

```bash
# Build all images defined in docker-compose.yml
docker-compose build

# Build specific service image
docker-compose build backend
docker-compose build frontend

# Build with specific Dockerfile
docker-compose build --file Dockerfile.prod

# Build and immediately start containers
docker-compose up --build

# Build specific service without cache
docker-compose build --no-cache backend
```

### Listing Built Images

```bash
# List all images
docker images

# List images by name
docker images agentic_os*

# Show image details (size, layers)
docker images --digests
docker images --format "{{.Repository}}\t{{.Tag}}\t{{.Size}}"

# Remove specific image
docker rmi agentic_os-backend:old-tag

# Remove all agentic_os images
docker rmi $(docker images -q agentic_os*)
```

### Example Docker Build Output
```
Sending build context to Docker daemon  45.23 MB
Step 1/15 : FROM python:3.11-slim
 ---> a1b2c3d4e5f6
Step 2/15 : WORKDIR /app/src
 ---> Using cache
 ---> a1b2c3d4e5f7
Step 3/15 : COPY backend/requirements.txt /tmp/requirements.txt
 ---> Using cache
 ---> a1b2c3d4e5f8
Step 4/15 : RUN pip install --no-cache-dir -r /tmp/requirements.txt
 ---> Running in a1b2c3d4e5f9
Successfully installed fastapi-0.95.1 sqlalchemy-2.0 ...
 ---> a1b2c3d4e5fa
Successfully built agentic_os-backend:latest
Successfully tagged agentic_os-backend:latest
```

---

## Viewing Logs

### Docker Compose Logs

```bash
# View logs from all services
docker-compose logs

# View logs from specific service
docker-compose logs backend
docker-compose logs frontend
docker-compose logs postgres

# Follow logs (streaming)
docker-compose logs -f

# Follow specific service logs
docker-compose logs -f backend

# Show last N lines
docker-compose logs --tail 50 backend

# Show logs from last 10 minutes
docker-compose logs --since 10m backend

# Show logs since specific time
docker-compose logs --since 2026-05-14T10:30:00 backend

# Combine options
docker-compose logs -f --tail 100 backend

# View logs with timestamps
docker-compose logs --timestamps backend
```

### Docker Engine Logs

```bash
# View Docker daemon logs
docker logs <container-id-or-name>

# View logs from backend container
docker logs agentic_os-backend-1

# Follow specific container logs
docker logs -f agentic_os-backend-1

# Show last 50 lines
docker logs --tail 50 agentic_os-backend-1

# View system logs (host-dependent)
# On Linux:
sudo journalctl -u docker -f

# On macOS:
log stream --predicate 'process == "Docker"' --level=debug
```

### Log Output Examples

**Backend Service Startup**:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
Started server process [1]
Waiting for application startup.
Application startup complete
```

**Database Connection**:
```
PostgreSQL 14.7 on x86_64-pc-linux-gnu
database system is ready to accept connections
Ready to accept connections
```

**Error Example**:
```
ERROR:root:Database connection failed: Could not connect to PostgreSQL at localhost:5432
Traceback (most recent call last):
  File "main.py", line 45, in initialize_db
psycopg2.OperationalError: could not connect to server: Connection refused
```

### Grep Logs for Errors

```bash
# Search logs for errors
docker-compose logs backend | grep -i error

# Find all ERROR level logs
docker-compose logs backend | grep "ERROR:"

# Find specific exception
docker-compose logs backend | grep "RuntimeError"

# View context around error (3 lines before/after)
docker-compose logs backend | grep -i error -A 3 -B 3

# Count errors
docker-compose logs backend | grep -i error | wc -l

# Export logs to file
docker-compose logs backend > backend_logs.txt
```

---

## Running Processes in Containers

### Executing Commands in Running Containers

```bash
# Execute command in backend container
docker-compose exec backend python -c "print('hello')"

# Interactive shell in container
docker-compose exec -it backend bash
docker-compose exec -it backend sh

# Execute as specific user
docker-compose exec -u postgres postgres psql -U postgres

# Run Python script in backend
docker-compose exec backend python script.py

# Run migrations
docker-compose exec backend python -m alembic upgrade head

# Install package in running container
docker-compose exec backend pip install new-package
```

### Database Operations in Container

```bash
# Connect to PostgreSQL interactive session
docker-compose exec postgres psql -U postgres -d agentic_os

# Run SQL query
docker-compose exec postgres psql -U postgres -d agentic_os -c "SELECT COUNT(*) FROM workflow_states;"

# Dump database
docker-compose exec postgres pg_dump -U postgres agentic_os > backup.sql

# Restore database
docker-compose exec -T postgres psql -U postgres agentic_os < backup.sql

# List databases
docker-compose exec postgres psql -U postgres -l

# Connect to specific database
docker-compose exec postgres psql -U postgres agentic_os
```

### Redis Operations in Container

```bash
# Connect to Redis CLI
docker-compose exec redis redis-cli

# View all keys in Redis
docker-compose exec redis redis-cli KEYS "*"

# Get value
docker-compose exec redis redis-cli GET key_name

# Delete all data (⚠️ DANGEROUS)
docker-compose exec redis redis-cli FLUSHALL

# Monitor Redis commands in real-time
docker-compose exec redis redis-cli MONITOR
```

### Neo4j Operations in Container

```bash
# Connect to Neo4j Cypher shell
docker-compose exec neo4j cypher-shell

# Execute Cypher query
docker-compose exec neo4j cypher-shell -u neo4j -p password "MATCH (n) RETURN COUNT(n) as count"

# Run backup
docker-compose exec neo4j neo4j-admin dump --database neo4j --to /backup/neo4j.dump

# Load CMDB seed data
docker-compose exec neo4j cypher-shell -u neo4j -p password < cmdb_seed.cypher
```

### Interactive Container Usage

```bash
# Start Python REPL in backend
docker-compose exec -it backend python
# Then: >>> import sys; print(sys.path)

# Check installed packages
docker-compose exec backend pip list

# View environment variables
docker-compose exec backend env

# Check container resource usage (from host)
docker stats agentic_os-backend-1
```

---

## Git Operations

### Repository Setup

```bash
# Clone repository
git clone https://github.com/bsmike1/axiometica-air.git
cd axiometica-air

# Initialize local repository
git init

# Add remote origin
git remote add origin https://github.com/bsmike1/axiometica-air.git

# Verify remote
git remote -v
# Shows: origin	https://github.com/... (fetch)
#        origin	https://github.com/... (push)
```

### Branching

```bash
# List local branches
git branch

# List all branches (local + remote)
git branch -a

# Create new branch
git branch feature/incident-redesign

# Create and switch to new branch
git checkout -b feature/incident-redesign
# Or (newer syntax):
git switch -c feature/incident-redesign

# Switch to existing branch
git checkout feature/incident-redesign
git switch feature/incident-redesign

# Delete local branch
git branch -d feature/incident-redesign

# Force delete branch
git branch -D feature/incident-redesign

# Rename branch
git branch -m old-name new-name

# Push new branch to remote
git push -u origin feature/incident-redesign
```

### Commits

```bash
# Check current status
git status

# Show detailed changes
git diff

# Stage changes
git add .
git add src/components/IncidentList.tsx
git add -p  # Interactive staging (patch mode)

# Unstage changes
git reset HEAD filename

# Commit changes
git commit -m "Add Phase 2 incident list redesign"

# Commit with detailed message
git commit -m "Add incident list card grid redesign

- Transform table view to responsive card grid
- Add lifecycle, severity, service filters
- Implement pagination (10/20/50 items)
- Support dark mode styling
- Add loading and empty states"

# Amend previous commit (add more changes)
git commit --amend --no-edit

# View commit history
git log

# Show one-line commit history
git log --oneline

# Show commits with statistics
git log --stat

# Show commits since date
git log --since="2026-05-01"
```

### Viewing Changes

```bash
# View changes in working directory
git diff

# View staged changes
git diff --cached

# View changes between branches
git diff main feature/incident-redesign

# View specific file changes
git diff src/components/IncidentList.tsx

# View commits on current branch not in main
git log main..HEAD

# Show diff for specific commit
git show abc123def456

# Show commit details
git log -1 -p
```

### Syncing with Remote

```bash
# Fetch updates from remote (doesn't merge)
git fetch origin

# Pull updates (fetch + merge)
git pull origin

# Pull from specific branch
git pull origin main

# Push commits to remote
git push origin feature/incident-redesign

# Push all branches
git push origin --all

# Delete remote branch
git push origin --delete feature/incident-redesign

# Force push (⚠️ DANGEROUS - can lose history)
git push --force-with-lease origin feature/incident-redesign
```

### Merging

```bash
# Merge feature branch into current branch
git merge feature/incident-redesign

# Merge main into feature branch
git merge main

# Abort merge if conflicts
git merge --abort

# View merge conflicts
git status

# Resolve conflicts manually, then:
git add .
git commit -m "Resolve merge conflicts"
```

### Rebasing

```bash
# Rebase current branch on main
git rebase main

# Interactive rebase (squash/edit/reorder commits)
git rebase -i main

# Abort rebase if needed
git rebase --abort

# Continue rebase after resolving conflicts
git rebase --continue
```

### Stashing Changes

```bash
# Stash uncommitted changes
git stash

# Stash with description
git stash save "WIP: incident list redesign"

# List stashes
git stash list

# Apply latest stash
git stash pop

# Apply specific stash
git stash apply stash@{0}

# Delete stash
git stash drop stash@{0}
```

---

## Configuration Management

### Environment Variables

```bash
# View current environment variables
env | grep AGENTI
printenv | grep DATABASE

# Set environment variable for current session
export DATABASE_URL="postgresql://user:pass@localhost/db"
export OPENAI_API_KEY="sk-..."
export NEO4J_PASSWORD="password123"

# Set permanently (add to .bashrc or .zshrc)
echo 'export DATABASE_URL="postgresql://..."' >> ~/.bashrc
source ~/.bashrc

# View specific variable
echo $DATABASE_URL
```

### .env File Management

```bash
# Create .env file
cat > .env << EOF
DATABASE_URL=postgresql://postgres:password@localhost:5432/agentic_os
REDIS_URL=redis://localhost:6379
NEO4J_URL=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
OPENAI_API_KEY=sk-...
BACKEND_PORT=8000
FRONTEND_PORT=3000
EOF

# Load .env file (if using docker-compose)
docker-compose --env-file .env up -d

# Check .env is being loaded
docker-compose config | grep DATABASE_URL
```

### Backend Configuration

```bash
# View backend config
docker-compose exec backend env | sort

# Update backend environment variable
export BACKEND_ENV=production
docker-compose restart backend

# Check Python environment
docker-compose exec backend python -c "import os; print(os.environ.get('DATABASE_URL'))"
```

### Frontend Configuration

```bash
# View frontend build config
cat frontend/.env.production

# Update API endpoint for frontend
export VITE_API_BASE_URL=http://localhost:8000
npm run build

# Check frontend config at runtime
docker-compose exec frontend cat .env.local
```

### Docker Compose Override

```bash
# Create docker-compose.override.yml for local development
cat > docker-compose.override.yml << EOF
version: '3.8'
services:
  backend:
    environment:
      - DEBUG=True
      - LOG_LEVEL=DEBUG
    volumes:
      - ./backend/src:/app/src
  frontend:
    environment:
      - VITE_DEBUG=True
EOF

# Compose automatically uses .override.yml if present
docker-compose config
```

---

## Diagnostics & Troubleshooting

### Health Checks

```bash
# Check backend health
curl http://localhost:8000/api/health

# Check frontend availability
curl http://localhost:3000

# Detailed health check with response time
time curl -w "\nStatus: %{http_code}\n" http://localhost:8000/api/health

# Database connectivity check
docker-compose exec postgres pg_isready -h localhost -p 5432

# Redis connectivity check
docker-compose exec redis redis-cli ping
# Expected response: PONG

# Neo4j connectivity check
curl -u neo4j:password http://localhost:7474/db/neo4j/summary
```

### Container Diagnostics

```bash
# View container resource usage
docker stats agentic_os-backend-1

# View detailed container information
docker inspect agentic_os-backend-1

# Check container network
docker inspect agentic_os-backend-1 | grep -A 10 "Networks"

# View container processes
docker top agentic_os-backend-1

# Check container file system
docker exec agentic_os-backend-1 df -h

# View container network ports
docker port agentic_os-backend-1
```

### Network Diagnostics

```bash
# Check network connectivity from container
docker-compose exec backend ping postgres
docker-compose exec backend curl -I http://postgres:5432

# Trace network route
docker-compose exec backend traceroute postgres

# Check DNS resolution
docker-compose exec backend nslookup postgres
docker-compose exec backend getent hosts postgres

# List network interfaces
docker-compose exec backend ip addr show
```

### File System Diagnostics

```bash
# Check disk usage in container
docker-compose exec backend du -sh /app

# List files in /app
docker-compose exec backend ls -la /app

# Check Python modules installed
docker-compose exec backend pip list

# Check Python environment
docker-compose exec backend python -c "import sys; print('\n'.join(sys.path))"

# View running processes
docker-compose exec backend ps aux
```

### Common Issues & Solutions

**Issue**: Backend won't start
```bash
# Check logs
docker-compose logs backend

# Check if port is in use
lsof -i :8000

# Rebuild image
docker-compose build --no-cache backend
docker-compose up backend

# Check database connection
docker-compose exec backend python -c "import sqlalchemy; print('SQLAlchemy OK')"
```

**Issue**: Database won't start
```bash
# Check PostgreSQL logs
docker-compose logs postgres

# Reset PostgreSQL data
docker-compose down -v postgres
docker-compose up -d postgres

# Check database exists
docker-compose exec postgres psql -U postgres -l | grep agentic_os
```

**Issue**: Frontend not connecting to backend
```bash
# Check frontend logs
docker-compose logs frontend

# Check API endpoint in frontend
docker-compose exec frontend env | grep API

# Test backend from frontend container
docker-compose exec frontend curl http://backend:8000/api/health

# Check CORS headers
curl -H "Origin: http://localhost:3000" -v http://localhost:8000/api/health
```

---

## Restarting Services

### Restart Strategies

```bash
# Restart all services cleanly
docker-compose restart

# Restart specific service
docker-compose restart backend

# Restart with down/up (cleans up more thoroughly)
docker-compose down
docker-compose up -d

# Restart backend only, keep database
docker-compose restart backend
docker-compose restart frontend

# Restart with health check verification
docker-compose up -d --wait

# Restart and rebuild
docker-compose up -d --build
```

### Graceful Shutdown

```bash
# Stop services gracefully (30-second timeout)
docker-compose stop -t 30

# More forceful stop (10-second timeout)
docker-compose stop -t 10

# Immediate stop
docker-compose stop -t 0

# Stop and remove (keep volumes)
docker-compose down

# Stop and remove everything
docker-compose down -v
```

### Recovery Procedures

```bash
# Full reset (remove all, rebuild, restart)
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d

# Reset database only
docker-compose exec postgres dropdb -U postgres agentic_os || true
docker-compose exec postgres createdb -U postgres agentic_os
docker-compose exec backend alembic upgrade head

# Reset cache only
docker-compose exec redis redis-cli FLUSHALL

# Reset Neo4j only
docker-compose exec neo4j rm -rf /data/databases/neo4j
docker-compose restart neo4j
```

---

## Database Management

### PostgreSQL Operations

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U postgres agentic_os

# Backup database
docker-compose exec postgres pg_dump -U postgres agentic_os > backup.sql

# Restore database
docker-compose exec postgres psql -U postgres agentic_os < backup.sql

# List tables
docker-compose exec postgres psql -U postgres agentic_os -c "\dt"

# View table structure
docker-compose exec postgres psql -U postgres agentic_os -c "\d workflow_states"

# Count records
docker-compose exec postgres psql -U postgres agentic_os -c "SELECT COUNT(*) FROM workflow_states;"

# Run SQL file
docker-compose exec postgres psql -U postgres agentic_os -f schema.sql

# View recent incidents
docker-compose exec postgres psql -U postgres agentic_os -c \
  "SELECT workflow_id, workflow_type, lifecycle_state, created_at FROM workflow_states ORDER BY created_at DESC LIMIT 10;"
```

### Database Migrations

```bash
# Check current migration version
docker-compose exec backend alembic current

# View available migrations
docker-compose exec backend alembic history

# Upgrade to latest migration
docker-compose exec backend alembic upgrade head

# Downgrade one migration
docker-compose exec backend alembic downgrade -1

# Create new migration
docker-compose exec backend alembic revision -m "Add new field to workflow_states"

# Show migration details
docker-compose exec backend alembic show rev_id
```

---

## Performance Monitoring

### Container Resource Usage

```bash
# Real-time resource usage
docker stats

# Resource usage for specific container
docker stats agentic_os-backend-1 --no-stream

# Show total usage
docker stats --no-stream --all

# Export metrics
docker stats --no-stream --all > container_metrics.txt
```

### Application Performance

```bash
# Check backend response times
time curl http://localhost:8000/api/health

# Load test backend
# Install: npm install -g autocannon
autocannon -c 10 -d 10 http://localhost:8000/api/workflows?limit=20

# Benchmark database queries
docker-compose exec backend time python benchmark_queries.py

# View slow queries
docker-compose exec postgres psql -U postgres agentic_os -c \
  "SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"
```

### Memory Usage

```bash
# Check container memory usage
docker stats agentic_os-backend-1 --format "{{.MemUsage}}"

# Check host memory
free -h
docker info | grep "Memory"

# Monitor memory over time
watch -n 1 'docker stats --no-stream agentic_os-backend-1'
```

### Disk Usage

```bash
# Container disk usage
docker exec agentic_os-backend-1 du -sh /app

# PostgreSQL data size
docker-compose exec postgres du -sh /var/lib/postgresql/data

# Docker volumes
docker volume ls
docker volume inspect <volume-name>

# Cleanup unused volumes
docker volume prune

# Total Docker disk usage
docker system df
```

---

## Quick Reference Commands

### Most Used Commands

```bash
# Start everything
docker-compose up -d

# Check status
docker-compose ps

# View backend logs
docker-compose logs -f backend

# Stop everything
docker-compose down

# Restart backend
docker-compose restart backend

# Connect to database
docker-compose exec postgres psql -U postgres agentic_os

# Check health
curl http://localhost:8000/api/health

# Git status
git status

# Git commit
git commit -am "message"

# Git push
git push origin branch-name

# Git pull
git pull origin main
```

### Useful Aliases

Add to ~/.bashrc or ~/.zshrc:

```bash
# Docker Compose shortcuts
alias dc='docker-compose'
alias dcup='docker-compose up -d'
alias dcdown='docker-compose down'
alias dclogs='docker-compose logs -f'
alias dcps='docker-compose ps'

# Backend shortcuts
alias backend-logs='docker-compose logs -f backend'
alias backend-shell='docker-compose exec -it backend bash'
alias backend-health='curl http://localhost:8000/api/health'

# Database shortcuts
alias db-shell='docker-compose exec postgres psql -U postgres agentic_os'
alias db-count='docker-compose exec postgres psql -U postgres agentic_os -c "SELECT COUNT(*) FROM workflow_states;"'

# Git shortcuts
alias gs='git status'
alias gc='git commit -m'
alias gp='git push'
alias gl='git log --oneline -10'
```

---

## Troubleshooting Checklist

- [ ] Backend running: `curl http://localhost:8000/api/health`
- [ ] Frontend running: `curl http://localhost:3000`
- [ ] Database connected: `docker-compose logs postgres | grep ready`
- [ ] Redis running: `docker-compose exec redis redis-cli ping`
- [ ] Neo4j running: `curl http://localhost:7474`
- [ ] No port conflicts: `lsof -i :8000` (should be empty or show docker)
- [ ] Disk space available: `df -h`
- [ ] Memory available: `free -h`
- [ ] Network connectivity: `docker-compose exec backend ping postgres`
- [ ] All containers healthy: `docker-compose ps` (shows "Up (healthy)")

---

**Document Version**: 1.0  
**Created**: 2026-05-14  
**Purpose**: Comprehensive reference for Docker, Git, and platform management  
**Status**: Ready for Use
