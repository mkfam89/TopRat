@echo off
REM Run the offline pytest suite (tests/). No network, no LibreOffice, no
REM repo-root writes — everything writable goes to pytest tmp dirs.
REM Regenerate goldens after an intentional config/behavior change with:
REM     python tests\make_fixtures.py   (then review the fixture diff)
REM Lives in tools\ — cd to the parent, where tests/ is.
REM
REM pytest is NOT part of the shipped bundle. requirements.txt is the user's
REM optional-feature list; pytest is developer-only, so it lives in
REM requirements-dev.txt and is installed on demand the first time this runs.
REM src\ops\package.py strips it back out when it builds a release zip.
setlocal
cd /d "%~dp0.."

set "PY="
if exist "%CD%\python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

REM Is pytest importable by the interpreter we picked? If not, install the dev
REM requirements into it. This is a one-time cost; every later run is a no-op.
"%PY%" -c "import pytest" >nul 2>&1
if errorlevel 1 (
  echo(
  echo   pytest is not installed for:  %PY%
  echo   Installing the developer requirements ^(one time^)...
  echo(
  "%PY%" -m pip install --no-warn-script-location -r requirements-dev.txt || goto pipfail
  echo(
  "%PY%" -c "import pytest" >nul 2>&1 || goto pipfail
)

"%PY%" -m pytest tests -q %*
exit /b %errorlevel%

:pipfail
echo(
echo   ERROR: pytest could not be installed, so the suite did not run.
echo   The app itself is unaffected - pytest is only needed to run tests.
echo(
echo   If this machine is offline or pip is blocked, install it by hand from a
echo   machine that can reach PyPI, or point this at another interpreter that
echo   already has both pytest and python-docx:
echo       "%PY%" -m pip install -r requirements-dev.txt
echo(
exit /b 1
