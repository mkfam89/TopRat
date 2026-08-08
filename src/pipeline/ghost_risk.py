#!/usr/bin/env python3
"""ghost_risk.py — heuristic ghost-job risk scorer. ADVISORY ONLY.

Computes a soft risk level from signals already present in candidates.csv / job_details.
This is a GUESS, never a verdict: NOTHING in the pipeline may skip, reject, reclassify,
or decline to tailor a job because of this score. Only the manual employer blocklist
(blocklist.py) can skip jobs. The dashboard surfaces the score with an explicit
"heuristic guess" caveat.

Signals:
  STRONG (rare, discriminating)
    salary_width    — implausibly wide posted range (max/min >= ratio OR spread > abs)
    remote_hybrid   — location claims remote AND hybrid/onsite (bait-and-switch tell)
    repost          — same company+role OR applyUrl under >=2 ids, dates >= gap apart
  WEAK (common, contextual)
    no_salary       — no salary posted (blank/undisclosed, or salarySource band/estimated)
    stale_active    — foundDate older than N days and still pending/candidate

no_salary is deliberately WEAK: most HiringCafe postings omit salary, so on its own it
does not distinguish a ghost from an ordinary listing. It only nudges a job that already
has a strong signal.

level = high  when >= `strong_for_high` strong signals fire (default 2)
        medium when exactly 1 strong signal fires
        low    when only weak signal(s) fire
        none   otherwise

Thresholds live in config/ghost_risk.json (optional; defaults below). score_job is a
pure function — no I/O. score_all() assembles the cross-job repost index from data the
caller passes in (still no network).
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os
import re
from datetime import datetime

from pipelib import CONFIG, cfg, load_json

GHOST_CFG = cfg('ghost_risk.json')

DEFAULTS = {
    'salary_width_ratio': 2.2,      # max/min at or above this = implausibly wide
    'salary_width_abs': 90000,      # OR absolute spread greater than this
    'stale_days': 60,               # still-active older than this = weak signal
    'repost_gap_days': 14,          # same posting re-listed at least this far apart
    'strong_for_high': 2,           # this many strong signals => level "high"
    'enabled': True,
}

_UNDISCLOSED = {'', 'undisclosed', 'n/a', 'na', 'none', '-', 'doe', 'competitive'}
_SALARY_NUM = re.compile(r'\$?\s*([\d][\d,]*(?:\.\d+)?)\s*([kKmM])?')


def load_cfg():
    cfg = dict(DEFAULTS)
    disk = load_json(GHOST_CFG, None)
    if isinstance(disk, dict):
        cfg.update({k: disk[k] for k in disk if k in DEFAULTS})
    return cfg


def _to_number(num_str, suffix):
    try:
        val = float(num_str.replace(',', ''))
    except ValueError:
        return None
    if suffix and suffix.lower() == 'k':
        val *= 1_000
    elif suffix and suffix.lower() == 'm':
        val *= 1_000_000
    # bare small numbers are almost certainly thousands ("90-130")
    elif val < 1000:
        val *= 1_000
    return val


def parse_salary_range(salary):
    """(min, max) in dollars from a posted salary string, or None if unparseable."""
    if not salary:
        return None
    s = str(salary).strip().lower()
    if s in _UNDISCLOSED:
        return None
    nums = []
    for m in _SALARY_NUM.finditer(str(salary)):
        v = _to_number(m.group(1), m.group(2))
        if v is not None:
            nums.append(v)
    nums = [n for n in nums if n >= 1000]     # drop stray non-pay digits
    if not nums:
        return None
    return (min(nums), max(nums))


def _has_salary(row):
    sal = str(row.get('salary', '') or '').strip().lower()
    if sal and sal not in _UNDISCLOSED:
        return True
    return False


def _salary_is_estimated(row):
    src = str(row.get('salarySource', '') or '').strip().lower()
    if src in ('band', 'estimated', 'jobsworth'):
        return True
    if str(row.get('salaryEstimated', '')).strip().lower() in ('true', '1', 'yes'):
        return True
    return False


def _remote_hybrid_conflict(row):
    loc = str(row.get('location', '') or '').lower()
    if 'remote' not in loc:
        return False
    return any(t in loc for t in ('hybrid', 'onsite', 'on-site', 'on site', 'in-office', 'in office'))


def _days_since(datestr, today=None):
    if not datestr:
        return None
    today = today or datetime.now()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return (today - datetime.strptime(str(datestr)[:10], fmt)).days
        except ValueError:
            continue
    return None


def score_job(row, cfg=None, reposted=False, today=None):
    """Pure scorer for one job row. Returns {'level','reasons','signals'}.

    `reposted` is supplied by the caller (repost needs cross-job history, which would
    be I/O here). Everything else is read from the row dict itself.
    """
    cfg = cfg or load_cfg()
    strong, weak, reasons = [], [], []

    # STRONG: implausibly wide posted range (only when a real range is present)
    rng = parse_salary_range(row.get('salary'))
    if rng and rng[0] > 0:
        lo, hi = rng
        ratio = hi / lo if lo else 0
        spread = hi - lo
        if ratio >= cfg['salary_width_ratio'] or spread > cfg['salary_width_abs']:
            strong.append('salary_width')
            reasons.append(f'implausibly wide salary range (${int(lo):,}–${int(hi):,})')

    # STRONG: remote/hybrid contradiction
    if _remote_hybrid_conflict(row):
        strong.append('remote_hybrid')
        reasons.append('location claims remote and hybrid/onsite (possible bait-and-switch)')

    # STRONG: reposted (caller-computed from history)
    if reposted:
        strong.append('repost')
        reasons.append('same posting re-listed weeks apart (reposted / never seems to close)')

    # WEAK: no salary posted (common; only nudges a job that already looks risky)
    if not _has_salary(row) or _salary_is_estimated(row):
        weak.append('no_salary')
        reasons.append('no salary posted (or only an estimate)')

    # WEAK: stale but still active
    status = str(row.get('status', '') or '').lower()
    if status in ('', 'candidate', 'pending'):
        d = _days_since(row.get('foundDate'), today)
        if d is not None and d > cfg['stale_days']:
            weak.append('stale_active')
            reasons.append(f'still active {d} days after it was found')

    n = len(strong)
    if n >= cfg['strong_for_high']:
        level = 'high'
    elif n == 1:
        level = 'medium'
    elif weak:
        level = 'low'
    else:
        level = 'none'
    return {'level': level, 'reasons': reasons, 'signals': strong + weak}


def build_repost_index(rows, job_details=None, cfg=None):
    """Set of ids that look reposted.

    A genuine repost = the same posting (matched by applyUrl, or by company+role key)
    appearing under **two or more distinct tracker ids** whose foundDates are at least
    `repost_gap_days` apart. Both conditions matter:

    - distinct ids, because a single job re-scraped keeps its id; a re-LISTING gets a
      fresh id;
    - a real time gap, because the same job scraped twice in one week (or a candidate
      row vs. its job_details entry, which use different foundDate conventions) is NOT
      a repost — that pollution is exactly what made an earlier version over-fire.
    """
    cfg = cfg or load_cfg()
    gap = cfg.get('repost_gap_days', 14)
    from blocklist import norm_employer

    def _norm_role(role):
        return re.sub(r'[^a-z0-9]+', '', str(role or '').lower())

    # group -> { id: foundDate }
    by_url, by_key = {}, {}

    def add(jid, company, role, url, found):
        if not jid:
            return
        if url:
            by_url.setdefault(url, {})[jid] = found or ''
        if company and role:
            by_key.setdefault(norm_employer(company) + '|' + _norm_role(role), {})[jid] = found or ''

    for r in rows:
        add(r.get('id') or r.get('trackerId'), r.get('company'), r.get('role'),
            r.get('applyUrl'), r.get('foundDate'))
    for jid, d in (job_details or {}).items():
        parts = jid.split('_', 1)
        add(jid, parts[0] if parts else jid, parts[1] if len(parts) > 1 else '',
            d.get('applyUrl'), d.get('foundDate'))

    def _date(s):
        for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
            try:
                return datetime.strptime(str(s)[:10], fmt)
            except ValueError:
                continue
        return None

    reposted = set()
    for grp in (by_url, by_key):
        for members in grp.values():
            if len(members) < 2:
                continue
            dates = [d for d in (_date(v) for v in members.values()) if d]
            if len(dates) >= 2 and (max(dates) - min(dates)).days >= gap:
                reposted |= set(members.keys())
    return reposted


def score_all(rows, job_details=None, cfg=None, today=None):
    """{ id: {level, reasons} } for a list of candidate/tracking rows. Advisory only.

    Returns an empty dict when disabled in config, so callers can wire it in without
    a feature flag of their own.
    """
    cfg = cfg or load_cfg()
    if not cfg.get('enabled', True):
        return {}
    reposted = build_repost_index(rows, job_details, cfg)
    out = {}
    for r in rows:
        jid = r.get('id') or r.get('trackerId')
        if not jid:
            continue
        res = score_job(r, cfg=cfg, reposted=jid in reposted, today=today)
        if res['level'] != 'none':
            out[jid] = {'level': res['level'], 'reasons': res['reasons']}
    return out


CAVEAT = ('Heuristic guess based on posting signals — not a confirmation the job is '
          'fake. Ghost-job estimates are inexact; use your own judgement.')
