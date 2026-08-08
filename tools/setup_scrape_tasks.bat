@echo off
REM One-time setup: registers Windows Task Scheduler jobs that run scrape.py
REM (daily 05:20, Sunday 22:45). Re-running is safe (/F overwrites).
REM
REM CHANGED 2026-08-05: the project path is no longer hardcoded to one machine — it is derived
REM from this file's own location, so a copy handed to someone else registers THEIR path. This
REM file also moved into tools\, so %~dp0.. is the app folder. A task registered before the move
REM has the old path baked in; re-running this file repairs it.
REM
REM NOTE: the dashboard's own scheduler (Schedule page) can run these same scrapes while the app
REM is open. Keeping both means the scrape may run twice — harmless, but check the Schedule page
REM before adding these if you want exactly one.
setlocal
cd /d "%~dp0.."
set "PROJ=%CD%"

set "PY="
if exist "%PROJ%\python\python.exe" set "PY=%PROJ%\python\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY set "PY=python"

REM Ensure LinkedIn scraper dependency is installed (safe to re-run).
"%PY%" -c "import jobspy" 2>NUL || "%PY%" -m pip install --quiet python-jobspy

REM Pending-job cap (src\lib\backlog.py) gates each run the same way the dashboard's own
REM scheduler does, so an unattended Windows Task can't keep growing a board that is already full.
schtasks /Create /F /SC DAILY /ST 05:20 /TN "JobScrape24h" ^
  /TR "cmd /c cd /d \"%PROJ%\" && (\"%PY%\" src\lib\backlog.py --check >> logs\scrape_log.txt 2>&1 && \"%PY%\" src\pipeline\scrape.py --url-key hiringcafe_24h --max-age-days 5 >> logs\scrape_log.txt 2>&1) || echo Skipping - pending cap reached. >> logs\scrape_log.txt"

schtasks /Create /F /SC WEEKLY /D SUN /ST 22:45 /TN "JobScrape7d" ^
  /TR "cmd /c cd /d \"%PROJ%\" && (\"%PY%\" src\lib\backlog.py --check >> logs\scrape_log.txt 2>&1 && \"%PY%\" src\pipeline\scrape.py --url-key hiringcafe_7d --max-age-days 5 >> logs\scrape_log.txt 2>&1) || echo Skipping - pending cap reached. >> logs\scrape_log.txt"

echo.
echo Registered for project:  %PROJ%
echo Test now with:  schtasks /Run /TN "JobScrape24h"
echo Then check "%PROJ%\scrape_meta.json" (status should be "ok") and scrape_log.txt.
pause
