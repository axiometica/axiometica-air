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
$KIND_NODE = "desktop-control-plane"

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

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Info "Preflight checks"
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) { Fail "kubectl not found in PATH" }

$ctx = kubectl config current-context 2>$null
if ($ctx -ne "docker-desktop") {
    Warn "Current context is '$ctx', not 'docker-desktop'"
    $ans = Read-Host "Switch to docker-desktop? [y/N]"
    if ($ans -eq "y") { kubectl config use-context docker-desktop }
    else              { Fail "Aborting — wrong context" }
}
Ok "Context: docker-desktop"

# ---------------------------------------------------------------------------
# 1. Build images + load into KinD containerd
# ---------------------------------------------------------------------------
# PowerShell pipes are text-mode and corrupt binary tar streams.
# Git Bash handles binary pipes correctly — never use WSL bash here.
function Find-GitBash {
    foreach ($p in @(
        "C:\Program Files\Git\bin\bash.exe",
        "C:\Program Files (x86)\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $p = Join-Path (Split-Path (Split-Path $git.Source -Parent) -Parent) "bin\bash.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Load-Image {
    param([string]$imageName)
    Info "Loading $imageName → KinD containerd"
    $bash = Find-GitBash
    if (-not $bash) { Fail "Git Bash not found. Install Git for Windows or load images manually." }
    & $bash -c "docker save '$imageName' | docker exec -i $KIND_NODE ctr --namespace=k8s.io images import -"
    if ($LASTEXITCODE -ne 0) { Fail "Failed to load $imageName into KinD" }
    Ok "$imageName loaded"
}

if (-not $SkipBuild) {
    Info "Building images via docker compose"
    Set-Location $REPO
    docker compose build backend celery_worker celery_default_worker celery_beat
    docker compose build frontend
    docker compose build nginx
    docker compose build watcher
    Ok "Images built"

    Info "Loading images into KinD containerd (node: $KIND_NODE)"
    Load-Image "agenticplatform_v2-backend:latest"
    Load-Image "agenticplatform_v2-frontend:latest"
    Load-Image "agenticplatform_v2-nginx:latest"
    Load-Image "agenticplatform_v2-watcher:latest"
    Ok "All images loaded"
} else {
    Warn "Skipping image build (-SkipBuild)"
    Warn "If pods show ErrImageNeverPull, load manually:"
    Warn "  bash -c `"docker save <image> | docker exec -i $KIND_NODE ctr --namespace=k8s.io images import -`""
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
} else { Warn "neo4j_seed.cypher not found — skipping" }

# ---------------------------------------------------------------------------
# 8. Wave 1 — Data tier
# ---------------------------------------------------------------------------
Info "Wave 1 — Data tier (postgres, redis, neo4j)"
kubectl apply -f "$BASE\03-postgres.yaml"
kubectl apply -f "$BASE\04-redis.yaml"
kubectl apply -f "$BASE\05-neo4j.yaml"
Wait-PodReady "app=postgres" 180
Wait-PodReady "app=redis"    90
Wait-PodReady "app=neo4j"    420

# ---------------------------------------------------------------------------
# 9. Wave 2 — Backend  +  KinD image-pull patch
# ---------------------------------------------------------------------------
Info "Wave 2 — Backend"
kubectl apply -f "$BASE\06-backend.yaml"
kubectl apply -f "$OVERLAY\patch-image-pull-never.yaml"
Wait-PodReady "app=backend" 180

# ---------------------------------------------------------------------------
# 10. DB migrations + seed data
# ---------------------------------------------------------------------------
if (-not $SkipMigrations) {
    Info "Running Alembic migrations"
    $pod = kubectl get pod -l app=backend -n $NS -o jsonpath="{.items[0].metadata.name}"
    kubectl exec -n $NS $pod -- alembic -c /app/src/agentic_os/alembic.ini upgrade head
    Ok "Migrations complete"
    Info "Running setup_oob.py"
    kubectl exec -n $NS $pod -- python /app/setup_oob.py
    Ok "Seed data loaded"
} else { Warn "Skipping migrations (-SkipMigrations)" }

# ---------------------------------------------------------------------------
# 11. Wave 3 — Workers + Flower
# ---------------------------------------------------------------------------
Info "Wave 3 — Celery workers + Flower"
kubectl apply -f "$BASE\07-celery.yaml"
kubectl apply -f "$BASE\08-flower.yaml"

# ---------------------------------------------------------------------------
# 12. Wave 4 — Frontend + Nginx  +  LoadBalancer patch
# ---------------------------------------------------------------------------
Info "Wave 4 — Frontend + Nginx"
kubectl apply -f "$BASE\09-frontend.yaml"
kubectl apply -f "$BASE\10-nginx.yaml"
kubectl apply -f "$OVERLAY\patch-nginx-loadbalancer.yaml"
Wait-PodReady "app=nginx" 120

# ---------------------------------------------------------------------------
# 13. Wave 5 — Observability
# ---------------------------------------------------------------------------
Info "Wave 5 — Watcher + Sentinel + Backup"
kubectl apply -f "$BASE\11-watcher.yaml"
kubectl apply -f "$BASE\12-sentinel.yaml"
kubectl apply -f "$BASE\13-postgres-backup.yaml"

# ---------------------------------------------------------------------------
# 14. Summary
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
