#!/usr/bin/env bash
# deploy-aks.sh — Deploy AgenticPlatform to AKS (or any managed Kubernetes cluster).
#
# Works with: Azure AKS, AWS EKS, GKE, DigitalOcean DOKS, and any cluster that has:
#   • A container registry the cluster can pull from
#   • metrics-server installed (standard on AKS/EKS/GKE; install separately on DOKS)
#   • nginx-ingress controller (or adapt INGRESS_CLASS below)
#
# Usage:
#   export ACR_NAME=myregistry            # Azure: registry name (without .azurecr.io)
#   export RESOURCE_GROUP=my-rg           # Azure: resource group
#   export CLUSTER_NAME=my-aks            # AKS cluster name
#   export PLATFORM_HOST=itsm.example.com # Public DNS hostname for the Ingress
#   bash k8s/scripts/deploy-aks.sh
#
# Non-Azure registry:
#   Set REGISTRY_PREFIX to the full registry prefix instead of ACR_NAME.
#   e.g.: export REGISTRY_PREFIX=123456789.dkr.ecr.us-east-1.amazonaws.com/agenticplatform
#   The script will skip 'az' commands and use REGISTRY_PREFIX directly.
#
# Options (env vars):
#   IMAGE_TAG         Tag for built images (default: git SHA, falls back to 'latest')
#   NAMESPACE         K8s namespace        (default: agentic-platform)
#   SKIP_BUILD        Set to '1' to skip docker build + push (images already in registry)
#   SKIP_MIGRATIONS   Set to '1' to skip Alembic + seed data
#   INGRESS_CLASS     Ingress class name   (default: nginx)
#   ALLOWED_ORIGINS   CORS origins for backend (default: https://$PLATFORM_HOST)
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NAMESPACE="${NAMESPACE:-agentic-platform}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')}"
INGRESS_CLASS="${INGRESS_CLASS:-nginx}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_MIGRATIONS="${SKIP_MIGRATIONS:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_DIR="$SCRIPT_DIR/../base"
AKS_OVERLAY="$SCRIPT_DIR/../overlays/aks"

# Derive registry prefix
if [[ -n "${ACR_NAME:-}" ]]; then
    REGISTRY_PREFIX="${ACR_NAME}.azurecr.io/agenticplatform"
elif [[ -n "${REGISTRY_PREFIX:-}" ]]; then
    : # use as-is
else
    echo "ERROR: Set ACR_NAME (Azure) or REGISTRY_PREFIX (other registries)." >&2
    exit 1
fi

PLATFORM_HOST="${PLATFORM_HOST:-}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-${PLATFORM_HOST:+https://$PLATFORM_HOST}}"

# Image map: compose service name → registry image name
declare -A IMAGES=(
    [backend]="${REGISTRY_PREFIX}/backend:${IMAGE_TAG}"
    [frontend]="${REGISTRY_PREFIX}/frontend:${IMAGE_TAG}"
    [nginx]="${REGISTRY_PREFIX}/nginx:${IMAGE_TAG}"
    [watcher]="${REGISTRY_PREFIX}/watcher:${IMAGE_TAG}"
    [sentinel]="${REGISTRY_PREFIX}/sentinel:${IMAGE_TAG}"
)
# The backend image is also used for celery workers
CELERY_IMAGE="${REGISTRY_PREFIX}/backend:${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo -e "\033[0;36m==> $*\033[0m"; }
ok()    { echo -e "\033[0;32m  OK  $*\033[0m"; }
warn()  { echo -e "\033[0;33m WARN $*\033[0m"; }
fail()  { echo -e "\033[0;31m FAIL $*\033[0m"; exit 1; }

wait_pod_ready() {
    local label="$1" timeout="${2:-180}"
    info "Waiting for pod: $label (${timeout}s)"
    kubectl wait pod -l "$label" -n "$NAMESPACE" \
        --for=condition=ready --timeout="${timeout}s"
    ok "$label ready"
}

require_cmd() { command -v "$1" &>/dev/null || fail "Required command not found: $1"; }

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
info "Preflight checks"
require_cmd kubectl
require_cmd docker

if [[ -n "${ACR_NAME:-}" ]]; then
    require_cmd az
fi

CTX=$(kubectl config current-context)
info "kubectl context: $CTX"
read -r -p "Deploying to '$CTX'. Continue? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ---------------------------------------------------------------------------
# 1. AKS credentials (skip for non-Azure)
# ---------------------------------------------------------------------------
if [[ -n "${ACR_NAME:-}" && -n "${RESOURCE_GROUP:-}" && -n "${CLUSTER_NAME:-}" ]]; then
    info "Fetching AKS credentials"
    az aks get-credentials \
        --resource-group "$RESOURCE_GROUP" \
        --name "$CLUSTER_NAME" \
        --overwrite-existing
    ok "Credentials updated"
fi

# ---------------------------------------------------------------------------
# 2. Build, tag, and push images
# ---------------------------------------------------------------------------
if [[ "$SKIP_BUILD" == "1" ]]; then
    warn "Skipping image build (-SKIP_BUILD=1)"
else
    info "Building images via docker compose (tag: $IMAGE_TAG)"
    cd "$REPO_DIR"
    docker compose build backend celery_worker celery_default_worker celery_beat
    docker compose build frontend
    docker compose build nginx
    docker compose build watcher
    docker compose build sentinel
    ok "Images built"

    if [[ -n "${ACR_NAME:-}" ]]; then
        info "Logging into ACR: $ACR_NAME"
        az acr login --name "$ACR_NAME"
    fi

    info "Tagging and pushing images → $REGISTRY_PREFIX"
    declare -A COMPOSE_NAMES=(
        [backend]="agenticplatform_v2-backend:latest"
        [frontend]="agenticplatform_v2-frontend:latest"
        [nginx]="agenticplatform_v2-nginx:latest"
        [watcher]="agenticplatform_v2-watcher:latest"
        [sentinel]="agenticplatform_v2-sentinel:latest"
    )
    for svc in backend frontend nginx watcher sentinel; do
        local_image="${COMPOSE_NAMES[$svc]}"
        remote_image="${IMAGES[$svc]}"
        docker tag "$local_image" "$remote_image"
        docker push "$remote_image"
        ok "Pushed $remote_image"
    done
fi

# ---------------------------------------------------------------------------
# 3. Parse .env for secrets
# ---------------------------------------------------------------------------
info "Loading .env"
ENV_FILE="$REPO_DIR/.env"
[[ -f "$ENV_FILE" ]] || fail ".env not found at $ENV_FILE"

get_env() {
    local key="$1" default="${2:-}"
    local val
    val=$(grep -E "^\s*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- \
          | sed "s/^['\"]//;s/['\"]$//")
    echo "${val:-$default}"
}

POSTGRES_PASSWORD=$(get_env POSTGRES_PASSWORD "agentic_os")
REDIS_PASSWORD=$(get_env REDIS_PASSWORD "localdev")
NEO4J_PASSWORD=$(get_env NEO4J_PASSWORD)
JWT_SECRET=$(get_env JWT_SECRET)
SECRET_ENCRYPTION_KEY=$(get_env SECRET_ENCRYPTION_KEY)
WATCHER_API_KEY=$(get_env WATCHER_API_KEY)
FLOWER_USER=$(get_env FLOWER_USER "admin")
FLOWER_PASSWORD=$(get_env FLOWER_PASSWORD "changeme")

for var in NEO4J_PASSWORD JWT_SECRET SECRET_ENCRYPTION_KEY WATCHER_API_KEY; do
    [[ -n "${!var}" ]] || fail "$var is not set in .env"
done
ok ".env loaded"

# ---------------------------------------------------------------------------
# 4. Namespace + RBAC
# ---------------------------------------------------------------------------
info "Applying namespace and RBAC"
kubectl apply -f "$BASE_DIR/00-namespace.yaml"
kubectl apply -f "$BASE_DIR/01-rbac.yaml"

# ---------------------------------------------------------------------------
# 5. PVCs  (use cluster default storage class — no storageClassName override)
# ---------------------------------------------------------------------------
info "Applying PVCs"
kubectl apply -f "$BASE_DIR/02-pvcs.yaml"

# ---------------------------------------------------------------------------
# 6. Secret (upsert)
# ---------------------------------------------------------------------------
info "Creating/updating platform-secrets"
kubectl create secret generic platform-secrets \
    --namespace "$NAMESPACE" \
    --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD" \
    --from-literal=NEO4J_PASSWORD="$NEO4J_PASSWORD" \
    --from-literal=JWT_SECRET="$JWT_SECRET" \
    --from-literal=SECRET_ENCRYPTION_KEY="$SECRET_ENCRYPTION_KEY" \
    --from-literal=WATCHER_API_KEY="$WATCHER_API_KEY" \
    --from-literal=FLOWER_USER="$FLOWER_USER" \
    --from-literal=FLOWER_PASSWORD="$FLOWER_PASSWORD" \
    --save-config --dry-run=client -o yaml | kubectl apply -f -
ok "Secret applied"

# ---------------------------------------------------------------------------
# 7. neo4j-seed ConfigMap
# ---------------------------------------------------------------------------
info "Applying neo4j-seed ConfigMap"
SEED_FILE="$REPO_DIR/backend/scripts/neo4j_seed.cypher"
if [[ -f "$SEED_FILE" ]]; then
    kubectl create configmap neo4j-seed \
        --from-file=seed.cypher="$SEED_FILE" \
        -n "$NAMESPACE" --save-config \
        --dry-run=client -o yaml | kubectl apply -f -
    ok "neo4j-seed applied"
else
    warn "neo4j_seed.cypher not found — skipping"
fi

# ---------------------------------------------------------------------------
# 8. Wave 1 — Data tier
# ---------------------------------------------------------------------------
info "Wave 1 — Data tier"
kubectl apply -f "$BASE_DIR/03-postgres.yaml"
kubectl apply -f "$BASE_DIR/04-redis.yaml"
kubectl apply -f "$BASE_DIR/05-neo4j.yaml"
wait_pod_ready "app=postgres" 180
wait_pod_ready "app=redis"    90
wait_pod_ready "app=neo4j"    420

# ---------------------------------------------------------------------------
# 9. Wave 2 — Backend  (patch ALLOWED_ORIGINS and image ref)
# ---------------------------------------------------------------------------
info "Wave 2 — Backend"
kubectl apply -f "$BASE_DIR/06-backend.yaml"

# Point deployment at the registry image
kubectl set image deployment/backend \
    backend="${IMAGES[backend]}" \
    -n "$NAMESPACE"

# Patch ALLOWED_ORIGINS if a hostname is set
if [[ -n "$ALLOWED_ORIGINS" ]]; then
    kubectl set env deployment/backend \
        ALLOWED_ORIGINS="$ALLOWED_ORIGINS" \
        -n "$NAMESPACE"
    ok "ALLOWED_ORIGINS set to $ALLOWED_ORIGINS"
fi

wait_pod_ready "app=backend" 180

# ---------------------------------------------------------------------------
# 10. DB migrations + seed data
# ---------------------------------------------------------------------------
if [[ "$SKIP_MIGRATIONS" == "1" ]]; then
    warn "Skipping migrations (SKIP_MIGRATIONS=1)"
else
    info "Running Alembic migrations"
    POD=$(kubectl get pod -l app=backend -n "$NAMESPACE" \
          -o jsonpath='{.items[0].metadata.name}')
    kubectl exec -n "$NAMESPACE" "$POD" -- \
        alembic -c /app/alembic.ini upgrade head
    ok "Migrations complete"

    info "Running setup_oob.py"
    kubectl exec -n "$NAMESPACE" "$POD" -- python /app/setup_oob.py
    ok "Seed data loaded"
fi

# ---------------------------------------------------------------------------
# 11. Wave 3 — Workers + Flower  (patch celery images)
# ---------------------------------------------------------------------------
info "Wave 3 — Celery workers + Flower"
kubectl apply -f "$BASE_DIR/07-celery.yaml"
kubectl apply -f "$BASE_DIR/08-flower.yaml"
for deploy in celery-worker celery-default-worker celery-beat; do
    container="${deploy//-worker/}-worker"
    # Map deployment name → container name
    case "$deploy" in
        celery-worker)         container="celery-worker" ;;
        celery-default-worker) container="celery-default-worker" ;;
        celery-beat)           container="celery-beat" ;;
    esac
    kubectl set image "deployment/$deploy" \
        "${container}=${CELERY_IMAGE}" \
        -n "$NAMESPACE" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 12. Wave 4 — Frontend + Nginx  (patch images)
# ---------------------------------------------------------------------------
info "Wave 4 — Frontend + Nginx"
kubectl apply -f "$BASE_DIR/09-frontend.yaml"
kubectl apply -f "$BASE_DIR/10-nginx.yaml"
kubectl set image deployment/frontend frontend="${IMAGES[frontend]}" -n "$NAMESPACE"
kubectl set image deployment/nginx     nginx="${IMAGES[nginx]}"     -n "$NAMESPACE"
wait_pod_ready "app=nginx" 120

# ---------------------------------------------------------------------------
# 13. Wave 5 — Observability
# ---------------------------------------------------------------------------
info "Wave 5 — Watcher + Sentinel + Backup"
kubectl apply -f "$BASE_DIR/11-watcher.yaml"
kubectl set image deployment/watcher watcher="${IMAGES[watcher]}" -n "$NAMESPACE"
kubectl apply -f "$BASE_DIR/12-sentinel.yaml"
# sentinel is a DaemonSet — use rollout restart to pick up the new registry image
kubectl rollout restart daemonset/sentinel -n "$NAMESPACE" 2>/dev/null || true
kubectl apply -f "$BASE_DIR/13-postgres-backup.yaml"

# ---------------------------------------------------------------------------
# 14. AKS overlay — HPA + PDB + Ingress
# ---------------------------------------------------------------------------
info "Applying AKS overlay (HPA, PDB, Ingress)"
kubectl apply -f "$AKS_OVERLAY/hpa.yaml"
kubectl apply -f "$AKS_OVERLAY/pdb.yaml"

if [[ -n "$PLATFORM_HOST" ]]; then
    PLATFORM_HOST="$PLATFORM_HOST" envsubst '${PLATFORM_HOST}' \
        < "$AKS_OVERLAY/ingress.yaml" | kubectl apply -f -
    ok "Ingress applied for host: $PLATFORM_HOST"
else
    warn "PLATFORM_HOST not set — skipping Ingress (apply manually with envsubst)"
    warn "  PLATFORM_HOST=itsm.example.com envsubst '\${PLATFORM_HOST}' < k8s/overlays/aks/ingress.yaml | kubectl apply -f -"
fi

# ---------------------------------------------------------------------------
# 15. Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "\033[0;36m================================================\033[0m"
echo -e "\033[0;32m  AKS deployment complete  (tag: $IMAGE_TAG)\033[0m"
echo -e "\033[0;36m================================================\033[0m"
echo ""
kubectl get pods -n "$NAMESPACE"
echo ""
kubectl get hpa -n "$NAMESPACE"
echo ""
if [[ -n "$PLATFORM_HOST" ]]; then
    echo -e "\033[0;32mPlatform  : https://$PLATFORM_HOST\033[0m"
else
    INGRESS_IP=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
                 -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "<pending>")
    echo -e "\033[0;33mIngress LB IP : $INGRESS_IP (point your DNS A record here)\033[0m"
fi
echo ""
echo "Watch rollout:"
echo "  kubectl get pods -n $NAMESPACE -w"
echo "  kubectl rollout status deployment/backend -n $NAMESPACE"
echo ""
echo "Scale manually:"
echo "  kubectl scale deployment/celery-worker --replicas=3 -n $NAMESPACE"
echo "  kubectl get hpa -n $NAMESPACE -w"
