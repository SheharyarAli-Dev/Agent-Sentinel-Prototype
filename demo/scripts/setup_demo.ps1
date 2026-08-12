# demo/scripts/setup_demo.ps1
# One-time setup for the Agent Sentinel demo.
# Safe: never deletes backend/.venv or frontend/node_modules.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo_common.ps1')

$RepoRoot = Get-RepoRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$VenvDir = Join-Path $BackendDir '.venv'
$VenvPython = Get-VenvPython $RepoRoot

$failCount = 0
$LogFile = Initialize-DemoLog $RepoRoot 'setup'

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Setup"
Write-Host "  Repository: $RepoRoot"
Write-Host "  Log:        $LogFile"
Write-Host "=============================================================="

# 1. Prerequisites. Git is OPTIONAL for an already-downloaded copy.
Write-Step "Checking prerequisites (git optional, node, npm)"
$prereqOk = $true
if (Test-CommandExists 'git') {
    Write-Ok "git available (used only to pull future updates)."
}
else {
    Write-Warn "git not found on PATH - not needed for this downloaded copy; install Git only if you want to pull future updates."
}

$nodeNpm = Get-NodeNpmInfo
if ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Fail "node not found on PATH - install Node.js 18+ (bundles npm 9+) from nodejs.org, then re-run."
    $prereqOk = $false
}
else {
    Write-Info "node        : $($nodeNpm.NodeText)"
    if ($null -eq (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Fail "npm not found on PATH - install Node.js 18+ (bundles npm 9+), then re-run."
        $prereqOk = $false
    }
    else {
        Write-Info "npm         : $($nodeNpm.NpmText)"
        $nodeCheck = Test-NodeCompatibility $nodeNpm.NodeMajor
        $npmCheck = Test-NpmCompatibility $nodeNpm.NpmMajor
        if ($nodeCheck.Status -eq 'pass') { Write-Ok $nodeCheck.Message } else { Write-Fail $nodeCheck.Message; $prereqOk = $false }
        if ($npmCheck.Status -eq 'pass')  { Write-Ok $npmCheck.Message }  else { Write-Fail $npmCheck.Message;  $prereqOk = $false }
    }
}
if (-not $prereqOk) {
    Write-Host ""
    Write-Fail "Install the required tools, then re-run setup_demo.bat. Log: $LogFile"
    exit 1
}

# 2. Create venv only if missing (never delete). Prefer an existing venv
#    interpreter; otherwise select py -3.12, then a real python, then py -3.
Write-Step "Checking backend virtual environment"
if (Test-Path -LiteralPath $VenvPython) {
    $pyVer = (& $VenvPython --version 2>&1).Trim()
    Write-Ok "backend\.venv already exists - kept intact ($pyVer)."
}
else {
    Write-Info "backend\.venv missing - selecting an interpreter to create it..."
    $discovered = Get-PythonInvocation -PreferVenvOrder
    if ($null -eq $discovered) {
        Write-Fail "No usable Python interpreter found. Install Python 3.12 (verified) from python.org, add it to PATH, then re-run."
        exit 1
    }
    $compat = Get-PythonCompatibility $discovered
    Write-Info ("Selected interpreter: {0} ({1})" -f $discovered.Command, $discovered.Source)
    Write-Info ("Version            : {0}" -f $discovered.Version)
    if ($compat.Status -eq 'fail') {
        Write-Fail $compat.Message
        Write-Host ""
        Write-Fail "Interpreter rejected - install Python 3.12 (verified) or 3.10-3.13 and re-run. Log: $LogFile"
        exit 1
    }
    if ($compat.Status -eq 'warn') { Write-Warn $compat.Message }

    Write-Info "Creating backend\.venv (first run)..."
    $venvArgs = @($discovered.Args)
    $venvOut = & $discovered.Command @venvArgs -m venv $VenvDir 2>&1
    $venvCode = $LASTEXITCODE
    if ($venvCode -ne 0) {
        $venvOut | ForEach-Object { Write-LogText ($_.ToString()) }
        Write-Fail "Failed to create the virtual environment (exit $venvCode)."
        Write-NativeTail $venvOut
        Write-Host ""
        Write-Fail "Install/repair the Python interpreter, then re-run. Log: $LogFile"
        exit 1
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Fail "Virtual environment created but python.exe is missing."
        exit 1
    }
    $pyVer = (& $VenvPython --version 2>&1).Trim()
    Write-Ok "backend\.venv created ($pyVer)."
}

# 3. Upgrade pip non-destructively (within the venv only).
Write-Step "Upgrading pip inside the virtual environment"
$pipUpOut = & $VenvPython -m pip install --upgrade pip 2>&1
$pipUpCode = $LASTEXITCODE
if ($pipUpCode -ne 0) {
    $pipUpOut | ForEach-Object { Write-LogText ($_.ToString()) }
    Write-Warn "pip upgrade failed (exit $pipUpCode) - continuing anyway."
}
else { Write-Ok "pip upgraded." }

# 4. Install backend requirements. Exit code is captured directly so a failed
#    command can never appear successful.
Write-Step "Installing backend requirements"
$pipOut = & $VenvPython -m pip install -r (Join-Path $BackendDir 'requirements.txt') 2>&1
$pipCode = $LASTEXITCODE
if ($pipCode -ne 0) {
    $pipOut | ForEach-Object { Write-LogText ($_.ToString()) }
    Write-Fail "pip install of requirements.txt failed (exit $pipCode)."
    Write-NativeTail $pipOut
    Write-Host ""
    Write-Fail "Fix the failing dependency (e.g. re-run with Python 3.12, the verified version), then re-run. Log: $LogFile"
    $failCount++
}
else { Write-Ok "Backend requirements installed." }

# 5. Runtime ML gate: torch + sentence-transformers MUST import in the venv.
Write-Step "Verifying ML imports (torch, sentence-transformers)"
if (-not (Test-VenvImports $VenvPython)) {
    Write-Fail "torch / sentence-transformers do not import inside backend\.venv - the backend cannot start."
    Write-Info "If a non-3.12 interpreter was used, its wheels may be incompatible; install Python 3.12 (verified) and re-run."
    $failCount++
}
else { Write-Ok "torch and sentence-transformers import cleanly." }

# 6. MiniLM model. First download needs internet; a complete cache skips it.
Write-Step "Downloading / verifying MiniLM model"
$cacheCheck = Test-MiniLMCacheComplete (Get-MiniLMCacheDir (Get-HfHomePath))
if ($cacheCheck.Complete) {
    Write-Ok "MiniLM cache already complete ($($cacheCheck.Reason))."
}
else {
    Write-Info "MiniLM cache is incomplete ($($cacheCheck.Reason)). Internet is required for the first model download."
    Write-Info "Downloading sentence-transformers/all-MiniLM-L6-v2 (may take a few minutes)..."
    $mlOut = & $VenvPython -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('MiniLM OK')" 2>&1
    $mlCode = $LASTEXITCODE
    if ($mlCode -ne 0) {
        $mlOut | ForEach-Object { Write-LogText ($_.ToString()) }
        Write-Fail "MiniLM model download/load failed (exit $mlCode); a working internet connection is required on first setup."
        Write-NativeTail $mlOut
        $failCount++
    }
    else {
        Write-Ok "MiniLM model downloaded and loaded."
    }
}

# 7. Backend test suite.
Write-Step "Running backend test suite"
Push-Location $BackendDir
try {
    $testOut = & $VenvPython -m pytest tests -q -p no:cacheprovider 2>&1
    $testCode = $LASTEXITCODE
    if ($testCode -ne 0) {
        $testOut | ForEach-Object { Write-LogText ($_.ToString()) }
        Write-Fail "Backend tests failed (exit $testCode)."
        Write-NativeTail $testOut
        $failCount++
    }
    else { Write-Ok "Backend tests passed." }
}
finally { Pop-Location }

# 8. Frontend dependencies via npm ci.
Write-Step "Installing frontend dependencies (npm ci)"
Push-Location $FrontendDir
try {
    $npmCiOut = & npm.cmd ci 2>&1
    $npmCiCode = $LASTEXITCODE
    if ($npmCiCode -ne 0) {
        $npmCiOut | ForEach-Object { Write-LogText ($_.ToString()) }
        Write-Fail "npm ci failed (exit $npmCiCode). Confirm Node 18+ and npm 9+ are on PATH, then re-run."
        Write-NativeTail $npmCiOut
        $failCount++
    }
    else { Write-Ok "Frontend dependencies installed." }
}
finally { Pop-Location }

# 9. Frontend production build.
Write-Step "Running frontend production build"
Push-Location $FrontendDir
try {
    $buildOut = & npm.cmd run build 2>&1
    $buildCode = $LASTEXITCODE
    if ($buildCode -ne 0) {
        $buildOut | ForEach-Object { Write-LogText ($_.ToString()) }
        Write-Fail "Frontend build failed (exit $buildCode)."
        Write-NativeTail $buildOut
        $failCount++
    }
    else { Write-Ok "Frontend build passed." }
}
finally { Pop-Location }

# 10. Final summary + exit code.
Write-Host ""
Write-Host "=============================================================="
if ($failCount -eq 0) {
    Write-Host "  SETUP PASSED" -ForegroundColor Green
    Write-Ok "Log written to: $LogFile"
    Write-Host "=============================================================="
    exit 0
}
else {
    Write-Host "  SETUP FAILED - $failCount step(s) failed." -ForegroundColor Red
    Write-Fail "Log written to: $LogFile"
    Write-Host "=============================================================="
    exit 1
}