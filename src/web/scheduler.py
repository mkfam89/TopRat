#!/usr/bin/env python3
"""scheduler.py — in-process job scheduler for dashboard_server.py.

Runs the deterministic, zero-token jobs (scrape / score / notify / git checkpoint)
from INSIDE the dashboard server, so friends & family need only launch the dashboard
instead of setting up Windows Task Scheduler. Cross-platform (uses sys.executable,
no .bat files). Stdlib only.

Behaviour (per the owner's choices):
  * Jobs fire ONLY while the dashboard is running.
  * Missed runs (server closed at the scheduled time) are NOT auto-run — they are
    flagged so the dashboard can show a "Run now" catch-up button.
  * The schedule is edited from the dashboard /schedule page.

Paths follow the code/data split: the live schedule is <data_root>/config/schedule.json,
shadowing the shipped defaults in config/schedule.default.json (which is what the page
falls back to, so the job list is never empty). Per-job state (last run time + result)
lives in <data_root>/scheduler_state.json and a human-readable transcript is appended to
<data_root>/schedule_log.txt. Step scripts always resolve beside the CODE.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, time, threading, subprocess, calendar
from datetime import datetime, timedelta

HERE = _paths.ROOT
def P(*p): return os.path.join(HERE, *p)

# ---------------- where the schedule + state live ----------------
# The code/data split moved personal files into the data root (pipelib.resolve_data_root).
# cleanup_user_data.py deletes the code-folder copy of config/schedule.json once the data
# root has it, so a HERE-relative path silently resolves to nothing and the /schedule page
# renders an EMPTY job list — jobs stop firing with no error anywhere. Read through pipelib
# so the user copy wins; write through pipelib so edits land in the data root.
#
# pipelib is imported defensively: this module is stdlib-only by design and must keep
# working in a bare/legacy single-folder install (CLAUDE.md — degrade gracefully).
try:
    import pipelib
    _DATA = pipelib.DATA if pipelib.SPLIT else HERE
    def _cfg_read(name): return pipelib.cfg(name)
    def _cfg_write(name): return pipelib.cfg_write(name)
except Exception:                     # no pipelib / legacy layout: everything beside the code
    pipelib = None
    _DATA = HERE
    def _cfg_read(name): return P('config', name)
    def _cfg_write(name):
        os.makedirs(P('config'), exist_ok=True)
        return P('config', name)

SCHED_PATH = _cfg_write('schedule.json')          # canonical WRITE target (data root once split)
SCHED_DEFAULT = P('config', 'schedule.default.json')   # shipped job definitions, versioned with the code
STATE_PATH = os.path.join(_DATA, 'scheduler_state.json')
LEGACY_STATE = P('scheduler_state.json')          # pre-split location, read once if the new one is absent
LOG_PATH = os.path.join(_DATA, 'schedule_log.txt')

# The pending-job cap (backlog.py). Imported defensively for the same reason pipelib is: this
# module must keep working in a bare install. No backlog module => nothing is ever paused.
try:
    import backlog
except Exception:
    backlog = None

_JOB_LOCK = threading.Lock()      # serialize job execution (never run two at once)
_STATE_LOCK = threading.Lock()    # guard the state file
# Jobs currently executing: {name: start_monotonic}. A dict, not a set, so the UI can show
# how long the run has been going and estimate what is left (against lastDurationSec).
# Truthiness and `name in _RUNNING` behave the same as the old set, so callers are unchanged.
_RUNNING = {}

# The BACKGROUND TAIL (`after_steps`) — steps that run after the job is reported DONE.
#
# Why it exists: the board renders from candidates.csv, which `jobpipe.py candidates` writes in
# about a second. salary_probe.py then ran for ~4 MINUTES inside the same `steps` list for a
# 2-of-88 hit rate, and because it held _RUNNING the whole time, the board's "Searching…" panel
# sat there for four minutes after the jobs it was waiting for were already on disk.
#
# So a tail step is deliberately invisible to status_all(): it is NOT in _RUNNING, it does not
# set runningJob, and it adds no row to the /schedule page. `_AFTER` exists for the log and for
# the overlap guard, not for the UI.
#
# It holds NO _JOB_LOCK, so a four-minute price lookup can never hold up the next search. The
# trade is that a tail can overlap the next scheduled job; that is safe here (the tail reads
# candidates.csv and writes salary_cache.json, which nothing in `steps` writes), but two TAILS
# writing salary_cache.json at once is not — hence _AFTER_LOCK, non-blocking. A skipped tail
# costs nothing: the cache is durable, so the jobs it missed are simply probed on the next run.
_AFTER_LOCK = threading.Lock()
_AFTER = {}                       # {name: start_time} — logging + overlap guard only

DEFAULT_TICK = 30
DEFAULT_GRACE = 300

# "May be stuck" thresholds. A run is flagged once it exceeds STUCK_FACTOR x its own previous
# duration, but never before STUCK_FLOOR — otherwise a job that last finished in 0.2s (an empty
# tailor queue) would be called stuck two seconds in. Advisory only; nothing is killed.
STUCK_FACTOR = 3
STUCK_FLOOR = 600

# ---------------- config + state io ----------------
def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None

# Fields the USER owns — set on the /schedule page, never overwritten by the defaults.
# Everything else about a job (label, type, steps) belongs to the CODE.
# archive_age / archive_age_unit are a job PARAMETER, not a time — but the user owns them
# (set on the archive card of the /schedule page), so they must survive the defaults merge
# the same way. archive.py reads them straight out of schedule.json.
USER_FIELDS = ('enabled', 'at', 'days', 'weekday', 'day',
               'interval_minutes', 'window_start', 'window_end',
               'archive_age', 'archive_age_unit')

def load_schedule():
    """The shipped job definitions, with the user's times and on/off state applied.

    Two problems this solves. First, falling back to SCHED_DEFAULT (instead of an empty
    dict) guarantees the /schedule page always lists the jobs: a fresh install, a wiped
    data root, or a corrupt user copy shows the stock schedule with its on/off switches
    rather than a blank page that looks like the scrapers were deleted.

    Second, the defaults are the source of truth for label/type/steps. A user copy is
    written once and then never re-seeded, so a renamed job, a reworded label, or a
    changed step list would otherwise never reach an existing install. Only USER_FIELDS
    are read back from the user copy.
    """
    base = _read_json(SCHED_DEFAULT) or {}
    user = _read_json(_cfg_read('schedule.json')) or {}
    cfg = base or user
    jobs = dict(cfg.get('jobs') or {})
    for name, ujob in (user.get('jobs') or {}).items():
        if not isinstance(ujob, dict):
            continue
        if name in jobs:
            jobs[name] = dict(jobs[name], **{k: v for k, v in ujob.items() if k in USER_FIELDS})
        else:
            jobs[name] = ujob          # a job the user has that the code no longer ships
    cfg = dict(cfg)
    cfg['jobs'] = jobs
    for k in ('tick_seconds', 'grace_seconds'):
        if k in user:
            cfg[k] = user[k]
    cfg.setdefault('tick_seconds', DEFAULT_TICK)
    cfg.setdefault('grace_seconds', DEFAULT_GRACE)
    return cfg

def save_schedule(cfg):
    """Persist merged schedule config atomically; returns the stored config."""
    os.makedirs(os.path.dirname(SCHED_PATH) or '.', exist_ok=True)
    tmp = SCHED_PATH + '.tmp'
    text = json.dumps(cfg, indent=2)
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text); f.flush()
        try: os.fsync(f.fileno())
        except OSError: pass
    os.replace(tmp, SCHED_PATH)
    return cfg

def _load_state():
    # Prefer the data root; fall back once to the pre-split copy beside the code so an
    # existing install keeps its lastRunAt history (and doesn't flag every job as missed)
    # the first time it starts after the migration. The next _save_state lands in the
    # data root, after which the legacy file is ignored.
    state = _read_json(STATE_PATH)
    if state is None and LEGACY_STATE != STATE_PATH:
        state = _read_json(LEGACY_STATE)
    return state or {}

def _save_state(state):
    with _STATE_LOCK:
        os.makedirs(os.path.dirname(STATE_PATH) or '.', exist_ok=True)
        tmp = STATE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(state, indent=2)); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        os.replace(tmp, STATE_PATH)

LOG_MAX_BYTES = 1_000_000
LOG_KEEP_LINES = 500

def _rotate_log():
    """Cap schedule_log.txt so it cannot grow unbounded.

    Job steps pipe their subprocess stdout/stderr straight into this file, so it
    reached 793KB before rotation existed. Keeps the newest LOG_KEEP_LINES lines.
    Never called while a subprocess holds the handle open.
    """
    try:
        if os.path.getsize(LOG_PATH) <= LOG_MAX_BYTES:
            return
    except OSError:
        return
    try:
        with open(LOG_PATH, encoding='utf-8', errors='replace') as f:
            kept = f.readlines()[-LOG_KEEP_LINES:]
        tmp = LOG_PATH + '.tmp'
        stamp = datetime.now().isoformat(timespec='seconds')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(f'[{stamp}] --- log rotated, kept last {len(kept)} lines ---\n')
            f.writelines(kept); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        os.replace(tmp, LOG_PATH)
    except Exception:
        pass

def _log(line):
    _rotate_log()
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().isoformat(timespec="seconds")}] {line}\n')
    except Exception:
        pass

# ---------------- trigger math ----------------
def _hhmm(s, default=(0, 0)):
    try:
        h, m = str(s).split(':'); return int(h), int(m)
    except Exception:
        return default

def _at_dt(date, hhmm):
    h, m = hhmm
    return datetime(date.year, date.month, date.day, h, m)

def _triggers_on(job, date):
    """All scheduled datetimes for this job on the given date (may be empty)."""
    t = job.get('type')
    wd = date.weekday()
    if t == 'daily':
        return [_at_dt(date, _hhmm(job.get('at', '00:00')))] if wd in job.get('days', [0,1,2,3,4,5,6]) else []
    if t == 'weekly':
        return [_at_dt(date, _hhmm(job.get('at', '00:00')))] if wd == job.get('weekday', 6) else []
    if t == 'interval_window':
        if wd not in job.get('days', [0,1,2,3,4,5,6]):
            return []
        start = _at_dt(date, _hhmm(job.get('window_start', '09:00')))
        end = _at_dt(date, _hhmm(job.get('window_end', '17:00')))
        step = max(1, int(job.get('interval_minutes', 60)))
        out, cur = [], start
        while cur <= end:
            out.append(cur); cur += timedelta(minutes=step)
        return out
    if t == 'monthly':
        last = calendar.monthrange(date.year, date.month)[1]
        dom = min(int(job.get('day', 1)), last)   # clamp e.g. day 31 to the month's last day
        return [_at_dt(date, _hhmm(job.get('at', '04:00')))] if date.day == dom else []
    return []

def most_recent_trigger(job, now):
    """Latest scheduled datetime <= now, scanning back up to ~2 months (covers monthly). None if never."""
    for d in range(0, 62):
        date = (now - timedelta(days=d)).date()
        past = [t for t in _triggers_on(job, date) if t <= now]
        if past:
            return max(past)
    return None

def next_trigger(job, now):
    for d in range(0, 62):
        date = (now + timedelta(days=d)).date()
        fut = [t for t in _triggers_on(job, date) if t > now]
        if fut:
            return min(fut)
    return None

def _parse_iso(s):
    try: return datetime.fromisoformat(s)
    except Exception: return None

def _due_info(name, job, state, now, grace):
    """Return (auto_due, missed, mrt) for an enabled job."""
    mrt = most_recent_trigger(job, now)
    if mrt is None:
        return False, False, None
    js = state.get('jobs', {}).get(name, {})
    last = _parse_iso(js.get('lastRunAt', '')) if js.get('lastRunAt') else None
    baseline = last or _parse_iso(state.get('firstSeen', '')) or now
    due = mrt > baseline
    if not due:
        return False, False, mrt
    within_grace = (now - mrt).total_seconds() <= grace
    return within_grace, (not within_grace), mrt

# ---------------- the pending-job cap ----------------
# Everything about WHAT the cap counts lives in backlog.py. This file only asks it two
# questions, and answers "no pause" whenever the module is missing or throws — a scheduler
# that stops running jobs because a helper raised would be a far worse failure than one that
# over-fetches for a day.
def _backlog_state():
    if backlog is None:
        return {'count': 0, 'cap': 0, 'enabled': False, 'over': False, 'paused': False}
    try:
        return backlog.state()
    except Exception as e:
        _log('backlog check failed (searches left running): %s' % e)
        return {'count': 0, 'cap': 0, 'enabled': False, 'over': False, 'paused': False}


def _paused_by_backlog(job):
    if backlog is None:
        return False
    try:
        return bool(backlog.pauses(job))
    except Exception:
        return False


# ---------------- execution ----------------
def _run_steps(name, steps, tag=''):
    """Run a list of steps sequentially; return 'ok' or the first failure string.

    Shared by the foreground `steps` and the background `after_steps` so the two can never
    drift on how a step is resolved, logged or timed out.
    """
    for step in steps or []:
        script = step[0]
        # Steps name a bare script ('scrape.py'); _paths.script() finds it under
        # src/<group>/, so moving a file between groups never breaks a saved schedule.
        cmd = [sys.executable, _paths.script(script)] + [str(x) for x in step[1:]]
        _rotate_log()
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as lf:
                lf.write(f'[{datetime.now().isoformat(timespec="seconds")}] '
                         f'{name}{tag}: $ {" ".join(cmd[1:])}\n')
                rc = subprocess.run(cmd, cwd=HERE, stdout=lf, stderr=lf, timeout=1800).returncode
        except Exception as e:
            return f'failed: {script} ({e})'
        if rc != 0:
            return f'failed: {script} exit {rc}'
    return 'ok'


def _start_after(name, job):
    """Kick off the job's `after_steps` in the background. Call AFTER _JOB_LOCK is released.

    Returns True if a tail was started. Never raises: a tail is by definition the part of the
    run nobody is waiting on, so a failure here must not colour the job's own result.
    """
    steps = job.get('after_steps') or []
    if not steps:
        return False
    if not _AFTER_LOCK.acquire(blocking=False):
        # See _AFTER_LOCK: the cache is durable, so a skipped tail costs a delay, not data.
        _log(f'{name}: after-steps skipped, another background tail is still running')
        return False

    def _tail():
        t0 = time.time()
        _AFTER[name] = t0
        result = 'ok'
        _log(f'{name}: after-steps START (background)')
        try:
            result = _run_steps(name, steps, tag=' [after]')
        except Exception as e:
            result = f'failed: {e}'
        finally:
            dur = round(time.time() - t0, 1)
            # Merged INTO the job's own state row rather than given a row of its own: the tail
            # is part of this job, and a second row would put it on the /schedule page, which
            # is exactly what "invisible tail" rules out.
            state = _load_state()
            rec = state.setdefault('jobs', {}).setdefault(name, {})
            rec['lastAfterResult'] = result
            rec['lastAfterDurationSec'] = dur
            rec['lastAfterAt'] = datetime.now().isoformat(timespec='seconds')
            _save_state(state)
            _log(f'{name}: after-steps DONE {result} ({dur}s)')
            _AFTER.pop(name, None)
            _AFTER_LOCK.release()

    threading.Thread(target=_tail, daemon=True).start()
    return True


def run_job(name, manual=False):
    """Run a job's steps sequentially. Serialized: refuses if any job is running.
    Returns a small dict describing the outcome (or start, for manual runs).

    A job may also declare `after_steps` — the background tail (see _AFTER_LOCK). Those run
    once `steps` succeed, AFTER this job is reported DONE and the lock is released, so the
    duration and the running state this returns describe `steps` only. That is the point: the
    tail is work nothing is waiting on, and holding the job open for it made the board look
    stuck long after the postings had landed."""
    cfg = load_schedule()
    job = cfg.get('jobs', {}).get(name)
    if not job:
        # Wording note: these two strings are shown to the USER (schedule toast, empty-board
        # panel), so they say "scheduled task" — "job" in this app means a job POSTING. The
        # phrase "unknown scheduled task" is also matched by cards.html runSearchNow(), which
        # tries the next name only for a missing one; keep the words "unknown" + "task".
        return {'ok': False, 'error': f'unknown scheduled task {name!r}'}
    if not _JOB_LOCK.acquire(blocking=False):
        return {'ok': False, 'error': 'another scheduled task is already running; try again shortly'}
    _RUNNING[name] = time.time()
    def _work():
        t0 = _RUNNING.get(name, time.time())
        result = 'ok'
        _log(f'{name}: START ({"manual" if manual else "scheduled"})')
        try:
            result = _run_steps(name, job.get('steps', []))
        finally:
            dur = round(time.time() - t0, 1)
            state = _load_state()
            state.setdefault('jobs', {})[name] = {
                'lastRunAt': datetime.now().isoformat(timespec='seconds'),
                'lastResult': result, 'lastDurationSec': dur,
                'lastTrigger': 'manual' if manual else 'scheduled'}
            _save_state(state)
            _log(f'{name}: DONE {result} ({dur}s)')
            _RUNNING.pop(name, None)
            _JOB_LOCK.release()
        # Only now — the lock is free, _RUNNING is clear and the job already reads as DONE, so
        # the board stops waiting here rather than when the last price lookup returns. Gated on
        # 'ok' because every tail so far prices the jobs a failed `steps` never produced.
        if result == 'ok':
            _start_after(name, job)
    th = threading.Thread(target=_work, daemon=True)
    th.start()
    if manual:
        return {'ok': True, 'started': name}
    th.join()  # scheduled path waits so the tick loop stays serialized
    return {'ok': True, 'ran': name}

# ---------------- status for the UI ----------------
def status_all():
    cfg = load_schedule()
    grace = int(cfg.get('grace_seconds', DEFAULT_GRACE))
    state = _load_state()
    now = datetime.now()
    tnow = time.time()
    jobs = {}
    running_now = None
    bstate = _backlog_state()
    bpaused = bool(bstate.get('paused'))
    for name, job in cfg.get('jobs', {}).items():
        js = state.get('jobs', {}).get(name, {})
        held = bpaused and _paused_by_backlog(job)
        auto, missed, mrt = (False, False, None)
        if job.get('enabled', True):
            auto, missed, mrt = _due_info(name, job, state, now, grace)
        # A run the cap deliberately held back is NOT a missed run. Missed means "the app was
        # closed and you may want to catch up", and it offers a Run-now button; this one was a
        # decision the app made and re-running it is the opposite of what the cap is for. Left
        # as missed, a paused search would also light the nav pill amber for as long as the
        # backlog lasts, which would train the user to ignore the pill.
        if held:
            missed = False
        nt = next_trigger(job, now)
        # Live progress for a run in flight. The only estimate available is the previous
        # run's duration, so etaSec is a guess and goes negative-clamped to 0 once the run
        # outlasts it (the UI then says "still running" rather than counting up a fake ETA).
        started = _RUNNING.get(name)
        elapsed = round(tnow - started, 1) if started else None
        prev = js.get('lastDurationSec')
        eta = None
        if elapsed is not None and isinstance(prev, (int, float)) and prev > 0:
            eta = max(0, round(prev - elapsed))
        # Stuck detection. A run has no progress signal — a step is one opaque subprocess —
        # so the only evidence available is "this is taking far longer than it ever has".
        # Advisory, never an action: the run is NOT killed, because a slow network day and a
        # genuinely hung scrape look identical from here. The UI says "may be stuck" and lets
        # the user decide. Steps carry their own hard 1800s subprocess timeout underneath.
        stuck = False
        if elapsed is not None:
            limit = max(prev * STUCK_FACTOR, STUCK_FLOOR) if isinstance(prev, (int, float)) and prev > 0 else STUCK_FLOOR
            stuck = elapsed > limit
        if started:
            running_now = name
        res = js.get('lastResult')
        jobs[name] = {
            'startedAt': datetime.fromtimestamp(started).isoformat(timespec='seconds') if started else None,
            'elapsedSec': elapsed,
            'etaSec': eta,
            'stuck': stuck,
            # A finished run that did not end in 'ok'. The pill used to ignore lastResult
            # entirely, so a failed nightly scrape still showed a calm green "next run" chip.
            'failed': bool(res and res != 'ok' and not started),
            'label': job.get('label', name),
            'enabled': job.get('enabled', True),
            # Which family the task belongs to ('search' / 'upkeep' / 'resumes'). Served so a
            # caller can tell a SEARCH run from any other run without hardcoding task names —
            # the nav popover uses it to re-show the board's paused bubble on a search run.
            'group': job.get('group'),
            'type': job.get('type'),
            'interval_minutes': job.get('interval_minutes'),
            'window_start': job.get('window_start'),
            'window_end': job.get('window_end'),
            'at': job.get('at'),
            'weekday': job.get('weekday'),
            'days': job.get('days'),
            'lastRunAt': js.get('lastRunAt'),
            'lastResult': js.get('lastResult'),
            'lastDurationSec': js.get('lastDurationSec'),
            'running': name in _RUNNING,
            'missed': bool(missed),
            # Held back by the pending-job cap rather than by the user's own on/off switch.
            # Kept separate from `enabled` on purpose: the switch is the user's setting and the
            # UI must keep showing it as ON, or clearing the backlog would look like it silently
            # turned the searches back on behind their back.
            'pausedByBacklog': bool(held),
            'nextRun': nt.isoformat(timespec='minutes') if nt else None}
    # ---- one headline state, decided HERE ----
    # The nav pill is a single chip and can only say one thing, so the priority order is
    # settled server-side rather than in the page's JS: a caller that shows this must show
    # the same answer everywhere. Worst-actionable-news first — a stuck or failed run is
    # something the user has to act on; a missed run is a nudge; "next run" is the resting
    # state. `nextRun` always rides along so the chip can show the time in every state.
    upcoming = sorted((v['nextRun'], k) for k, v in jobs.items() if v['enabled'] and v['nextRun'])
    nxt = upcoming[0][1] if upcoming else None
    missed = [k for k, v in jobs.items() if v['missed']]

    def _first(pred):
        return next((k for k, v in jobs.items() if pred(v)), None)

    stuck_job = _first(lambda v: v['stuck'])
    failed_job = _first(lambda v: v['failed'] and v['enabled'])
    held_job = _first(lambda v: v['pausedByBacklog'] and v['enabled'])
    if stuck_job:
        state_kind, state_job = 'stuck', stuck_job
    elif running_now:
        state_kind, state_job = 'running', running_now
    elif failed_job:
        state_kind, state_job = 'failed', failed_job
    # Ranked above 'missed' and 'idle': a chip saying "next run 9:00" while the cap is holding
    # that run back would be a lie, and the pause is something only the user can lift.
    elif held_job:
        state_kind, state_job = 'paused', held_job
    elif missed:
        state_kind, state_job = 'missed', missed[0]
    elif nxt:
        state_kind, state_job = 'idle', nxt
    else:
        state_kind, state_job = 'off', None

    return {'now': now.isoformat(timespec='seconds'),
            'anyRunning': bool(_RUNNING),
            'state': state_kind,
            'stateJob': state_job,
            'stateLabel': jobs.get(state_job, {}).get('label') if state_job else None,
            'stateDetail': jobs.get(state_job, {}).get('lastResult') if state_kind == 'failed' else None,
            'stateElapsedSec': jobs.get(state_job, {}).get('elapsedSec') if state_job else None,
            'runningJob': running_now,
            'runningLabel': jobs.get(running_now, {}).get('label') if running_now else None,
            'missedCount': len(missed),
            # The cap, so every surface that shows the pill can explain the pause without a
            # second request. Shape = backlog.state() + nothing.
            'backlog': bstate,
            'nextJob': nxt,
            'nextLabel': jobs[nxt]['label'] if nxt else None,
            'nextRun': upcoming[0][0] if upcoming else None,
            'tick_seconds': cfg.get('tick_seconds', DEFAULT_TICK),
            'jobs': jobs}

# ---------------- background loop ----------------
def _loop():
    # Seed firstSeen so triggers from before the app ever ran aren't counted as "missed".
    state = _load_state()
    if not state.get('firstSeen'):
        state['firstSeen'] = datetime.now().isoformat(timespec='seconds')
        _save_state(state)
    while True:
        cfg = load_schedule()
        tick = int(cfg.get('tick_seconds', DEFAULT_TICK))
        grace = int(cfg.get('grace_seconds', DEFAULT_GRACE))
        try:
            state = _load_state()
            now = datetime.now()
            # One check per tick, not one per job: the count is the same for all of them, and
            # it reads three files. Only recomputed here, so a run that lands mid-tick cannot
            # change the answer half way down the job list.
            held = bool(_backlog_state().get('paused'))
            for name, job in cfg.get('jobs', {}).items():
                if not job.get('enabled', True):
                    continue
                # The cap holds back the jobs that ADD postings, and only the SCHEDULED path:
                # run_job() itself is left open, so the /schedule "Run now" button and the
                # board's "Run a search" panel still work. A press is the user asking for it,
                # and the cap exists to stop unattended growth, not to overrule a decision.
                # Nothing is written here — drop back under the cap and the next tick runs.
                if held and _paused_by_backlog(job):
                    continue
                auto, missed, mrt = _due_info(name, job, state, now, grace)
                if auto and not _RUNNING:
                    run_job(name, manual=False)
                    state = _load_state()  # refresh after run
        except Exception as e:
            _log(f'loop error: {e}')
        time.sleep(max(5, tick))

INTERRUPTED = 'interrupted: the app closed mid-run'

def _recover_interrupted():
    """Record runs that were killed with the process, so they don't vanish silently.

    run_job writes its result in a `finally`, which covers a step raising — but not the app
    being closed, the machine sleeping, or a hard kill while a subprocess is out. In those
    cases scheduler_state.json keeps the PREVIOUS run's 'ok' forever, so the dashboard cheerily
    reports a good last run that never actually finished, and the user never learns the nightly
    scrape has been dying half way for a week.

    The transcript already has the evidence: run_job logs `<name>: START` and `<name>: DONE`.
    A START with no DONE after it is an interrupted run. Reading the tail of the log is enough
    (it is rotated to the last 500 lines) and needs no new state file. Deterministic, no Claude.
    """
    try:
        with open(LOG_PATH, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()[-400:]
    except Exception:
        return
    last = {}                                  # name -> ('START'|'DONE', iso stamp)
    for ln in lines:
        stamp = ln[1:ln.find(']')] if ln.startswith('[') and ']' in ln else ''
        body = ln.split('] ', 1)[-1].strip()
        if ': START' in body:
            last[body.split(':', 1)[0]] = ('START', stamp)
        elif ': DONE' in body:
            last[body.split(':', 1)[0]] = ('DONE', stamp)
    orphans = {n: s for n, (kind, s) in last.items() if kind == 'START'}
    if not orphans:
        return
    state = _load_state()
    changed = False
    for name, stamp in orphans.items():
        js = state.setdefault('jobs', {}).setdefault(name, {})
        # Only overwrite when the recorded run is OLDER than the orphaned START — otherwise a
        # later successful run has already superseded it and the state is correct as it stands.
        prev_at = _parse_iso(js.get('lastRunAt', '') or '')
        start_at = _parse_iso(stamp)
        if start_at and (prev_at is None or prev_at < start_at):
            js.update({'lastRunAt': stamp, 'lastResult': INTERRUPTED,
                       'lastDurationSec': None, 'lastTrigger': js.get('lastTrigger', 'scheduled')})
            changed = True
            _log(f'{name}: recovered — the app closed during the run that started {stamp}')
    if changed:
        _save_state(state)

def start():
    """Launch the scheduler background thread (call once from the server)."""
    _recover_interrupted()
    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    _log('scheduler started')
    return th
