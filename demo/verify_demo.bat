@echo off
REM ============================================================
REM  Agent Sentinel Demo - environment verification
REM ============================================================
setlocal
set "SCRIPTS_DIR=%~dp0scripts"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_DIR%\verify_demo.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo ============================================================
    echo   READY - environment verified
    echo ============================================================
) else (
    echo ============================================================
    echo   NOT READY - see the FAIL lines above
    echo ============================================================
)
echo.
pause
exit /b %EXITCODE%
