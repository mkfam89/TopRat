@echo off
REM ============================================================================
REM  Make Portable Python.bat - build the bundled interpreter in  python\
REM
REM  Run this ONCE on your own machine before zipping the app for someone else.
REM  It downloads the official Windows "embeddable package" from python.org and
REM  unpacks it into  python\  beside the scripts, so "Start Here.bat" needs no
REM  Python installed on the other person's computer:
REM
REM      no installer, no PATH changes, no administrator password, no UAC prompt,
REM      and nothing for their antivirus to look at.
REM
REM  Everything it uses ships with Windows 10/11 already: curl.exe and tar.exe.
REM
REM  Why the extra steps after unzipping:
REM   * the embeddable build deliberately disables `import site`, so pip and any
REM     installed package are invisible until the ._pth file is edited. That is
REM     the `python3XX._pth` rewrite below.
REM   * pip is not included, so get-pip.py bootstraps it.
REM   * python-docx is the ONE non-stdlib import the app needs (resume writing).
REM     Everything else is standard library, which is why this works at all.
REM   * pywebview is added so the shipped copy opens in its OWN WINDOW instead of
REM     a browser tab. It is deliberately NOT treated like python-docx: on
REM     Windows it drags in pythonnet (.NET) and wants the Edge WebView2
REM     runtime, either of which can be absent or blocked on a locked-down
REM     machine. A failure there prints a warning and the build still finishes,
REM     because app.py falls back to the browser and loses no functionality.
REM     A bundle that cannot be built is worse than a bundle without a window.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
title Build the portable Python bundle

REM Pin a version so a shipped zip is reproducible. Bump both lines together.
set "PYVER=3.12.10"
set "PYTAG=312"
set "ZIPURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"

echo(
echo   Building a portable Python %PYVER% into:  %CD%\python\
echo   ------------------------------------------------------------
echo(

if exist "python\python.exe" (
  echo   python\ already exists.
  choice /c YN /m "   Delete it and rebuild"
  if errorlevel 2 goto done
  rmdir /s /q "python"
)

where curl >nul 2>&1 || goto nocurl
where tar  >nul 2>&1 || goto nocurl

echo   [1/4] Downloading %ZIPURL%
curl -L --fail --progress-bar -o "%TEMP%\pyembed.zip" "%ZIPURL%" || goto dlfail

echo   [2/4] Unpacking into python\
mkdir "python" 2>nul
tar -xf "%TEMP%\pyembed.zip" -C "python" || goto unzipfail
del /q "%TEMP%\pyembed.zip" 2>nul

echo   [3/4] Enabling site-packages (so pip and python-docx are importable)
REM The shipped ._pth has "#import site" commented out. Uncomment it, and add the
REM app folder so `import pipelib` works no matter where the process is started.
> "python\python%PYTAG%._pth" echo python%PYTAG%.zip
>>"python\python%PYTAG%._pth" echo .
>>"python\python%PYTAG%._pth" echo ..
>>"python\python%PYTAG%._pth" echo Lib\site-packages
>>"python\python%PYTAG%._pth" echo import site

echo   [4/5] Adding pip, then python-docx
curl -L --fail --progress-bar -o "%TEMP%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py" || goto dlfail
"python\python.exe" "%TEMP%\get-pip.py" --no-warn-script-location || goto pipfail
del /q "%TEMP%\get-pip.py" 2>nul
"python\python.exe" -m pip install --no-warn-script-location python-docx || goto pipfail

echo   [5/5] Adding pywebview (the app window) - optional, see notes at the top
REM setuptools FIRST, and it is not optional here. get-pip.py installs pip alone
REM these days, and the embeddable zip has no setuptools either - so the moment a
REM dependency ships as an sdist instead of a wheel, pip dies with
REM "BackendUnavailable: Cannot import 'setuptools.build_meta'". pywebview hits
REM this through proxy_tools (sdist-only). python-docx never did, because all of
REM its dependencies are wheels - which is why this only shows up now.
set "WINDOW=no"
REM Failure here is NOT fatal, same as the rest of step 5: if setuptools cannot be
REM fetched, pywebview will fail next and the warning below explains it. The
REM bundle still ships and the app still runs - it just opens in a browser.
"python\python.exe" -m pip install --no-warn-script-location setuptools wheel
"python\python.exe" -m pip install --no-warn-script-location pywebview && set "WINDOW=yes"
if "%WINDOW%"=="yes" (
  REM Installing is not the same as working: pythonnet can install and still fail
  REM to import when .NET is missing. Import it now so the warning happens HERE,
  REM on your machine, and not on the other person's first launch.
  "python\python.exe" -c "import webview" >nul 2>&1 || set "WINDOW=no"
)

echo(
echo   Checking the bundle...
"python\python.exe" -c "import sys, docx; print('   OK - Python', sys.version.split()[0], '+ python-docx')" || goto verifyfail
if "%WINDOW%"=="yes" (
  echo   OK - pywebview: the app opens in its own window
) else (
  echo(
  echo   WARNING: pywebview is not usable in this bundle.
  echo   The app still works - it opens in the normal browser instead of its own
  echo   window. Nothing else is affected. On Windows this usually means .NET or
  echo   the Edge WebView2 runtime is missing; installing Microsoft Edge fixes it.
  echo   To retry later:  python\python.exe -m pip install pywebview
)

echo(
echo   ------------------------------------------------------------
echo   Done. Zip this whole folder and hand it over. The other person
echo   double-clicks "Start Here.bat" - nothing to install.
echo(
echo   NOTE: this bundle is 64-bit Windows only.
goto done

:nocurl
echo(
echo   ERROR: curl.exe and tar.exe were not found. They ship with Windows 10
echo   (1803+) and Windows 11. On an older Windows, download the embeddable
echo   zip by hand from python.org and unpack it into  python\.
goto fail

:dlfail
echo(
echo   ERROR: the download failed. Check the internet connection, then re-run.
echo   If python.org is blocked here, fetch the zip elsewhere and unpack it
echo   into  python\  by hand.
goto fail

:unzipfail
echo(
echo   ERROR: the zip did not unpack. Delete  python\  and re-run.
goto fail

:pipfail
echo(
echo   ERROR: pip or python-docx did not install. The interpreter is there, so
echo   the app will run - but resume writing needs python-docx. Re-run this
echo   file, or install it by hand:
echo       python\python.exe -m pip install python-docx
goto fail

:verifyfail
echo(
echo   ERROR: the bundle did not import cleanly. Delete  python\  and re-run.
goto fail

:fail
echo   ------------------------------------------------------------
pause
exit /b 1

:done
echo(
pause
exit /b 0
