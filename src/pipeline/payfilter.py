#!/usr/bin/env python3
"""payfilter.py — drop the pending jobs that cannot pay what the user asked for.

The setting is ONE number: profile.json `search.salary_min`, the "Min salary" field on
/setup. Until now that number only shaped the SEARCH URLS (the LinkedIn bucket and
hiring.cafe's minCompensationLowEnd — see profile_lib), which is a request the boards
honour loosely and not at all for a posting that hides its pay. So the board still filled
with jobs paying under it. This applies the same number to the RESULT, once salary_probe.py
has established what each job actually pays.

THE RULE (chosen 2026-08-13, and every clause is a deliberate refusal to guess):

  evidence — act only on a figure with a real source behind it:
      posted    the posting's own structured pay field
      posting   regexed off the job page by salary_probe stage 1
      jobsworth Adzuna's market prediction for the title (stage 2)
    A salary_bands.json family average is NOT evidence about a specific employer — it is
    this pipeline's own guess, and dropping a job on it would be the tool arguing with
    itself. Band-priced jobs are kept. So are jobs with no figure at all: most postings
    hide pay, and silence is not a low offer. `_ACT_SOURCES` is the whole policy.

  edge — compare the TOP of the range. "$95K-$130K" clears a $105K minimum: pay lands
    anywhere in a band, the top end is what you negotiate toward, and a range that
    STARTS low is not a job that cannot pay you. Only a job whose ceiling is under the
    number is removed. For Adzuna this is doubly deliberate — its range is a point
    estimate +/-12%, so the top is the generous read of a prediction that is already
    uncertain, which is exactly how much weight a prediction deserves.

  drop — the row leaves candidates.csv, the same treatment the discovery-floor cutoff
    gives a low-match job. The counts are printed by every caller, so a run always says
    how many jobs the pay rule removed and the log keeps the audit trail.

  never drop — a job that is applied, tailored, skipped, blocked, queued or bookmarked.
    Those rows are a record of a decision already made (or work already spent), and this
    filter only ever narrows the field of jobs still awaiting one. Same exemptions the
    discovery floor uses, for the same reason.

Set salary_min to 0 (or leave it blank on /setup) and this module does nothing at all.

Everything here is stdlib and deterministic — no Claude, no network, per the CLAUDE.md
"everything works without Claude" rule.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import csv as _csv
import io
import re

from pipelib import (CANDIDATES_CSV, SALARY_CACHE, cfg, load_json, _safe_write_text)

# The three sources that count as evidence. See the module docstring: 'band' and '' are
# absent on purpose, and adding either turns this filter from "cannot pay you" into
# "we guessed it cannot pay you".
_ACT_SOURCES = ('posted', 'posting', 'jobsworth')

# Statuses that are never dropped, whatever they pay.
_EXEMPT_STATUS = ('applied', 'tailored', 'skipped', 'blocked')

# A pay figure quoted per hour/week/month cannot be compared to an annual minimum without
# assuming an hours-per-week that the posting did not state ($60/hr is ~$125K, not $60K).
# Rather than assume, treat it as unparseable and keep the job.
_PERIODIC = re.compile(r'(?:/|per\s+)\s*(?:hr|hour|wk|week|mo|month|day)|hourly|weekly|monthly',
                       re.I)
_FIGURE = re.compile(r'\$\s*([\d,]+(?:\.\d+)?)\s*([kK])?')


def min_expected(profile=None):
    """The user's minimum, from profile.json search.salary_min. 0 = the filter is off."""
    if profile is None:
        profile = load_json(cfg('profile.json'), {}) or {}
    try:
        return int((profile.get('search') or {}).get('salary_min') or 0)
    except (TypeError, ValueError):
        return 0


def top_figure(salary):
    """Highest annual dollar amount in a salary string, or None if it cannot be read.

    '$95K-$130K' -> 130000, '$56,000-$71,000 (est.)' -> 71000, '$106K' -> 106000.
    None means "do not judge this job" — an empty value, a per-hour rate, or anything
    this parser does not recognise. Every None is a KEPT job, never a dropped one."""
    s = (salary or '').strip()
    if not s or _PERIODIC.search(s):
        return None
    vals = []
    for raw, k in _FIGURE.findall(s):
        try:
            n = float(raw.replace(',', ''))
        except ValueError:
            continue
        # '$130K' is explicit. A bare '$130' in a salary field is shorthand for the same
        # thing — no annual salary is three digits — so scale it rather than drop the job
        # on a figure that would fail any minimum.
        if k or n < 1000:
            n *= 1000
        vals.append(int(n))
    return max(vals) if vals else None


