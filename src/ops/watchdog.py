#!/usr/bin/env python3
"""watchdog.py - make sure the dashboard is running; start it minimized if it is not.

Deterministic, zero-token, standard-library only. The Windows Task Scheduler runs this every
few minutes (see make_watchdog.py). One run does exactly one thing:

    is something listening on the dashboard port?
        yes -> exit 0, silently
        no  -> start dashboard_server.py hidden, wait for the port, open the board minimized

Why a separate script instead of a loop inside the dashboard: the supervisor must not share
fate with the thing it supervises. This process starts, checks, and exits. Nothing to crash,
nothing to leak, and the Task Scheduler keeps its own run history and exit codes.

    python src/ops/watchdog.py             # check, and start the dashboard if it is down
    python src/ops/watchdog.py --check     # report only. It starts nothing (safe to run any time)
    python src/ops/watchdog.py --no-open   # start the server, but do not open a browser window
    python src/ops/watchdog.py --force     # start a server even if the port answers (diagnostics)

PAUSE: set "watchdogPauseUntil" in config/gui_settings.json to a unix timestamp, and this
script does nothing until then. Without it, the watchdog reopens the dashboard within minutes
every time you close it, and you can never actually quit the app.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, socket, subprocess, time

HERE = _paths.ROOT
SERVER = _paths.script('dashboard_server.py')
TAG = 'watchdog.py'
DEFAULT_PORT = 8765
STARTUP_WAIT = 25.0      # seconds to wait for the port after launching the server

try:
    import runlog
except Exception:                                    # logging must never break the watchdog
    class runlog:                                    # noqa: N801 - tiny stand-in
        @staticmethod
        def log(*a, **k): pass

try:
    from pipelib import cfg
except Exception:                                    # data root unresolvable: fall back to code dir
    def cfg(name): return os.path.join(HERE, 'config', name)


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def settings():
    return _read_json(cfg('gui_settings.json'), {})


def _env(suffix, default=''):
    """TOP_RAT_<suffix>, falling back to the old JOB_AGENT_<suffix>.

    A local copy of pipelib.env on purpose: this module must keep working when
    pipelib is absent (it is imported defensively everywhere else here), and the
    port lookup is the one thing that cannot afford an import error.
    """
    for name in ('TOP_RAT_' + suffix, 'JOB_AGENT_' + suffix):
        v = (os.environ.get(name) or '').strip()
        if v:
            return v
    return default


def wanted_port():
    """Same order dashboard_server.resolve_port uses, minus the --port argv form.

    $TOP_RAT_PORT  >  config/instance.json "port"  >  8765. Keep this in step with
    dashboard_server.resolve_port(); a mismatch makes the watchdog probe the wrong socket
    and start a second server on every run.
    """
    env = _env('PORT')
    if env.isdigit():
        return int(env)
    p = _read_json(os.path.join(HERE, 'config', 'instance.json')).get('port')
    try:
        return int(p)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def resolve_port():
    """The port to PROBE: the configured one, or where the server actually went.

    The server may bind a different port than the config names — if something foreign holds
    8765 it moves to 8766 and records that in config/runtime.json. This function is the
    reason that file exists: probing the configured port alone would find nothing, declare
    the board down, and start a second server on every single tick.

    Precedence is deliberate. The configured port (env > instance.json > 8765) is checked
    FIRST and wins whenever something is on it, so an explicit $TOP_RAT_PORT still aims the
    watchdog where the operator pointed it. Only when that port is silent do we consult the
    record — and only if the port it names is listening, since a crash leaves the file
    behind and a stale number must not hide a genuinely stopped board.
    """
    want = wanted_port()
    if is_up(want):
        return want
    rt = _read_json(os.path.join(HERE, 'config', 'runtime.json')).get('port')
    if isinstance(rt, int) and rt != want and is_up(rt):
        return rt
    return want


def is_up(port, timeout=1.5):
    """True when something accepts a TCP connection on the dashboard port."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def paused_for():
    """Seconds of pause remaining (0 when not paused)."""
    try:
        until = float(settings().get('watchdogPauseUntil') or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, int(until - time.time()))


