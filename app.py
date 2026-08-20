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

"No usable web view" includes Windows machines with no WebView2 runtime. On
those, pywebview falls back to MSHTML — the IE11 engine — which has no flexbox,
no grid and no CSS custom properties, so the board renders as unstyled default
HTML: blank job canvas, native select boxes, overlapping controls. That looks
broken, and a browser tab is strictly better. So MSHTML is treated as "no web
view" rather than as a working window. See has_modern_webview().

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


# WebView2's Evergreen runtime records its version in `pv` under this GUID.
# Machine-wide installs land under WOW6432Node even on x64 (the updater is 32-bit);
# per-user installs write the un-prefixed path, usually in HKCU. Any one of the
# three answering is enough.
_WEBVIEW2_GUID = r'{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
_WEBVIEW2_KEYS = (
    r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients' + '\\' + _WEBVIEW2_GUID,
    r'SOFTWARE\Microsoft\EdgeUpdate\Clients' + '\\' + _WEBVIEW2_GUID,
)


def has_modern_webview():
    """Is there a web view that can actually render the board?

    Only Windows can answer no. GTK/Cocoa have no equivalent trap: pywebview
    either finds a WebKit there or raises, and the raise is already handled.

    Windows is different because pywebview ALWAYS finds something — if WebView2
    is absent it quietly uses MSHTML (IE11) and reports success, so the caller
    cannot tell a good window from a broken one by return value alone. Hence the
    registry probe: absent runtime means we refuse the window and use a browser.

    Every lookup failure counts as absent. Being wrong in that direction costs a
    browser tab; being wrong the other way ships the unstyled board.
    """
    if not sys.platform.startswith('win'):
        return True
    try:
        import winreg
    except Exception:
        return True                      # not really Windows — don't block the window
    hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    for hive in hives:
        for key in _WEBVIEW2_KEYS:
            try:
                with winreg.OpenKey(hive, key) as k:
                    pv = (winreg.QueryValueEx(k, 'pv')[0] or '').strip()
            except Exception:
                continue
            if pv and pv != '0.0.0.0':   # 0.0.0.0 is the "registered, not installed" placeholder
                return True
    return False


def has_browser():
    """Is there anything registered to open an http:// link?

    Only Windows can answer no, and only because it is possible to have a Windows
    with no browser at all — Windows Sandbox ships that way. `webbrowser.open()`
    cannot be trusted to tell us: it goes through ShellExecute, which reports
    SUCCESS and then puts up its own "We can't open this 'http' link" dialog, so
    the return value is True while the user is looking at an error.

    Hence reading the association directly. Any lookup failure counts as "there
    is one" — being wrong that way costs a redundant URL banner, while being
    wrong the other way is the bug this exists to stop.
    """
    if not sys.platform.startswith('win'):
        return True
    try:
        import winreg
    except Exception:
        return True
    key = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool((winreg.QueryValueEx(k, 'ProgId')[0] or '').strip())
    except Exception:
        return False


def show_url(url):
    """Last resort: no web view, no browser. Say so, and make the URL copyable.

    NOT an error path. The dashboard is up and serving; the machine simply has
    nothing to display it with. Printing the address is the whole remedy, so the
    exit code stays 0 and the server keeps running.
    """
    line = '  ' + url + '  '
    print('')
    print('  ' + '=' * len(line))
    print('  ' + line)
    print('  ' + '=' * len(line))
    print('')
    print('  The Top Rat is RUNNING - this machine just has no browser and no')
    print('  WebView2 runtime, so nothing here can show it to you.')
    print('  Copy the address above into a browser on this machine or another one.')
    print('')


def open_in_browser(url):
    """Fallback path: the ordinary browser. Same board, same features.

    Returns True if the board was (as far as can be determined) shown.
    """
    if not has_browser():
        show_url(url)
        return False
    import webbrowser
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        show_url(url)
        return False
    print('Opened %s in your browser.' % url)
    return True


def open_in_window(url):
    """Native window via pywebview. Returns False if that is not possible here.

    Every failure is caught and reported as False rather than raised: the caller
    falls back to the browser, and a missing GUI toolkit must never be fatal.
    """
    try:
        import webview
    except Exception:
        return False

    if not has_modern_webview():
        print('No WebView2 runtime found — opening the board in your browser instead.')
        print("(Windows' built-in IE engine renders the board incorrectly. Nothing is")
        print(' wrong with your install; every feature works the same in a browser.)')
        return False

    try:
        webview.create_window(version_string(), url,
                              width=1280, height=860,
                              min_size=(900, 600), confirm_close=False)
        icon = icon_path()
        # gui= is the belt to has_modern_webview()'s braces: if the registry probe
        # is ever wrong, naming the backend makes pywebview RAISE instead of
        # silently dropping to MSHTML, and the except below turns that into the
        # browser fallback. Never call start() without it on Windows.
        kw = {'gui': 'edgechromium'} if sys.platform.startswith('win') else {}
        # icon= is not accepted by older pywebview at all, hence the TypeError
        # retry. A WRONG-format icon cannot be handled here — see icon_path().
        try:
            webview.start(icon=icon, **kw) if icon else webview.start(**kw)
        except TypeError:
            webview.start(**kw)
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