def resolve(row, cache=None):
    """(value, source) for one candidates.csv row — the same precedence salary.py uses.

    The row's own `salary` column is the posting's structured pay, which outranks
    anything probed. A value carrying "(est." in that column is a band price applied by
    an earlier run, NOT posted pay, so it falls through to the probe cache — where an
    Adzuna figure carries the identical "(est.)" suffix but a real source next to it.
    That is why the source is read from the cache and never sniffed off the string."""
    if cache is None:
        cache = load_json(SALARY_CACHE, {}) or {}
    posted = (row.get('salary') or '').strip()
    if posted and '(est' not in posted.lower():
        return posted, 'posted'
    hit = cache.get(row.get('id') or row.get('trackerId') or '') or {}
    if hit.get('salary'):
        return hit['salary'], (hit.get('source') or 'probe')
    return posted, ('band' if posted else 'none')


def exempt(row):
    """True for a job this filter must leave alone: a decision was made, or work was spent."""
    return (row.get('status') in _EXEMPT_STATUS
            or (row.get('hasResume') or '').lower() == 'yes'
            or str(row.get('queued') or '').lower() in ('1', 'yes', 'true')
            or str(row.get('bookmarked') or '').lower() in ('1', 'yes', 'true'))


def verdict(row, minimum, cache=None):
    """(keep, reason) for one row. reason is only meaningful when keep is False."""
    if not minimum:
        return True, ''
    if exempt(row):
        return True, ''
    value, source = resolve(row, cache)
    if source not in _ACT_SOURCES:
        return True, ''
    top = top_figure(value)
    if top is None or top >= minimum:
        return True, ''
    return False, '%s < $%s (%s)' % (value, format(minimum, ',d'), source)


def filter_rows(rows, minimum=None, cache=None):
    """Split rows into (kept, dropped). Pure — no file access when both args are passed,
    which is what the tests use. `dropped` items are (row, reason) so a caller can log
    exactly what left the board and why."""
    if minimum is None:
        minimum = min_expected()
    if cache is None:
        cache = load_json(SALARY_CACHE, {}) or {}
    kept, dropped = [], []
    for r in rows:
        ok, why = verdict(r, minimum, cache)
        (kept.append(r) if ok else dropped.append((r, why)))
    return kept, dropped


def sweep(path=None, verbose=True):
    """Re-apply the rule to candidates.csv in place. Called after the salary probe, when
    figures exist that did not exist when jobpipe wrote the file.

    Returns {'minimum', 'scanned', 'kept', 'dropped', 'rows'} — `rows` being the dropped
    (id, reason) pairs, so the caller can print them. Rewrites nothing when the filter is
    off or when it would drop nothing, so a no-op run never touches the file's mtime."""
    path = path or CANDIDATES_CSV
    minimum = min_expected()
    out = {'minimum': minimum, 'scanned': 0, 'kept': 0, 'dropped': 0, 'rows': []}
    if not minimum or not os.path.isfile(path):
        return out
    with open(path, encoding='utf-8') as f:
        rd = _csv.DictReader(f)
        cols, rows = rd.fieldnames, list(rd)
    out['scanned'] = len(rows)
    if not cols or not rows:
        return out
    kept, dropped = filter_rows(rows)
    out['kept'], out['dropped'] = len(kept), len(dropped)
    out['rows'] = [(r.get('id', ''), why) for r, why in dropped]
    if not dropped:
        return out
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=cols, lineterminator='\n')
    w.writeheader()
    w.writerows(kept)
    # Atomic + verified, like every other write to the data root: a half-written
    # candidates.csv is an empty board, and the mount has truncated files before.
    _safe_write_text(path, buf.getvalue())
    if verbose:
        for jid, why in out['rows']:
            print('  pay-drop %-52s %s' % (jid[:52], why))
    return out


def note(res):
    """One-line summary for a pipeline log. Empty string when the filter did nothing."""
    if not res.get('minimum'):
        return ''
    if not res.get('dropped'):
        return 'pay filter: 0 dropped (minimum $%s)' % format(res['minimum'], ',d')
    return ('pay filter: %d of %d dropped below $%s (kept %d)'
            % (res['dropped'], res['scanned'], format(res['minimum'], ',d'), res['kept']))


if __name__ == '__main__':
    # Manual run: `python payfilter.py` sweeps candidates.csv and says what it did.
    r = sweep()
    print(note(r) or 'pay filter: off (profile.json search.salary_min is 0)')
