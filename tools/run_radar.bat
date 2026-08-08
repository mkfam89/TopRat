@echo off
REM Job radar: refresh listings, re-score, push NEW matches to your phone (ntfy). Pure scripts, no tokens.
REM Lives in tools\ — cd to the parent, which is the app folder (scrape.py, listings.json, logs).
setlocal
cd /d "%~dp0.."

REM Bundled interpreter first, so a shipped copy with no system Python still runs the radar.
set "PY="
if exist "%CD%\python\python.exe" set "PY=%CD%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

echo ===== Radar run %date% %time% ===== >> logs\radar_log.txt
REM Pending-job cap (src\lib\backlog.py): same gate the dashboard's own scheduler uses, so an
REM unattended Windows Task can't keep growing a board the dashboard would otherwise pause.
"%PY%" src\lib\backlog.py --check >> logs\radar_log.txt 2>&1
if errorlevel 1 (
  echo Skipping this run - pending cap reached. >> logs\radar_log.txt
  goto :eof
)
REM Self-heal: ensure python-jobspy is present so LinkedIn scraping never silently drops.
"%PY%" -c "import jobspy" 2>NUL || "%PY%" -m pip install --quiet python-jobspy >> logs\radar_log.txt 2>&1
"%PY%" src\pipeline\scrape.py --url-key hiringcafe_24h --max-age-days 1 >> logs\radar_log.txt 2>&1
"%PY%" src\pipeline\jobpipe.py candidates --listings listings.json >> logs\radar_log.txt 2>&1
"%PY%" src\pipeline\notify.py >> logs\radar_log.txt 2>&1
