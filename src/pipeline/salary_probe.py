#!/usr/bin/env python3
"""salary_probe.py — per-job salary discovery. NO browser, NO LLM, stdlib only.

Runs on the user's machine (Task Scheduler / scheduler.py) AFTER scrape.py and BEFORE the
Claude tailoring runs, so a real salary is already on disk by the time a resume is built.
Costs zero Claude tokens: the resolution happens here, not in the model.

Why this exists: hiringcafe.com only exposes structured `yearly_min/max_compensation`, which is
null whenever the employer didn't mark pay transparent (`is_compensation_transparent: false`),
and its payload carries no job-description body at all. So a posting that states pay in its
text was invisible to the pipeline and fell back to a hand-maintained band.

Two stages, best source first:
  1. POSTING  — resolve the pay off the job page. `probe_posting` goes cleanest-source-first:
                jobdesc.fetch() (per-host JSON APIs + Taleo session/Paylocity handlers +
                JSON-LD + generic strip) → schema.org JSON-LD baseSalary → raw-HTML regex.
                Static ATSes (Greenhouse, Lever, Ashby, Breezy, Personio, SmartRecruiters),
                clean-JSON-API ones (Eightfold), and server-rendered-but-awkward ones
                (Taleo, Paylocity) resolve here;
                truly JS-only sites (Workday, Paycom, hibob) still miss — that's expected
                and recorded, not an error (the user can set pay via the card's edit button).
  2. JOBSWORTH— Adzuna's salary predictor (title + skills -> point estimate), widened to a
                range. Free tier: 25/min, 250/day, 1000/week, 2500/month. Skipped entirely
                when config/adzuna.json is absent, so stage 1 works with no signup.
The family band in jobpipe.py (`salary_lookup`) remains the last resort and is NOT used here.

Results are cached in salary_cache.json so a job is probed once, not every run.

Usage:
  python src/pipeline/salary_probe.py                  # probe candidates with no known salary
  python src/pipeline/salary_probe.py --ids A,B         # probe specific tracker ids
  python src/pipeline/salary_probe.py --all             # include jobs that already have a salary
  python src/pipeline/salary_probe.py --refresh         # ignore the cache, re-probe
  python src/pipeline/salary_probe.py --stats           # per-ATS hit rate from the cache, no fetching
  python src/pipeline/salary_probe.py --dry-run         # show what would be probed
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

try:
    # Reuse the robust, already-tested per-host fetchers (Greenhouse/Lever JSON APIs,
    # schema.org JSON-LD, generic strip — all cached). This is why a posting that
    # "clearly states pay" but renders it via React (job-boards.greenhouse.io) is now
    # resolved: jobdesc pulls the clean server-side `content`, not the JS shell.
    import jobdesc as _jobdesc
except Exception:                     # pragma: no cover - jobdesc always ships alongside
    _jobdesc = None

HERE = _paths.ROOT
# Cache/candidates/quota are per-user state -> pipelib DATA root (falls back to HERE
# when the data repo isn't split out). adzuna.json is a secret and lives with the data.
from pipelib import (DATA, CONFIG, cfg, SALARY_CACHE as CACHE, SALARY_QUOTA as QUOTA,
                     CANDIDATES_CSV as CANDIDATES, TRACKING_CSV)
ADZUNA_CFG = cfg('adzuna.json')

# Adzuna free-tier ceilings (developer.adzuna.com/docs/terms_of_service, checked 2026-07-20).
# Counted locally so we stop BEFORE they start rejecting us, and reported so the dashboard
# can say "estimates are coming from the band fallback right now" instead of going quiet.
ADZUNA_LIMITS = {'minute': 25, 'day': 250, 'week': 1000, 'month': 2500}

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126 Safari/537.36')
TIMEOUT = 20
MAX_BYTES = 3_000_000
POLITE_DELAY = 1.0          # seconds between employer-page fetches
ADZUNA_DELAY = 2.5          # stays under 25 hits/minute

# Plausibility guards — anything outside these is not a salary.
ANNUAL_MIN, ANNUAL_MAX = 30_000, 600_000
HOURLY_MIN, HOURLY_MAX = 15, 300
HOURS_PER_YEAR = 2080

# Phrases that mean a nearby number is not base pay. Only disqualifies when it sits
# CLOSE to the number and no explicit pay wording is present — postings routinely mention
# a 401(k) match or bonus target in the same paragraph as the real salary range.
NEAR_NOISE = re.compile(
    r'(401\s*\(?k\)?|bonus|equity|stock|rsu|revenue|funding|raised|valuation|'
    r'tuition|reimburse|discount|deductible|premium|insurance|per\s+diem)', re.I)

# Explicit "this number is pay" wording.
PAY_WORDS = re.compile(
    r'(salary|salaries|compensation|pay\s*(range|rate|scale|band)|base\s*pay|wage|'
    r'hiring\s*range|expected\s*(pay|salary)|earn|remuneration|total\s*target)', re.I)

CURRENCY = re.compile(r'\b(usd|dollars?)\b', re.I)


# ---------- small utils ----------
def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower().replace('www.', '')
    except Exception:
        return ''


def money(n):
    return '$%s' % format(int(round(n)), ',d')


def fmt_range(lo, hi, suffix=''):
    return '%s-%s%s' % (money(lo), money(hi), suffix)


# ---------- stage 1: parse the posting ----------
def fetch(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        ctype = (r.headers.get('Content-Type') or '').lower()
        raw = r.read(MAX_BYTES)
    enc = 'utf-8'
    m = re.search(r'charset=([\w-]+)', ctype)
    if m:
        enc = m.group(1)
    return raw.decode(enc, errors='replace'), ctype


def visible_text(doc):
    """Strip scripts/styles/tags and decode entities. Keeps JSON-LD, which often holds pay."""
    doc = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', doc)
    doc = re.sub(r'(?s)<[^>]+>', ' ', doc)
    doc = html.unescape(doc)
    return re.sub(r'[\s ]+', ' ', doc)


NUM = r'\$?\s*(\d{2,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s*[kK]\b|\d{2,3}(?:\.\d+)?)'
RANGE_RE = re.compile(
    NUM + r'\s*(?:-|–|—|to|through|up to)\s*' + NUM +
    r'(?P<tail>[^.]{0,40})', re.I)


def to_number(tok):
    tok = tok.strip().replace('$', '').replace(',', '').strip()
    if tok.lower().endswith('k'):
        return float(tok[:-1].strip()) * 1000
    return float(tok)


def classify(lo, hi, context, has_currency):
    """Return ('annual'|'hourly'|None, lo, hi) after plausibility + unit checks."""
    ctx = context.lower()
    hourly_hint = re.search(r'(per\s+hour|/\s*hour|hourly|an\s+hour|/hr|per\s+hr)', ctx)
    annual_hint = re.search(r'(per\s+year|/\s*year|annual|annually|a\s+year|/yr|per\s+annum)', ctx)
    if hourly_hint and HOURLY_MIN <= lo <= hi <= HOURLY_MAX:
        return 'hourly', lo, hi
    if ANNUAL_MIN <= lo <= hi <= ANNUAL_MAX:
        return 'annual', lo, hi
    # bare small numbers with an explicit annual hint are probably "k" figures
    if annual_hint and 30 <= lo <= hi <= 600:
        return 'annual', lo * 1000, hi * 1000
    # a bare 2-3 digit range is only an hourly rate if the page actually says so;
    # otherwise it's "50 - 100 engineers", "10 - 20 years", etc.
    if hourly_hint and has_currency and HOURLY_MIN <= lo <= hi <= HOURLY_MAX:
        return 'hourly', lo, hi
    return None, lo, hi


def parse_pay(text):
    """Best pay range found in page text, or None. Prefers ranges near pay wording."""
    best = None
    for m in RANGE_RE.finditer(text):
        seg = m.group(0)
        pre = text[max(0, m.start() - 130):m.start()]
        post = text[m.end():m.end() + 60]
        context = pre + seg + post
        pay_words = PAY_WORDS.search(pre[-90:] + seg + post[:40])
        has_currency = ('$' in seg) or bool(CURRENCY.search(pre[-40:] + seg + post[:20]))
        # A number with neither a currency marker nor pay wording is not a salary.
        if not has_currency and not pay_words:
            continue
        # Noise only disqualifies when it crowds the number AND nothing calls it pay.
        if not pay_words and NEAR_NOISE.search(pre[-45:] + seg + post[:25]):
            continue
        try:
            lo, hi = to_number(m.group(1)), to_number(m.group(2))
        except Exception:
            continue
        if hi < lo:
            lo, hi = hi, lo
        kind, lo, hi = classify(lo, hi, context, has_currency)
        if not kind:
            continue
        # score: explicit pay wording nearby wins over an incidental range
        score = 0
        if pay_words:
            score += 2
        if kind == 'annual':
            score += 1
        if best is None or score > best[0]:
            best = (score, kind, lo, hi)
    if not best:
        return None
    _, kind, lo, hi = best
    if kind == 'hourly':
        return {'salary': fmt_range(lo * HOURS_PER_YEAR, hi * HOURS_PER_YEAR),
                'note': 'converted from %s-%s/hr' % (lo, hi)}
    return {'salary': fmt_range(lo, hi), 'note': ''}


def _sld_amount(v):
    """Pull a numeric amount out of a schema.org value/minValue/maxValue field."""
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return None


def parse_jsonld_salary(page):
    """Structured pay from schema.org JobPosting.baseSalary, or None.

    The most reliable source when present: it carries an explicit unit
    (HOUR/WEEK/MONTH/YEAR) so hourly contract rates convert correctly with no guessing.
    Many ATS pages (Ashby, Workable, iCIMS, SmartRecruiters) embed it server-side even
    when the visible pay text is JS-rendered."""
    for m in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page):
        blob = (m.group(1) or '').strip()
        if not blob:
            continue
        try:
            parsed = json.loads(blob)
        except Exception:
            continue
        stack = [parsed]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x); continue
            if not isinstance(x, dict):
                continue
            g = x.get('@graph')
            if isinstance(g, list):
                stack.extend(g)
            bs = x.get('baseSalary') or x.get('estimatedSalary')
            if not isinstance(bs, dict):
                continue
            val = bs.get('value')
            if isinstance(val, dict):
                lo = _sld_amount(val.get('minValue'))
                hi = _sld_amount(val.get('maxValue'))
                if lo is None and hi is None:
                    v = _sld_amount(val.get('value'))
                    lo = hi = v
                unit = str(val.get('unitText') or bs.get('unitText') or '').upper()
            else:
                lo = hi = _sld_amount(val)
                unit = str(bs.get('unitText') or '').upper()
            if lo is None and hi is None:
                continue
            lo = lo if lo is not None else hi
            hi = hi if hi is not None else lo
            if hi < lo:
                lo, hi = hi, lo
            if unit in ('HOUR', 'HOURLY') and HOURLY_MIN <= lo <= hi <= HOURLY_MAX:
                return {'salary': fmt_range(lo * HOURS_PER_YEAR, hi * HOURS_PER_YEAR),
                        'note': 'converted from %s-%s/hr (JobPosting.baseSalary)' % (lo, hi)}
            if unit in ('WEEK', 'WEEKLY') and 40000 <= lo * 52 <= hi * 52 <= ANNUAL_MAX:
                return {'salary': fmt_range(lo * 52, hi * 52),
                        'note': 'converted from %s-%s/wk' % (lo, hi)}
            if unit in ('MONTH', 'MONTHLY') and ANNUAL_MIN <= lo * 12 <= hi * 12 <= ANNUAL_MAX:
                return {'salary': fmt_range(lo * 12, hi * 12),
                        'note': 'converted from %s-%s/mo' % (lo, hi)}
            if ANNUAL_MIN <= lo <= hi <= ANNUAL_MAX:
                return {'salary': fmt_range(lo, hi), 'note': 'JobPosting.baseSalary'}
    return None


# Hosts that never serve the posting body to an anonymous fetch (login wall or pure JS
# shell). Skipped at stage 1 so we don't spend a request to learn what we already know —
# these fall straight through to Jobsworth.
GATED_HOSTS = (
    'linkedin.com', 'indeed.com', 'glassdoor.com', 'ziprecruiter.com',
    'dice.com', 'monster.com',
)


def probe_posting(url):
    """(result_dict|None, status_string)

    Resolution order, cleanest source first:
      1. jobdesc.fetch()  - per-host JSON APIs (Greenhouse/Lever), then schema.org JSON-LD,
                            then a generic strip. Fixes React-rendered ATS pages (e.g.
                            job-boards.greenhouse.io) whose visible pay text a raw fetch
                            never sees, and normalizes entity-escaped `content`.
      2. JSON-LD baseSalary - structured pay with an explicit unit, parsed off the raw HTML.
      3. raw HTML strip    - the original stdlib fetch + regex, kept so nothing regresses.
    """
    if not url:
        return None, 'no-url'
    h = host_of(url)
    if any(h.endswith(g) or h == g for g in GATED_HOSTS):
        return None, 'gated-host (%s)' % h

    # 1. Clean text via the shared fetchers (cached; handles the JS-rendered ATSes).
    if _jobdesc is not None:
        try:
            jd = _jobdesc.fetch(url)
        except Exception:
            jd = None
        if jd and jd.get('ok') and jd.get('text'):
            hit = parse_pay(jd['text'])
            if hit:
                return hit, 'ok (jobdesc:%s)' % (jd.get('source') or 'text')

    # 2 + 3. Fetch the raw page once; try structured JSON-LD pay, then the text regex.
    try:
        doc, ctype = fetch(url)
    except urllib.error.HTTPError as e:
        return None, 'http-%s' % e.code
    except Exception as e:
        return None, 'fetch-error: %s' % type(e).__name__
    if 'json' not in ctype:
        sld = parse_jsonld_salary(doc)
        if sld:
            return sld, 'ok (jsonld-salary)'
    if 'json' in ctype:
        text = re.sub(r'[\s ]+', ' ', doc)
    else:
        text = visible_text(doc)
    # Parse FIRST — if a pay range is there, page length is irrelevant. The length check
    # only explains a miss (a JS shell has no body to parse), it must not veto a hit.
    hit = parse_pay(text)
    if hit:
        return hit, 'ok'
    if len(text) < 800:
        return None, 'thin-page (js-rendered?)'
    return None, 'no-pay-in-page'


# ---------- stage 2: Adzuna Jobsworth ----------
def adzuna_creds():
    cfg = load_json(ADZUNA_CFG, {}) or {}
    aid, akey = cfg.get('app_id', ''), cfg.get('app_key', '')
    return (aid, akey) if aid and akey else (None, None)


def _periods(now=None):
    now = now or datetime.now()
    iso = now.isocalendar()
    return {'day': now.strftime('%Y-%m-%d'),
            'week': '%04d-W%02d' % (iso[0], iso[1]),
            'month': now.strftime('%Y-%m')}


def quota_state():
    """Current Adzuna usage, with counters auto-reset when their period rolls over."""
    q = load_json(QUOTA, {}) or {}
    p = _periods()
    for scope in ('day', 'week', 'month'):
        if q.get(scope + 'Key') != p[scope]:
            q[scope + 'Key'] = p[scope]
            q[scope + 'Count'] = 0
    q.setdefault('limits', ADZUNA_LIMITS)
    q.setdefault('exhausted', False)
    q.setdefault('exhaustedScope', '')
    q.setdefault('exhaustedAt', '')
    q.setdefault('lastError', '')
    # An exhausted flag only survives while its period is still current.
    if q.get('exhausted') and q.get('exhaustedScope'):
        scope = q['exhaustedScope']
        if q.get(scope + 'Count', 0) < ADZUNA_LIMITS.get(scope, 10 ** 9):
            q['exhausted'] = False
            q['exhaustedScope'] = ''
    return q


def quota_blocked(q=None):
    """(True, scope) when a call would exceed a free-tier ceiling."""
    q = q or quota_state()
    for scope in ('day', 'week', 'month'):
        if q.get(scope + 'Count', 0) >= ADZUNA_LIMITS[scope]:
            return True, scope
    if q.get('exhausted'):
        return True, q.get('exhaustedScope') or 'reported'
    return False, ''


def quota_note(q=None):
    """One-line human summary for the dashboard / console."""
    q = q or quota_state()
    blocked, scope = quota_blocked(q)
    used = 'Adzuna %d/%d today, %d/%d this week, %d/%d this month' % (
        q.get('dayCount', 0), ADZUNA_LIMITS['day'],
        q.get('weekCount', 0), ADZUNA_LIMITS['week'],
        q.get('monthCount', 0), ADZUNA_LIMITS['month'])
    if blocked:
        return ('Adzuna %s quota exhausted - salary estimates are falling back to the '
                'salary_bands.json range. %s' % (scope, used))
    return used


def quota_bump(n=1, exhausted_scope=None, error=''):
    q = quota_state()
    for scope in ('day', 'week', 'month'):
        q[scope + 'Count'] = q.get(scope + 'Count', 0) + n
    if exhausted_scope:
        q['exhausted'] = True
        q['exhaustedScope'] = exhausted_scope
        q['exhaustedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    if error:
        q['lastError'] = error
    q['updatedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    save_json(QUOTA, q)
    return q


def probe_jobsworth(title, description, country='us', spread=0.12):
    """Adzuna salary predictor -> widened range. Returns (result|None, status)."""
    aid, akey = adzuna_creds()
    if not aid:
        return None, 'no-adzuna-key'
    blocked, scope = quota_blocked()
    if blocked:
        return None, 'quota-exhausted (%s) - using band fallback' % scope
    q = urllib.parse.urlencode({
        'app_id': aid, 'app_key': akey,
        'title': title or '', 'description': description or title or '',
        'content-type': 'application/json',
    })
    url = 'https://api.adzuna.com/v1/api/jobs/%s/jobsworth?%s' % (country, q)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA,
                                                   'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read(200_000).decode('utf-8', errors='replace'))
        quota_bump(1)
    except urllib.error.HTTPError as e:
        # 429 = rate limited, 403 = over quota / key disabled. Either way stop asking:
        # flip the exhausted flag so the rest of this run (and the dashboard) uses the band.
        if e.code in (429, 403):
            quota_bump(1, exhausted_scope='reported',
                       error='HTTP %s from Adzuna at %s' % (e.code, datetime.now().strftime('%Y-%m-%d %H:%M')))
            return None, 'quota-exhausted (http-%s) - using band fallback' % e.code
        quota_bump(1, error='HTTP %s' % e.code)
        return None, 'adzuna-http-%s' % e.code
    except Exception as e:
        return None, 'adzuna-error: %s' % type(e).__name__
    sal = data.get('salary')
    if not sal:
        return None, 'adzuna-no-estimate'
    lo, hi = float(sal) * (1 - spread), float(sal) * (1 + spread)
    lo, hi = round(lo / 1000) * 1000, round(hi / 1000) * 1000
    if not (ANNUAL_MIN <= lo <= hi <= ANNUAL_MAX):
        return None, 'adzuna-implausible'
    return ({'salary': fmt_range(lo, hi, ' (est.)'),
             'note': 'Adzuna Jobsworth point estimate %s +/-%d%%'
                     % (money(float(sal)), int(spread * 100))}, 'ok')


# ---------- driver ----------
def _read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f))


def read_candidates():
    """candidates.csv first, then any tracking.csv job not already listed.

    candidates.csv only holds jobs still awaiting a resume, but the dashboard's per-job
    salary button can target ANY row on the tracker — including jobs already tailored.
    tracking.csv is the durable source of truth and carries id/company/role/applyUrl, so
    merging it means every visible job is probeable."""
    rows = _read_csv(CANDIDATES)
    seen = {r.get('id') for r in rows if r.get('id')}
    for t in _read_csv(TRACKING_CSV):
        tid = t.get('id')
        if tid and tid not in seen:
            seen.add(tid)
            rows.append(t)
    return rows


def pick(row, *names):
    for n in names:
        if row.get(n):
            return row[n]
    return ''


def has_salary(row):
    s = (pick(row, 'salary') or '').strip().lower()
    return bool(s) and s not in ('undisclosed', 'n/a', 'none', '-')


def stage2_pending(entry):
    """A cached miss that has NOT yet had a real Adzuna (stage-2) attempt, so a later
    full run could still resolve it. True when the job was probed stage-1-only
    (--no-adzuna hourly radar leaves no jobsworthStatus), before creds existed
    ('no-adzuna-key'), or while quota was exhausted. A definitive Adzuna miss
    (adzuna-no-estimate / -implausible / -http-*) is NOT pending — don't churn on it."""
    if (entry or {}).get('salary'):
        return False
    js = (entry or {}).get('jobsworthStatus') or ''
    return js in ('', 'no-adzuna-key') or js.startswith('quota-exhausted')


