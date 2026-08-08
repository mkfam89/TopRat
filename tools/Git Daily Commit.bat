@echo off
REM Lives in tools\ now, so cd to the PARENT (the app folder) — git_daily.py and the
REM repo are up one level. Everything here is a maintainer tool, not something the
REM person using the app should ever need to click.
cd /d "%~dp0.."
title Git Daily Commit
python src\ops\git_daily.py
echo.
pause
