#!/usr/bin/env python3
"""scrape.py — standalone hiringcafe.com scraper. NO browser, NO LLM, stdlib only.

Runs on Khoa's machine via Windows Task Scheduler BEFORE the Claude scheduled runs.
Writes listings.json + scrape_meta.json in the project folder. The scheduled Claude
run checks scrape_meta.json: if fresh, it skips the Chrome scrape entirely.

Usage:
  python src/pipeline/scrape.py --url-key hiringcafe_24h     (daily; default)
  python src/pipeline/scrape.py --url-key hiringcafe_7d      (Saturday sweep)

Transport (verified 2026-07-10 against the live site): hiringcafe.com is a Next.js app;
job results are served publicly (no auth) from its data route:
  GET https://hiringcafe.com/_next/data/<buildId>/index.json?searchState=<enc json>&page=N
buildId is parsed from the homepage's __NEXT_DATA__ and changes per deployment
(a stale one returns 404 -> we refetch it). Results: pageProps.ssrHits[] with
job_information.title, v5_processed_job_data.{core_job_title, company_name,
technical_tools, formatted_workplace_location, workplace_type,
yearly_min_compensation, yearly_max_compensation}.
If the shape ever changes, page 0's raw response is saved to scrape_raw_sample.json
and scrape_meta.json gets status="failed" so Claude falls back to Chrome.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import json, os, re, sys, time, argparse
import urllib.request, urllib.parse
from datetime import datetime

HERE = _paths.ROOT
# Personal data (search_urls/skills/state) resolves off pipelib's DATA root, which
# falls back to HERE when the data repo isn't split out — see pipelib.resolve_data_root.
from pipelib import DATA, CONFIG, cfg, SCRAPE_META, env as _env
LISTINGS = os.path.join(DATA, 'listings.json')
RAW_SAMPLE = os.path.join(DATA, 'scrape_raw_sample.json')
SITE = 'https://hiringcafe.com'  # migrated to this domain 2026-07; the previous domain no longer serves the data route
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
MAX_PAGES = 6

def load_json(p, default=None):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception: return default

def save_json(p, d):
    with open(p, 'w', encoding='utf-8') as f: json.dump(d, f, indent=2)

def http_get(url, extra_headers=None):
    headers = {'User-Agent': UA, 'Referer': SITE + '/'}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.status, r.read().decode('utf-8', 'replace')

def get_build_id():
    _, html = http_get(SITE + '/')
    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    if not m: raise ValueError('buildId not found in homepage HTML')
    return m.group(1)

def search_state_from_url(url):
    q = urllib.parse.urlparse(url).query
    blob = (urllib.parse.parse_qs(q).get('searchState') or [None])[0]
    if not blob: raise ValueError('no searchState param in URL')
    return json.loads(blob)

def data_page(build_id, state, page):
    ss = urllib.parse.quote(json.dumps(state, separators=(',', ':')), safe='')
    url = f'{SITE}/_next/data/{build_id}/index.json?searchState={ss}&page={page}'
    # Tolerate transient upstream hiccups: an HTTP error (405/5xx) or an empty /
    # non-JSON body (Cloudflare/HTML error page) yields (status, None) instead of
    # raising, so the caller can refetch the buildId and retry rather than failing
    # the whole run.
    # x-nextjs-data:1 is REQUIRED on the _next/data route — without it the server
    # returns the HTML page instead of JSON (=> json.loads fails => "status 0").
    try:
        status, body = http_get(url, extra_headers={'x-nextjs-data': '1'})
        return status, json.loads(body)
    except Exception as e:
        return getattr(e, 'code', 0), None

def pick(d, *paths):
    for path in paths:
        cur = d
        ok = True
        for k in path.split('.'):
            if isinstance(cur, dict) and k in cur: cur = cur[k]
            else: ok = False; break
        if ok and cur not in (None, '', [], {}): return cur
    return ''

def known_skill_matcher():
    """Reuse jobpipe's skill configs to extract a requiredSkills line from text (fallback)."""
    skills = (load_json(cfg('skills.json'), {}) or {}).get('skills', [])
    al = load_json(cfg('skill_aliases.json'), {}) or {}
    canon = {}
    for s in skills: canon[s.lower()] = s
    for c, variants in (al.get('aliases') or {}).items():
        for v in variants: canon[v.lower()] = c
    pats = sorted(canon.keys(), key=len, reverse=True)
    rx = re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(p) for p in pats) + r')(?![A-Za-z0-9])', re.I) if pats else None
    def extract(text):
        if not rx or not text: return []
        seen, out = set(), []
        for m in rx.finditer(text):
            c = canon.get(m.group(1).lower())
            if c and c not in seen: seen.add(c); out.append(c)
        return out
    return extract

