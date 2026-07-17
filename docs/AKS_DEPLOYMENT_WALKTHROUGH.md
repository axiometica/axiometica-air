# AKS Deployment Walkthrough

End-to-end guide for deploying Axiometica AIR to Azure Kubernetes Service (AKS) from scratch.

**Time estimate:** 45–90 minutes (most of this is Azure provisioning wait time).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Azure resource setup](#2-azure-resource-setup)
3. [One-time cluster configuration](#3-one-time-cluster-configuration)
4. [DNS setup](#4-dns-setup)
5. [TLS certificates](#5-tls-certificates)
6. [Prepare your deployment environment](#6-prepare-your-deployment-environment)
7. [Run the deploy script](#7-run-the-deploy-script)
8. [Verify the deployment](#8-verify-the-deployment)
9. [Access the platform](#9-access-the-platform)
10. [Day-2 operations](#10-day-2-operations)
11. [Teardown](#11-teardown)

---

## 1. Prerequisites

### Tools

Install these before starting. Versions listed are tested minimums.

| Tool | Install | Version check |
|------|---------|---------------|
| Azure CLI | `winget install Microsoft.AzureCLI` | `az --version` |
| kubectl | Bundled with Azure CLI or `az aks install-cli` | `kubectl version --client` |
| Helm | `winget install Helm.Helm` | `helm version` |
| Docker Desktop | docker.com/products/docker-desktop | `docker version` |
| Git Bash | Bundled with Git for Windows | `bash --version` |
| envsubst | In `gettext` — Git Bash bundles this | `envsubst --version` |

> **Windows note:** The deploy script (`deploy-aks.sh`) is a bash script. Run it from Git Bash, not PowerShell or cmd.

### Azure permissions

Your account needs at minimum:

- **Contributor** on the subscription (for resource group + AKS + ACR creation)
- **AcrPush** on the container registry (for image pushes)

Verify your login and set the right subscription:

```bash
az login
az account show
# If not the right subscription:
az account set --subscription "My Subscription Name"
```

---

## 2. Azure resource setup

All resources go in one resource group for easy cleanup.

### 2.1 Variables — set these once

Open Git Bash and export these. They are used throughout the walkthrough.

```bash
export RESOURCE_GROUP=axiometica-rg
export LOCATION=eastus
export ACR_NAME=axiometicaacr          # Must be globally unique, lowercase, 5–50 chars
export CLUSTER_NAME=axiometica-aks
export NODE_VM_SIZE=Standard_D4s_v3   # 4 vCPU / 16 GiB — minimum recommended
export NODE_COUNT=3                    # Start with 3; HPA will drive pod scheduling
export PLATFORM_HOST=itsm.example.com # Replace with your actual DNS hostname
```

### 2.2 Create resource group

```bash
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION
```

### 2.3 Create Azure Container Registry

```bash
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled false
```

> **SKU choice:** Basic is fine for getting started. Upgrade to Standard or Premium if you need geo-replication, content trust, or higher throughput.

### 2.4 Create AKS cluster

This creates a cluster and immediately attaches the ACR so pods can pull images without image pull secrets.

```bash
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --node-count $NODE_COUNT \
  --node-vm-size $NODE_VM_SIZE \
  --attach-acr $ACR_NAME \
  --generate-ssh-keys \
  --enable-managed-identity \
  --network-plugin azure
```

> **Wait time:** 5–10 minutes. Grab a coffee.

Verify:

```bash
az aks show \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --query "provisioningState" -o tsv
# Should print: Succeeded
```

### 2.5 Fetch kubectl credentials

```bash
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --overwrite-existing

kubectl get nodes
# All nodes should be Ready within ~1 minute
```

Expected output:

```
NAME                                STATUS   ROLES   AGE   VERSION
aks-nodepool1-12345678-vmss000000   Ready    agent   2m    v1.29.x
aks-nodepool1-12345678-vmss000001   Ready    agent   2m    v1.29.x
aks-nodepool1-12345678-vmss000002   Ready    agent   2m    v1.29.x
```

---

## 3. One-time cluster configuration

These are installed once per cluster and persist across platform upgrades.

### 3.1 nginx-ingress controller

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.replicaCount=2 \
  --set controller.nodeSelector."kubernetes\.io/os"=linux \
  --set defaultBackend.nodeSelector."kubernetes\.io/os"=linux
```

Wait for the LoadBalancer to get an external IP (2–3 minutes):

```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller -w
# Wait until EXTERNAL-IP changes from <pending> to an IP address
```

Note the IP — you will need it in the DNS step:

```bash
export INGRESS_IP=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Ingress IP: $INGRESS_IP"
```

### 3.2 cert-manager (automated TLS)

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set installCRDs=true
```

Wait for cert-manager pods to be ready:

```bash
kubectl rollout status deployment/cert-manager -n cert-manager
kubectl rollout status deployment/cert-manager-webhook -n cert-manager
```

Create a Let's Encrypt ClusterIssuer. Replace the email address:

```bash
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@example.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
    - http01:
        ingress:
          class: nginx
EOF
```

> **Staging issuer:** For testing, replace the ACME server URL with `https://acme-staging-v02.api.letsencrypt.org/directory` and name it `letsencrypt-staging`. Staging certificates are not browser-trusted but have no rate limits.

### 3.3 metrics-server

AKS ships with metrics-server pre-installed. Verify:

```bash
kubectl top nodes
# Should return CPU and memory usage per node
```

If `kubectl top` returns an error, install it:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

---

## 4. DNS setup

Point your domain at the ingress controller's external IP.

### Option A — Azure DNS

If your domain is managed in Azure DNS:

```bash
export DNS_ZONE=example.com          # Your Azure DNS zone name
export DNS_RG=dns-rg                 # Resource group containing the zone

az network dns record-set a add-record \
  --resource-group $DNS_RG \
  --zone-name $DNS_ZONE \
  --record-set-name itsm \
  --ipv4-address $INGRESS_IP
```

This creates `itsm.example.com → $INGRESS_IP`.

### Option B — External registrar

Log into your DNS provider (Cloudflare, Route 53, GoDaddy, etc.) and create an **A record**:

```
Name:   itsm          (or @ for apex)
Type:   A
Value:  <your INGRESS_IP>
TTL:    300 (5 min for initial setup; increase later)
```

### Verify DNS propagation

```bash
# May take 1–15 minutes depending on TTL
nslookup $PLATFORM_HOST
# or
dig +short $PLATFORM_HOST
# Should return your INGRESS_IP
```

---

## 5. TLS certificates

cert-manager issues the certificate automatically when the Ingress is applied (step 7). The Ingress manifest references:

```yaml
annotations:
  cert-manager.io/cluster-issuer: letsencrypt-prod
tls:
- hosts:
  - itsm.example.com
  secretName: agentic-platform-tls
```

cert-manager watches for Ingress objects with that annotation, triggers an ACME HTTP-01 challenge through nginx, and stores the resulting certificate in the `agentic-platform-tls` Secret. This happens automatically — no manual steps required after cert-manager is installed.

> **Prerequisite:** DNS must be fully propagated before cert-manager can complete the ACME HTTP-01 challenge. If the certificate stays in a `False/Pending` state, check DNS first.

To add the cert-manager annotation to the Ingress, edit `k8s/overlays/aks/ingress.yaml` and add the annotation before deploying:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    # ... existing annotations ...
```

---

## 6. Prepare your deployment environment

### 6.1 Clone and enter the repo

```bash
git clone https://github.com/axiometica/axiometica-air.git
cd axiometica-air
```

### 6.2 Create your .env file

Copy the template and fill in all values:

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
# Database
POSTGRES_PASSWORD=<strong-password>
NEO4J_PASSWORD=<strong-password>
REDIS_PASSWORD=<strong-password>

# Application secrets
JWT_SECRET=<64-char-random-string>
SECRET_ENCRYPTION_KEY=<64-char-random-string>
WATCHER_API_KEY=<32-char-random-string>

# Flower UI
FLOWER_USER=admin
FLOWER_PASSWORD=<strong-password>
```

Generate strong values:

```bash
# Each of these produces a suitable random secret
openssl rand -hex 32
```

> **Security:** Never commit `.env` to git. It is in `.gitignore`. The deploy script reads it at deploy time and stores values in a K8s Secret.

### 6.3 Set deployment environment variables

```bash
export ACR_NAME=axiometicaacr
export RESOURCE_GROUP=axiometica-rg
export CLUSTER_NAME=axiometica-aks
export PLATFORM_HOST=itsm.example.com
export ALLOWED_ORIGINS=https://itsm.example.com
```

---

## 7. Run the deploy script

From the repo root in Git Bash:

```bash
bash k8s/scripts/deploy-aks.sh
```

The script prompts you to confirm the kubectl context before making any changes:

```
==> kubectl context: axiometica-aks
Deploying to 'axiometica-aks'. Continue? [y/N] y
```

### What happens, step by step

| Step | What it does |
|------|-------------|
| Preflight | Checks kubectl, docker, az are installed |
| AKS credentials | `az aks get-credentials` — refreshes your kubeconfig |
| Build images | `docker compose build` for backend, frontend, nginx, watcher |
| Tag + push | Tags each image `<acr>.azurecr.io/agenticplatform/<svc>:<git-sha>` and pushes |
| Load .env | Parses `.env`; validates required secrets are present |
| Namespace + RBAC | Creates `agentic-platform` namespace and watcher ServiceAccount |
| PVCs | Creates 7 PersistentVolumeClaims (uses AKS default `managed` storage class) |
| platform-secrets | Upserts the K8s Secret from `.env` values |
| neo4j-seed ConfigMap | Loads `backend/scripts/neo4j_seed.cypher` |
| Wave 1 — Data | Deploys Postgres, Redis, Neo4j; waits for all to be Ready |
| Wave 2 — Backend | Deploys backend; patches image to ACR ref; sets ALLOWED_ORIGINS |
| Migrations | Runs `alembic upgrade head` + `setup_oob.py` inside the backend pod |
| Wave 3 — Workers | Deploys celery-worker, celery-default-worker, celery-beat, Flower |
| Wave 4 — Frontend | Deploys frontend and nginx; patches images |
| Wave 5 — Observability | Deploys watcher, sentinel DaemonSet, postgres-backup |
| AKS overlay | Applies HPA, PodDisruptionBudgets, nginx-ingress Ingress |

### Estimated duration

| Phase | Time |
|-------|------|
| Image build (all services) | 5–15 min (cached layers are fast) |
| Image push to ACR | 2–5 min |
| Neo4j ready | Up to 7 min on first boot |
| Migrations + seed | 1–3 min |
| Total | ~15–30 min |

### Skip flags for re-deploys

```bash
# Images already in ACR — skip build and push
SKIP_BUILD=1 bash k8s/scripts/deploy-aks.sh

# Schema already migrated — skip Alembic + seed
SKIP_BUILD=1 SKIP_MIGRATIONS=1 bash k8s/scripts/deploy-aks.sh
```

---

## 8. Verify the deployment

### 8.1 All pods running

```bash
kubectl get pods -n agentic-platform
```

Expected state after ~5 minutes:

```
NAME                                    READY   STATUS    RESTARTS   AGE
backend-6d7f9b5c8-abc12                 1/1     Running   0          4m
backend-6d7f9b5c8-def34                 1/1     Running   0          4m
celery-beat-7c8d9f6b5-ghi56             1/1     Running   0          3m
celery-default-worker-5b6c7d8e9-jkl78   1/1     Running   0          3m
celery-worker-4a5b6c7d8-mno90           1/1     Running   0          3m
flower-3f4g5h6i7-pqr12                  1/1     Running   0          3m
frontend-2e3f4g5h6-stu34                1/1     Running   0          2m
frontend-2e3f4g5h6-vwx56                1/1     Running   0          2m
neo4j-1d2e3f4g5-yza78                   1/1     Running   0          6m
nginx-0c1d2e3f4-bcd90                   1/1     Running   0          2m
nginx-0c1d2e3f4-efg12                   1/1     Running   0          2m
postgres-9b0c1d2e3-hij34                1/1     Running   0          7m
redis-8a9b0c1d2-klm56                   1/1     Running   0          7m
sentinel-nop78                          1/1     Running   0          1m
sentinel-qrs90                          1/1     Running   0          1m
sentinel-tuv12                          1/1     Running   0          1m
watcher-7f8g9h0i1-wxy34                 1/1     Running   0          1m
```

> **DaemonSet:** `sentinel` runs one pod per node, so you will see 3 pods if you have 3 nodes.

### 8.2 HPA status

```bash
kubectl get hpa -n agentic-platform
```

```
NAME             REFERENCE                   TARGETS   MINPODS   MAXPODS   REPLICAS
backend          Deployment/backend          12%/70%   2         5         2
celery-worker    Deployment/celery-worker    8%/80%    1         8         1
frontend         Deployment/frontend         5%/70%    2         4         2
```

> **`<unknown>` targets:** If TARGETS shows `<unknown>/70%`, metrics-server hasn't scraped the pods yet. Wait 60–90 seconds and re-check.

### 8.3 Ingress

```bash
kubectl get ingress -n agentic-platform
```

```
NAME               CLASS   HOSTS                ADDRESS        PORTS     AGE
agentic-platform   nginx   itsm.example.com     20.x.x.x      80, 443   2m
```

The ADDRESS should match `$INGRESS_IP`.

### 8.4 TLS certificate

```bash
kubectl get certificate -n agentic-platform
```

```
NAME                   READY   SECRET                  AGE
agentic-platform-tls   True    agentic-platform-tls    3m
```

`READY: True` means cert-manager has issued the certificate and stored it. If it stays `False`, check:

```bash
kubectl describe certificate agentic-platform-tls -n agentic-platform
kubectl describe certificaterequest -n agentic-platform
# Look for ACME challenge status or DNS propagation errors
```

### 8.5 Backend health

```bash
POD=$(kubectl get pod -l app=backend -n agentic-platform \
      -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n agentic-platform $POD -- \
  curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Expected:

```json
{
  "status": "healthy",
  "database": "connected",
  "neo4j": "connected",
  "redis": "connected"
}
```

### 8.6 Resource usage

```bash
kubectl top pods -n agentic-platform
kubectl top nodes
```

---

## 9. Access the platform

### Platform UI

Open your browser: `https://itsm.example.com`

You should see the Axiometica AIR login page with a valid TLS certificate. Log in with the credentials from `setup_oob.py` (default admin created during seeding).

### Flower (Celery task monitor)

Flower is not exposed via the Ingress — access it through a port-forward only:

```bash
kubectl port-forward -n agentic-platform svc/flower 5555:5555
# Open http://localhost:5555 in your browser
# Credentials: FLOWER_USER / FLOWER_PASSWORD from .env
```

### Neo4j Browser

```bash
kubectl port-forward -n agentic-platform svc/neo4j 7474:7474 7687:7687
# Open http://localhost:7474 in your browser
# Connect to: bolt://localhost:7687
# Credentials: neo4j / NEO4J_PASSWORD from .env
```

---

## 10. Day-2 operations

### Upgrading the platform

Build and push new images, then update the deployments:

```bash
# 1. Build and push
IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose build backend frontend nginx watcher
for svc in backend frontend nginx watcher; do
  docker tag agenticplatform_v2-${svc}:latest \
    ${ACR_NAME}.azurecr.io/agenticplatform/${svc}:${IMAGE_TAG}
  docker push ${ACR_NAME}.azurecr.io/agenticplatform/${svc}:${IMAGE_TAG}
done

# 2. Roll out new images
REGISTRY=${ACR_NAME}.azurecr.io/agenticplatform
kubectl set image deployment/backend   backend=${REGISTRY}/backend:${IMAGE_TAG}   -n agentic-platform
kubectl set image deployment/frontend  frontend=${REGISTRY}/frontend:${IMAGE_TAG} -n agentic-platform
kubectl set image deployment/nginx     nginx=${REGISTRY}/nginx:${IMAGE_TAG}       -n agentic-platform
kubectl set image deployment/watcher   watcher=${REGISTRY}/watcher:${IMAGE_TAG}   -n agentic-platform

# 3. Watch the rollout
kubectl rollout status deployment/backend -n agentic-platform

# 4. If schema changed, run migrations against the new pod
POD=$(kubectl get pod -l app=backend -n agentic-platform \
      -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n agentic-platform $POD -- \
  alembic -c /app/src/agentic_os/alembic.ini upgrade head
```

### Rolling back

```bash
# Roll back all deployments one revision
for deploy in backend frontend nginx watcher; do
  kubectl rollout undo deployment/$deploy -n agentic-platform
done

# Verify
kubectl rollout status deployment/backend -n agentic-platform
```

### Scaling manually

```bash
# Scale celery workers during an incident storm
kubectl scale deployment/celery-worker --replicas=6 -n agentic-platform

# Scale backend
kubectl scale deployment/backend --replicas=4 -n agentic-platform

# HPA takes over again automatically once load normalises
```

### Watching HPA scale events

```bash
kubectl get hpa -n agentic-platform -w
# Also see scale events in:
kubectl describe hpa backend -n agentic-platform
```

### Checking logs

```bash
# Backend (all replicas)
kubectl logs -n agentic-platform -l app=backend -f --max-log-requests=5

# Watcher
kubectl logs -n agentic-platform deploy/watcher -f

# Celery worker (tail last 100 lines)
kubectl logs -n agentic-platform -l app=celery-worker --tail=100

# A specific pod
kubectl logs -n agentic-platform <pod-name> -f
```

### Exec into a pod

```bash
# Backend shell
kubectl exec -n agentic-platform deploy/backend -it -- /bin/bash

# Run a Django management command or one-off Python script
kubectl exec -n agentic-platform deploy/backend -- python /app/some_script.py
```

### Rotating secrets

After updating `.env`:

```bash
# Re-apply the Secret
kubectl create secret generic platform-secrets \
  --namespace agentic-platform \
  $(grep -E '^[A-Z_]+=.' .env | grep -v '#' | \
    sed "s/^/--from-literal=/" | tr '\n' ' ') \
  --save-config --dry-run=client -o yaml | kubectl apply -f -

# Restart pods to pick up new secret values
kubectl rollout restart deployment/backend -n agentic-platform
kubectl rollout restart deployment/watcher -n agentic-platform
kubectl rollout restart deployment/celery-worker -n agentic-platform
```

### Cluster autoscaling

To add automatic node scaling (so you don't need to manually add nodes during high load):

```bash
az aks update \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --enable-cluster-autoscaler \
  --min-count 2 \
  --max-count 10
```

The cluster autoscaler + HPA work together: HPA adds pods, the cluster autoscaler adds nodes when pods can't be scheduled.

---

## 11. Teardown

### Remove only the platform (keep cluster and ACR)

```bash
kubectl delete namespace agentic-platform
# PVCs (and their data) are also deleted with the namespace
```

### Remove everything

```bash
az group delete --name $RESOURCE_GROUP --yes --no-wait
```

This deletes the AKS cluster, ACR, all node VMs, disks, load balancers, and the resource group. It is not reversible.

---

## Troubleshooting quick reference

| Symptom | Check |
|---------|-------|
| Pod stuck in `Pending` | `kubectl describe pod <name> -n agentic-platform` — look for `Insufficient memory/cpu` or `no nodes available` |
| Pod in `CrashLoopBackOff` | `kubectl logs <pod> -n agentic-platform --previous` |
| `ErrImagePull` | Verify ACR is attached: `az aks check-acr --name $CLUSTER_NAME --resource-group $RESOURCE_GROUP --image $ACR_NAME.azurecr.io/agenticplatform/backend:latest` |
| Certificate stuck `Pending` | DNS not propagated yet, or HTTP-01 challenge failed — `kubectl describe challenge -n agentic-platform` |
| Ingress `ADDRESS` blank | nginx-ingress controller LoadBalancer still pending — `kubectl get svc -n ingress-nginx` |
| `kubectl top` returns error | metrics-server not ready — `kubectl rollout status deployment/metrics-server -n kube-system` |
| Backend returns 502 from browser | Backend not yet ready or ALLOWED_ORIGINS mismatch — check `kubectl logs deploy/backend -n agentic-platform` |
| HPA `TARGETS: <unknown>` | metrics-server hasn't scraped pods yet — wait 90 seconds |
