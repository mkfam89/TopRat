#!/usr/bin/env python3
"""diag_hiringcafe.py - one-off diagnostic for the hiring.cafe scraper failure.
Runs on Khoa's PC. Tests BOTH domains (hiring.cafe and hiringcafe.com):
  1) homepage fetch: status, final URL after redirects, buildId found?
  2) the _next/data/<buildId>/index.json route: status + first 300 chars of body
Prints everything so we can see whether it's a domain move, a Cloudflare/HTML
block, a 404 (buildId/route changed), or a schema change. Read-only; changes nothing.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import json, urllib.request, urllib.parse, re, sys

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126 Safari/537.36')

def fetch(url, headers=None):
    h = {'User-Agent': UA, 'Accept': 'text/html,application/json,*/*'}
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode('utf-8', 'replace')
            return r.status, r.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, url, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return 0, url, f'<EXCEPTION> {type(e).__name__}: {e}'

def buildid(html):
    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None

def test_domain(base):
    print('=' * 70)
    print('DOMAIN:', base)
    st, final, html = fetch(base + '/')
    print(f'  homepage: status={st}  final_url={final}')
    if final.rstrip('/') != base.rstrip('/'):
        print('  >> REDIRECTED to a different URL (domain/path moved)')
    bid = buildid(html) if isinstance(html, str) and st == 200 else None
    print(f'  buildId found: {bid!r}')
    if isinstance(html, str) and ('Just a moment' in html or 'cf-browser-verification' in html):
        print('  >> looks like a CLOUDFLARE challenge page')
    if not bid:
        print('  (no buildId -> cannot build the _next/data route for this domain)')
        return
    # Same minimal searchState the scraper uses; page 0.
    state = {"searchQuery": "", "locations": [], "workplaceTypes": [], "page": 0}
    ss = urllib.parse.quote(json.dumps(state, separators=(',', ':')), safe='')
    durl = f'{base}/_next/data/{bid}/index.json?searchState={ss}&page=0'
    st2, final2, body2 = fetch(durl, headers={'Referer': base + '/'})
    print(f'  data route: status={st2}  final_url={final2}')
    print(f'  body[:300]: {str(body2)[:300]!r}')
    try:
        json.loads(body2); print('  >> body parses as JSON (route still works!)')
    except Exception:
        print('  >> body is NOT valid JSON (HTML error page / redirect / block)')

if __name__ == '__main__':
    for d in ('https://hiring.cafe', 'https://hiringcafe.com'):
        test_domain(d)
    print('=' * 70)
    print('Done. Paste this whole output back to Claude.')
    input('Press Enter to close...')
