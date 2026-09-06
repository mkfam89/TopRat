"""The four ways Top Rat 1.2.1 could burn a first-time Mac user (QA 1.2.1).

Every test here pins a fix for something the macOS pass found, and each one is written so
it fails on Linux and Windows too - the platform branches are injected, nothing here needs
a Mac. That matters: the defects below were all found by a human looking at a screen, and
CI has never run on macOS.

  gate 3   the launcher adopted whatever answered on port 8765, so an old copy left
           running kept opening instead of the new one - silently, forever
  F-1      a missing stylesheet was served as 200 with an empty body, so the board went
           unstyled with nothing in the console or the network panel to explain it
  F-2      one route out of the macOS folder panel still fell through to tkinter
  F-3/F-4  strings that name Windows, shown to people not running Windows
"""
import os
import subprocess
import sys

import pytest

import app
import watchdog as wd


@pytest.fixture(autouse=True)
def _no_env_port(monkeypatch):
    for name in ('TOP_RAT_PORT', 'JOB_AGENT_PORT'):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# gate 3 — "the port answers" is not "MY board answers"
# ---------------------------------------------------------------------------

def _ports(monkeypatch, live, holders):
    """Pretend `live` ports are listening and `holders` maps port -> codeDir."""
    monkeypatch.setattr(wd, 'is_up', lambda p, timeout=1.5: p in live)
    monkeypatch.setattr(wd, 'port_holder', lambda p, timeout=1.0: holders.get(p, ''))


def test_our_port_finds_this_copy(monkeypatch):
    want = wd.wanted_port()
    _ports(monkeypatch, {want}, {want: wd.HERE})
    assert wd.our_port() == want


def test_a_stranger_on_the_port_is_not_adopted(monkeypatch):
    """The exact QA failure: an 18-day-old server from a deleted folder held 8765."""
    want = wd.wanted_port()
    _ports(monkeypatch, {want}, {want: os.path.join(os.sep, 'Users', 'x', 'Downloads',
                                                    'TopRat-main')})
    assert wd.our_port() is None


def test_something_that_is_not_top_rat_is_not_adopted(monkeypatch):
    """No /api/dev/ping answer at all — another program, not another copy."""
    want = wd.wanted_port()
    _ports(monkeypatch, {want}, {})
    assert wd.our_port() is None


def test_our_copy_is_found_where_it_moved_to(monkeypatch):
    """A stranger on the wanted port pushes the server up one; we follow it there."""
    want = wd.wanted_port()
    _ports(monkeypatch, {want, want + 1},
           {want: os.path.join(os.sep, 'other', 'copy'), want + 1: wd.HERE})
    assert wd.our_port() == want + 1


def test_ensure_server_starts_a_copy_instead_of_reusing_a_stranger(monkeypatch):
    """app.py must not hand the window a port it cannot prove is its own."""
    want = wd.wanted_port()
    stranger = os.path.join(os.sep, 'other', 'copy')
    state = {'started': False}

    def fake_start():
        state['started'] = True
        return True, 'launched'

    def fake_our_port(seconds=0.0):
        return (want + 1) if state['started'] else None

    monkeypatch.setattr(wd, 'is_up', lambda p, timeout=1.5: p == want)
    monkeypatch.setattr(wd, 'port_holder', lambda p, timeout=1.0: stranger)
    monkeypatch.setattr(wd, 'our_port', fake_our_port)
    monkeypatch.setattr(wd, 'start_server', fake_start)

    port, err = app.ensure_server()
    assert err == ''
    assert state['started'], 'adopted the stranger instead of starting our own server'
    assert port == want + 1


# ---------------------------------------------------------------------------
# F-1 — a missing asset must be a 404, never a 200 with an empty body
# ---------------------------------------------------------------------------

def _get(path, monkeypatch, web_dir):
    import dashboard_server as ds
    monkeypatch.setattr(ds, 'WEB', str(web_dir))
    h = ds.Handler.__new__(ds.Handler)
    h.path = path
    sent = {}
    h._send = lambda code, body, *a, **k: sent.update(code=code, body=body)
    try:
        ds.Handler.do_GET(h)
    except Exception:
        pass                       # a later route may need state we did not stub
    return sent


