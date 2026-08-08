@echo off
setlocal
cd /d "%~dp0"
title Remove Job Dashboard Watchdog

set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (echo Python was not found - nothing to do. & pause & exit /b 1)

echo Removing the watchdog scheduled task...
"%PY%" src\ops\make_watchdog.py uninstall
echo.
echo Done. Nothing restarts the dashboard for you now - the jobs run only while it is open.
echo.
pause
