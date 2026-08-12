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

Write-Host "=============================================================="
Write-Host "  Agent Sentinel Demo - Setup"
Write-Host "  Repository: $RepoRoot"
Write-Host "=============================================================="

# 1 + 3. Root already resolved; prerequisites check.
Write-Step "Checking prerequisites (git, python, npm)"
$prereqOk = $true
if (-not (Test-CommandExists 'git')) { Write-Fail "git not found on PATH."; $prereqOk = $false }
if (-not (Test-CommandExists 'python')) { Write-Fail "python not found on PATH."; $prereqOk = $false }
if (-not (Test-CommandExists 'npm')) { Write-Fail "npm not found on PATH."; $prereqOk = $false }
if (-not $prereqOk) {
    Write-Host ""
    Write-Fail "Install the missing tools, then re-run setup_demo.bat."
    exit 1
}
Write-Ok "git, python, npm all available."

# 4. Create venv only if missing (never delete).
Write-Step "Checking backend virtual environment"
if (Test-Path -LiteralPath $VenvPython) {
    Write-Ok "backend\.venv already exists - kept intact."
} else {
    Write-Info "Creating backend\.venv (first run)..."
    & python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to create virtual environment."; exit 1 }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Fail "Virtual environment created but python.exe is missing."
        exit 1
    }
    Write-Ok "backend\.venv created."
}

# 5. Activate the virtual environment (scoped to this process).
& (Join-Path $VenvDir 'Scripts\Activate.ps1')

# 6. Upgrade pip non-destructively (within the venv only).
Write-Step "Upgrading pip inside the virtual environment"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Warn "pip upgrade failed - continuing anyway." }
else { Write-Ok "pip upgraded." }

# 7. Install backend requirements.
Write-Step "Installing backend requirements"
& $VenvPython -m pip install -r (Join-Path $BackendDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install of requirements.txt failed."; $failCount++ }
else { Write-Ok "Backend requirements installed." }

# 8. Frontend dependencies via npm ci.
Write-Step "Installing frontend dependencies (npm ci)"
Push-Location $FrontendDir
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { Write-Fail "npm ci failed."; $failCount++ }
    else { Write-Ok "Frontend dependencies installed." }
}
finally { Pop-Location }

# 9. Download + verify the MiniLM model (first run downloads ~90 MB).
Write-Step "Downloading / verifying MiniLM model"
Write-Info "First run downloads sentence-transformers/all-MiniLM-L6-v2 (may take a few minutes)."
& $VenvPython -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('MiniLM OK')"
if ($LASTEXITCODE -ne 0) { Write-Fail "MiniLM model download/load failed."; $failCount++ }
else { Write-Ok "MiniLM model available." }

# 10. Backend test suite.
Write-Step "Running backend test suite"
Push-Location $BackendDir
try {
    & $VenvPython -m pytest tests -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Write-Fail "Backend tests failed."; $failCount++ }
    else { Write-Ok "Backend tests passed." }
}
finally { Pop-Location }

# 11. Frontend production build.
Write-Step "Running frontend production build"
Push-Location $FrontendDir
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Write-Fail "Frontend build failed."; $failCount++ }
    else { Write-Ok "Frontend build passed." }
}
finally { Pop-Location }

# 12 + 13. Final summary + exit code.
Write-Host ""
Write-Host "=============================================================="
if ($failCount -eq 0) {
    Write-Host "  SETUP PASSED" -ForegroundColor Green
    Write-Host "=============================================================="
    exit 0
}
else {
    Write-Host "  SETUP FAILED - $failCount step(s) failed." -ForegroundColor Red
    Write-Host "=============================================================="
    exit 1
}
