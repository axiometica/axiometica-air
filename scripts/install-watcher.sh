#!/usr/bin/env bash
# =============================================================================
# Agentic Platform — Standalone Watcher Install Script
#
# Installs the watcher as a systemd service on a Linux VM (bare metal,
# cloud EC2/Azure/GCP, or VMware guest).  Does NOT require Docker.
#
# Usage:
#   curl -fsSL https://your-platform/install-watcher.sh | \
#     WATCHER_API_URL=https://your-platform.example.com \
#     WATCHER_API_KEY=<key> \
#     WATCHER_NAME=watcher_prod_web01 \
#     bash
#
# Or download and run manually:
#   chmod +x install-watcher.sh
#   ./install-watcher.sh
#
# Required environment variables:
#   WATCHER_API_URL   — HTTPS URL of the Agentic Platform NGINX endpoint
#   WATCHER_API_KEY   — API key (get from Admin → Users → Watcher Bot)
#   WATCHER_NAME      — unique name for this watcher instance (no spaces)
#
# Optional:
#   WATCHER_ADAPTER   — force adapter: docker | ssh | kubernetes | aws_ssm
#   WATCHER_REPO_URL  — git repo URL (default: GitHub)
#   WATCHER_BRANCH    — git branch  (default: main)
#   WATCHER_USER      — system user to run the service (default: watcher)
#   INSTALL_DIR       — install directory (default: /opt/agentic-watcher)
#   SENTINEL_CONTAINER — set to empty string to disable eBPF (default: empty)
# =============================================================================
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
WATCHER_NAME="${WATCHER_NAME:-watcher_vm_$(hostname -s)}"
WATCHER_ADAPTER="${WATCHER_ADAPTER:-}"
WATCHER_REPO_URL="${WATCHER_REPO_URL:-https://github.com/axiometica/axiometica-air.git}"
WATCHER_BRANCH="${WATCHER_BRANCH:-main}"
WATCHER_USER="${WATCHER_USER:-watcher}"
INSTALL_DIR="${INSTALL_DIR:-/opt/agentic-watcher}"
SENTINEL_CONTAINER="${SENTINEL_CONTAINER:-}"   # empty = no eBPF
PYTHON="${PYTHON:-python3}"
SERVICE_NAME="agentic-watcher"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "Run as root: sudo bash install-watcher.sh"
[[ -n "${WATCHER_API_URL:-}" ]] || error "WATCHER_API_URL is required"
[[ -n "${WATCHER_API_KEY:-}" ]] || error "WATCHER_API_KEY is required"

info "Installing Agentic Watcher '${WATCHER_NAME}' → ${WATCHER_API_URL}"

# ── Detect OS ─────────────────────────────────────────────────────────────────
. /etc/os-release 2>/dev/null || true
OS_ID="${ID:-linux}"
info "OS: ${PRETTY_NAME:-Linux}"

# ── Install Python 3.11+ ──────────────────────────────────────────────────────
install_python() {
    if command -v python3.11 &>/dev/null; then
        PYTHON="python3.11"; return
    fi
    if command -v python3 &>/dev/null && python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
        PYTHON="python3"; return
    fi
    warn "Python 3.11+ not found — installing..."
    case "$OS_ID" in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq python3.11 python3.11-venv python3.11-dev git
            PYTHON="python3.11"
            ;;
        rhel|centos|rocky|almalinux|fedora|amzn)
            if command -v dnf &>/dev/null; then
                dnf install -y -q python3.11 python3.11-devel git
            else
                yum install -y -q python311 python311-devel git
            fi
            PYTHON="python3.11"
            ;;
        *)
            error "Unsupported OS '${OS_ID}'. Install Python 3.11 manually then re-run."
            ;;
    esac
}

install_git() {
    command -v git &>/dev/null && return
    case "$OS_ID" in
        ubuntu|debian) apt-get install -y -qq git ;;
        *) yum install -y -q git || dnf install -y -q git ;;
    esac
}

install_python
install_git
info "Python: $($PYTHON --version)"

# ── Create system user ────────────────────────────────────────────────────────
if ! id "$WATCHER_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /sbin/nologin "$WATCHER_USER"
    info "Created system user: ${WATCHER_USER}"
fi

# ── Clone / update watcher code ───────────────────────────────────────────────
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "Updating existing install at ${INSTALL_DIR}"
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$WATCHER_BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$WATCHER_BRANCH"
else
    info "Cloning watcher code to ${INSTALL_DIR}"
    git clone --branch "$WATCHER_BRANCH" --depth 1 "$WATCHER_REPO_URL" "$INSTALL_DIR"
fi

