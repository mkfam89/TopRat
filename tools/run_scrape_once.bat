@echo off
REM One-off manual scrape (no notify): ensures jobspy is installed, then runs scrape.py.
REM Lives in tools\ — cd to the parent (the app folder).
setlocal
cd /d "%~dp0.."

set "PY="
if exist "%CD%\python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

echo === Ensuring python-jobspy is installed ===
"%PY%" -c "import jobspy" 2>NUL && (echo jobspy already present) || "%PY%" -m pip install python-jobspy
echo.
echo === Running scrape.py ===
"%PY%" src\pipeline\scrape.py --url-key hiringcafe_24h --max-age-days 1
echo.
echo === scrape_meta.json ===
type scrape_meta.json
echo.
echo Done. You can close this window.
pause
