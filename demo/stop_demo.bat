@echo off
REM ============================================================
REM  Agent Sentinel Demo - stop services started by start_demo
REM ============================================================
setlocal
set "SCRIPTS_DIR=%~dp0scripts"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_DIR%\stop_demo.ps1"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo ============================================================
    echo   STOP COMPLETE
    echo ============================================================
) else (
    echo ============================================================
    echo   STOP FINISHED WITH WARNINGS - see messages above
    echo ============================================================
)
echo.
pause
exit /b %EXITCODE%
