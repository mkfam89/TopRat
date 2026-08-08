@echo off
REM ============================================================================
REM  Stop Board.bat  —  stop the dashboard and everything that restarts it.
REM
REM  Closing the browser tab does not stop the server, and closing the server's
REM  window does not always either: the watchdog scheduled task starts it again
REM  within minutes, hidden, with no console. A server left running keeps port
REM  8765 AND keeps the data root it resolved when it started, so a config
REM  change can look like it did nothing at all.
REM
REM  This pauses the watchdog first, then stops every process holding the port
REM  or running the board. It does NOT uninstall the watchdog task — the pause
REM  expires on its own (default 10 minutes).
REM
REM    Stop Board.bat            stop the board
REM    Stop Board.bat --list     show what is running, stop nothing
REM    Stop Board.bat --all      also stop a scrape/tailor run in progress
REM    Stop Board.bat --resume   clear the watchdog pause early
REM ============================================================================
setlocal
cd /d "%~dp0.."
title Stop the job board

REM Bundled interpreter first, so a shipped copy with no system Python still works.
set "PY="
if exist "%CD%\python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

"%PY%" src\ops\stop_board.py %*

echo(
pause
