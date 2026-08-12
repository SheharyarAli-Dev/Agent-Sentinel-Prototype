# demo/scripts/verify_demo.ps1
# Reports the full environment status for the Agent Sentinel demo.
# Does NOT modify repository state (no git changes; tests/build run read-only-ish).
# torch import is readiness-blocking: the backend and Sentence Transformers
# depend on it, so if torch cannot import inside backend/.venv the demo is
# NOT READY.

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'demo_common.ps1')

$RepoRoot = Get-RepoRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$VenvPython = Get-VenvPython $RepoRoot

$ready = $true
$LogFile = Initialize-DemoLog $RepoRoot 'verify'

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Environment Verification"
Write-Host "  Repository: $RepoRoot"
Write-Host "  Log:        $LogFile"
Write-Host "=============================================================="

# ---- Git (optional for a downloaded copy; never affects readiness) ----
Write-Step "Git status"
if (Test-CommandExists 'git') {
    Push-Location $RepoRoot
    try {
        $branch = (& git rev-parse --abbrev-ref HEAD 2>&1).Trim()
        $commit = (& git log -1 --oneline 2>&1).Trim()
        $clean = @(& git status --porcelain 2>&1)
        Write-Info "Branch        : $branch"
        Write-Info "Latest commit : $commit"
        if ($clean.Count -eq 0) {
            Write-Ok "Working tree is clean."
        }
        else {
            Write-Warn "Working tree has changes ($($clean.Count) item(s)):"
            $clean | ForEach-Object { Write-Info "  $_" }
        }
    }
    finally { Pop-Location }
}
else {
    Write-Warn "git not found on PATH - not required for this downloaded copy; Git is needed only to pull future updates."
}

# ---- Python (setup-time interpreter selection + venv) ----
Write-Step "Python"
$found = Get-PythonInvocation -PreferVenvOrder
if ($null -ne $found) {
    $compat = Get-PythonCompatibility $found
    Write-Info ("Setup interpreter: {0} ({1})" -f $found.Command, $found.Source)
    Write-Info ("Version          : {0}" -f $found.Version)
    if ($compat.Status -eq 'ok')   { Write-Ok $compat.Message }
    if ($compat.Status -eq 'warn') { Write-Warn $compat.Message }
    if ($compat.Status -eq 'fail') { Write-Fail $compat.Message; $ready = $false }
}
else {
    Write-Fail "No usable Python interpreter found for a future setup. Install Python 3.12 (verified) from python.org."
    $ready = $false
}

if (Test-Path -LiteralPath $VenvPython) {
    $pyVer = (& $VenvPython --version 2>&1).Trim()
    Write-Ok "Venv python  : $pyVer"
}
else {
    Write-Fail "backend\.venv not found - run setup_demo.bat first."
    $ready = $false
}

