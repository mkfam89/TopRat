@echo off
REM Run the offline pytest suite (tests/). No network, no LibreOffice, no
REM repo-root writes — everything writable goes to pytest tmp dirs.
REM Regenerate goldens after an intentional config/behavior change with:
REM     python tests\make_fixtures.py   (then review the fixture diff)
REM Lives in tools\ — cd to the parent, where tests/ is.
setlocal
cd /d "%~dp0.."

set "PY="
if exist "%CD%\python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

"%PY%" -m pytest tests -q %*
