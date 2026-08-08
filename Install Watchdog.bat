@echo off
REM Command-line equivalent of the "Keep it running" switch on the Setup and Schedule
REM pages. Same code path (make_watchdog.py), so use whichever you prefer. Kept as a file
REM because the deterministic script path must never depend on the dashboard being open.
setlocal
cd /d "%~dp0"
title Install Job Dashboard Watchdog

REM Bundled interpreter first, exactly like Start Here.bat — a shipped copy has no
REM system Python to fall back on.
set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY goto nopython

echo Registering the watchdog scheduled task...
echo (Windows will then start the dashboard, minimized, whenever it is not running.)
echo.
"%PY%" src\ops\make_watchdog.py install --every 5
echo.

echo Checking the dashboard now...
"%PY%" src\ops\watchdog.py
echo.

REM Resolve this instance's port (config/instance.json, else 8765) so the URL is never hardcoded.
REM Temp file, not for/f — the app folder path can contain spaces (see Start Here.bat).
set "PORT=8765"
"%PY%" src\web\dashboard_server.py --print-port > "%TEMP%\ja_port.txt" 2>nul
set /p PORT=<"%TEMP%\ja_port.txt"
del /q "%TEMP%\ja_port.txt" 2>nul
if not defined PORT set "PORT=8765"

echo All set. Open the board any time at http://127.0.0.1:%PORT%/
echo Turn the watchdog off again on the Schedule page, or run "Uninstall Watchdog.bat".
echo.
pause
exit /b 0

:nopython
echo Python was not found, so the watchdog cannot be installed.
echo Double-click "Start Here.bat" first - it explains how to fix this.
echo.
pause
exit /b 1
