#!/usr/bin/env python3
"""backlog.py — the pending-job cap that pauses the searches.

The searches run whether or not anyone is reading the board, so a week away turns
into hundreds of undecided postings and the board stops being a worklist. This module
is the one place that answers two questions:

    how many jobs are waiting on a decision?   ->  count()
    should the searches hold off?              ->  is_paused()

**Zero tokens, no Claude** (CLAUDE.md hard rule): it reads tracking.csv, candidates.csv
and job_tracker.json and does arithmetic. scheduler.py gates the `search`-group jobs on
`is_paused()`; dashboard_server.py serves `state()` to the board's banner. Both callers
read the SAME function, so the chip and the scheduler can never disagree about whether
the searches are paused.

The pause is COMPUTED, never stored. Nothing writes an `enabled: false` into
schedule.json, so the user's own on/off switches are untouched and the searches resume
by themselves the moment the count drops back under the cap. There is no paused flag to
get stuck on, and no state to repair if the count is edited by hand.

"Pending" is deliberately the same set the board's Pending list shows: every tracked or
candidate job that is not applied, not skipped, and not from a banned employer. Applied
and skipped jobs are decided — counting them would hold the pause on forever.

CLI:
    python src/lib/backlog.py            # human-readable status
    python src/lib/backlog.py --json     # {"count":…, "cap":…, "over":…, "paused":…}
    python src/lib/backlog.py --ids      # the pending ids, one per line
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import csv, json, os, sys

try:
    import pipelib
    TRACKING_CSV = pipelib.TRACKING_CSV
    CANDIDATES_CSV = pipelib.CANDIDATES_CSV
    JSON_PATH = pipelib.JSON_PATH
    _cfg = pipelib.cfg
except Exception:                       # bare/legacy single-folder install — degrade, never crash
    pipelib = None
    _HERE = _paths.ROOT
    TRACKING_CSV = os.path.join(_HERE, 'tracking.csv')
    CANDIDATES_CSV = os.path.join(_HERE, 'candidates.csv')
    JSON_PATH = os.path.join(_HERE, 'job_tracker.json')
    def _cfg(name): return os.path.join(_HERE, 'config', name)

DEFAULT_CAP = 50
# The scheduled jobs the cap holds back. `group` comes from config/schedule.default.json and is
# owned by the CODE (it is not in scheduler.USER_FIELDS), so a user cannot accidentally move the
# git checkpoint into the paused set by editing their schedule. Only the jobs that ADD postings
# are paused: the backup still runs, and the archive job still runs — archiving DRAINS the board,
# so pausing it would make the backlog harder to clear, not easier.
PAUSED_GROUPS = ('search',)


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


def _read_csv(path):
    try:
        with open(path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def cap():
    """The number of pending jobs that pauses the searches. 0 (or less) turns the cap off.

    Lives in gui_settings.json beside the other board numbers, so the Settings page edits it
    the same way it edits the thresholds. A missing key means DEFAULT_CAP, which is what keeps
    the script path working on a machine that has never opened the dashboard.
    """
    s = _read_json(_cfg('gui_settings.json'), {})
    try:
        return int(s.get('backlogCap', DEFAULT_CAP))
    except (TypeError, ValueError):
        return DEFAULT_CAP


def _blocked_check():
    """(fn, ok) — is this company banned? Falls back to 'nothing is banned' when the module
    or the file is unavailable, which can only ever make the count LARGER than the board's.
    That direction is the safe one: the cap trips a little early rather than silently late."""
    try:
        import blocklist as bl
        keys = set(bl.load_blocklist().get('employers', {}).keys())
        if not keys:
            return (lambda co: False), True
        return (lambda co: bool(co) and bl.norm_employer(co) in keys), True
    except Exception:
        return (lambda co: False), False


def pending_ids():
    """Ids of every job waiting on a decision, in board order (tracked first, then candidates).

    Mirrors the board's Pending list exactly — /api/jobs derives status the same way and drops
    banned employers the same way — because the banner names a number the user is about to act
    on, and a count that disagrees with the list underneath it is worse than no count.
    """
    return _pending()[0]


def _pending():
    """(ids, bookmarked_count). One pass, because every caller that wants the count also wants
    to know how many of them the user has marked as worth keeping."""
    tj = _read_json(JSON_PATH, {})
    applied = tj.get('applied', {}) if isinstance(tj.get('applied'), dict) else {}
    skipped = set(tj.get('skipped_jobs', []) or [])
    book = set(tj.get('bookmarked', []) or [])
    is_blocked, _ = _blocked_check()
    out, seen, nbook = [], set(), 0
    for row in _read_csv(TRACKING_CSV) + _read_csv(CANDIDATES_CSV):
        jid = (row.get('id') or '').strip()
        if not jid or jid in seen:
            continue
        seen.add(jid)
        if jid in applied or jid in skipped:
            continue
        if is_blocked(row.get('company', '')):
            continue
        out.append(jid)
        if jid in book:
            nbook += 1
    return out, nbook


def count():
    return len(pending_ids())


def state():
    """The whole answer in one dict — what every caller reads.

    `paused` is `enabled and over`: a cap of 0 reports the true count and pauses nothing, so
    turning the feature off never hides how much is waiting.

    `bookmarked` is how many of the pending jobs the user has starred. It is here so the
    delete confirmation can say so out loud — a bookmark is the one signal that a posting was
    kept on purpose, and deleting 50 jobs without mentioning that 4 of them were starred is
    the kind of thing that is only noticed a week later.
    """
    ids, nbook = _pending()
    c, n = cap(), len(ids)
    enabled = c > 0
    over = enabled and n >= c
    return {'count': n, 'cap': c, 'enabled': enabled, 'over': over, 'paused': over,
            'bookmarked': nbook, 'groups': list(PAUSED_GROUPS)}


def is_paused():
    return state()['paused']


def pauses(job):
    """Would the cap hold this scheduled job back? Group membership only — the caller decides
    whether the cap is currently tripped, so this stays a pure, testable predicate."""
    return isinstance(job, dict) and job.get('group') in PAUSED_GROUPS


def main(argv):
    st = state()
    if '--ids' in argv:
        print('\n'.join(pending_ids()))
        return 0
    if '--json' in argv:
        print(json.dumps(st))
        return 0
    if '--check' in argv:
        # For non-Python callers that can't import this module: a Windows Task's .bat file
        # runs standalone, outside scheduler.py's in-process loop, so it needs its own gate.
        # Exit 1 means "hold off" (paused); exit 0 means "go ahead" (not paused, or cap off).
        if st['paused']:
            print('PAUSED: %d/%d pending, cap reached.' % (st['count'], st['cap']))
            return 1
        print('OK: %d/%d pending.' % (st['count'], st['cap']))
        return 0
    if not st['enabled']:
        print('Backlog cap is off. %d jobs are waiting on a decision.' % st['count'])
    elif st['paused']:
        print('PAUSED: %d jobs are waiting on a decision, and the cap is %d. '
              'The searches hold off until the count drops below %d.'
              % (st['count'], st['cap'], st['cap']))
    else:
        print('OK: %d of %d. The searches run normally.' % (st['count'], st['cap']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
