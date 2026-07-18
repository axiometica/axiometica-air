#Requires -Version 5.1
<#
.SYNOPSIS
  Deploy AgenticPlatform to Docker Desktop KinD (local development).

.DESCRIPTION
  Builds images with docker compose, loads them into KinD's containerd
  (PowerShell pipes are text-mode and corrupt binary tar streams — Git Bash
  handles the binary pipe correctly), creates the K8s Secret from .env,
  applies base/ manifests + KinD overlay patches, and runs DB migrations.

.PARAMETER SkipBuild
  Skip docker compose build and KinD image load (images already loaded).

.PARAMETER SkipMigrations
  Skip Alembic migrations and setup_oob.py seed data.

.PARAMETER Namespace
  Target Kubernetes namespace (default: agentic-platform).

.EXAMPLE
  cd C:\Users\mikeb\OneDrive\Documents\Projects\AgenticPlatform_v2
  .\k8s\scripts\deploy-kind.ps1
  .\k8s\scripts\deploy-kind.ps1 -SkipBuild -SkipMigrations
#>
param(
    [switch]$SkipBuild,
    [switch]$SkipMigrations,
    [string]$Namespace = "agentic-platform"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO    = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$BASE    = Join-Path (Split-Path $PSScriptRoot -Parent) "base"
$OVERLAY = Join-Path (Split-Path $PSScriptRoot -Parent) "overlays\kind"
$NS      = $Namespace

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host " WARN $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host " FAIL $msg" -ForegroundColor Red; exit 1 }

function Wait-PodReady {
    param([string]$label, [int]$timeoutSec = 180)
    $ErrorActionPreference = "SilentlyContinue"
    Info "Waiting for pod: $label (${timeoutSec}s)"
    $deadline = [DateTime]::Now.AddSeconds($timeoutSec)
    while ([DateTime]::Now -lt $deadline) {
        kubectl wait pod -l $label -n $NS --for=condition=ready --timeout=10s 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "$label ready"; $ErrorActionPreference = "Stop"; return }
        Start-Sleep 5
    }
    Write-Host "Pod state at timeout:" -ForegroundColor Yellow
    kubectl get pod -l $label -n $NS
    kubectl describe pod -l $label -n $NS | Select-String -Pattern "State:|Reason:|Warning|Error" -Context 0,1
    $ErrorActionPreference = "Stop"
    Fail "Timed out waiting for $label"
}

# Apply a manifest that contains locally-built images.
# Rewrites image refs to use the local registry and sets imagePullPolicy: Always
# so rollout restarts always reflect the latest docker compose build output.
# Docker Desktop K8s caches images in a separate containerd namespace (k8s.io)
# from Docker's store (moby); imagePullPolicy: IfNotPresent reuses the stale
# cached digest even after a rebuild. The local registry side-steps this entirely.
function Apply-Local($file) {
    (Get-Content $file -Raw) `
        -replace 'image: agenticplatform_v2-', "image: ${script:REG}/agenticplatform_v2-" `
        -replace 'imagePullPolicy: IfNotPresent', 'imagePullPolicy: Always' |
        kubectl apply -f -
}

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Info "Preflight checks"
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) { Fail "kubectl not found in PATH" }

# Switch to docker-desktop context (Docker Desktop built-in Kubernetes)
$ctx = kubectl config current-context 2>$null
if ($ctx -ne "docker-desktop") {
    Info "Switching context to docker-desktop"
    kubectl config use-context docker-desktop 2>$null | Out-Null
    $ctx = kubectl config current-context 2>$null
    if ($ctx -ne "docker-desktop") {
        Fail "Could not switch to docker-desktop context. Enable Kubernetes in Docker Desktop: Settings -> Kubernetes -> Enable Kubernetes"
    }
}
Ok "Context: $ctx"

# ---------------------------------------------------------------------------
# 1. Local registry (permanent fix for Docker Desktop K8s containerd caching)
# ---------------------------------------------------------------------------
# Docker Desktop K8s stores images in the k8s.io containerd namespace, separate
# from Docker's moby namespace. imagePullPolicy: IfNotPresent reuses whatever
# digest was cached on first pull — rebuilding with docker compose does NOT
# update the cached digest; pods see stale code after rollout restart.
# A local registry at localhost:5000 side-steps this: K8s pulls over HTTP each
# time (imagePullPolicy: Always), so rebuilds are always reflected.
Info "Ensuring local registry is running at localhost:5000"
$script:REG = "localhost:5000"
# docker ps exits 0 even when nothing matches. Temporarily silence errors so
# docker rm on a missing/stopped container doesn't trip $ErrorActionPreference=Stop.
$regId = docker ps -q -f "name=k8s-local-registry"
if (-not $regId) {
    $ErrorActionPreference = "SilentlyContinue"
    docker rm -f k8s-local-registry | Out-Null
    $ErrorActionPreference = "Stop"
    docker run -d -p 5000:5000 --name k8s-local-registry --restart=always registry:2 | Out-Null
    Ok "Started local registry at $script:REG"
} else {
    Ok "Local registry already running at $script:REG"
}

