# demo/scripts/stop_demo.ps1
# Stops only the backend/frontend processes started by start_demo.ps1.
# Uses PID files under demo/state and verifies the command line before killing.
# Returns a nonzero exit code and prints STOP INCOMPLETE if a launcher-owned
# process is still alive after the stop attempt.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo_common.ps1')

$RepoRoot = Get-RepoRoot
$StateDir = Join-Path (Join-Path $RepoRoot 'demo') 'state'
$BackendPidFile = Join-Path $StateDir 'backend.pid'
$FrontendPidFile = Join-Path $StateDir 'frontend.pid'

$LogFile = Initialize-DemoLog $RepoRoot 'stop'
$script:stopIncomplete = $false

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Stop"
Write-Host "  Log:        $LogFile"
Write-Host "=============================================================="

function Stop-FromPidFile {
    param([string]$PidFile, [string]$Label, [string]$Marker)

    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Info "$Label not started by this launcher (no PID file) - nothing to stop."
        return
    }

    $raw = (Get-Content -LiteralPath $PidFile -Raw | Out-String).Trim()
    if ($raw -notmatch '^\d+$') {
        Write-Info "$Label PID file is invalid - removing stale file."
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    $pidValue = [int]$raw

    # Verify the process actually belongs to this launcher before killing.
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        Write-Info "$Label (PID $pidValue) is not running - removing stale PID file."
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    if ($proc.CommandLine -notlike "*$Marker*") {
        Write-Warn "$Label (PID $pidValue) does not match launcher command '$Marker' - NOT killing to avoid harming an unrelated process."
        Write-Info "Removing stale PID file only."
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return
    }

    Write-Step "Stopping $Label (PID $pidValue)"
    & taskkill.exe /PID $pidValue /T /F 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500
    $stillRunning = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -ne $stillRunning) {
        Write-Fail "$Label is still running (PID $pidValue). Close its window manually if needed."
        $script:stopIncomplete = $true
    }
    else {
        Write-Ok "$Label stopped (process tree)."
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $StateDir)) {
    Write-Info "No demo state directory found - nothing was started by this launcher."
    Write-Host ""
    Write-Ok "NOTHING TO STOP"
    Write-Ok "Log written to: $LogFile"
    exit 0
}

Stop-FromPidFile $BackendPidFile 'Backend' 'uvicorn app.main:app'
Stop-FromPidFile $FrontendPidFile 'Frontend' 'npm.cmd run dev'

Write-Host ""
Write-Host "=============================================================="
if ($script:stopIncomplete) {
    Write-Host "  STOP INCOMPLETE - one or more processes could not be stopped." -ForegroundColor Red
    Write-Host "=============================================================="
    Write-Fail "Log written to: $LogFile"
    exit 1
}
else {
    Write-Host "  STOP COMPLETE" -ForegroundColor Green
    Write-Host "=============================================================="
    Write-Ok "Log written to: $LogFile"
    exit 0
}