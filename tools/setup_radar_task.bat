@echo off
REM One-time setup: registers "JobRadarHourly" to run run_radar.bat every 60 min, 07:00-18:00 daily.
REM Double-click once to install (re-running is safe; /F overwrites).
REM
REM PATH NOTE (2026-08-05): this file and run_radar.bat moved into tools\, so the task's action
REM path changed. A task registered before the move still points at the OLD root path and fails
REM silently — re-running this file repairs it, because /F overwrites the existing task.
setlocal
set "PROJ=%~dp0"
schtasks /Create /F /TN "JobRadarHourly" /SC DAILY /ST 07:00 /RI 60 /DU 11:00 /TR "\"%PROJ%run_radar.bat\""
if %errorlevel%==0 (echo SUCCESS: JobRadarHourly created - runs every hour, 7am-6pm.) else (echo FAILED - see the error above; try re-running as Administrator.)
echo.
echo Registered action:  "%PROJ%run_radar.bat"
echo.
echo Test now:  schtasks /Run /TN "JobRadarHourly"
echo Verify:    schtasks /Query /TN "JobRadarHourly" /V /FO LIST ^| findstr /I "Task To Run"
echo Remove:    schtasks /Delete /TN "JobRadarHourly" /F
pause