# ---------------------------------------------------------------------------
# 2. Build images + push to local registry
# ---------------------------------------------------------------------------
if (-not $SkipBuild) {
    Info "Building images via docker compose"
    Set-Location $REPO
    docker compose build backend celery_worker celery_default_worker celery_beat
    docker compose build frontend
    docker compose build nginx
    docker compose build watcher
    docker compose build sentinel

    Info "Pushing locally-built images to $script:REG"
    $localImages = @(
        "agenticplatform_v2-backend",
        "agenticplatform_v2-celery_worker",
        "agenticplatform_v2-celery_default_worker",
        "agenticplatform_v2-celery_beat",
        "agenticplatform_v2-frontend",
        "agenticplatform_v2-nginx",
        "agenticplatform_v2-watcher",
        "agenticplatform_v2-sentinel"
    )
    foreach ($img in $localImages) {
        docker tag "${img}:latest" "$script:REG/${img}:latest" 2>$null | Out-Null
        docker push "$script:REG/${img}:latest"
    }
    Ok "All images pushed to $script:REG"
} else {
    Warn "Skipping image build (-SkipBuild)"
    $script:REG = "localhost:5000"
}

# ---------------------------------------------------------------------------
# 2. Parse .env
# ---------------------------------------------------------------------------
Info "Loading .env"
$envFile = Join-Path $REPO ".env"
if (-not (Test-Path $envFile)) { Fail ".env not found at $envFile" }

$envMap = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
        $k = $Matches[1]
        $v = $Matches[2] -replace '^"(.*)"$','$1' -replace "^'(.*)'$",'$1'
        $envMap[$k] = $v
    }
}

function Get-Env { param([string]$key, [string]$default = "")
    if ($envMap.ContainsKey($key) -and $envMap[$key] -ne "") { return $envMap[$key] }
    return $default
}

$secrets = [ordered]@{
    POSTGRES_PASSWORD     = Get-Env "POSTGRES_PASSWORD" "agentic_os"
    REDIS_PASSWORD        = Get-Env "REDIS_PASSWORD"    "localdev"
    NEO4J_PASSWORD        = Get-Env "NEO4J_PASSWORD"    ""
    JWT_SECRET            = Get-Env "JWT_SECRET"        ""
    SECRET_ENCRYPTION_KEY = Get-Env "SECRET_ENCRYPTION_KEY" ""
    WATCHER_API_KEY       = Get-Env "WATCHER_API_KEY"   ""
    FLOWER_USER           = Get-Env "FLOWER_USER"       "admin"
    FLOWER_PASSWORD       = Get-Env "FLOWER_PASSWORD"   "changeme"
}
foreach ($key in @("NEO4J_PASSWORD","JWT_SECRET","SECRET_ENCRYPTION_KEY","WATCHER_API_KEY")) {
    if ($secrets[$key] -eq "") { Fail "$key is not set in .env" }
}
Ok ".env loaded"

# ---------------------------------------------------------------------------
# 3. Namespace
# ---------------------------------------------------------------------------
Info "Applying namespace"
kubectl apply -f "$BASE\00-namespace.yaml"

# ---------------------------------------------------------------------------
# 4. RBAC (watcher ServiceAccount + ClusterRole)
# ---------------------------------------------------------------------------
Info "Applying RBAC"
kubectl apply -f "$BASE\01-rbac.yaml"

# ---------------------------------------------------------------------------
# 5. PVCs  — base first, then KinD patch sets storageClassName: hostpath
# ---------------------------------------------------------------------------
Info "Applying PersistentVolumeClaims"
kubectl apply -f "$BASE\02-pvcs.yaml"
kubectl apply -f "$OVERLAY\patch-pvcs-hostpath.yaml"

