"""backlog.py — the pending-job cap that pauses the searches.

Two things are load-bearing and both are tested here:

  1. The COUNT must equal the board's own Pending list. The banner offers to skip or delete
     "all 55", so a count that drifts from the list underneath it would act on a set the user
     never saw. Applied, skipped, banned and duplicated rows are the four ways it can drift.
  2. The pause must be COMPUTED, never stored. Nothing may write an `enabled: false` into the
     schedule, because that is the user's own switch and a stored pause could get stuck on.

Read-only over the repo: every fixture is built under tmp_path and pointed at through
TOP_RAT_DATA, so no test can touch the real tracker.
"""
import csv
import json
import os

import pytest

from tracker import TRACKING_COLS, CANDIDATE_COLS


# Modules that latch their paths onto pipelib.DATA at import time. The fixture below
# reloads them under a scratch root and MUST reload them back, or the scratch path
# outlives the test.
DATA_BOUND = ('pipelib', 'blocklist', 'backlog', 'scheduler')


def _reload_data_bound():
    """Re-resolve DATA_BOUND against whatever the data root currently is."""
    import importlib
    import sys
    for name in DATA_BOUND:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)


def _write_csv(path, cols, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in cols})


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A scratch data root, with a factory for the board's contents.

    backlog reads its paths off pipelib at import time, so the module is reloaded after
    TOP_RAT_DATA is set. Returning the reloaded module (rather than importing it at the top
    of the file) is what keeps the real data root out of reach.

    BOTH names are set, and the legacy one is DELETED rather than left alone: pipelib.env
    prefers TOP_RAT_* over the legacy JOB_AGENT_*, so a TOP_RAT_DATA inherited from the
    caller's shell would outrank a fixture that only set the old name — and the tests would
    silently run against whatever data root that shell pointed at instead of tmp_path.
    """
    root = tmp_path / 'data'
    (root / 'config').mkdir(parents=True)
    monkeypatch.setenv('TOP_RAT_DATA', str(root))
    monkeypatch.delenv('JOB_AGENT_DATA', raising=False)

    def build(tracked=(), candidates=(), applied=(), skipped=(), bookmarked=(),
              banned=(), cap=50):
        _write_csv(root / 'tracking.csv', TRACKING_COLS, tracked)
        _write_csv(root / 'candidates.csv', CANDIDATE_COLS, candidates)
        (root / 'job_tracker.json').write_text(json.dumps({
            'applied': {i: '2026-08-01' for i in applied},
            'skipped_jobs': list(skipped),
            'bookmarked': list(bookmarked),
            'job_details': {}, 'notes': {}, 'answers': {}}), encoding='utf-8')
        (root / 'config' / 'employer_blocklist.json').write_text(json.dumps({
            'version': 1, 'employers': {b: {'display': b} for b in banned}}), encoding='utf-8')
        (root / 'config' / 'gui_settings.json').write_text(
            json.dumps({'backlogCap': cap}), encoding='utf-8')
        # These three resolve their paths at IMPORT time off pipelib's data root, so all
        # three have to be reloaded once the scratch root is set — blocklist included, or it
        # keeps reading the real employer_blocklist.json and no ban ever matches here.
        import importlib
        import pipelib
        import blocklist
        import backlog
        importlib.reload(pipelib)
        importlib.reload(blocklist)
        importlib.reload(backlog)
        assert str(root) in pipelib.DATA, 'the scratch data root is not in effect'
        return backlog

    yield build

    # Teardown is as load-bearing as setup, and its absence was a real bug. monkeypatch
    # restores TOP_RAT_DATA on its own, but not until AFTER this point (finalizers run in
    # reverse dependency order), and the reloads above latched the scratch path into module
    # globals — putting the variable back does not move a module that already resolved. So
    # undo the env FIRST, then reload onto the real root.
    #
    # Without these two lines the scratch root outlived the test and every later test in the
    # session read its config out of a tmp_path pytest had already deleted. That is the whole
    # "config goldens are order-dependent" defect: 8 tests in test_blocklist, test_parse_card
    # and test_scoring passed alone and failed in a full run, because test_backlog is second
    # in collection order and poisoned everything after it.
    monkeypatch.undo()
    _reload_data_bound()


def _jobs(prefix, n, **kw):
    return [dict(id='%s%02d' % (prefix, i), company='Co%d' % i, role='Eng',
                 status='pending', **kw) for i in range(n)]


# --------------------------------------------------------------------------- the count

def test_pending_excludes_applied_and_skipped(board):
    """A decided job must never hold the pause on. Counting them would mean the cap could
    only ever be cleared by deleting the board's whole history."""
    bl = board(tracked=_jobs('t', 10), applied=['t00', 't01'], skipped=['t02'])
    assert bl.count() == 7
    assert not {'t00', 't01', 't02'} & set(bl.pending_ids())


def test_pending_excludes_banned_employers(board):
    """The board hides banned employers from every list except Banned, so counting them would
    promise more jobs than the Pending list can show."""
    tracked = _jobs('t', 5)
    tracked[0]['company'] = tracked[1]['company'] = 'Felix Pago'
    bl = board(tracked=tracked, banned=['felixpago'])
    assert bl.count() == 3


