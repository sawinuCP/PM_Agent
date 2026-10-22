# Local dev with Docker Postgres + Jira MCP; backend + frontend on the host.
# Usage (from repo root): .\scripts\start_local.ps1
#
# Requires: Docker, .venv with backend[dev], frontend npm install, backend/.env

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing $VenvPython — run: python -m venv .venv; .\.venv\Scripts\pip install -e backend[dev]"
}
if (-not (Test-Path (Join-Path $Backend ".env"))) {
    Write-Error "Missing backend/.env — copy backend/.env.example and fill keys."
}

Write-Host "==> Docker infra (postgres :5433, jira-mcp :8001)..."
Push-Location $Root
docker compose up -d postgres jira-mcp | Out-Null
Pop-Location

Write-Host "==> Waiting for Postgres healthy..."
for ($i = 0; $i -lt 30; $i++) {
    $hc = docker inspect -f "{{.State.Health.Status}}" azend-postgres-1 2>$null
    if ($hc -eq "healthy") { break }
    Start-Sleep -Seconds 2
}

Write-Host "==> Applying Alembic migrations (once, before API boot)..."
Push-Location $Backend
& $VenvPython -c "from app.db.migrate import run_alembic_upgrade; run_alembic_upgrade()"
Pop-Location

# Stop stale API processes on :8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Write-Host "==> Starting backend on http://127.0.0.1:8000 ..."
$backend = Start-Process -FilePath $VenvPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload" `
    -WorkingDirectory $Backend -PassThru -WindowStyle Normal

$backendUp = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $live = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health/live" -TimeoutSec 3
        if ($live.status -eq "ok") { $backendUp = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $backendUp) {
    Write-Error "Backend did not respond on :8000. Check backend/.env (DATABASE_URL, API keys)."
}

try {
    $rdy = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health/ready" -TimeoutSec 15
    Write-Host "==> Ready:" ($rdy | ConvertTo-Json -Compress)
} catch {
    Write-Warning "Readiness check: $_"
}

Write-Host ""
Write-Host "Backend PID $($backend.Id)  ->  http://127.0.0.1:8000/docs"
Write-Host "Starting frontend         ->  http://127.0.0.1:5173  (proxies /api -> backend)"
Write-Host "Stop backend: Stop-Process -Id $($backend.Id)"
Write-Host ""

Push-Location $Frontend
npm run dev
Pop-Location

Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
