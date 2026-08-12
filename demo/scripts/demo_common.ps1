# demo/scripts/demo_common.ps1
# Shared helpers for the Agent Sentinel demo launcher.
# Dot-sourced by the demo_*.ps1 scripts. Works from any installation path.

# -- Paths & tooling -----------------------------------------------------------

function Get-RepoRoot {
    # This file lives at <repo>/demo/scripts/demo_common.ps1
    return Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Get-VenvPython {
    param([string]$RepoRoot)
    return Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
}

function Test-CommandExists {
    param([string]$Name)
    return ($null -ne (Get-Command $Name -ErrorAction SilentlyContinue))
}

# -- Logging -------------------------------------------------------------------

function Initialize-DemoLog {
    param([string]$RepoRoot, [string]$Name)
    $LogDir = Join-Path (Join-Path $RepoRoot 'demo') 'logs'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $file = Join-Path $LogDir ("{0}_{1}.log" -f $Name, $stamp)
    $script:DemoLogFile = $file
    ("==== Agent Sentinel demo {0} log ====" -f $Name) | Add-Content -LiteralPath $file -Encoding utf8
    return $file
}

function Write-LogText {
    param([string]$Text)
    if ($script:DemoLogFile) { Add-Content -LiteralPath $script:DemoLogFile -Encoding utf8 $Text }
}

# Console helpers that also append to the active demo log (never secrets).
function Write-Step { param([string]$Msg) Write-Host ""; Write-Host ("[STEP] " + $Msg) -ForegroundColor Cyan; Write-LogText ("[STEP] " + $Msg) }
function Write-Ok   { param([string]$Msg) Write-Host ("[ OK ] " + $Msg) -ForegroundColor Green; Write-LogText ("[ OK ] " + $Msg) }
function Write-Fail { param([string]$Msg) Write-Host ("[FAIL] " + $Msg) -ForegroundColor Red; Write-LogText ("[FAIL] " + $Msg) }
function Write-Warn { param([string]$Msg) Write-Host ("[WARN] " + $Msg) -ForegroundColor Yellow; Write-LogText ("[WARN] " + $Msg) }
function Write-Info { param([string]$Msg) Write-Host ("       " + $Msg); Write-LogText ("   " + $Msg) }

function Write-NativeTail {
    # Print the last lines of captured native output to the console (concise).
    param([object]$Output, [int]$Lines = 15)
    if ($null -eq $Output) { return }
    $Output | Select-Object -Last $Lines | ForEach-Object { Write-Info (($_.ToString()).Trim()) }
}

function Test-VenvImports {
    # Verifies the runtime ML dependencies import inside the venv. Returns $true
    # only on a genuine success (real native exit code; native output is logged).
    param([string]$VenvPython)
    $scr = 'import torch, sentence_transformers; print(torch.__version__, sentence_transformers.__version__)'
    $out = & $VenvPython -c $scr 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $out | ForEach-Object { Write-LogText ($_.ToString()) }
        return $false
    }
    return $true
}

# -- Python discovery ----------------------------------------------------------

function Get-PythonInvocation {
    # Discovers a usable interpreter. By default tries a real 'python' first,
    # then the 'py -3' launcher. With -PreferVenvOrder (venv creation) the
    # verified 'py -3.12' is preferred, then a real 'python', then 'py -3'.
    # Rejects interpreters that do not report a usable "Python 3.x.y" version
    # (e.g. the Microsoft Store App Execution Alias stub, which errors or
    # reports nothing usable).
    param([switch]$PreferVenvOrder)
    if ($PreferVenvOrder) {
        $candidates = @(
            @{ Command = 'py'; Args = @('-3.12') },
            @{ Command = 'python'; Args = @() },
            @{ Command = 'py'; Args = @('-3') }
        )
    }
    else {
        $candidates = @(
            @{ Command = 'python'; Args = @() },
            @{ Command = 'py'; Args = @('-3') }
        )
    }

    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Command -ErrorAction SilentlyContinue
        if ($null -eq $cmd -or $null -eq $cmd.Source) { continue }

        $cmdArgs = @($c.Args)
        $out = & $c.Command @cmdArgs --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Info ("Skipping '{0}': could not run it ({1})" -f $c.Command, (($out | Out-String).Trim()))
            continue
        }
        $verText = ($out | Out-String).Trim()
        if ($verText -notmatch 'Python (\d+)\.(\d+)\.') {
            Write-Info ("Skipping '{0}': did not report a Python version ('{1}') - likely a Store alias or stub." -f $c.Command, $verText)
            continue
        }

        return [pscustomobject]@{
            Command = $c.Command
            Args    = $c.Args
            Source  = $cmd.Source
            Version = $verText
            Major   = [int]$Matches[1]
            Minor   = [int]$Matches[2]
        }
    }
    return $null
}