def python_exe(windowless=True):
    """pythonw.exe when available, so no console window flashes on launch."""
    exe = sys.executable or 'python'
    if windowless and exe.lower().endswith('python.exe'):
        pyw = exe[:-len('python.exe')] + 'pythonw.exe'
        if os.path.exists(pyw):
            return pyw
    return exe


def _no_window_flags():
    """DETACHED_PROCESS | CREATE_NO_WINDOW, and 0 off Windows."""
    if os.name != 'nt':
        return 0
    return 0x00000008 | 0x08000000


def start_server():
    """Launch dashboard_server.py hidden and detached. Returns (ok, message)."""
    if not os.path.exists(SERVER):
        return False, 'dashboard_server.py is not next to watchdog.py (' + HERE + ')'
    cmd = [python_exe(), SERVER, '--no-browser']
    try:
        kwargs = dict(cwd=HERE, close_fds=True, creationflags=_no_window_flags(),
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
        if os.name != 'nt':
            kwargs.pop('creationflags')
            kwargs['start_new_session'] = True
        subprocess.Popen(cmd, **kwargs)
        return True, 'launched ' + os.path.basename(cmd[0])
    except Exception as e:
        return False, 'could not launch the dashboard: ' + str(e)


def wait_for_port(port, seconds=STARTUP_WAIT):
    """Poll the port until the server answers. Returns how long it took, or None on timeout."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        if is_up(port):
            return time.time() - t0
        time.sleep(0.5)
    return None


def open_minimized(url):
    """Open the board in a MINIMIZED browser window, so logging in does not put the
    dashboard in your face. Returns (ok, message).

    `start /min` is the only reliable way to do this for a URL: the window style has to be
    applied by the shell that resolves the http association, not by us. Caveat worth knowing
    - if your browser is ALREADY open, the URL becomes a tab in that existing window and
    Windows has nothing to minimize, so the tab lands wherever that window already is.
    """
    if os.name != 'nt':
        try:
            import webbrowser
            webbrowser.open(url)
            return True, 'opened (no minimize support on this OS)'
        except Exception as e:
            return False, str(e)
    try:
        # '' is the mandatory (empty) window title that `start` eats before the URL.
        subprocess.Popen(['cmd', '/c', 'start', '', '/min', url],
                         cwd=HERE, creationflags=0x08000000,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True, 'opened minimized'
    except Exception as e:
        return False, 'could not open the browser: ' + str(e)


def main():
    argv = sys.argv[1:]
    check_only = '--check' in argv
    port = resolve_port()
    url = 'http://127.0.0.1:%d/' % port
    up = is_up(port)
    pause = paused_for()

    if check_only:
        print('dashboard: ' + ('UP' if up else 'DOWN') + ' on ' + url)
        print('paused:    ' + ('yes, %d min left' % (pause // 60) if pause else 'no'))
        print('launcher:  ' + python_exe() + ' ' + SERVER + ' --no-browser')
        return 0

    if pause and '--force' not in argv:
        runlog.log(TAG, 'OK', 'paused %ds, nothing done' % pause)
        return 0

    if up and '--force' not in argv:
        return 0            # the normal case. Deliberately silent: this runs every few minutes

    ok, msg = start_server()
    if not ok:
        runlog.log(TAG, 'ERROR', msg)
        return 1

    took = wait_for_port(port)
    if took is None:
        runlog.log(TAG, 'ERROR', 'dashboard did not answer on %s within %.0fs (%s)'
                   % (url, STARTUP_WAIT, msg))
        return 1

    detail = 'dashboard was DOWN, restarted in %.1fs (%s)' % (took, msg)
    if '--no-open' not in argv:
        bok, bmsg = open_minimized(url)
        detail += '; ' + bmsg if bok else '; browser NOT opened: ' + bmsg
    runlog.log(TAG, 'OK', detail)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
