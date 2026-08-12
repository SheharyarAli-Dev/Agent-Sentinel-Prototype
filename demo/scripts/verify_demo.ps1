# demo/scripts/verify_demo.ps1
# Reports the full environment status for the Agent Sentinel demo.
# Does NOT modify repository state (no git changes; tests/build run read-only-ish).

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'demo_common.ps1')

$RepoRoot = Get-RepoRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$VenvPython = Get-VenvPython $RepoRoot

$ready = $true

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Environment Verification"
Write-Host "  Repository: $RepoRoot"
Write-Host "=============================================================="

# ---- Git branch + latest commit + working tree ----
Write-Step "Git status"
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

# ---- Python ----
Write-Step "Python"
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
if (Test-CommandExists 'node') {
    $nodeVer = (& node --version 2>&1).Trim()
    Write-Ok "node         : $nodeVer"
}
else { Write-Fail "node not found on PATH."; $ready = $false }
if (Test-CommandExists 'npm') {
    $npmVer = (& npm --version 2>&1).Trim()
    Write-Ok "npm          : $npmVer"
}
else { Write-Fail "npm not found on PATH."; $ready = $false }

# ---- sentence-transformers ----
Write-Step "sentence-transformers"
if (Test-Path -LiteralPath $VenvPython) {
    $stOut = (& $VenvPython -c "import sentence_transformers; print(sentence_transformers.__version__)" 2>&1)
    if ($LASTEXITCODE -eq 0) { Write-Ok "version      : $((($stOut | Out-String).Trim()))" }
    else { Write-Fail "not importable in venv ($(($stOut | Out-String).Trim()))"; $ready = $false }
}
else { Write-Fail "venv missing - cannot check."; $ready = $false }

# ---- MiniLM load from cache ----
Write-Step "MiniLM model (from cache)"
$HfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE '.cache\huggingface' }
$ModelCache = Join-Path $HfHome 'hub\models--sentence-transformers--all-MiniLM-L6-v2'
if (-not (Test-Path -LiteralPath $ModelCache)) {
    Write-Fail "MiniLM not cached - run setup_demo.bat once to download it."
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
        Write-Ok "MiniLM loads offline from cache."
    }
    else {
        $mlErrText = if (Test-Path -LiteralPath $mlErrFile) { (Get-Content -LiteralPath $mlErrFile -Raw).Trim() } else { $mlOut }
        Write-Fail "cache exists but model failed to load offline: $mlErrText"
        $ready = $false
    }
    Remove-Item -LiteralPath $mlErrFile -Force -ErrorAction SilentlyContinue
}
else { Write-Fail "venv missing - cannot check."; $ready = $false }

# ---- Backend tests ----
Write-Step "Backend test suite"
if (Test-Path -LiteralPath $VenvPython) {
    Push-Location $BackendDir
    try {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $testOut = (& $VenvPython -m pytest tests -q -p no:cacheprovider 2>&1)
        if ($LASTEXITCODE -eq 0) {
            $tail = ($testOut | Select-Object -Last 1).Trim()
            Write-Ok "pytest       : $tail"
        }
        else {
            Write-Fail "backend tests FAILED."
            $testOut | Select-Object -Last 15 | ForEach-Object { Write-Info "  $_" }
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
        if ($LASTEXITCODE -eq 0) { Write-Ok "npm run build passed." }
        else {
            Write-Fail "npm run build FAILED."
            $buildOut | Select-Object -Last 15 | ForEach-Object { Write-Info "  $_" }
            $ready = $false
        }
    }
    finally { Pop-Location }
}
else { Write-Fail "frontend\node_modules not found - run setup_demo.bat first."; $ready = $false }

# ---- Ports ----
Write-Step "Ports 8000 (backend) / 5173 (frontend)"
if ((Get-LivePortCount 8000) -gt 0) { Write-Info "Port 8000   : OCCUPIED" }
else { Write-Info "Port 8000   : available" }
if ((Get-LivePortCount 5173) -gt 0) { Write-Info "Port 5173   : OCCUPIED" }
else { Write-Info "Port 5173   : available" }

# ---- Final result ----
Write-Host ""
Write-Host "=============================================================="
if ($ready) {
    Write-Host "  READY - the demo can be started with start_demo.bat" -ForegroundColor Green
    Write-Host "=============================================================="
    exit 0
}
else {
    Write-Host "  NOT READY - review the FAIL lines above" -ForegroundColor Red
    Write-Host "=============================================================="
    exit 1
}