def test_missing_stylesheet_is_a_404_not_an_empty_200(tmp_path, monkeypatch):
    """A 200 with 0 bytes renders as unstyled HTML and reports nothing, anywhere.

    That is the failure mode a scripted check cannot see: status 200, no console error,
    no network error, and only a human notices the page has no styling.
    """
    sent = _get('/ui.css', monkeypatch, tmp_path)      # empty dir: no ui.css
    assert sent['code'] == 404, 'a missing stylesheet still answers %s' % sent.get('code')


def test_the_stylesheet_is_served_when_it_is_there(tmp_path, monkeypatch):
    (tmp_path / 'ui.css').write_text(':root{--x:1}', encoding='utf-8')
    sent = _get('/ui.css', monkeypatch, tmp_path)
    assert sent['code'] == 200 and sent['body']


# ---------------------------------------------------------------------------
# F-2 — the macOS folder panel must not fall through to tkinter
# ---------------------------------------------------------------------------

class _Ran:
    def __init__(self, rc, out='', err=''):
        self.returncode, self.stdout, self.stderr = rc, out.encode(), err.encode()


def _osascript(monkeypatch, results):
    """Feed _pick_folder_macos a queue of fake osascript results."""
    import dashboard_server as ds
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        r = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ds.subprocess, 'run', fake_run)
    return calls


def test_a_failed_panel_tells_the_user_instead_of_opening_tkinter(monkeypatch):
    """Returning None here re-opens the off-screen tkinter picker this code replaced."""
    import dashboard_server as ds
    _osascript(monkeypatch, [_Ran(1, err='execution error: something else (-1700)')])
    got = ds._pick_folder_macos('Pick', '')       # no default location -> no retry left
    assert got is not None, 'still falls through to tkinter'
    path, err = got
    assert path == '' and 'Type the path' in err


def test_a_failed_retry_also_stops_short_of_tkinter(monkeypatch):
    import dashboard_server as ds
    _osascript(monkeypatch, [_Ran(1, err='error (-1728)'), _Ran(1, err='error (-1728)')])
    got = ds._pick_folder_macos('Pick', os.path.expanduser('~'))
    assert got is not None and got[0] == '' and got[1]


def test_a_cancel_is_still_a_cancel(monkeypatch):
    """Cancelling is a RESULT. It must not produce an error message either."""
    import dashboard_server as ds
    _osascript(monkeypatch, [_Ran(1, err='User canceled. (-128)')])
    assert ds._pick_folder_macos('Pick', '') == ('', '')


def test_no_osascript_at_all_is_the_one_case_that_falls_through(monkeypatch):
    import dashboard_server as ds
    _osascript(monkeypatch, [FileNotFoundError('osascript')])
    assert ds._pick_folder_macos('Pick', '') is None


# ---------------------------------------------------------------------------
# F-3 / F-4 — no Windows-isms in front of a Mac user
# ---------------------------------------------------------------------------

def test_no_ui_string_blames_windows_for_a_launchd_failure():
    """'Windows refused' fired on macOS too, exactly when the user was already puzzled.

    Matches the ASSIGNMENT, not the words: the fix is allowed to explain itself in a
    comment, and any future button text that names an OS is caught the same way.
    """
    import re
    import dashboard_server as ds
    src = open(ds.__file__.replace('.pyc', '.py'), encoding='utf-8').read()
    hits = re.findall(r"textContent\s*=\s*'[^']*Windows[^']*'", src)
    assert hits == [], 'button text names an OS the user may not be running: %s' % hits


@pytest.mark.parametrize('platform, osname, want, unwanted', [
    ('darwin', 'posix', 'Activity Monitor', 'Task Manager'),
    ('win32', 'nt', 'Task Manager', 'Activity Monitor'),
])
def test_stop_info_prints_one_platform(platform, osname, want, unwanted,
                                       monkeypatch, capsys):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(os, 'name', osname)
    monkeypatch.setattr(sys, 'argv', ['app.py', '--stop-info'])
    assert app.main() == 0
    out = capsys.readouterr().out
    assert want in out
    assert unwanted not in out


def test_stop_info_names_the_port_holder_on_macos(monkeypatch, capsys):
    """"Find the python process" is not enough once several copies have existed."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(os, 'name', 'posix')
    monkeypatch.setattr(sys, 'argv', ['app.py', '--stop-info'])
    app.main()
    assert 'lsof -nP -iTCP:%d' % wd.wanted_port() in capsys.readouterr().out
