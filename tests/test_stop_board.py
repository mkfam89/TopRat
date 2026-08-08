"""stop_board matching + safety rules.

These are the tests that matter for a tool whose job is to kill processes: what it
matches, and — far more important — what it must NOT match. An early substring version
matched pid 1 because an unrelated command line merely mentioned 'dashboard_server.py'.
"""
import os

import pytest

import stop_board as sb


# ---- what counts as "running the script" -----------------------------------

@pytest.mark.parametrize('cmd', [
    'python src/web/dashboard_server.py',
    'python3 /home/k/app/src/web/dashboard_server.py --no-browser',
    r'C:\Python312\pythonw.exe C:\Users\k\app\src\web\dashboard_server.py --port 8765',
    r'"C:\Program Files\Python312\python.exe" "C:\a b\src\web\dashboard_server.py"',
    'python -u -X dev src/web/dashboard_server.py',
    './dashboard_server.py',
    'py src\\ops\\watchdog.py --check',
])
def test_runs_script_matches_real_launches(cmd):
    assert sb._runs_script(cmd, sb.BOARD_SCRIPTS), cmd


@pytest.mark.parametrize('cmd', [
    # merely MENTIONS the name — the whole class of false positive that killed pid 1
    'grep -rn dashboard_server.py src/',
    'ps -eo pid=,args= | grep dashboard_server.py',
    'tail -f logs/dashboard_server.py.log',
    'notepad.exe dashboard_server.py',
    r'C:\Windows\system32\cmd.exe /c echo dashboard_server.py',
    'code --goto src/web/dashboard_server.py:1460',
    # a different script in the same folder
    'python src/web/scheduler.py',
    # substring of a longer name
    'python my_dashboard_server.py_backup.py',
    '',
])
def test_runs_script_ignores_mere_mentions(cmd):
    assert not sb._runs_script(cmd, sb.BOARD_SCRIPTS), cmd


def test_pipeline_names_are_separate_from_board_names():
    # --all widens the net; the default must not sweep up a running scrape
    assert not sb._runs_script('python src/pipeline/scrape.py', sb.BOARD_SCRIPTS)
    assert sb._runs_script('python src/pipeline/scrape.py', sb.PIPELINE_SCRIPTS)


# ---- who is off limits ------------------------------------------------------

def test_protected_pids_covers_self_parents_and_init():
    me = os.getpid()
    # a synthetic chain: 1 -> 100 (console) -> 200 (.bat) -> me
    procs = [(1, 0, 'init'), (100, 1, 'console'), (200, 100, 'cmd /c Stop Board.bat'),
             (me, 200, 'python stop_board.py'), (999, 1, 'python dashboard_server.py')]
    safe = sb.protected_pids(procs)
    assert {0, 1, me, 200, 100} <= safe
    assert 999 not in safe          # the actual board is still fair game


def test_protected_pids_survives_a_parent_cycle():
    me = os.getpid()
    procs = [(me, 500, 'python stop_board.py'), (500, me, 'bogus cycle')]
    safe = sb.protected_pids(procs)      # must terminate, not spin
    assert me in safe and 500 in safe


def test_protected_pids_with_no_process_table():
    # every finder can fail on a locked-down box; the guard must still hold the basics
    safe = sb.protected_pids([])
    assert {0, 1, os.getpid()} <= safe


# ---- port resolution --------------------------------------------------------

def test_resolve_port_prefers_env(monkeypatch):
    monkeypatch.setenv('JOB_AGENT_PORT', '9123')
    assert sb.resolve_port() == 9123


def test_resolve_port_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv('JOB_AGENT_PORT', raising=False)
    monkeypatch.setattr(sb, 'HERE', str(tmp_path))     # no instance.json here
    assert sb.resolve_port() == sb.DEFAULT_PORT
