#!/bin/bash
# ============================================================================
#  Install Watchdog.command - the mac/linux twin of "Install Watchdog.bat".
#
#  Command-line equivalent of the "Keep it running" switch on the Setup and
#  Schedule pages. Same code path (make_watchdog.py, which forwards to
#  watchdog_launchd.py on a Mac), so use whichever you prefer. It is kept as a
#  file because the deterministic script path must never depend on the
#  dashboard being open.
#
#  On macOS this registers a LaunchAgent (com.toprat.watchdog) in your own
#  ~/Library/LaunchAgents. It needs no administrator rights and it changes
#  nothing outside your account.
#
#  Same rules as Start Here.command: cd to this folder first, and never close
#  the window on an error.
# ============================================================================
cd "$(dirname "$0")" || exit 1

echo
echo "  Top Rat - keep it running"
echo "  ------------------------------------------------------------"
echo

fail() {
    echo
    read -n 1 -s -r -p "  Press any key to close."
    echo
    exit 1
}

# Bundled interpreter first, exactly like Start Here.command - a shipped copy has no
# system Python to fall back on.
PY=""
if [ -x "./python/bin/python3" ]; then
    PY="./python/bin/python3"
else
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; then
            PY="$c"
            break
        fi
    done
fi

if [ -z "$PY" ]; then
    echo "  Python was not found, so the watchdog cannot be installed."
    echo "  Double-click \"Start Here.command\" first - it explains how to fix this."
    fail
fi

echo "  Registering the watchdog..."
echo "  (Your computer will then start the dashboard, minimized, whenever it is not running.)"
echo
"$PY" src/ops/make_watchdog.py install --every 5 || fail
echo

echo "  Checking the dashboard now..."
"$PY" src/ops/watchdog.py
echo

# Resolve this instance's port (config/instance.json, else 8765) so the URL is never hardcoded.
PORT="$("$PY" src/web/dashboard_server.py --print-port 2>/dev/null)"
case "$PORT" in
    ''|*[!0-9]*) PORT=8765 ;;
esac

echo "  All set. Open the board any time at http://127.0.0.1:$PORT/"
echo "  Turn the watchdog off again on the Schedule page, or run \"Uninstall Watchdog.command\"."
echo
read -n 1 -s -r -p "  Press any key to close."
echo
exit 0