# ---------------------------------------------------------------------------
# 6. Secret (upsert)
# ---------------------------------------------------------------------------
Info "Creating/updating platform-secrets"
$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    $lines = $secrets.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
    [System.IO.File]::WriteAllLines($tmpFile, $lines, [System.Text.Encoding]::UTF8)
    kubectl create secret generic platform-secrets `
        --from-env-file=$tmpFile -n $NS --save-config `
        --dry-run=client -o yaml | kubectl apply -f -
    Ok "Secret applied"
} finally { Remove-Item $tmpFile -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------------------
# 7. neo4j-seed ConfigMap
# ---------------------------------------------------------------------------
Info "Creating neo4j-seed ConfigMap"
$seedFile = Join-Path $REPO "backend\scripts\neo4j_seed.cypher"
if (Test-Path $seedFile) {
    kubectl create configmap neo4j-seed `
        "--from-file=seed.cypher=$seedFile" `
        -n $NS --save-config --dry-run=client -o yaml | kubectl apply -f -
    Ok "neo4j-seed applied"
} else { Warn "neo4j_seed.cypher not found - skipping" }

# ---------------------------------------------------------------------------
# 8. Wave 1 — Data tier
# ---------------------------------------------------------------------------
Info "Wave 1 - Data tier (postgres, redis, neo4j)"
kubectl apply -f "$BASE\03-postgres.yaml"
kubectl apply -f "$BASE\04-redis.yaml"
kubectl apply -f "$BASE\05-neo4j.yaml"
Wait-PodReady "app=postgres" 180
Wait-PodReady "app=redis"    90
Wait-PodReady "app=neo4j"    420

# ---------------------------------------------------------------------------
# 9. Wave 2 — Backend  +  KinD image-pull patch
# ---------------------------------------------------------------------------
Info "Wave 2 - Backend"
Apply-Local "$BASE\06-backend.yaml"
Wait-PodReady "app=backend" 180

# ---------------------------------------------------------------------------
# 10. DB migrations + seed data
# ---------------------------------------------------------------------------
if (-not $SkipMigrations) {
    Info "Running Alembic migrations"
    $pod = kubectl get pod -l app=backend -n $NS -o jsonpath="{.items[0].metadata.name}"
    # Stamp to head if tables already exist but version table is empty (re-deploy over existing DB)
    $current = kubectl exec -n $NS $pod -- alembic -c /app/alembic.ini current 2>$null
    if (-not ($current -match "\w")) {
        Warn "No alembic version recorded - stamping to head (DB tables already present)"
        kubectl exec -n $NS $pod -- alembic -c /app/alembic.ini stamp head
    } else {
        kubectl exec -n $NS $pod -- alembic -c /app/alembic.ini upgrade head
    }
    Ok "Migrations complete"
    Info "Running setup_oob.py"
    kubectl exec -n $NS $pod -- python /app/setup_oob.py
    Ok "Seed data loaded"
} else { Warn "Skipping migrations (-SkipMigrations)" }

# ---------------------------------------------------------------------------
# 11. Wave 3 — Workers + Flower
# ---------------------------------------------------------------------------
Info "Wave 3 - Celery workers + Flower"
Apply-Local "$BASE\07-celery.yaml"
Apply-Local "$BASE\08-flower.yaml"

# ---------------------------------------------------------------------------
# 12. Wave 4 — Frontend + Nginx  +  LoadBalancer patch
# ---------------------------------------------------------------------------
Info "Wave 4 - Frontend + Nginx"
Apply-Local "$BASE\09-frontend.yaml"
Apply-Local "$BASE\10-nginx.yaml"
kubectl apply -f "$OVERLAY\patch-nginx-loadbalancer.yaml"
Wait-PodReady "app=nginx" 120

# ---------------------------------------------------------------------------
# 13. Wave 5 — Observability
# ---------------------------------------------------------------------------
Info "Wave 5 - Watcher + Sentinel + Backup"
Apply-Local "$BASE\11-watcher.yaml"
Apply-Local "$BASE\12-sentinel.yaml"
kubectl apply -f "$BASE\13-postgres-backup.yaml"

# ---------------------------------------------------------------------------
# 14. Force rollout restart (belt-and-suspenders after Apply-Local)
# ---------------------------------------------------------------------------
# Apply-Local already sets imagePullPolicy: Always so new pods pull from the
# local registry. This block also restarts any deployment that was already
# running at the same revision (Apply was a no-op) so it picks up the fresh
# image. On a first-run the deployments won't exist yet — silenced.
Info "Force rollout restart - ensuring pods pick up rebuilt images"
$deployments = @("backend","celery-worker","celery-default-worker","celery-beat","flower","frontend","nginx","watcher")
foreach ($d in $deployments) {
    kubectl rollout restart deployment/$d -n $NS 2>$null | Out-Null
    Ok "Restarted deployment/$d (or skipped - not yet deployed)"
}
# sentinel is a DaemonSet
kubectl rollout restart daemonset/sentinel -n $NS 2>$null | Out-Null
Ok "Restarted daemonset/sentinel (or skipped - not yet deployed)"
Wait-PodReady "app=watcher" 180

# ---------------------------------------------------------------------------
# 15. Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  KinD deployment complete" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
kubectl get pods -n $NS
Write-Host ""
Write-Host "Platform  : https://localhost" -ForegroundColor Green
Write-Host "Flower    : kubectl port-forward -n $NS svc/flower 5555:5555" -ForegroundColor Yellow
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  kubectl get pods -n $NS -w"
Write-Host "  kubectl logs -n $NS deploy/backend -f"
Write-Host "  kubectl exec -n $NS deploy/backend -it -- /bin/bash"
