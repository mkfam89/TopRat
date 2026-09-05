#!/bin/bash
# ============================================================================
#  Uninstall Watchdog.command - the mac/linux twin of "Uninstall Watchdog.bat".
#
#  Unloads and deletes the LaunchAgent that restarts the dashboard. Run this
#  before you delete the app folder, so nothing is left trying to start an app
#  that is gone. The switch on the Schedule page does the same thing.
# ============================================================================
cd "$(dirname "$0")" || exit 1

echo
echo "  Top Rat - remove the watchdog"
echo "  ------------------------------------------------------------"
echo

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
    echo "  Python was not found - nothing to do."
    echo
    read -n 1 -s -r -p "  Press any key to close."
    echo
    exit 1
fi

echo "  Removing the watchdog..."
"$PY" src/ops/make_watchdog.py uninstall
echo
echo "  Done. Nothing restarts the dashboard for you now - the jobs run only while it is open."
echo
read -n 1 -s -r -p "  Press any key to close."
echo
exit 0
