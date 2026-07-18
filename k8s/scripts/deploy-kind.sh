#!/usr/bin/env bash
# Deploy AgenticPlatform to a local KinD cluster (Linux / macOS).
#
# Usage:
#   ./deploy-kind.sh                        # full deploy
#   ./deploy-kind.sh --skip-build           # skip image build + load
#   ./deploy-kind.sh --skip-migrations      # skip Alembic + seed
#   ./deploy-kind.sh --skip-build --skip-migrations
#   ./deploy-kind.sh --namespace my-ns      # custom namespace (default: agentic-platform)

set -euo pipefail

SKIP_BUILD=false
SKIP_MIGRATIONS=false
NS="agentic-platform"

for arg in "$@"; do
  case $arg in
    --skip-build)       SKIP_BUILD=true ;;
    --skip-migrations)  SKIP_MIGRATIONS=true ;;
    --namespace=*)      NS="${arg#*=}" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(dirname "$SCRIPT_DIR")"
BASE="$K8S_DIR/base"
OVERLAY="$K8S_DIR/overlays/kind"
REPO="$(dirname "$K8S_DIR")"

# ── Colour helpers ────────────────────────────────────────────────────────────
info() { echo -e "\033[36m==> $*\033[0m"; }
ok()   { echo -e "\033[32m  OK  $*\033[0m"; }
warn() { echo -e "\033[33m WARN $*\033[0m"; }
fail() { echo -e "\033[31m FAIL $*\033[0m"; exit 1; }

wait_pod_ready() {
  local label=$1 timeout=${2:-180}
  info "Waiting for pod: $label (${timeout}s)"
  local deadline=$(( $(date +%s) + timeout ))
  while (( $(date +%s) < deadline )); do
    if kubectl wait pod -l "$label" -n "$NS" --for=condition=ready --timeout=10s &>/dev/null; then
      ok "$label ready"; return
    fi
    sleep 5
  done
  echo "Pod state at timeout:"
  kubectl get pod -l "$label" -n "$NS"
  kubectl describe pod -l "$label" -n "$NS" | grep -E "State:|Reason:|Warning|Error" || true
  fail "Timed out waiting for $label"
}

# ── 0. Preflight ──────────────────────────────────────────────────────────────
info "Preflight checks"
command -v kubectl &>/dev/null || fail "kubectl not found in PATH"
command -v docker  &>/dev/null || fail "docker not found in PATH"

# Install kind if missing
if ! command -v kind &>/dev/null; then
  info "kind not found - installing"
  OS=$(uname -s | tr '[:upper:]' '[:lower:]')
  ARCH=$(uname -m)
  [[ $ARCH == "x86_64" ]] && ARCH="amd64"
  [[ $ARCH == "aarch64" || $ARCH == "arm64" ]] && ARCH="arm64"
  KIND_URL="https://kind.sigs.k8s.io/dl/latest/kind-${OS}-${ARCH}"
  curl -fsSL "$KIND_URL" -o /tmp/kind
  chmod +x /tmp/kind
  sudo mv /tmp/kind /usr/local/bin/kind
  ok "kind installed to /usr/local/bin/kind"
fi

# Create the cluster if it doesn't exist
if ! kind get clusters 2>/dev/null | grep -q "^desktop$"; then
  info "KinD cluster 'desktop' not found - creating it now"
  kind create cluster --config "$K8S_DIR/kind-config.yaml" --wait 120s
  ok "Cluster 'desktop' created"
else
  ok "Cluster 'desktop' already exists"
fi

kubectl config use-context kind-desktop &>/dev/null
CTX=$(kubectl config current-context)
ok "Context: $CTX"

KIND_NODE=$(docker ps --format "{{.Names}}" 2>/dev/null | grep "control-plane" | head -1)
[[ -z $KIND_NODE ]] && KIND_NODE="desktop-control-plane"
ok "KinD node: $KIND_NODE"

# ── 1. Build images + load into KinD ─────────────────────────────────────────
load_image() {
  local img=$1
  info "Loading $img -> KinD containerd"
  docker save "$img" | docker exec -i "$KIND_NODE" ctr --namespace=k8s.io images import -
  ok "$img loaded"
}