def retryable_miss(entry):
    """A cached miss worth re-probing WITHOUT --refresh: stage 2 never really ran AND
    Adzuna creds now exist, so a full run can finally try. Jobs probed before signup
    (or by the free stage-1-only hourly radar) therefore don't stay 'none' forever."""
    return stage2_pending(entry) and adzuna_creds()[0] is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', default='')
    ap.add_argument('--all', action='store_true', help='include jobs that already list pay')
    ap.add_argument('--refresh', action='store_true', help='ignore cached results')
    ap.add_argument('--stats', action='store_true', help='report cache hit rates, no fetching')
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    ap.add_argument('--no-adzuna', action='store_true', help='stage 1 only')
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--json', action='store_true', dest='as_json',
                    help='machine-readable result (used by the dashboard button)')
    ap.add_argument('--quota', action='store_true', help='print Adzuna quota state and exit')
    a = ap.parse_args()

    cache = load_json(CACHE, {}) or {}

    if a.quota:
        q = quota_state()
        blocked, scope = quota_blocked(q)
        if a.as_json:
            print(json.dumps({'quota': q, 'blocked': blocked, 'scope': scope,
                              'note': quota_note(q),
                              # lets the dashboard nudge the user to set up Adzuna for
                              # more accurate estimates when creds are absent.
                              'configured': adzuna_creds()[0] is not None}, indent=2))
        else:
            print(quota_note(q))
        return 0

    if a.stats:
        by_host, by_source = {}, {}
        for v in cache.values():
            h = v.get('host', '?')
            d = by_host.setdefault(h, {'hit': 0, 'miss': 0})
            d['hit' if v.get('salary') else 'miss'] += 1
            by_source[v.get('source', '?')] = by_source.get(v.get('source', '?'), 0) + 1
        print('cached probes: %d' % len(cache))
        print('by source:', json.dumps(by_source, indent=2))
        print('%-34s %5s %5s %6s' % ('ATS host', 'hit', 'miss', 'rate'))
        for h, d in sorted(by_host.items(), key=lambda kv: -(kv[1]['hit'] + kv[1]['miss'])):
            tot = d['hit'] + d['miss']
            print('%-34s %5d %5d %5.0f%%' % (h, d['hit'], d['miss'], 100.0 * d['hit'] / tot))
        return 0

    rows = read_candidates()
    want = {i.strip() for i in a.ids.split(',') if i.strip()}
    todo = []
    for r in rows:
        jid = pick(r, 'id', 'trackerId')
        if not jid:
            continue
        if want and jid not in want:
            continue
        if not want and not a.all and has_salary(r):
            continue
        # Re-probe a cached miss only when THIS run can add something: a full run
        # (Adzuna-capable) revisits stage-2-pending misses; a --no-adzuna hourly run
        # has nothing new to offer an already-probed job, so it only takes fresh ids.
        if (not a.refresh and jid in cache
                and not (not a.no_adzuna and retryable_miss(cache.get(jid)))):
            continue
        todo.append(r)
    todo = todo[:a.limit]

    if not todo:
        if a.as_json:
            print(json.dumps({'ok': True, 'probed': 0, 'results': {},
                              'quotaNote': quota_note(), 'message': 'nothing to probe'}))
        else:
            print('salary_probe: nothing to probe (%d cached, %d candidates)'
                  % (len(cache), len(rows)))
        return 0
    if a.dry:
        for r in todo:
            print('would probe:', pick(r, 'id'), '|', pick(r, 'applyUrl', 'apply_url'))
        return 0

    stage1 = stage2 = 0
    for r in todo:
        jid = pick(r, 'id', 'trackerId')
        url = pick(r, 'applyUrl', 'apply_url')
        title = pick(r, 'role', 'title')
        company = pick(r, 'company', 'companyDisplay')
        skills = pick(r, 'requiredSkills', 'skills')
        entry = {'checkedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
                 'host': host_of(url) or 'none', 'company': company, 'title': title}

        hit, status = probe_posting(url)
        entry['postingStatus'] = status
        if hit:
            entry.update({'salary': hit['salary'], 'source': 'posting', 'note': hit['note']})
            stage1 += 1
        elif not a.no_adzuna:
            desc = skills.replace(';', ',') if skills else title
            hit2, status2 = probe_jobsworth(title, desc)
            entry['jobsworthStatus'] = status2
            if hit2:
                entry.update({'salary': hit2['salary'], 'source': 'jobsworth',
                              'note': hit2['note']})
                stage2 += 1
            else:
                entry.update({'salary': '', 'source': 'none', 'note': ''})
            time.sleep(ADZUNA_DELAY)
        else:
            entry.update({'salary': '', 'source': 'none', 'note': ''})

        cache[jid] = entry
        if not a.as_json:
            print('%-14s %-30s %s' % (entry.get('source', 'none'),
                                      (entry['host'] or '')[:30],
                                      entry.get('salary') or ('- ' + status)))
        save_json(CACHE, cache)
        time.sleep(POLITE_DELAY)

    note = quota_note()
    blocked, _ = quota_blocked()
    if a.as_json:
        print(json.dumps({
            'ok': True, 'probed': len(todo),
            'posting': stage1, 'jobsworth': stage2,
            'unresolved': len(todo) - stage1 - stage2,
            'results': {r_id: cache[r_id] for r_id in
                        [pick(r, 'id', 'trackerId') for r in todo] if r_id in cache},
            'quotaBlocked': blocked, 'quotaNote': note,
        }, indent=2))
        return 0

    print('salary_probe: %d probed | posting=%d jobsworth=%d unresolved=%d'
          % (len(todo), stage1, stage2, len(todo) - stage1 - stage2))
    print('salary_probe: %s' % note)
    if blocked:
        print('salary_probe: WARNING - Adzuna is out of quota, so unresolved jobs will use '
              'the salary_bands.json estimate until the window resets.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
