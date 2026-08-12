# demo/scripts/start_demo.ps1
# Starts the Agent Sentinel demo: backend + frontend in separate windows,
# waits for backend health, then opens the dashboard.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo_common.ps1')

$RepoRoot = Get-RepoRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$VenvPython = Get-VenvPython $RepoRoot
$StateDir = Join-Path (Join-Path $RepoRoot 'demo') 'state'
$BackendPidFile = Join-Path $StateDir 'backend.pid'
$FrontendPidFile = Join-Path $StateDir 'frontend.pid'
$HealthUrl = 'http://127.0.0.1:8000/health'
$FrontendUrl = 'http://127.0.0.1:5173/'

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Start"
Write-Host "  Repository: $RepoRoot"
Write-Host "=============================================================="

# 2 + 3. Verify prerequisites.
Write-Step "Verifying installed components"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Fail "backend\.venv not found. Run setup_demo.bat first."
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules'))) {
    Write-Fail "frontend\node_modules not found. Run setup_demo.bat first."
    exit 1
}
Write-Ok "backend\.venv and frontend\node_modules found."

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

# 7. Offline model mode when the MiniLM cache is present.
$HfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE '.cache\huggingface' }
$ModelCache = Join-Path $HfHome 'hub\models--sentence-transformers--all-MiniLM-L6-v2'
if (Test-Path -LiteralPath $ModelCache) {
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    Write-Ok "MiniLM model cache found - offline mode enabled."
}
else {
    Write-Warn "MiniLM model cache not found - backend will download it on first use."
}

# 12. Prewarm the semantic model during backend startup so the dashboard stays
# responsive on the first evaluation. The backend only prewarms when this opt-in
# env var is set; both spawned processes inherit it, and the browser is opened
# only after /health is ready (which implies startup + prewarm finished).
$env:AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL = '1'

# 11. Avoid duplicate processes when ports are already occupied.
$backendRunning = (Get-LivePortCount 8000) -gt 0
$frontendRunning = (Get-LivePortCount 5173) -gt 0

if ($backendRunning) {
    Write-Warn "Port 8000 is already in use - backend not started again."
}
else {
    # 4 + 6. Backend in its own window, no --reload.
    Write-Step "Starting backend (uvicorn, no --reload)"
    $beArgs = '/k title Agent Sentinel Backend && "' + $VenvPython + '" -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
    $beProc = Start-Process cmd.exe -ArgumentList $beArgs -WorkingDirectory $BackendDir -WindowStyle Normal -PassThru
    $beProc.Id | Set-Content -LiteralPath $BackendPidFile -Encoding ascii
    Write-Ok "Backend started (PID $($beProc.Id))."
}

if ($frontendRunning) {
    Write-Warn "Port 5173 is already in use - frontend not started again."
}
else {
    # 5. Frontend in its own window.
    Write-Step "Starting frontend (Vite dev server)"
    $feArgs = '/k title Agent Sentinel Frontend && npm.cmd run dev -- --host 127.0.0.1'
    $feProc = Start-Process cmd.exe -ArgumentList $feArgs -WorkingDirectory $FrontendDir -WindowStyle Normal -PassThru
    $feProc.Id | Set-Content -LiteralPath $FrontendPidFile -Encoding ascii
    Write-Ok "Frontend started (PID $($feProc.Id))."
}

# 8. Wait for backend /health with a bounded timeout.
Write-Step "Waiting for backend health at $HealthUrl"
$deadline = (Get-Date).AddSeconds(90)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    }
    catch { Start-Sleep -Seconds 2 }
}
if ($healthy) {
    Write-Ok "Backend is healthy."
}
else {
    # 10. Troubleshooting output.
    Write-Fail "Backend did not become healthy within 90 seconds."
    Write-Info "Troubleshooting:"
    Write-Info "  - Check the Agent Sentinel Backend window for errors."
    Write-Info "  - Confirm nothing else already owns port 8000."
    Write-Info "  - Run verify_demo.bat for a full environment report."
    exit 1
}

# 9. Open the dashboard in the default browser.
Write-Step "Opening dashboard in your default browser"
Start-Process $FrontendUrl
Write-Ok "Dashboard opened: $FrontendUrl"

Write-Host ""
Write-Host "=============================================================="
Write-Host "  DEMO STARTED" -ForegroundColor Green
Write-Host "=============================================================="
exit 0
