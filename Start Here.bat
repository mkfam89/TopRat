@echo off
REM ============================================================================
REM  Start Here.bat - the ONE file to double-click.
REM
REM  Opens the Top Rat. It finds an interpreter, starts the dashboard if it is
REM  not already running, and opens the board in a browser. Nothing else in this
REM  folder needs to be run by hand.
REM
REM  Design notes (for whoever maintains this):
REM   * The window NEVER closes on an error. A .bat that flashes and vanishes is
REM     the single worst experience for a non-technical user, so every failure
REM     path ends at :fail with a readable message and a pause.
REM   * Interpreter order is bundled -> py launcher -> PATH. The bundled copy
REM     wins so a shipped zip never depends on what the user has installed.
REM   * The interpreter is TESTED, not just located: `where python` happily finds
REM     the Windows Store stub, which is not Python and exits without running.
REM   * Starting the server is delegated to watchdog.py (already handles "is it
REM     up?", hidden launch, and waiting for the port) instead of repeating that
REM     logic here in batch. --no-open because THIS run should show the board.
REM ============================================================================
setlocal
cd /d "%~dp0"
title Top Rat
echo(
echo   Top Rat
echo   ------------------------------------------------------------
echo(

REM ---- 1. find a working Python ---------------------------------------------
set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY call :try py
if not defined PY call :try python
if not defined PY goto nopython

REM Verify whatever we found actually runs (Store stub / broken install guard).
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if errorlevel 1 goto nopython

REM ---- 2. set up the app window, once ----------------------------------------
REM Only relevant for someone running their OWN Python: the shipped bundle
REM already has pywebview (see tools\Make Portable Python.bat), so this is
REM skipped entirely there. We INSTALL it automatically and fall back to the
REM browser when that does not work - app.py opens a browser tab whenever
REM webview is missing and loses no feature, so the fallback costs nothing,
REM while a question the user has no way to evaluate buys nothing. Attempted at
REM most once: the marker is written whatever the outcome.
call :setup_window

REM ---- 3. start the dashboard and show it ------------------------------------
REM app.py does the three steps this file used to do by hand (start if down, find
REM the port, open the board) and additionally shows a real app window when the
REM optional pywebview package is present. With pywebview absent it opens the
REM normal browser, which is exactly the old behaviour - so this is a superset,
REM never a regression.
echo   Starting the dashboard...
"%PY%" app.py
if errorlevel 1 goto nostart
echo(
echo   Done. The Top Rat is running in the background.
echo(
echo   Tip: on the Setup page, turn on "Keep it running" and Windows will
echo        start it for you from now on - you will not need this file again.
echo(
timeout /t 6 >nul
exit /b 0

REM ---------------------------------------------------------------- helpers --
:try
where %1 >nul 2>&1 && set "PY=%1"
exit /b 0

:setup_window
REM Every path here exits 0. Nothing in this routine may stop the app starting.
if exist "%~dp0python\python.exe" exit /b 0
if exist "%~dp0config\app_window.answered" exit /b 0
"%PY%" -c "import webview" >nul 2>&1 && exit /b 0
echo(
echo   One-time setup: fetching the app window package (about 5 MB, from the
echo   Python package index) so the board can open in its own window. If it
echo   does not work the board opens in your browser instead - same app, just
echo   a tab. This can take a minute.
echo(
REM Marker whatever the outcome: retrying a download that failed, on every
REM launch, is worse than opening in the browser and saying so once.
if not exist "%~dp0config" mkdir "%~dp0config" 2>nul
>"%~dp0config\app_window.answered" echo attempted
REM Plain install first; --user is the retry for a Python whose site-packages
REM this user cannot write (Program Files, or a machine-wide install). pip
REM output is discarded - a wall of red text about an OPTIONAL package reads as
REM a broken app, which it is not.
"%PY%" -m pip install --quiet --disable-pip-version-check pywebview >nul 2>&1
REM Installed is not the same as importable, so test the thing we actually need.
"%PY%" -c "import webview" >nul 2>&1
if errorlevel 1 "%PY%" -m pip install --quiet --disable-pip-version-check --user pywebview >nul 2>&1
"%PY%" -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo   Could not install it - no harm done, the app opens in your browser.
  echo   Usually it means no internet connection right now. To try again later,
  echo   delete config\app_window.answered and run this file again.
) else (
  echo   Done - the app will open in its own window.
)
echo(
exit /b 0

:nopython
echo   Python was not found on this computer.
echo(
echo   This app runs on Python, so it cannot start without it.
echo(
if exist "%~dp0tools\Make Portable Python.bat" (
  echo   FIX: double-click  tools\Make Portable Python.bat
  echo        It downloads a private copy of Python into this folder. It does
  echo        not change anything else on your computer, and does not need an
  echo        administrator password.
) else (
  echo   FIX: install Python from  https://www.python.org/downloads/
  echo        On the first screen of the installer, TICK "Add python.exe to PATH".
)
echo(
echo   Then double-click this file again.
echo(
goto fail

:nostart
echo(
echo   The dashboard did not start.
echo(
echo   What to try, in order:
echo     1. Double-click this file again - a slow computer can time out once.
echo     2. Restart the computer, then try again.
echo     3. Open  logs\execution.log  and look at the last few lines. They say
echo        what failed. Send those lines to whoever gave you this app.
echo(
goto fail

:fail
echo   ------------------------------------------------------------
pause
exit /b 1