def fmt_salary(lo, hi):
    def f(x):
        try: return '${:,.0f}'.format(float(x))
        except Exception: return ''
    a, b = f(lo), f(hi)
    if a and b: return f'{a} - {b}'
    return a or b or ''

def map_item(item, extract):
    title = pick(item, 'job_information.title', 'v5_processed_job_data.core_job_title')
    company = pick(item, 'v5_processed_job_data.company_name', 'enriched_company_data.name', 'board_token')
    loc = pick(item, 'v5_processed_job_data.formatted_workplace_location',
               'v5_processed_job_data.workplace_cities')
    if isinstance(loc, list): loc = ', '.join(str(x) for x in loc[:2])
    wt = str(pick(item, 'v5_processed_job_data.workplace_type'))
    if wt and wt.lower() == 'remote' and 'remote' not in str(loc).lower():
        loc = (str(loc) + ' (Remote)').strip()
    sal = fmt_salary(pick(item, 'v5_processed_job_data.yearly_min_compensation'),
                     pick(item, 'v5_processed_job_data.yearly_max_compensation'))
    slug = pick(item, 'id', 'objectID')
    skills = pick(item, 'v5_processed_job_data.technical_tools')
    if not isinstance(skills, list) or not skills:
        desc = pick(item, 'v5_processed_job_data.requirements_summary', 'job_information.description')
        skills = extract((str(title) or '') + ' ' + str(desc)[:6000])
    posted = pick(item, 'v5_processed_job_data.estimated_publish_date')
    # Use the REAL employer/ATS apply link (Dayforce/Workday/LinkedIn/etc.), NOT a
    # hiringcafe.com redirect. Fall back to the hiringcafe.com page only if none is present.
    real = pick(item, 'apply_url', 'v5_processed_job_data.apply_url', 'job_information.apply_url')
    apply_url = str(real) if real else (SITE + '/job/' + urllib.parse.quote(str(slug), safe=''))
    appl = pick(item, 'num_applicants', 'applicants', 'applicant_count',
                'v5_processed_job_data.num_applicants', 'v5_processed_job_data.applicant_count')
    # Stable posting identity for repost detection (reposts.py). requisition_id is the
    # employer's own req number — far more stable across re-listings than the hiringcafe
    # slug (which changes per listing). Matching still works off applyUrl when absent.
    req = pick(item, 'requisition_id', 'v5_processed_job_data.requisition_id')
    return {'source': 'hiring_cafe', 'id': 'hc_' + str(slug)[:48], 'title': str(title),
            'company': str(company), 'location': str(loc), 'salary': sal,
            'applyUrl': apply_url, 'reqId': str(req) if req else '',
            'requiredSkills': [str(s) for s in skills][:25],
            'postedDate': str(posted)[:10] if posted else '',
            'applicants': appl if isinstance(appl, (int, float)) else '',
            'expired': bool(pick(item, 'is_expired'))}

def _ensure_jobspy():
    """Import jobspy; if missing, self-heal by pip-installing it ONCE, then retry.
    Runs on the user's machine so LinkedIn scraping doesn't silently stay disabled
    when the package isn't present. Fail-soft: returns (scrape_jobs, note)."""
    try:
        from jobspy import scrape_jobs
        return scrape_jobs, ''
    except Exception:
        pass
    # Do NOT reach for the network+pip in a packaged install: inside a frozen bundle
    # there is no pip, and on a shipped copy a surprise 600s install is worse than a
    # clear "LinkedIn is off" message. Set TOP_RAT_NO_AUTOINSTALL=1 to opt out too.
    if getattr(sys, 'frozen', False) or _env('NO_AUTOINSTALL'):
        return None, ('LinkedIn search is unavailable: the optional python-jobspy package '
                      'is not installed. Install it with: pip install python-jobspy')
    try:
        import subprocess
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'python-jobspy'],
                       timeout=600, check=False)
        from jobspy import scrape_jobs
        return scrape_jobs, 'jobspy auto-installed this run'
    except Exception as e:
        return None, f'jobspy not installed and auto-install failed ({e}); run: pip install python-jobspy'