def test_a_job_in_both_csvs_is_counted_once(board):
    """A tailored job appears in tracking.csv AND candidates.csv. Counting it twice would put
    the banner's number above the number of cards on screen."""
    bl = board(tracked=_jobs('t', 3), candidates=_jobs('t', 3) + _jobs('c', 2))
    ids = bl.pending_ids()
    assert len(ids) == len(set(ids)) == 5


def test_rows_without_an_id_are_ignored(board):
    bl = board(tracked=_jobs('t', 3) + [{'id': '', 'company': 'X'}, {'company': 'Y'}])
    assert bl.count() == 3


def test_bookmarked_pending_jobs_are_counted_and_reported(board):
    """Bookmarked jobs are still waiting on a decision, so they count — but the delete
    confirmation names them, which is what this field is for."""
    bl = board(tracked=_jobs('t', 5), bookmarked=['t01', 't02'])
    st = bl.state()
    assert st['count'] == 5 and st['bookmarked'] == 2


# ---------------------------------------------------------------------------- the cap

def test_pauses_at_the_cap_not_one_past_it(board):
    assert board(tracked=_jobs('t', 50), cap=50).state()['paused'] is True
    assert board(tracked=_jobs('t', 49), cap=50).state()['paused'] is False


def test_cap_zero_disables_the_pause_but_keeps_the_count(board):
    """Turning the feature off must not hide how full the board is."""
    st = board(tracked=_jobs('t', 80), cap=0).state()
    assert st['enabled'] is False and st['paused'] is False and st['count'] == 80


def test_a_bad_cap_value_falls_back_to_the_default(board):
    """gui_settings.json is hand-editable. A junk value must not take the scheduler down."""
    bl = board(tracked=_jobs('t', 5), cap='fifty')
    assert bl.cap() == bl.DEFAULT_CAP


def test_a_missing_gui_settings_uses_the_default(board):
    bl = board(tracked=_jobs('t', 5))
    os.remove(os.path.join(os.path.dirname(bl.TRACKING_CSV), 'config', 'gui_settings.json'))
    assert bl.cap() == bl.DEFAULT_CAP


def test_an_empty_board_is_never_paused(board):
    assert board().state() == {'count': 0, 'cap': 50, 'enabled': True, 'over': False,
                               'paused': False, 'bookmarked': 0, 'groups': ['search']}


# --------------------------------------------------------------- which tasks are held

def test_only_the_search_group_is_paused(board):
    """The cap holds back what ADDS postings. The archive job drains the board, so pausing it
    would make a full board harder to clear, not easier."""
    bl = board()
    assert bl.pauses({'group': 'search'}) is True
    assert bl.pauses({'group': 'upkeep'}) is False
    assert bl.pauses({'group': 'resumes'}) is False
    assert bl.pauses({'group': None}) is False
    assert bl.pauses({}) is False
    assert bl.pauses(None) is False


def test_the_shipped_search_tasks_are_the_ones_held(board):
    """Guards the wiring, not the list: `group` lives in schedule.default.json, and a new
    search task added there must be held without anyone editing backlog.py."""
    bl = board()
    import scheduler
    jobs = scheduler.load_schedule()['jobs']
    held = {n for n, j in jobs.items() if bl.pauses(j)}
    assert held == {'radar', 'daily_scrape', 'weekly_sweep'}


def test_the_pause_is_computed_and_writes_nothing(board, tmp_path):
    """The whole auto-resume promise rests on this. If the gate ever wrote the pause into
    schedule.json it would also have to remember to undo it, and a half-undone pause would
    leave the user's searches switched off with no explanation."""
    bl = board(tracked=_jobs('t', 60), cap=50)
    import importlib
    import scheduler
    importlib.reload(scheduler)
    assert scheduler.status_all()['state'] == 'paused'
    for name in ('radar', 'daily_scrape', 'weekly_sweep'):
        job = scheduler.status_all()['jobs'][name]
        assert job['pausedByBacklog'] is True
        assert job['enabled'] is True, 'the user switch must never be flipped'
        assert job['missed'] is False, 'a held run is not a missed run'
    assert not os.path.exists(str(tmp_path / 'data' / 'config' / 'schedule.json'))


def test_dropping_under_the_cap_resumes_with_no_other_action(board):
    bl = board(tracked=_jobs('t', 60), cap=50)
    import importlib
    import scheduler
    importlib.reload(scheduler)
    assert scheduler.status_all()['state'] == 'paused'
    # the only change: the user cleared the board
    bl2 = board(tracked=_jobs('t', 10), cap=50)
    importlib.reload(scheduler)
    st = scheduler.status_all()
    assert st['state'] != 'paused'
    assert st['jobs']['radar']['pausedByBacklog'] is False


def test_a_broken_backlog_module_never_stops_the_scheduler(monkeypatch):
    """Fail OPEN. Over-fetching for a day is recoverable; a scheduler that silently stops
    running every task because a helper raised is not."""
    import scheduler
    monkeypatch.setattr(scheduler.backlog, 'state', lambda: 1 / 0)
    assert scheduler._backlog_state()['paused'] is False
    monkeypatch.setattr(scheduler, 'backlog', None)
    assert scheduler._backlog_state()['paused'] is False
    assert scheduler._paused_by_backlog({'group': 'search'}) is False
