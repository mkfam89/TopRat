#!/usr/bin/env python3
"""app.py — open Top Rat in its own desktop window.

What this is
------------
A thin shell around the dashboard that already exists. It does exactly three
things: make sure the server is running, find the port, and show the board in a
native window instead of a browser tab.

It deliberately contains NO application logic. Everything it needs — "is the
server up?", "start it hidden", "wait for the port" — already lives in
src/ops/watchdog.py, so this file imports those instead of repeating them.

Degrading gracefully (the project's standing rule)
--------------------------------------------------
The native window needs the optional `pywebview` package. If it is missing, or
the OS has no usable web view, THIS IS NOT AN ERROR: the board opens in your
normal browser instead and works identically. A window is a nicety, never a
dependency. The same reasoning as the rest of the project — the deterministic
path must always survive the loss of the optional layer.

Closing the window
------------------
Closing the window does NOT stop the background server, exactly like closing a
browser tab does not. That is deliberate: the scheduler may be mid-scrape, and
the watchdog would only restart it anyway. Run `python app.py --stop-info` to
see how to stop it for real.

Usage
-----
    python app.py                # start (if needed) and show the window
    python app.py --browser      # skip the window, use the normal browser
    python app.py --stop-info    # print how to stop the background server
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
import _paths  # noqa: F401  -- puts every src/ folder on sys.path; _paths.ROOT = project folder

try:
    from _version import APP_NAME, version_string
except Exception:                       # a partial copy should still launch
    APP_NAME, version_string = 'Top Rat', lambda: 'Top Rat'

import watchdog                          # src/ops/watchdog.py — the proven start/wait logic

try:
    from pipelib import env as _env      # TOP_RAT_* with a JOB_AGENT_* fallback
except Exception:                        # a partial copy should still launch
    def _env(suffix, default=''):
        for n in ('TOP_RAT_' + suffix, 'JOB_AGENT_' + suffix):
            v = (os.environ.get(n) or '').strip()
            if v:
                return v
        return default

_ICON_DIR = os.path.join(_paths.ROOT, 'assets', 'icon')


def icon_path():
    """The icon file for THIS platform, or '' for none.

    Format is not negotiable per platform, and getting it wrong is not a soft
    failure. pywebview's Windows backend hands the file to System.Drawing.Icon,
    which accepts .ico ONLY — given a .png it raises inside a .NET dispatcher
    thread, so the process dies with an unhandled ArgumentException that no
    try/except around webview.start() can catch. GTK/Cocoa want the .png.

    Because that failure mode is uncatchable, TOP_RAT_NO_ICON=1 turns the icon
    off entirely: an ugly window beats a window that will not open.
    """
    if _env('NO_ICON'):
        return ''
    name = 'job-agent-icon.ico' if sys.platform.startswith('win') else 'job-agent-icon-256.png'
    p = os.path.join(_ICON_DIR, name)
    return p if os.path.exists(p) else ''


def ensure_server():
    """Start the dashboard if it is not already up. Returns (port, error_message).

    Reuses watchdog.py wholesale rather than re-implementing process launching:
    it already handles the hidden/detached launch and the "did the port answer?"
    poll, and it is the same path the .bat launcher and the autostart entry use.
    """
    port = watchdog.resolve_port()
    if watchdog.is_up(port):
        return port, ''
    ok, msg = watchdog.start_server()
    if not ok:
        return port, msg
    if watchdog.wait_for_port(port) is None:
        return port, ('the dashboard did not answer on port %d in time. '
                      'Check logs/execution.log for the reason.' % port)
    return port, ''


def open_in_browser(url):
    """Fallback path: the ordinary browser. Same board, same features."""
    import webbrowser
    webbrowser.open(url)
    print('Opened %s in your browser.' % url)
    print('(Install pywebview for a real app window: pip install pywebview)')


def open_in_window(url):
    """Native window via pywebview. Returns False if that is not possible here.

    Every failure is caught and reported as False rather than raised: the caller
    falls back to the browser, and a missing GUI toolkit must never be fatal.
    """
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window(version_string(), url,
                              width=1280, height=860,
                              min_size=(900, 600), confirm_close=False)
        icon = icon_path()
        # icon= is not accepted by older pywebview at all, hence the TypeError
        # retry. A WRONG-format icon cannot be handled here — see icon_path().
        try:
            webview.start(icon=icon) if icon else webview.start()
        except TypeError:
            webview.start()
        return True
    except Exception as e:
        print('Could not open an app window (%s) — using your browser instead.' % e)
        return False


def main():
    argv = sys.argv[1:]

    if '--stop-info' in argv:
        print('The dashboard runs in the background, so closing the window leaves it up.')
        print('To stop it: end the "python" process running dashboard_server.py')
        print('  Windows   Task Manager -> Details -> python.exe / pythonw.exe')
        print('  macOS     Activity Monitor -> search "dashboard_server"')
        print('If you turned on "Keep it running", switch that off on the Setup page first,')
        print('or it will simply start again.')
        return 0

    print(version_string())
    port, err = ensure_server()
    if err:
        print('')
        print('The dashboard could not be started.')
        print('  ' + err)
        print('')
        print('Try: run it once in a visible window to see the error, with')
        print('  python src/web/dashboard_server.py')
        return 1

    url = 'http://127.0.0.1:%d/' % port
    if '--browser' in argv or not open_in_window(url):
        open_in_browser(url)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
