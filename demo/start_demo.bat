@echo off
REM ============================================================
REM  Agent Sentinel Demo - start backend + frontend (double-click me)
REM ============================================================
setlocal
set "SCRIPTS_DIR=%~dp0scripts"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_DIR%\start_demo.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo ============================================================
    echo   DEMO STARTED - dashboard opening in your browser
    echo ============================================================
) else (
    echo ============================================================
    echo   START FAILED - review the troubleshooting messages above
    echo ============================================================
)
echo.
pause
exit /b %EXITCODE%