# ── Create Python virtualenv and install deps ─────────────────────────────────
VENV="${INSTALL_DIR}/.venv"
if [[ ! -d "$VENV" ]]; then
    $PYTHON -m venv "$VENV"
fi
info "Installing Python dependencies..."
"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet -r "${INSTALL_DIR}/backend/requirements.txt"

# ── Persistent state directory ────────────────────────────────────────────────
STATE_DIR="${INSTALL_DIR}/backend/.state"
mkdir -p "$STATE_DIR"
chown -R "${WATCHER_USER}:${WATCHER_USER}" "$STATE_DIR"

# Seed empty state files if they don't exist
[[ -f "${STATE_DIR}/watcher_config.json" ]] || cat > "${STATE_DIR}/watcher_config.json" <<'EOF'
{
  "poll_interval": 20,
  "cooldown_seconds": 60,
  "syscall_threshold": 5000,
  "cpu_threshold": 80.0,
  "memory_threshold": 85.0,
  "disk_threshold": 90.0,
  "connection_threshold": 1000,
  "min_consecutive_polls": 3,
  "discovery_enabled": false,
  "discovery_interval_polls": 15
}
EOF

[[ -f "${STATE_DIR}/watcher_status.json" ]] || cat > "${STATE_DIR}/watcher_status.json" <<'EOF'
{"state":"initialising","active_conditions":{},"active_workflow_ids":{}}
EOF

# ── Environment file ──────────────────────────────────────────────────────────
ENV_FILE="/etc/default/${SERVICE_NAME}"
info "Writing environment to ${ENV_FILE}"
cat > "$ENV_FILE" <<EOF
# Agentic Platform Watcher — environment variables
# Edit this file to change configuration; then: systemctl restart ${SERVICE_NAME}

# ── Required ──────────────────────────────────────────────────────────────────
WATCHER_NAME=${WATCHER_NAME}
WATCHER_API_URL=${WATCHER_API_URL}
WATCHER_API_KEY=${WATCHER_API_KEY}

# ── Adapter (auto-detected if empty) ─────────────────────────────────────────
WATCHER_ADAPTER=${WATCHER_ADAPTER}

# SSH adapter (set if monitoring remote VMs)
# WATCHER_SSH_HOST=
# WATCHER_SSH_USER=root
# WATCHER_SSH_KEY_PATH=/home/${WATCHER_USER}/.ssh/id_rsa

# AWS SSM adapter
# WATCHER_SSM_INSTANCE_IDS=i-0123456789abcdef0,i-abcdef0123456789
# AWS_REGION=us-east-1

# ── Platform endpoints ────────────────────────────────────────────────────────
WATCHER_NGINX_URL=${WATCHER_API_URL}
WATCHER_KILL_API_URL=http://$(hostname -I | awk '{print $1}'):8080

# ── Monitoring thresholds ─────────────────────────────────────────────────────
WATCHER_POLL_INTERVAL=20
WATCHER_COOLDOWN_SECONDS=60
WATCHER_CPU_THRESHOLD=80.0
WATCHER_MEMORY_THRESHOLD=85.0
WATCHER_DISK_THRESHOLD=90.0
WATCHER_ANOMALY_THRESHOLD=5000
WATCHER_MIN_CONSECUTIVE_POLLS=3

# ── Sentinel (eBPF) — empty = disabled ───────────────────────────────────────
SENTINEL_CONTAINER=${SENTINEL_CONTAINER}

# ── Python ────────────────────────────────────────────────────────────────────
PYTHONUNBUFFERED=1
PYTHONPATH=${INSTALL_DIR}/backend/src
EOF
chmod 600 "$ENV_FILE"

# ── systemd unit ─────────────────────────────────────────────────────────────
info "Writing systemd unit"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Agentic Platform Watcher (${WATCHER_NAME})
Documentation=https://github.com/axiometica/axiometica-air
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${WATCHER_USER}
Group=${WATCHER_USER}
WorkingDirectory=${INSTALL_DIR}/backend
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python watcher_main.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ReadWritePaths=${STATE_DIR}

[Install]
WantedBy=multi-user.target
EOF

# ── Enable and start ──────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# ── Verify ────────────────────────────────────────────────────────────────────
sleep 3
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    info "✅ Watcher '${WATCHER_NAME}' is running"
    echo ""
    echo "  Status:  systemctl status ${SERVICE_NAME}"
    echo "  Logs:    journalctl -u ${SERVICE_NAME} -f"
    echo "  Config:  ${ENV_FILE}"
    echo ""
    info "The watcher will appear as PENDING in the platform's"
    info "Monitoring Setup page. Approve it to start receiving events."
else
    error "Watcher failed to start. Check: journalctl -u ${SERVICE_NAME} -n 50"
fi
