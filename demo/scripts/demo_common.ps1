# demo/scripts/demo_common.ps1
# Shared helpers for the Agent Sentinel demo launcher.
# Dot-sourced by the demo_*.ps1 scripts. Works from any installation path.

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

function Get-LivePortCount {
    param([int]$Port)
    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    return $conns.Count
}

function Write-Step { param([string]$Msg) Write-Host ""; Write-Host ("[STEP] " + $Msg) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host ("[ OK ] " + $Msg) -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host ("[FAIL] " + $Msg) -ForegroundColor Red }
function Write-Warn { param([string]$Msg) Write-Host ("[WARN] " + $Msg) -ForegroundColor Yellow }
function Write-Info { param([string]$Msg) Write-Host ("       " + $Msg) }
