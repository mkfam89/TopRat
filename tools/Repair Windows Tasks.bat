@echo off
REM ============================================================================
REM  Repair Windows Tasks.bat  —  run this ONCE after the tools\ move.
REM
REM  WHY THIS EXISTS: JobRadarHourly / JobScrape24h / JobScrape7d were registered
REM  with the FULL PATH of the .bat files baked into the task action. Those files
REM  moved from the app root into tools\, so any task registered before the move
REM  now points at a file that is not there. Windows does not warn about this —
REM  the task simply runs, fails, and reports a non-zero result nobody reads.
REM
REM  This re-registers each task that already exists, at the new path. Tasks you
REM  never installed are left alone: this repairs, it does not add automation.
REM ============================================================================
setlocal
cd /d "%~dp0.."
set "PROJ=%CD%"
set "TOOLS=%~dp0"
title Repair Windows scheduled tasks

echo(
echo   Repairing scheduled tasks for:
echo     %PROJ%
echo   ------------------------------------------------------------
echo(

set "FOUND=0"

schtasks /Query /TN "JobRadarHourly" >nul 2>&1
if not errorlevel 1 (
  set "FOUND=1"
  echo   JobRadarHourly  - found, re-pointing at tools\run_radar.bat
  schtasks /Create /F /TN "JobRadarHourly" /SC DAILY /ST 07:00 /RI 60 /DU 11:00 /TR "\"%TOOLS%run_radar.bat\"" >nul
  if errorlevel 1 (echo     FAILED - re-run this file as Administrator.) else (echo     OK)
) else (
  echo   JobRadarHourly  - not registered, skipping
)

schtasks /Query /TN "JobScrape24h" >nul 2>&1
if not errorlevel 1 (
  set "FOUND=1"
  echo   JobScrape24h    - found, re-pointing at this folder
  call "%TOOLS%setup_scrape_tasks.bat" >nul 2>&1
  echo     OK ^(both JobScrape24h and JobScrape7d re-registered^)
) else (
  echo   JobScrape24h    - not registered, skipping
)

echo(
if "%FOUND%"=="0" (
  echo   Nothing needed repairing - none of those tasks are registered on this
  echo   computer. The dashboard's own Schedule page is running your jobs.
) else (
  echo   Done. Verify what each task now runs with:
  echo     schtasks /Query /TN "JobRadarHourly" /V /FO LIST ^| findstr /I "Task To Run"
)
echo(
pause