def linkedin_via_jobspy(url_key, urls, extract):
    """Optional LinkedIn scrape via python-jobspy (auto-installed if missing).
    Reads search params from config linkedin_24h URL. Fail-soft: returns ([], reason)."""
    scrape_jobs, imp_note = _ensure_jobspy()
    if scrape_jobs is None:
        return [], imp_note
    li = urls.get('linkedin_24h')
    if not li: return [], 'linkedin_24h not in config/search_urls.json'
    q = urllib.parse.parse_qs(urllib.parse.urlparse(li).query)
    kw = (q.get('keywords') or [''])[0]
    loc = (q.get('location') or ['Houston, Texas, United States'])[0]
    try: dist = int((q.get('distance') or ['50'])[0])
    except Exception: dist = 50
    hours = 24 if url_key.endswith('24h') else 168
    # jobspy's scrape_jobs signature varies by version — pass only kwargs it actually accepts,
    # so an older/newer jobspy doesn't error out (which would silently drop all LinkedIn results).
    want = {'site_name': ['linkedin'], 'search_term': kw, 'location': loc, 'distance': dist,
            'results_wanted': 40, 'hours_old': hours, 'linkedin_fetch_description': True}
    try:
        import inspect
        supported = set(inspect.signature(scrape_jobs).parameters)
        kwargs = {k: v for k, v in want.items() if k in supported}
    except Exception:
        kwargs = {'site_name': ['linkedin'], 'search_term': kw, 'location': loc, 'results_wanted': 40}
    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:
        return [], f'jobspy failed: {e}'
    out = []
    for r in df.to_dict('records'):
        jid = str(r.get('id') or r.get('job_url') or '').rstrip('/').split('/')[-1]
        sal = ''
        try:
            if r.get('min_amount') == r.get('min_amount') and r.get('min_amount') and str(r.get('interval')) == 'yearly':
                sal = fmt_salary(r.get('min_amount'), r.get('max_amount'))
        except Exception: pass
        desc = str(r.get('description') or '')
        title = str(r.get('title') or '')
        if not title: continue
        out.append({'source': 'linkedin_search', 'id': 'li_' + jid[:44], 'title': title,
                    'company': str(r.get('company') or ''), 'location': str(r.get('location') or ''),
                    'salary': sal, 'applyUrl': str(r.get('job_url') or ''),
                    'postedDate': str(r.get('date_posted') or '')[:10],
                    'requiredSkills': extract(title + ' ' + desc[:8000])})
    return out, imp_note

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url-key', default='hiringcafe_24h')
    ap.add_argument('--out', default=LISTINGS)
    ap.add_argument('--no-linkedin', action='store_true', dest='no_linkedin')
    ap.add_argument('--max-age-days', type=float, default=None, dest='max_age_days',
                    help='Drop hits whose estimated_publish_date is older than N days. '
                         'Overrides config max_post_age_days. 0 = no age filter.')
    a = ap.parse_args()
    meta_path = SCRAPE_META
    urls = load_json(cfg('search_urls.json'), {}) or {}
    url = urls.get(a.url_key)
    meta = {'timestamp': datetime.now().isoformat(timespec='seconds'),
            'urlKey': a.url_key, 'source': 'api', 'status': 'failed', 'count': 0}
    extract = known_skill_matcher()
    listings, seen = [], set()
    expired_ct = 0
    stale_ct = 0
    crowded_ct = 0
    _gui = load_json(cfg('gui_settings.json'), {}) or {}
    _strat = load_json(cfg('strategy.json'), {}) or {}
    # Applicant cap (dashboard toggle). NOTE: hiringcafe.com does not report applicant counts,
    # so this only filters sources that DO (e.g. LinkedIn via jobspy); hiringcafe.com hits have
    # applicants='' and pass through — freshness (max_age) is the practical proxy there.
    exclude_crowded = bool(_gui.get('excludeManyApplicants', _strat.get('exclude_many_applicants', True)))
    try: max_applicants = int(_gui.get('maxApplicants', _strat.get('max_applicants', 50)))
    except Exception: max_applicants = 50
    if a.max_age_days is not None:
        max_age = a.max_age_days
    else:
        try: max_age = float(_strat.get('max_post_age_days', 30))
        except Exception: max_age = 30.0

    def _too_old(posted):
        if not posted or max_age <= 0: return False
        try: age = (datetime.now() - datetime.strptime(posted[:10], '%Y-%m-%d')).days
        except Exception: return False
        return age > max_age
    # --- hiringcafe.com (Next.js public data route) ---
    hc_error = ''
    try:
        if not url: raise ValueError(f'{a.url_key} not in config/search_urls.json')
        state = search_state_from_url(url)
        build_id = get_build_id()
        for page in range(MAX_PAGES):
            status, data = data_page(build_id, state, page)
            if data is None and page == 0:       # stale buildId / transient bad response — refetch + retry once
                time.sleep(2)
                build_id = get_build_id()
                status, data = data_page(build_id, state, page)
            if data is None:
                if page == 0:
                    raise ValueError(f'hiringcafe.com returned no usable JSON (status {status}) after buildId refetch')
                break                            # transient failure mid-pagination — keep what we already have
            props = (data.get('pageProps') or {}) if isinstance(data, dict) else {}
            if page == 0:
                save_json(RAW_SAMPLE,
                          {'note': 'page 0 raw response (debug aid)', 'keys': sorted(props.keys()),
                           'firstHit': (props.get('ssrHits') or [None])[0]})
            if props.get('ssrError'): raise ValueError(f"ssrError: {props['ssrError']}")
            hits = props.get('ssrHits') or []
            if not hits: break
            for it in hits:
                m = map_item(it, extract)
                if m['id'] in seen or not m['title']: continue
                if m.get('expired'):
                    expired_ct += 1; continue   # skip postings hiringcafe.com marks closed
                if _too_old(m.get('postedDate')):
                    stale_ct += 1; continue      # skip stale reposts (published > max_post_age_days ago)
                if exclude_crowded and isinstance(m.get('applicants'), (int, float)) and m['applicants'] > max_applicants:
                    crowded_ct += 1; continue    # skip jobs with too many applicants (when the source reports it)
                seen.add(m['id']); listings.append(m)
            if props.get('ssrIsLastPage'): break
            time.sleep(1.5)
    except Exception as e:
        hc_error = str(e)
    meta['hiringcafe'] = {'count': len(listings), 'error': hc_error, 'expiredSkipped': expired_ct, 'staleSkipped': stale_ct, 'crowdedSkipped': crowded_ct}
    # --- LinkedIn (optional, via python-jobspy) ---
    if a.no_linkedin:
        li, li_note = [], 'disabled (--no-linkedin)'
    else:
        li, li_note = linkedin_via_jobspy(a.url_key, urls, extract)
    added = 0
    for x in li:
        if x['id'] not in seen:
            seen.add(x['id']); listings.append(x); added += 1
    meta['linkedin'] = {'count': added, 'note': li_note}
    # --- write ---
    if listings:
        save_json(a.out, listings)
        miss = sum(1 for x in listings if not x['requiredSkills'])
        meta.update(status='ok', count=len(listings), missingSkills=miss, out=os.path.basename(a.out))
        print(f'OK: {len(listings)} listings ({added} linkedin) -> {a.out} (no-skills: {miss})')
        # Best-effort: geocode any new job cities into geo_cache.json so the
        # locality geo pass (locality.py) can radius-match them. Zero Claude
        # tokens; capped + throttled; failure never breaks the scrape.
        try:
            import locality
            ok_g, fail_g, _ = locality.refresh_from_files(cap=20)
            if ok_g or fail_g:
                print(f'locality: geocoded {ok_g} new location(s), {fail_g} failed', file=sys.stderr)
        except Exception as e:
            print(f'NOTE: locality refresh skipped: {e}', file=sys.stderr)
        if hc_error: print(f'NOTE: hiringcafe.com failed this run: {hc_error}', file=sys.stderr)
        if li_note: print(f'NOTE: linkedin: {li_note}', file=sys.stderr)
    else:
        meta['error'] = f'hiringcafe: {hc_error or "no results"}; linkedin: {li_note or "no results"}'
        print(f'FAILED: {meta["error"]}', file=sys.stderr)
    save_json(meta_path, meta)
    sys.exit(0 if meta['status'] == 'ok' else 1)

if __name__ == '__main__':
    import runlog; runlog.run('scrape.py', main)
