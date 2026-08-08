#!/usr/bin/env python3
"""salary.py — salary bands, title normalization, probe + resolution chain.

Split out of jobpipe.py: salary_lookup, _norm_salary_title, _probe_salaries,
_resolve_salary, and the salary / salary-apply / salary-set commands.
Imported by jobpipe.py (which re-exports for tests); external callers keep
shelling out to `python src/pipeline/jobpipe.py <cmd>`.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import sys, os, re, json
from datetime import datetime

import pipelib
from pipelib import HERE, SALARY_BANDS, load_json
from tracker import read_tracker, write_tracker

def _probe_salaries(ids, timeout=180):
    """Best-effort: run salary_probe.py for ids that have no salary yet, so a resume is never
    tailored with a blank/band-only figure when a real one was one fetch away. Never fatal —
    if the probe fails or times out we fall through to the band as before."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    script = _paths.script('salary_probe.py')
    if not os.path.isfile(script):
        return {}
    import subprocess  # local import: matches how backup.py is shelled out elsewhere
    try:
        out = subprocess.run([sys.executable, script, '--ids', ','.join(ids), '--json'],
                             cwd=HERE, capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout or '{}')
        if data.get('quotaBlocked'):
            print('# NOTE: %s' % data.get('quotaNote', 'Adzuna quota exhausted'), file=sys.stderr)
        return data.get('results', {}) or {}
    except Exception as e:
        print('# NOTE: salary probe skipped (%s)' % type(e).__name__, file=sys.stderr)
        return {}


def _resolve_salary(cid, posted, est, cache_path=None):
    """ONE salary per job, best source first, so the tailoring run never has to choose:
         1. posted     — pay the posting itself lists (hiringcafe.com structured field)
         2. posting    — pay salary_probe.py regexed out of the job page HTML
         3. jobsworth  — Adzuna salary-predictor estimate (also from salary_probe.py)
         4. band       — config/salary_bands.json family range (last resort)
    Returns (value, source). Never returns 'Undisclosed'.
    cache_path lets jobpipe.py pass its (monkeypatchable) SALARY_CACHE through;
    defaults to the shared pipelib constant."""
    if cache_path is None:
        cache_path = pipelib.SALARY_CACHE
    p = (posted or '').strip()
    if p and p.lower() not in ('undisclosed', 'n/a', 'none', '-'):
        return p, 'posted'
    hit = (load_json(cache_path, {}) or {}).get(cid) or {}
    if hit.get('salary'):
        return hit['salary'], hit.get('source', 'probe')
    return (est or {}).get('range', ''), 'band' if (est or {}).get('range') else 'none'

# _SALARY_TITLE_FOLD is applied before matching so title-format variants stop missing.
# Measured 2026-07-20: 41/194 tracked roles (21%) matched NO family, almost all because
# tracking ids are camel-split ("Dev Ops Engineer", "Sr Infra Systems Eng") or abbreviated,
# while the keyword lists spell things out ("devops", "infrastructure", "systems engineer").
_SALARY_TITLE_FOLD = [
    (r'\bdev\s*sec\s*ops\b', 'devsecops'),
    (r'\bdev\s*ops\b', 'devops'),
    (r'\bsite\s*reliability\b', 'site reliability'),
    (r'\bsre\b', 'site reliability engineer'),
    (r'\binfra\b', 'infrastructure'),
    (r'\bsys\b', 'systems'),
    (r'\bengineering\b', 'engineer'),
    (r'\beng\b', 'engineer'),
    (r'\bengineer\s+i{1,3}\b', 'engineer'),
    (r'\bsr\.?\b', 'senior'),
    (r'\bjr\.?\b', 'junior'),
    (r'\bmgr\b', 'manager'),
    (r'\badmin\b', 'administrator'),
    (r'\bops\b', 'operations'),
    (r'\bqa\b', 'qa'),
]

def _norm_salary_title(title):
    """Lowercase, strip punctuation, expand common abbreviations. Keeps family matching
    robust to how a title happens to be spelled in candidates.csv vs a tracker id."""
    t = re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).strip()
    for pat, rep in _SALARY_TITLE_FOLD:
        t = re.sub(pat, rep, t)
    return re.sub(r'\s+', ' ', t).strip()

