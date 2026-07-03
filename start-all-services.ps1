# Agentic Platform - Complete Service Startup Script (PowerShell)
# Starts all essential services in proper dependency order

param(
    [switch]$CleanStart = $false
)

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
Set-Location $scriptDir

function Log-Info($msg)   { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Log-OK($msg)     { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Log-Warn($msg)   { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Log-Err($msg)    { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Log-Header($msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor White; Write-Host "" }

function Wait-ForContainer($containerName, $maxAttempts, $noHealthCheck = $false) {
    Log-Info "Waiting for $containerName..."
    for ($i = 1; $i -le $maxAttempts; $i++) {
        $status = docker ps --filter "name=^/$containerName$" --format "{{.Status}}" 2>$null
        if ($status -like "*healthy*") {
            Log-OK "$containerName is healthy"
            return $true
        }
        if ($noHealthCheck -and $status -like "*Up*") {
            Log-OK "$containerName is running"
            return $true
        }
        if ($status -like "*Up*") {
            Write-Host -NoNewline "." -ForegroundColor Gray
        }
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    if ($status) {
        Log-Warn "$containerName status: $status"
        return $true
    }
    Log-Err "$containerName did not start in time"
    return $false
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " Agentic Platform - Service Startup"      -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Directory: $scriptDir"

# Verify docker-compose
$null = docker-compose --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Log-Err "docker-compose not found in PATH"
    exit 1
}

# Tear down
if ($CleanStart) {
    Log-Info "Clean start: removing containers and volumes..."
    docker-compose down -v 2>$null | Out-Null
} else {
    Log-Info "Stopping existing containers..."
    docker-compose down 2>$null | Out-Null
}
Start-Sleep -Seconds 2

Log-Header "LAYER 1: Core Databases"
Log-Info "Starting postgres, redis, neo4j..."
docker-compose up -d postgres redis neo4j 2>$null | Out-Null
Wait-ForContainer "agentic_os_postgres" 40
Wait-ForContainer "agentic_os_redis"    30
Wait-ForContainer "agentic_os_neo4j"    60

Log-Header "LAYER 2: Backend"
Log-Info "Starting backend..."
docker-compose up -d backend 2>$null | Out-Null
Wait-ForContainer "agentic_os_backend" 60

Log-Header "LAYER 3: Workers & Scheduler"
Log-Info "Starting celery_worker, celery_default_worker, celery_beat, and flower..."
docker-compose up -d celery_worker celery_default_worker celery_beat flower 2>$null | Out-Null
Wait-ForContainer "agentic_os_celery_worker"         30
Wait-ForContainer "agentic_os_celery_default_worker" 30
Wait-ForContainer "agentic_os_celery_beat"           15
Wait-ForContainer "agentic_os_flower"                15 $true

Log-Header "LAYER 4: Monitoring"
Log-Info "Starting sentinel and watcher..."
docker-compose up -d sentinel watcher 2>$null | Out-Null
Wait-ForContainer "sentinel_senses" 30
Wait-ForContainer "watcher_brain"   15 $true

Log-Header "LAYER 5: Frontend and Nginx"
Log-Info "Starting frontend and nginx..."
docker-compose up -d frontend nginx 2>$null | Out-Null
Wait-ForContainer "agentic_os_frontend" 15 $true
Wait-ForContainer "agentic_os_nginx"    20

Log-Header "All Services Status"
docker-compose ps

Log-Header "Access Points"
Write-Host "  Frontend (Nginx):  http://localhost"              -ForegroundColor Green
Write-Host "  Frontend (Vite):   http://localhost:3000"         -ForegroundColor Green
Write-Host "  Backend API:       http://localhost:8000"         -ForegroundColor Green
Write-Host "  API Docs:          http://localhost:8000/api/docs" -ForegroundColor Green
Write-Host "  Flower:            http://localhost:5555"         -ForegroundColor Green
Write-Host "  Neo4j Browser:     http://localhost:7474"         -ForegroundColor Green

Log-Header "API Readiness Check"
Start-Sleep -Seconds 3

try {
    $resp = Invoke-WebRequest -Uri "http://localhost:8000/api/ready" -UseBasicParsing -TimeoutSec 5
    $json = $resp.Content | ConvertFrom-Json
    $apiStatus = $json.status
    if ($apiStatus -eq "healthy") {
        Log-OK "API is HEALTHY"
    } elseif ($apiStatus -eq "degraded") {
        Log-Warn "API is DEGRADED - failed components:"
        $json.checks.PSObject.Properties | ForEach-Object {
            $check = $_.Value
            $checkStatus = $check.status
            if ($checkStatus -notin @("connected","accessible","initialized","ready","available","loaded")) {
                Log-Warn "  $($_.Name): $checkStatus - $($check.error)"
            }
        }
    } else {
        Log-Warn "API status: $apiStatus"
    }
} catch {
    Log-Warn "Could not reach API - may still be starting up"
}

Write-Host ""
Write-Host "Monitor logs:  docker-compose logs -f" -ForegroundColor Gray
Write-Host "Stop all:      docker-compose down"    -ForegroundColor Gray
Write-Host ""