if [[ $SKIP_BUILD == false ]]; then
  info "Building images via docker compose"
  cd "$REPO"
  docker compose build backend celery_worker celery_default_worker celery_beat
  docker compose build frontend
  docker compose build nginx
  docker compose build watcher
  docker compose build sentinel

  info "Loading images into KinD containerd (node: $KIND_NODE)"
  load_image "agenticplatform_v2-backend:latest"
  load_image "agenticplatform_v2-celery_worker:latest"
  load_image "agenticplatform_v2-celery_default_worker:latest"
  load_image "agenticplatform_v2-celery_beat:latest"
  load_image "agenticplatform_v2-frontend:latest"
  load_image "agenticplatform_v2-nginx:latest"
  load_image "agenticplatform_v2-watcher:latest"
  load_image "agenticplatform_v2-sentinel:latest"
  ok "All images loaded"
else
  warn "Skipping image build (--skip-build)"
  warn "If pods show ErrImageNeverPull, load manually:"
  warn "  docker save <image> | docker exec -i $KIND_NODE ctr --namespace=k8s.io images import -"
fi

# ── 2. Parse .env ─────────────────────────────────────────────────────────────
info "Loading .env"
ENV_FILE="$REPO/.env"
[[ -f $ENV_FILE ]] || fail ".env not found at $ENV_FILE"

get_env() {
  local key=$1 default=${2:-}
  local val
  val=$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//")
  echo "${val:-$default}"
}

POSTGRES_PASSWORD=$(get_env POSTGRES_PASSWORD "agentic_os")
REDIS_PASSWORD=$(get_env REDIS_PASSWORD "localdev")
NEO4J_PASSWORD=$(get_env NEO4J_PASSWORD "")
JWT_SECRET=$(get_env JWT_SECRET "")
SECRET_ENCRYPTION_KEY=$(get_env SECRET_ENCRYPTION_KEY "")
WATCHER_API_KEY=$(get_env WATCHER_API_KEY "")
FLOWER_USER=$(get_env FLOWER_USER "admin")
FLOWER_PASSWORD=$(get_env FLOWER_PASSWORD "changeme")

for key in NEO4J_PASSWORD JWT_SECRET SECRET_ENCRYPTION_KEY WATCHER_API_KEY; do
  [[ -z ${!key} ]] && fail "$key is not set in .env"
done
ok ".env loaded"

# ── 3. Namespace ──────────────────────────────────────────────────────────────
info "Applying namespace"
kubectl apply -f "$BASE/00-namespace.yaml"

# ── 4. RBAC ───────────────────────────────────────────────────────────────────
info "Applying RBAC"
kubectl apply -f "$BASE/01-rbac.yaml"

# ── 5. PVCs ───────────────────────────────────────────────────────────────────
info "Applying PersistentVolumeClaims"
kubectl apply -f "$BASE/02-pvcs.yaml"
kubectl apply -f "$OVERLAY/patch-pvcs-hostpath.yaml"

# ── 6. Secret (upsert) ────────────────────────────────────────────────────────
info "Creating/updating platform-secrets"
TMP_ENV=$(mktemp)
trap "rm -f $TMP_ENV" EXIT
cat > "$TMP_ENV" <<EOF
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
NEO4J_PASSWORD=${NEO4J_PASSWORD}
JWT_SECRET=${JWT_SECRET}
SECRET_ENCRYPTION_KEY=${SECRET_ENCRYPTION_KEY}
WATCHER_API_KEY=${WATCHER_API_KEY}
FLOWER_USER=${FLOWER_USER}
FLOWER_PASSWORD=${FLOWER_PASSWORD}
EOF
kubectl create secret generic platform-secrets \
  --from-env-file="$TMP_ENV" -n "$NS" --save-config \
  --dry-run=client -o yaml | kubectl apply -f -
ok "Secret applied"

