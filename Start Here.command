#!/bin/bash
# ============================================================================
#  Start Here.command - the ONE file to double-click on macOS and Linux.
#
#  The mac/linux twin of "Start Here.bat". Same job: find a working Python,
#  hand off to app.py, and never vanish silently.
#
#  Design notes (for whoever maintains this):
#   * The window NEVER closes on an error. A launcher that flashes and
#     disappears is the worst possible experience for a non-technical user, so
#     every failure path ends at a readable message and a "press a key".
#   * cd to the script's own folder first: double-clicking in Finder starts you
#     in the HOME directory, not here, and every relative path would miss.
#   * Interpreter order is bundled -> python3 -> python. The bundled copy wins
#     so a shipped folder never depends on what the user happens to have.
#   * .command is the extension Finder makes double-clickable. Linux users can
#     run the same file from a terminal: ./Start\ Here.command
# ============================================================================
cd "$(dirname "$0")" || exit 1

echo
echo "  Top Rat"
echo "  ------------------------------------------------------------"
echo

fail() {
    echo
    echo "  ------------------------------------------------------------"
    echo
    read -n 1 -s -r -p "  Press any key to close."
    echo
    exit 1
}

# ---- 1. find a working Python ---------------------------------------------
PY=""
if [ -x "./python/bin/python3" ]; then
    PY="./python/bin/python3"
else
    for c in python3 python; do
        # Test that it RUNS and is new enough - merely existing is not enough,
        # since some systems ship a "python" that is a stub or a Python 2.
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; then
            PY="$c"
            break
        fi
    done
fi

if [ -z "$PY" ]; then
    echo "  Python 3.8 or newer was not found on this computer."
    echo
    echo "  This app runs on Python, so it cannot start without it."
    echo
    echo "  FIX (macOS): install it from  https://www.python.org/downloads/"
    echo "               or, if you use Homebrew:  brew install python"
    echo "  FIX (Linux): sudo apt install python3     (or your package manager)"
    echo
    echo "  Then double-click this file again."
    fail
fi

# ---- 2. set up the app window, once ---------------------------------------
# Only for someone running their OWN Python; a bundled python/ already has
# pywebview. We install it automatically and fall back to the browser when that
# does not work, and we try at most once - the marker is written whatever the
# outcome, so a machine that cannot install it is not punished on every launch.
# Nothing in this block may prevent the app from starting: a failed install is
# reported and ignored, never propagated, because app.py opens the browser
# instead and loses nothing.
if [ ! -x "./python/bin/python3" ] &&
   [ ! -f "./config/app_window.answered" ] &&
   ! "$PY" -c 'import webview' >/dev/null 2>&1; then
    echo
    echo "  One-time setup: fetching the app window package so the board can open"
    echo "  in its own window (a few MB on Linux; more on macOS, which pulls in"
    echo "  pyobjc). If it does not work the board opens in your browser instead -"
    echo "  same app, just a tab. This can take a minute."
    echo
    mkdir -p ./config 2>/dev/null
    echo attempted > ./config/app_window.answered
    # Flag ladder. A plain install is right on most systems, but a Homebrew or
    # distro Python is "externally managed" (PEP 668) and refuses one outright,
    # and a system-wide Python may simply not be writable by this user. Each
    # retry answers one of those. $FLAGS is deliberately unquoted so it splits
    # into separate arguments. Output is discarded: a wall of red pip text about
    # an OPTIONAL package would read as a broken app, which it is not.
    for FLAGS in "" "--user" "--break-system-packages" "--user --break-system-packages"; do
        "$PY" -m pip install --quiet --disable-pip-version-check $FLAGS pywebview \
            >/dev/null 2>&1
        # Installed is not the same as importable - test the thing we need.
        "$PY" -c 'import webview' >/dev/null 2>&1 && break
    done
    if "$PY" -c 'import webview' >/dev/null 2>&1; then
        echo "  Done - the app will open in its own window."
    else
        echo "  Could not install it - no harm done, the app opens in your browser."
        echo "  Usual causes: no internet right now, or a Python that refuses"
        echo "  installs. To try again later, delete config/app_window.answered"
        echo "  and run this file again."
    fi
    echo
fi

# ---- 3. start the dashboard and show it ------------------------------------
echo "  Starting the dashboard..."
if ! "$PY" app.py; then
    echo
    echo "  The dashboard did not start."
    echo
    echo "  What to try, in order:"
    echo "    1. Run this file again - a slow computer can time out once."
    echo "    2. Restart the computer, then try again."
    echo "    3. Open  logs/execution.log  and look at the last few lines. They"
    echo "       say what failed. Send those lines to whoever gave you this app."
    fail
fi

echo
echo "  Done. The Top Rat is running in the background."
echo
echo "  Tip: on the Setup page, turn on \"Keep it running\" and it will start"
echo "       for you from now on - you will not need this file again."
echo
sleep 4
exit 0
