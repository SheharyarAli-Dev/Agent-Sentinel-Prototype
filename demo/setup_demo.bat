@echo off
REM ============================================================
REM  Agent Sentinel Demo - one-time setup (double-click me)
REM ============================================================
setlocal
set "SCRIPTS_DIR=%~dp0scripts"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_DIR%\setup_demo.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo ============================================================
    echo   SETUP PASSED - you can now run start_demo.bat
    echo ============================================================
) else (
    echo ============================================================
    echo   SETUP FAILED - review the messages above
    echo ============================================================
)
echo.
pause
exit /b %EXITCODE%