function Get-PythonCompatibility {
    # Python 3.12 is the verified project version (the project venv is 3.12.10).
    # 3.10-3.11 are treated as compatible provided dependency installation,
    # torch / sentence-transformers imports and the backend tests succeed.
    # 3.13 is accepted only with a clear "not the verified version" warning and
    # must pass the same full checks; it is never silently reported as verified.
    # Anything outside 3.10-3.13 is unsupported.
    param($Python)
    if ($Python.Minor -eq 12) {
        return [pscustomobject]@{ Status = 'ok';   Message = 'Python 3.12 is the verified project version (project venv is 3.12.10).' }
    }
    if ($Python.Minor -eq 13) {
        return [pscustomobject]@{ Status = 'warn'; Message = 'Python 3.13 is NOT the verified project version (3.12). It is accepted only if venv creation, pip install, torch import, sentence-transformers import, and the full backend test suite all pass.' }
    }
    if ($Python.Minor -ge 10 -and $Python.Minor -le 11) {
        return [pscustomobject]@{ Status = 'warn'; Message = ("Python 3.{0} is not the verified 3.12 but is treated as compatible if dependency installation, imports, and backend tests succeed." -f $Python.Minor) }
    }
    return [pscustomobject]@{ Status = 'fail'; Message = ("Python 3.{0} is outside the supported 3.10-3.13 range (verified version: 3.12)." -f $Python.Minor) }
}

# -- Node / npm discovery ------------------------------------------------------

function Get-NodeNpmInfo {
    $info = [pscustomobject]@{
        Found     = $false
        NodeText  = ''
        NodeMajor = 0
        NodeMinor = 0
        NpmText   = ''
        NpmMajor  = 0
    }
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($null -eq $nodeCmd) { return $info }

    $nodeOut = (& node --version 2>&1 | Out-String).Trim()
    if ($nodeOut -match 'v(\d+)\.(\d+)') {
        $info.NodeMajor = [int]$Matches[1]
        $info.NodeMinor = [int]$Matches[2]
    }
    $info.NodeText = $nodeOut

    if ($null -ne $npmCmd) {
        $npmOut = (& npm --version 2>&1 | Out-String).Trim()
        if ($npmOut -match '(\d+)\.') { $info.NpmMajor = [int]$Matches[1] }
        $info.NpmText = $npmOut
        $info.Found = $true
    }
    return $info
}

function Test-NodeCompatibility {
    param([int]$NodeMajor)
    # Vite 5 (frontend/vite.config.ts, package.json "vite": "^5.3.4") requires Node 18+.
    if ($NodeMajor -ge 18) {
        return [pscustomobject]@{ Status = 'pass'; Message = ("Node {0} is compatible with Vite 5 (18+ required)." -f $NodeMajor) }
    }
    return [pscustomobject]@{ Status = 'fail'; Message = ("Node {0} is too old - Vite 5 requires Node 18 or newer. Install Node 18+ and re-run." -f $NodeMajor) }
}

function Test-NpmCompatibility {
    param([int]$NpmMajor)
    # frontend/package-lock.json uses lockfileVersion 3, which requires npm 9+.
    if ($NpmMajor -ge 9) {
        return [pscustomobject]@{ Status = 'pass'; Message = ("npm {0} can install package-lock v3." -f $NpmMajor) }
    }
    return [pscustomobject]@{ Status = 'fail'; Message = ("npm {0} cannot install package-lock v3 - install npm 9 or newer (ships with Node 18+)." -f $NpmMajor) }
}

# -- MiniLM / Hugging Face cache ------------------------------------------------

function Get-HfHomePath {
    if ($env:HF_HOME -and $env:HF_HOME.Trim() -ne '') { return $env:HF_HOME.Trim() }
    return Join-Path $env:USERPROFILE '.cache\huggingface'
}

function Get-MiniLMCacheDir {
    param([string]$HfHome)
    return Join-Path $HfHome 'hub\models--sentence-transformers--all-MiniLM-L6-v2'
}

function Test-MiniLMCacheComplete {
    # Determines whether the local cache can satisfy an offline MiniLM load.
    # Requires the model folder, no *.incomplete download markers, and at least
    # one snapshot that contains config.json.
    param([string]$CacheDir)
    if (-not (Test-Path -LiteralPath $CacheDir)) {
        return [pscustomobject]@{ Complete = $false; Reason = 'model cache folder not found' }
    }
    $incomplete = @(Get-ChildItem -LiteralPath $CacheDir -Recurse -Filter '*.incomplete' -ErrorAction SilentlyContinue)
    if ($incomplete.Count -gt 0) {
        return [pscustomobject]@{ Complete = $false; Reason = 'incomplete download marker(s) present (*.incomplete)' }
    }
    $snapRoot = Join-Path $CacheDir 'snapshots'
    if (Test-Path -LiteralPath $snapRoot) {
        $snaps = @(Get-ChildItem -LiteralPath $snapRoot -Directory -ErrorAction SilentlyContinue)
        foreach ($snap in $snaps) {
            if (Test-Path -LiteralPath (Join-Path $snap.FullName 'config.json')) {
                return [pscustomobject]@{ Complete = $true; Reason = 'complete snapshot with config.json' }
            }
        }
    }
    return [pscustomobject]@{ Complete = $false; Reason = 'no usable snapshot/config.json in cache' }
}

# -- Ports / URLs ---------------------------------------------------------------

function Get-LivePortCount {
    param([int]$Port)
    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    return $conns.Count
}

function Get-PortOwnerInfo {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $conn) {
        return [pscustomobject]@{ InUse = $false; ProcessName = ''; Pid = 0 }
    }
    $p = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($null -eq $p) {
        return [pscustomobject]@{ InUse = $true; ProcessName = ('PID {0}' -f $conn.OwningProcess); Pid = $conn.OwningProcess }
    }
    return [pscustomobject]@{ InUse = $true; ProcessName = $p.ProcessName; Pid = $p.Id }
}

function Test-UrlReady {
    # Poll a URL until it returns HTTP 200 or the bounded timeout expires.
    param([string]$Uri, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}