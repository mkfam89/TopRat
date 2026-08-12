"""Port fall-forward: the server may not land on the port the config names.

The dangerous failure this guards is not "the dashboard picked an odd port". It is the
watchdog probing 8765, finding nothing because the server moved to 8766, concluding the
board is down, and launching a second server — every tick, forever. So the tests that
matter are about AGREEMENT: the server records where it went, and the two tools that
resolve the port independently (watchdog.py, stop_board.py) find it there.

Sockets here are real but bound to 127.0.0.1 on an OS-assigned free port, so nothing
depends on 8765 being free on the machine running the suite.
"""
import json
import os
import socket
import threading

import pytest

import stop_board as sb
import watchdog as wd


# ---- helpers ----------------------------------------------------------------

def _busy_port():
    """A listening socket on a free port. Returns (port, close_fn)."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen(1)
    return s.getsockname()[1], s.close


def _free_port():
    """A port number nothing is listening on (bound, then released)."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _write_runtime(tmp_path, port):
    cfg = tmp_path / 'config'
    cfg.mkdir(exist_ok=True)
    (cfg / 'runtime.json').write_text(json.dumps({'port': port, 'pid': 1}), encoding='utf-8')


@pytest.fixture(autouse=True)
def _no_env_port(monkeypatch):
    for name in ('TOP_RAT_PORT', 'JOB_AGENT_PORT'):
        monkeypatch.delenv(name, raising=False)


# ---- the two independent resolvers must agree -------------------------------

@pytest.mark.parametrize('mod', [sb, wd], ids=['stop_board', 'watchdog'])
def test_configured_port_wins_when_something_is_on_it(mod, monkeypatch, tmp_path):
    """A live configured port is the answer, even if a record names another one.

    Precedence matters: an operator who sets $TOP_RAT_PORT or instance.json is pointing
    these tools at a specific instance, and a leftover record must not override that.
    """
    port, close = _busy_port()
    other, close2 = _busy_port()
    try:
        monkeypatch.setattr(mod, 'HERE', str(tmp_path))
        monkeypatch.setenv('TOP_RAT_PORT', str(port))
        _write_runtime(tmp_path, other)
        assert mod.resolve_port() == port
    finally:
        close(); close2()


@pytest.mark.parametrize('mod', [sb, wd], ids=['stop_board', 'watchdog'])
def test_falls_back_to_the_recorded_port_when_the_configured_one_is_silent(mod, monkeypatch, tmp_path):
    """The whole point: the server moved, and both tools follow it there."""
    moved_to, close = _busy_port()
    try:
        monkeypatch.setattr(mod, 'HERE', str(tmp_path))
        monkeypatch.setenv('TOP_RAT_PORT', str(_free_port()))
        _write_runtime(tmp_path, moved_to)
        assert mod.resolve_port() == moved_to
    finally:
        close()


@pytest.mark.parametrize('mod', [sb, wd], ids=['stop_board', 'watchdog'])
def test_a_stale_record_never_hides_a_stopped_board(mod, monkeypatch, tmp_path):
    """A crash leaves runtime.json behind. Pointing at a dead port would report the board
    as running (stop_board) or leave it stopped (watchdog) — both silent failures."""
    want = _free_port()
    monkeypatch.setattr(mod, 'HERE', str(tmp_path))
    monkeypatch.setenv('TOP_RAT_PORT', str(want))
    _write_runtime(tmp_path, _free_port())        # recorded, but nothing is listening
    assert mod.resolve_port() == want


@pytest.mark.parametrize('mod', [sb, wd], ids=['stop_board', 'watchdog'])
def test_missing_or_junk_record_is_ignored(mod, monkeypatch, tmp_path):
    want = _free_port()
    monkeypatch.setattr(mod, 'HERE', str(tmp_path))
    monkeypatch.setenv('TOP_RAT_PORT', str(want))
    assert mod.resolve_port() == want             # no runtime.json at all
    cfg = tmp_path / 'config'; cfg.mkdir(exist_ok=True)
    (cfg / 'runtime.json').write_text('not json {', encoding='utf-8')
    assert mod.resolve_port() == want


