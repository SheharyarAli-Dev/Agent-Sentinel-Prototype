# demo/scripts/start_demo.ps1
# Starts the Agent Sentinel demo: backend + frontend in separate windows,
# waits for BOTH backend health and frontend readiness, then opens the
# dashboard. Fails clearly if Vite exits early or never becomes ready.

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

$LogFile = Initialize-DemoLog $RepoRoot 'start'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Start"
Write-Host "  Repository: $RepoRoot"
Write-Host "  Log:        $LogFile"
Write-Host "=============================================================="

# 1. Verify installed components; prefer the existing venv interpreter.
Write-Step "Verifying installed components"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Fail "backend\.venv not found. Run setup_demo.bat first. Log: $LogFile"
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules'))) {
    Write-Fail "frontend\node_modules not found. Run setup_demo.bat first. Log: $LogFile"
    exit 1
}
$pyVer = (& $VenvPython --version 2>&1).Trim()
Write-Ok "backend\.venv found ($pyVer)."
Write-Ok "frontend\node_modules found."

# 2. Offline model mode ONLY when the MiniLM cache is actually complete.
Write-Step "MiniLM cache check"
$cacheCheck = Test-MiniLMCacheComplete (Get-MiniLMCacheDir (Get-HfHomePath))
if ($cacheCheck.Complete) {
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    Write-Ok "MiniLM cache complete - offline mode enabled ($($cacheCheck.Reason))."
}
else {
    Write-Warn "MiniLM cache is NOT complete ($($cacheCheck.Reason)) - offline mode stays OFF."
    Write-Info "Internet is required for the first model download; run setup_demo.bat once with internet access."
}

# 3. Prewarm the semantic model during backend startup so the dashboard stays
#    responsive on the first evaluation (opt-in preserved from the backend).
$env:AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL = '1'

# 4. Avoid duplicate processes when ports are already occupied.
$backendRunning = (Get-LivePortCount 8000) -gt 0
$frontendRunning = (Get-LivePortCount 5173) -gt 0

$feProc = $null
if ($backendRunning) {
    Write-Warn "Port 8000 is already in use - backend not started again."
}
else {
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
    Write-Step "Starting frontend (Vite dev server)"
    $feArgs = '/k title Agent Sentinel Frontend && npm.cmd run dev -- --host 127.0.0.1'
    $feProc = Start-Process cmd.exe -ArgumentList $feArgs -WorkingDirectory $FrontendDir -WindowStyle Normal -PassThru
    $feProc.Id | Set-Content -LiteralPath $FrontendPidFile -Encoding ascii
    Write-Ok "Frontend started (PID $($feProc.Id))."
}

# 5. Wait for backend /health with a bounded timeout.
Write-Step "Waiting for backend health at $HealthUrl"
if (Test-UrlReady -Uri $HealthUrl -TimeoutSeconds 90) {
    Write-Ok "Backend is healthy."
}
else {
    Write-Fail "Backend did not become healthy within 90 seconds. Log: $LogFile"
    Write-Info "Troubleshooting:"
    Write-Info "  - Check the Agent Sentinel Backend window for errors."
    Write-Info "  - Confirm nothing else already owns port 8000."
    Write-Info "  - Run verify_demo.bat for a full environment report."
    exit 1
}

# 6. Wait for frontend readiness; detect Vite exiting early.
Write-Step "Waiting for frontend readiness at $FrontendUrl"
$frontendReady = $false
$viteNode = $null
$deadline = (Get-Date).AddSeconds(90)
while (-not $frontendReady -and (Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $frontendReady = $true; break }
    }
    catch { }

    if (-not $frontendRunning) {
        $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*vite*' -or $_.CommandLine -like '*run dev*' })
        if ($null -eq $viteNode -and $procs.Count -gt 0) { $viteNode = $procs[0] }
        if ($null -ne $viteNode) {
            $still = Get-Process -Id ([int]$viteNode.ProcessId) -ErrorAction SilentlyContinue
            if ($null -eq $still) {
                Write-Fail "Vite exited before the frontend became ready. Log: $LogFile"
                Write-Info "Check the Agent Sentinel Frontend window for the server error, then re-run start_demo.bat."
                exit 1
            }
        }
    }
    Start-Sleep -Seconds 2
}

if ($frontendReady) {
    Write-Ok "Frontend is ready."
}
else {
    Write-Fail "Frontend did not become ready within 90 seconds. Log: $LogFile"
    Write-Info "Troubleshooting:"
    Write-Info "  - Check the Agent Sentinel Frontend window for errors."
    Write-Info "  - Confirm nothing else occupies port 5173 and that an existing Vite server is answering."
    Write-Info "  - Run verify_demo.bat for a full environment report."
    exit 1
}

# 7. Open the dashboard ONLY after both backend and frontend are ready.
Write-Step "Opening dashboard in your default browser"
Start-Process $FrontendUrl
Write-Ok "Dashboard opened: $FrontendUrl"

Write-Host ""
Write-Host "=============================================================="
Write-Host "  DEMO STARTED" -ForegroundColor Green
Write-Ok "Log written to: $LogFile"
Write-Host "=============================================================="
exit 0