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

# ---- 2. offer the app window, once ----------------------------------------
# Only for someone running their OWN Python; a bundled python/ already has
# pywebview. We ask instead of installing silently, and ask at most once - the
# marker is written whatever the answer, so "no" stays no. Nothing in this block
# may prevent the app from starting: a failed install is reported and ignored,
# never propagated, because app.py opens the browser instead and loses nothing.
if [ ! -x "./python/bin/python3" ] &&
   [ ! -f "./config/app_window.answered" ] &&
   ! "$PY" -c 'import webview' >/dev/null 2>&1; then
    echo
    echo "  One-time question: this app can open in its own window instead of a"
    echo "  browser tab. That needs an extra download from the Python package"
    echo "  index (a few MB on Linux; more on macOS, which pulls in pyobjc)."
    echo "  Everything works either way - the browser is not a lesser version"
    echo "  of the app, it is just a tab."
    echo
    printf "  Set up the app window? [y/N] "
    read -r ans
    mkdir -p ./config 2>/dev/null
    echo asked > ./config/app_window.answered
    case "$ans" in
        [Yy]*)
            echo
            echo "  Installing... (this happens only once)"
            if "$PY" -m pip install --quiet --disable-pip-version-check pywebview &&
               "$PY" -c 'import webview' >/dev/null 2>&1; then
                echo "  Done - the app will open in its own window."
            else
                echo
                echo "  That did not work - no harm done, the app opens in your browser."
                echo "  Usual causes: no internet right now, or a system Python that"
                echo "  refuses installs (macOS/Homebrew). The app is unaffected."
            fi
            ;;
        *)
            echo "  Fine - opening in your browser. To change your mind later, delete"
            echo "  config/app_window.answered and run this file again."
            ;;
    esac
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