def test_both_resolvers_still_read_instance_json(monkeypatch, tmp_path):
    """wanted_port() is duplicated in both modules on purpose (neither may depend on
    pipelib importing). Duplication only stays safe while it stays identical."""
    cfg = tmp_path / 'config'; cfg.mkdir()
    (cfg / 'instance.json').write_text('{"port": 8766}', encoding='utf-8')
    for mod in (sb, wd):
        monkeypatch.setattr(mod, 'HERE', str(tmp_path))
        assert mod.wanted_port() == 8766
        assert mod.resolve_port() == 8766         # nothing listening -> the wanted port


# ---- the server side: when to move, and when to refuse ----------------------

@pytest.fixture(scope='module')
def ds():
    return pytest.importorskip('dashboard_server')


def test_bind_port_takes_the_wanted_port_when_it_is_free(ds):
    srv, port, moved_from = ds.bind_port(_free_port(), wait=0)
    try:
        assert moved_from == 0                    # no note printed, nothing moved
    finally:
        srv.server_close()


def test_bind_port_moves_over_for_a_foreign_listener(ds, monkeypatch):
    """Something else owns the port. Taking the next one beats refusing to start."""
    monkeypatch.setattr(ds, 'port_is_ours', lambda p, timeout=1.0: None)
    busy, close = _busy_port()
    try:
        srv, port, moved_from = ds.bind_port(busy, wait=0)
        try:
            assert port != busy and moved_from == busy
            assert busy < port <= busy + ds.PORT_SCAN
        finally:
            srv.server_close()
    finally:
        close()


def test_bind_port_refuses_when_this_copy_already_holds_it(ds, monkeypatch):
    """Two dashboards on one data root would fight over every file they write. Double-
    clicking Start Here twice must reopen the first window, not start a rival."""
    monkeypatch.setattr(ds, 'port_is_ours', lambda p, timeout=1.0: True)
    busy, close = _busy_port()
    try:
        with pytest.raises(ds.AlreadyRunning) as e:
            ds.bind_port(busy, wait=0)
        assert e.value.port == busy
    finally:
        close()


def test_port_is_ours_compares_the_code_dir_not_just_liveness(ds, monkeypatch):
    """A second COPY of the app on our port is a foreign listener: we move over. Only the
    same codeDir means 'already running'."""
    import http.client

    class _Resp:
        def __init__(self, body): self._b = body.encode()
        def read(self): return self._b

    class _Conn:
        body = '{}'
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): return _Resp(self.body)
        def close(self): pass

    monkeypatch.setattr(http.client, 'HTTPConnection', _Conn)

    _Conn.body = json.dumps({'ok': True, 'codeDir': ds.HERE})
    assert ds.port_is_ours(1) is True
    _Conn.body = json.dumps({'ok': True, 'codeDir': os.path.join(ds.HERE, 'other-copy')})
    assert ds.port_is_ours(1) is False
    _Conn.body = json.dumps({'hello': 'some other web app'})
    assert ds.port_is_ours(1) is None


def test_runtime_record_round_trips_and_only_deletes_its_own(ds, monkeypatch, tmp_path):
    rt = tmp_path / 'config' / 'runtime.json'
    monkeypatch.setattr(ds, 'RUNTIME_JSON', str(rt))
    ds.runtime_write(8770)
    d = json.loads(rt.read_text(encoding='utf-8'))
    assert d['port'] == 8770 and d['pid'] == os.getpid()

    # Another instance overwrote the record — clearing ours must not remove theirs.
    rt.write_text(json.dumps({'port': 8771, 'pid': os.getpid() + 1}), encoding='utf-8')
    ds.runtime_clear()
    assert rt.exists()

    ds.runtime_write(8770)
    ds.runtime_clear()
    assert not rt.exists()