# ---- Node / npm ----
Write-Step "Node.js / npm"
if ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Fail "node not found on PATH - install Node.js 18+ (bundles npm 9+)."
    $ready = $false
}
else {
    $nodeNpm = Get-NodeNpmInfo
    Write-Info "node         : $($nodeNpm.NodeText)"
    if ($null -eq (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Fail "npm not found on PATH - install Node.js 18+ (bundles npm 9+)."
        $ready = $false
    }
    else {
        Write-Info "npm          : $($nodeNpm.NpmText)"
        $nodeCheck = Test-NodeCompatibility $nodeNpm.NodeMajor
        $npmCheck = Test-NpmCompatibility $nodeNpm.NpmMajor
        if ($nodeCheck.Status -eq 'pass') { Write-Ok $nodeCheck.Message } else { Write-Fail $nodeCheck.Message; $ready = $false }
        if ($npmCheck.Status -eq 'pass')  { Write-Ok $npmCheck.Message }  else { Write-Fail $npmCheck.Message;  $ready = $false }
    }
}

# ---- torch import (readiness-blocking) ----
Write-Step "torch"
if (Test-Path -LiteralPath $VenvPython) {
    $thOut = (& $VenvPython -c "import torch; print(torch.__version__)" 2>&1)
    $thCode = $LASTEXITCODE
    if ($thCode -eq 0) {
        Write-Ok "torch        : $(($thOut | Out-String).Trim())"
    }
    else {
        Write-Fail "torch does not import inside backend\.venv - the backend cannot start. NOT READY."
        $thOut | Select-Object -Last 5 | ForEach-Object { Write-Info ("  " + $_.ToString().Trim()) }
        $ready = $false
    }
}
else { Write-Fail "venv missing - cannot check torch."; $ready = $false }

# ---- sentence-transformers ----
Write-Step "sentence-transformers"
if (Test-Path -LiteralPath $VenvPython) {
    $stOut = (& $VenvPython -c "import sentence_transformers; print(sentence_transformers.__version__)" 2>&1)
    $stCode = $LASTEXITCODE
    if ($stCode -eq 0) {
        Write-Ok "version      : $(($stOut | Out-String).Trim())"
    }
    else {
        Write-Fail "sentence-transformers not importable in venv ($(($stOut | Out-String).Trim()))"
        $ready = $false
    }
}
else { Write-Fail "venv missing - cannot check."; $ready = $false }

# ---- MiniLM: cache completeness + REAL offline load (final authority) ----
Write-Step "MiniLM model (cache completeness + offline load)"
$ModelCache = Get-MiniLMCacheDir (Get-HfHomePath)
$cacheCheck = Test-MiniLMCacheComplete $ModelCache
if (-not $cacheCheck.Complete) {
    Write-Fail "MiniLM cache is incomplete ($($cacheCheck.Reason)) - internet is required for the first model download."
    Write-Info "Run setup_demo.bat once with internet access so the model downloads completely."
    $ready = $false
}
elseif (Test-Path -LiteralPath $VenvPython) {
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    # stderr holds only expected noise (weight-loading progress bars, library
    # warnings). Redirect it to a temp file so it never surfaces as a PowerShell
    # NativeCommandError; a genuine Python failure is still detected via exit code.
    $mlErrFile = Join-Path $env:TEMP ("verify_minilm_err_{0}.txt" -f [guid]::NewGuid().ToString('N'))
    $mlOut = (& $VenvPython -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('load-ok')" 2> $mlErrFile)
    $mlCode = $LASTEXITCODE
    if ($mlCode -eq 0) {
        Write-Ok "Cache looks complete ($($cacheCheck.Reason)) and the model loads offline from cache."
    }
    else {
        $mlErrText = if (Test-Path -LiteralPath $mlErrFile) { (Get-Content -LiteralPath $mlErrFile -Raw).Trim() } else { ($mlOut | Out-String).Trim() }
        Write-Fail "cache appears complete but the model failed to load offline: $mlErrText"
        $ready = $false
    }
    Remove-Item -LiteralPath $mlErrFile -Force -ErrorAction SilentlyContinue
}
else { Write-Fail "venv missing - cannot verify offline load."; $ready = $false }

# ---- Backend tests ----
Write-Step "Backend test suite"
if (Test-Path -LiteralPath $VenvPython) {
    Push-Location $BackendDir
    try {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $testOut = (& $VenvPython -m pytest tests -q -p no:cacheprovider 2>&1)
        $testCode = $LASTEXITCODE
        $testOut | ForEach-Object { Write-LogText ($_.ToString()) }
        if ($testCode -eq 0) {
            $tail = ($testOut | Select-Object -Last 1).Trim()
            Write-Ok "pytest       : $tail"
        }
        else {
            Write-Fail "backend tests FAILED."
            $testOut | Select-Object -Last 15 | ForEach-Object { Write-Info ("  " + $_.ToString().Trim()) }
            $ready = $false
        }
    }
    finally { Pop-Location }
}
else { Write-Fail "venv missing - cannot run tests."; $ready = $false }

# ---- Frontend build ----
Write-Step "Frontend production build"
if (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules')) {
    Push-Location $FrontendDir
    try {
        $buildOut = (& npm.cmd run build 2>&1)
        $buildCode = $LASTEXITCODE
        $buildOut | ForEach-Object { Write-LogText ($_.ToString()) }
        if ($buildCode -eq 0) {
            Write-Ok "npm run build passed."
        }
        else {
            Write-Fail "npm run build FAILED."
            $buildOut | Select-Object -Last 15 | ForEach-Object { Write-Info ("  " + $_.ToString().Trim()) }
            $ready = $false
        }
    }
    finally { Pop-Location }
}
else { Write-Fail "frontend\node_modules not found - run setup_demo.bat first."; $ready = $false }

# ---- Ports with ownership ----
Write-Step "Ports 8000 (backend) / 5173 (frontend)"
$beOwner = Get-PortOwnerInfo 8000
$feOwner = Get-PortOwnerInfo 5173
if ($beOwner.InUse) { Write-Info "Port 8000   : OCCUPIED by $($beOwner.ProcessName) (PID $($beOwner.Pid))" }
else { Write-Info "Port 8000   : available" }
if ($feOwner.InUse) { Write-Info "Port 5173   : OCCUPIED by $($feOwner.ProcessName) (PID $($feOwner.Pid))" }
else { Write-Info "Port 5173   : available" }

# ---- Final result ----
Write-Host ""
Write-Host "=============================================================="
if ($ready) {
    Write-Host "  READY - the demo can be started with start_demo.bat" -ForegroundColor Green
    Write-Ok "Log written to: $LogFile"
    Write-Host "=============================================================="
    exit 0
}
else {
    Write-Host "  NOT READY - review the FAIL lines above" -ForegroundColor Red
    Write-Fail "Log written to: $LogFile"
    Write-Host "=============================================================="
    exit 1
}