# ── 7. neo4j-seed ConfigMap ───────────────────────────────────────────────────
info "Creating neo4j-seed ConfigMap"
SEED_FILE="$REPO/backend/scripts/neo4j_seed.cypher"
if [[ -f $SEED_FILE ]]; then
  kubectl create configmap neo4j-seed \
    "--from-file=seed.cypher=$SEED_FILE" \
    -n "$NS" --save-config --dry-run=client -o yaml | kubectl apply -f -
  ok "neo4j-seed applied"
else
  warn "neo4j_seed.cypher not found - skipping"
fi

# ── 8. Wave 1 — Data tier ────────────────────────────────────────────────────
info "Wave 1 - Data tier (postgres, redis, neo4j)"
kubectl apply -f "$BASE/03-postgres.yaml"
kubectl apply -f "$BASE/04-redis.yaml"
kubectl apply -f "$BASE/05-neo4j.yaml"
wait_pod_ready "app=postgres" 180
wait_pod_ready "app=redis"    90
wait_pod_ready "app=neo4j"    420

# ── 9. Wave 2 — Backend ───────────────────────────────────────────────────────
info "Wave 2 - Backend"
kubectl apply -f "$BASE/06-backend.yaml"
kubectl apply -f "$OVERLAY/patch-image-pull-never.yaml"
wait_pod_ready "app=backend" 180

# ── 10. DB migrations + seed data ────────────────────────────────────────────
if [[ $SKIP_MIGRATIONS == false ]]; then
  info "Running Alembic migrations"
  POD=$(kubectl get pod -l app=backend -n "$NS" -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n "$NS" "$POD" -- alembic -c /app/alembic.ini upgrade head
  ok "Migrations complete"
  info "Running setup_oob.py"
  kubectl exec -n "$NS" "$POD" -- python /app/setup_oob.py
  ok "Seed data loaded"
else
  warn "Skipping migrations (--skip-migrations)"
fi

# ── 11. Wave 3 — Workers + Flower ────────────────────────────────────────────
info "Wave 3 - Celery workers + Flower"
kubectl apply -f "$BASE/07-celery.yaml"
kubectl apply -f "$BASE/08-flower.yaml"

# ── 12. Wave 4 — Frontend + Nginx ────────────────────────────────────────────
info "Wave 4 - Frontend + Nginx"
kubectl apply -f "$BASE/09-frontend.yaml"
kubectl apply -f "$BASE/10-nginx.yaml"
kubectl apply -f "$OVERLAY/patch-nginx-loadbalancer.yaml"
wait_pod_ready "app=nginx" 120

# ── 13. Wave 5 — Observability ───────────────────────────────────────────────
info "Wave 5 - Watcher + Sentinel + Backup"
kubectl apply -f "$BASE/11-watcher.yaml"
kubectl apply -f "$BASE/12-sentinel.yaml"
kubectl apply -f "$BASE/13-postgres-backup.yaml"

# ── 14. Force rollout restart ─────────────────────────────────────────────────
# Ensures pods pick up the freshly-loaded images even when kubectl apply was a
# no-op (same manifest, already-running revision). Silenced on first run where
# deployments don't exist yet.
info "Force rollout restart - ensuring pods pick up rebuilt images"
for d in backend celery-worker celery-default-worker celery-beat flower frontend nginx watcher; do
  kubectl rollout restart "deployment/$d" -n "$NS" &>/dev/null || true
done
# sentinel is a DaemonSet, not a Deployment
kubectl rollout restart daemonset/sentinel -n "$NS" &>/dev/null || true
wait_pod_ready "app=watcher" 180

# ── 15. Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "\033[36m================================================\033[0m"
echo -e "\033[32m  KinD deployment complete (Linux)\033[0m"
echo -e "\033[36m================================================\033[0m"
echo ""
kubectl get pods -n "$NS"
echo ""
echo -e "\033[32mPlatform  : https://localhost\033[0m"
echo -e "\033[33mFlower    : kubectl port-forward -n $NS svc/flower 5555:5555\033[0m"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n $NS -w"
echo "  kubectl logs -n $NS deploy/backend -f"
echo "  kubectl exec -n $NS deploy/backend -it -- /bin/bash"