def salary_lookup(title):
    """Match a job title to a salary-band family. Empty 'range' => needs one WebSearch,
    then persist it with salary-set so the family never gets searched again."""
    cfg = load_json(SALARY_BANDS, {}) or {}
    raw = (title or '').lower()
    t = _norm_salary_title(title)
    senior = bool(re.search(r'\b(senior|sr\.?|lead|iii|iv)\b', raw) or
                  re.search(r'\b(senior|lead)\b', t))
    for fam, spec in (cfg.get('families') or {}).items():
        if any(k in t for k in spec.get('match', [])):
            rng = (spec.get('seniorRange') if senior and spec.get('seniorRange') else spec.get('range')) or ''
            return {'family': fam, 'senior': senior, 'range': rng, 'updated': spec.get('updated', '')}
    return {'family': '', 'senior': senior, 'range': '', 'updated': ''}

def _bucket_source(val, src):
    """Collapse a raw salary source into one of three display buckets the tracker styles:
      confirmed — real pay from the posting (posted/posting)
      adzuna    — Adzuna market prediction (jobsworth)
      rough     — family-band guess or any estimate with no precise source
    When no explicit source was stored (older job_details), infer from the string: a value
    carrying "(est.)" is a rough estimate, a bare figure is treated as confirmed pay."""
    s = (src or '').strip().lower()
    if s in ('posted', 'posting', 'confirmed', 'jd', 'manual'):
        return 'confirmed'
    if s in ('jobsworth', 'adzuna'):
        return 'adzuna'
    if s in ('band', 'rough', 'est'):
        return 'rough'
    if not val:
        return ''
    return 'rough' if re.search(r'\(est', val, re.I) else 'confirmed'


def _read_rows_csv(path):
    import csv as _csv
    if not os.path.isfile(path):
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(_csv.DictReader(f))


def cmd_salaries(a):
    """Resolve ONE salary + source for EVERY visible job (candidates.csv ∪ tracking.csv ∪
    job_details) so the dashboard can show a figure for all of them and — crucially — so a
    probed salary persists across refreshes (it lives in job_details, which this reads first).
    Precedence per job: job_details (probe / salary-apply) → the row's posted pay → salary_cache
    (posting/jobsworth) → family band. Prints {id: {value, source, kind}} as JSON; jobs that
    resolve to nothing are omitted (the UI shows a $ Find button for those)."""
    from pipelib import CANDIDATES_CSV, TRACKING_CSV, SALARY_CACHE
    jd = (read_tracker().get('job_details') or {})
    rows = {}
    for path in (CANDIDATES_CSV, TRACKING_CSV):
        for r in _read_rows_csv(path):
            cid = (r.get('id') or '').strip()
            if cid:
                rows.setdefault(cid, r)
    out = {}
    for cid in (set(rows) | set(jd)):
        r = rows.get(cid, {})
        d = jd.get(cid) or {}
        if d.get('salary'):
            val, src = d['salary'], (d.get('salarySource') or '')
        else:
            role = r.get('role') or d.get('title') or ''
            posted = (r.get('salary') or '').strip()
            est = salary_lookup(role)
            val, src = _resolve_salary(cid, posted, est, cache_path=SALARY_CACHE)
        if not val:
            continue
        out[cid] = {'value': val, 'source': src or '', 'kind': _bucket_source(val, src)}
    print(json.dumps(out))


def cmd_salary(a):
    print(json.dumps(salary_lookup(a.title), indent=2))

def cmd_salary_apply(a):
    """Write a discovered salary onto a tracked job. Needed because cmd_update deliberately
    NEVER overwrites an existing job_details entry — a re-probe has to be able to correct one.
    Used by the dashboard's per-job salary button."""
    tracker = read_tracker()
    jd = tracker.setdefault('job_details', {}).setdefault(a.id, {})
    jd['salary'] = a.value or ''
    if a.source:
        jd['salarySource'] = a.source
    jd['salaryCheckedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    write_tracker(tracker)
    print(json.dumps({'ok': True, 'id': a.id, 'salary': jd['salary'],
                      'source': jd.get('salarySource', '')}))


def cmd_salary_set(a):
    cfg = load_json(SALARY_BANDS, {}) or {}
    fams = cfg.setdefault('families', {})
    spec = fams.setdefault(a.family, {'match': []})
    if a.match:
        spec['match'] = sorted(set((spec.get('match') or []) +
                                   [m.strip().lower() for m in a.match.split(',') if m.strip()]))
    if a.range: spec['range'] = a.range
    if a.senior_range: spec['seniorRange'] = a.senior_range
    spec['updated'] = datetime.now().strftime('%Y-%m')
    with open(SALARY_BANDS, 'w', encoding='utf-8') as f: json.dump(cfg, f, indent=2)
    print(f"salary_bands.json: {a.family} range={spec.get('range','')} senior={spec.get('seniorRange','')}")
