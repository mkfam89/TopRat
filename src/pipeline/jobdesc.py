#!/usr/bin/env python3
"""jobdesc.py — on-demand job-description fetcher (script-only, zero-token, stdlib).

Fetches a posting's description text from its apply URL ON DEMAND, so the pipeline never
has to store large description blobs. Called by the dashboard's /api/jobdesc endpoint when
the user opens a card; results are cached on disk so re-opens are instant and repeat
external calls stay low.

Extraction order (first hit wins):
  1. per-host JSON API   — Greenhouse / Lever public endpoints return clean description JSON.
  2. schema.org JSON-LD  — `<script type="application/ld+json">` JobPosting.description; present
                            on a large fraction of ATS pages (Ashby, Workday, Workable, iCIMS, …).
  3. generic HTML→text   — strip tags from the fetched page as a last resort.

Degrades gracefully: returns ok=False + a reason when a site is JavaScript-only or blocks us,
and the caller (UI) falls back to a "view original posting" link. Pure stdlib, no Claude, no
pip installs — consistent with the project's zero-token script-path rule.

CLI:
  python src/pipeline/jobdesc.py --url <url>              # fetch + print extracted text
  python src/pipeline/jobdesc.py --url <url> --json       # machine-readable {ok,text,source,...}
  python src/pipeline/jobdesc.py --url <url> --refresh    # ignore cache
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, re, json, html, time, argparse
import http.cookiejar
import urllib.request, urllib.parse

HERE = _paths.ROOT
CACHE = os.path.join(HERE, 'cache', 'jobdesc_cache.json')   # regenerable; safe to delete
TTL_SECONDS = 14 * 24 * 3600          # re-fetch a posting at most every 14 days
MAX_CHARS = 20000                     # cap stored/returned text
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


# ---------- cache ----------
def _load_cache():
    try:
        with open(CACHE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(c):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(c, f)
        os.replace(tmp, CACHE)
    except Exception:
        pass


# ---------- http ----------
def _http_get(url, headers=None, timeout=20):
    h = {'User-Agent': UA,
         'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
         'Accept-Language': 'en-US,en;q=0.9'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get('Content-Type', ''), r.read().decode('utf-8', 'replace')


# ---------- text helpers ----------
_TAG = re.compile(r'<[^>]+>')
_MULTISPACE = re.compile(r'[ \t ]+')
_MULTINL = re.compile(r'\n\s*\n\s*\n+')

def html_to_text(s):
    """Collapse an HTML fragment into readable plain text with bullet/paragraph breaks.

    Unescapes entities first so entity-escaped HTML (e.g. Greenhouse's `content`, which arrives
    as `&lt;p&gt;…`) becomes real tags before we convert/strip them."""
    if not s:
        return ''
    s = html.unescape(s)
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'(?i)<\s*li[^>]*>', '\n• ', s)                 # <li> -> bullet
    s = re.sub(r'(?i)<\s*/?(br|p|div|ul|ol|h[1-6]|tr|section)\s*/?>', '\n', s)
    s = _TAG.sub('', s)
    s = html.unescape(s)
    s = _MULTISPACE.sub(' ', s)
    lines = [ln.strip() for ln in s.splitlines()]
    s = '\n'.join(ln for ln in lines)
    s = _MULTINL.sub('\n\n', s).strip()
    return s[:MAX_CHARS]


# ---------- unreadable-page gate ----------
# A page that fetches fine can still carry no description: a sign-in wall, an expired
# posting, a JavaScript shell, a raw JSON config blob, or nothing but the site's own menus
# and legal text. Those all clear a naive length check, so without this gate they get cached
# and rendered as if they were the job. assess() names what came back instead, and fetch()
# returns that message with NO text, so the UI can say why rather than show the metadata.
MIN_CHARS = 120                       # below this there is nothing to read either way

_RE_EXPIRED = re.compile(r'(?i)\b(this (job|position|posting|vacancy|role)[^.\n]{0,40}'
                         r'(has expired|is no longer|was removed)'
                         r'|sorry, this job has expired'
                         r'|no longer (accepting applications|active|available|open)'
                         r'|position (has been|was) filled'
                         r'|posting (has been|was) (closed|removed))')
_RE_LOGIN = re.compile(r'(?i)(join or sign in\b'
                       r'|join to apply\b'
                       r'|sign in to (view|find|see|apply|continue)'
                       r'|log ?in to (view|see|apply|continue)'
                       r'|create an account to (view|see|apply)'
                       r'|new to linkedin\? join now'
                       r'|to view or add a comment, sign in)')
_RE_JS = re.compile(r'(?i)(enable javascript|javascript (is )?(required|disabled|must be)'
                    r'|requires javascript|please enable js|browser (does not|doesn\'t) support)')
_RE_TEMPLATE = re.compile(r'\{\{\s*[\w$][\w.$\[\]\'"| ]{0,60}\}\}')   # unrendered Angular/Handlebars

# Phrases a real posting almost always carries at least a couple of. Deliberately
# description-specific — words like "team" or "apply" also live in site chrome.
_SIGNALS = ('responsibilit', 'qualification', 'requirement', 'you will', "you'll", 'you’ll',
            'we are looking', 'we’re looking', "we're looking", 'about the role',
            'about the job', 'about the position', 'about this role', 'what you will do',
            "what you'll do", 'years of experience', 'years’ experience', 'preferred',
            'minimum', 'duties', 'benefits', 'compensation', 'salary range', 'job summary',
            'essential functions', 'degree in', 'proficien', 'experience in',
            'experience with', 'ability to')
# Deliberately NOT signals: "skills", "candidate", "job description" — all three appear in
# empty field labels and sign-in chrome (a Taleo shell that renders nothing but the labels
# "Description / Qualifications / Refer a candidate" would otherwise read as a real posting).


def _signal_count(low):
    """How many distinct posting-ish phrases the text carries."""
    return sum(1 for s in _SIGNALS if s in low)


def _looks_like_raw_json(text):
    """A config/redirect blob served as the page — data about the page, not the job."""
    s = text.strip()
    if s.startswith(('{', '[')) and s.endswith(('}', ']')) and '":' in s:
        return True
    return bool(re.match(r'(?is)^\s*(window\.__|var __|\{"widget")', s))


def _mostly_chrome(text):
    """Nav bars, language pickers and link lists strip down to many very short lines."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    short = sum(1 for ln in lines if len(ln.strip()) < 30)
    return short / len(lines) > 0.6


# reason code → what to tell the user. Plain language, no jargon: the point is that they
# know why the pane is empty and that the "View original posting" link is the way through.
MESSAGES = {
    'empty':    'Nothing readable came back from the posting. Open it on the site to read it.',
    'metadata': 'The site sent page data instead of the posting — it builds the description '
                'in the browser. Open the original posting to read it.',
    'js':       'This page needs a real browser to render. Open the original posting to read it.',
    'login':    'This posting is behind a sign-in wall. The site only shows the full '
                'description to signed-in users. Open the original posting to read it.',
    'expired':  'This posting has been taken down — the site says it is no longer accepting '
                'applications.',
    'chrome':   "Only the site's own menus and legal text came back, with no job description. "
                'Open the original posting to read it.',
}
# Shown ABOVE a description that is still readable, so a taken-down posting whose text is
# still on the page warns instead of disappearing.
NOTICE_EXPIRED = 'Heads up: this posting says it is no longer accepting applications.'


def assess(text, url=''):
    """Classify extracted text. Returns (reason_code, human message).

    An empty reason means the text is renderable — the message is then either '' or an
    advisory to show alongside it (an expired posting whose description is still on the
    page). A non-empty reason means show the message INSTEAD of the text.

    Conservative by design: the strong signatures (redirect blob, sign-in wall, unrendered
    template) reject on their own, while the fuzzy "it's just chrome" call also requires the
    posting vocabulary to be absent."""
    text = (text or '').strip()
    if len(text) < MIN_CHARS:
        return 'empty', MESSAGES['empty']
    low = text.lower()
    sig = _signal_count(low)
    if _looks_like_raw_json(text):
        return 'metadata', MESSAGES['metadata']
    if len(_RE_TEMPLATE.findall(text)) >= 2:
        return 'js', MESSAGES['js']
    if _RE_EXPIRED.search(low):
        # Some boards leave the full text under the "expired" banner. Keep it and warn,
        # rather than hiding a description the user can still read.
        return ('', NOTICE_EXPIRED) if sig >= 4 else ('expired', MESSAGES['expired'])
    if _RE_JS.search(low) and sig < 3:
        return 'js', MESSAGES['js']
    # A wall page may still print a teaser; only call it a wall when the posting
    # vocabulary is thin, so a page that got past the wall is never thrown away.
    if _RE_LOGIN.search(low) and sig < 4:
        return 'login', MESSAGES['login']
    if sig < 2 and (_mostly_chrome(text) or len(text) < 600):
        return 'chrome', MESSAGES['chrome']
    return '', ''


def _iter_json_objects(parsed):
    """Walk a parsed JSON-LD value yielding every dict (handles arrays + @graph)."""
    stack = [parsed]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            yield x
            g = x.get('@graph')
            if isinstance(g, list):
                stack.extend(g)
        elif isinstance(x, list):
            stack.extend(x)


# ---------- extractors (pure — unit-testable without network) ----------
def extract_jsonld(page):
    """schema.org JobPosting.description from any ld+json block. Returns (text, title)."""
    for m in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page):
        blob = (m.group(1) or '').strip()
        if not blob:
            continue
        try:
            parsed = json.loads(blob)
        except Exception:
            continue
        for obj in _iter_json_objects(parsed):
            t = obj.get('@type')
            types = t if isinstance(t, list) else [t]
            if 'JobPosting' in types and obj.get('description'):
                return html_to_text(obj['description']), (obj.get('title') or '')
    return '', ''


def extract_generic(page):
    """Last-resort: strip the whole page to text. Good enough for server-rendered HTML."""
    # Prefer a <main>/<article>/description container if present, else whole body.
    for pat in (r'(?is)<main\b[^>]*>(.*?)</main>',
                r'(?is)<article\b[^>]*>(.*?)</article>',
                r'(?is)<div[^>]+class=["\'][^"\']*(?:job|posting|description|content)[^"\']*["\'][^>]*>(.*?)</div>'):
        m = re.search(pat, page)
        if m and len(_TAG.sub('', m.group(1))) > 400:
            return html_to_text(m.group(1))
    body = re.search(r'(?is)<body\b[^>]*>(.*?)</body>', page)
    return html_to_text(body.group(1) if body else page)


def _greenhouse_api(url):
    """Map a Greenhouse posting URL to its JSON API {board, id}, or None."""
    q = urllib.parse.urlparse(url)
    if 'greenhouse.io' not in q.netloc:
        return None
    m = re.search(r'/([^/]+)/jobs/(\d+)', q.path)
    if m:
        return 'https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s' % (m.group(1), m.group(2))
    qs = urllib.parse.parse_qs(q.query)
    tok = (qs.get('token') or [''])[0]
    board = (qs.get('for') or [''])[0]
    if tok and board:
        return 'https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s' % (board, tok)
    return None


def _lever_api(url):
    """Map a Lever posting URL to its JSON API, or None."""
    q = urllib.parse.urlparse(url)
    if 'lever.co' not in q.netloc:
        return None
    m = re.search(r'/([^/]+)/([0-9a-f-]{16,})', q.path)
    if m:
        return 'https://api.lever.co/v0/postings/%s/%s?mode=json' % (m.group(1), m.group(2))
    return None


def parse_greenhouse(api_text):
    d = json.loads(api_text)
    return html_to_text(d.get('content', '') or '')


def parse_lever(api_text):
    d = json.loads(api_text)
    if isinstance(d, list):                      # some endpoints wrap in a list
        d = d[0] if d else {}
    parts = [d.get('description', '') or '']
    for sec in (d.get('lists') or []):
        if sec.get('text'):
            parts.append('\n%s\n' % sec['text'])
        parts.append(sec.get('content', '') or '')
    parts.append(d.get('additional', '') or '')
    return html_to_text('\n'.join(p for p in parts if p))


def _eightfold_api(url):
    """Map an Eightfold careers URL to its public position JSON API, or None.
    `https://<co>.eightfold.ai/careers/job/<id>` → `/api/pcsx/position_details?position_id=<id>`.
    The `domain` query param the SPA sends is NOT required — the endpoint resolves by id."""
    q = urllib.parse.urlparse(url)
    if 'eightfold.ai' not in q.netloc.lower():
        return None
    m = re.search(r'/careers/job/(\d+)', q.path) or re.search(r'[?&](?:jobid|pid|position_id)=(\d+)', url, re.I)
    if not m:
        return None
    return '%s://%s/api/pcsx/position_details?position_id=%s&hl=en' % (q.scheme, q.netloc, m.group(1))


def parse_eightfold(api_text):
    d = json.loads(api_text)
    data = d.get('data') or d
    parts = []
    if data.get('name'):
        parts.append('<h1>%s</h1>' % data['name'])
    parts.append(data.get('jobDescription', '') or data.get('job_description', '') or '')
    return html_to_text('\n'.join(p for p in parts if p))


def _workday_api(url):
    """Map a Workday posting URL to its public CXS JSON endpoint, or None.

    `https://<tenant>.wdN.myworkdayjobs.com[/<lang>]/<site>/job/<path>`
      → `https://<tenant>.wdN.myworkdayjobs.com/wday/cxs/<tenant>/<site>/job/<path>`

    Needed because the posting URL itself answers a cold request with a redirect stub
    (`{"widget":"redirect","url":"…","externalSpa":true}`) — page data, not the job."""
    q = urllib.parse.urlparse(url)
    host = q.netloc.lower()
    if 'myworkdayjobs.com' not in host and 'myworkdaysite.com' not in host:
        return None
    tenant = host.split('.')[0]
    m = re.match(r'^/(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?([^/]+)/job/(.+)$', q.path)
    if not m or not tenant:
        return None
    return '%s://%s/wday/cxs/%s/%s/job/%s' % (q.scheme, q.netloc, tenant, m.group(1), m.group(2))


def redirect_target(url, body):
    """The canonical path out of a Workday redirect stub, absolutised. '' when the body
    isn't a stub. Workday answers some job URLs with the stub and serves the real record
    only at the path it names."""
    s = (body or '').strip()
    if not s.startswith('{') or '"redirect"' not in s:
        return ''
    try:
        d = json.loads(s)
    except Exception:
        return ''
    if not isinstance(d, dict) or d.get('widget') != 'redirect' or not d.get('url'):
        return ''
    return urllib.parse.urljoin(url, d['url'])


def parse_workday(api_text):
    """CXS job record → text. The description is HTML in `jobPostingInfo.jobDescription`."""
    d = json.loads(api_text)
    info = d.get('jobPostingInfo') or d.get('jobPosting') or d
    if not isinstance(info, dict):
        return ''
    parts = []
    if info.get('title'):
        parts.append('<h1>%s</h1>' % info['title'])
    for k in ('location', 'timeType', 'jobRequisitionLocation'):
        v = info.get(k)
        if isinstance(v, str) and v.strip():
            parts.append('<p>%s</p>' % v.strip())
    parts.append(info.get('jobDescription', '') or '')
    return html_to_text('\n'.join(p for p in parts if p))


def _session_get(url, prime_urls=(), tries=3, timeout=20):
    """GET with a per-call cookie jar. Some ATSes (Taleo careersection) return a JS shell
    to a cold request and only serve the real server-rendered job once a session exists:
    the first hit sets `JSESSIONID`, and the app expects the careersection entry to have
    been visited. So we optionally `prime_urls` (fetch the section root to seed the session
    in the same jar) and then retry the job URL until the real (URL-encoded) body appears.

    The "got content" signal is the encoded body's `%3C` count: the real page carries
    hundreds; a shell has almost none. Never raises for a prime miss — priming is
    best-effort, the job fetch is what matters."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    h = {'User-Agent': UA,
         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
         'Accept-Language': 'en-US,en;q=0.9',
         'Referer': (prime_urls[0] if prime_urls else url)}
    for pu in prime_urls:
        try:
            with opener.open(urllib.request.Request(pu, headers=h), timeout=timeout) as r:
                r.read(1)
        except Exception:
            pass
    body = ''
    for _ in range(max(1, tries)):
        try:
            with opener.open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                body = r.read().decode('utf-8', 'replace')
        except Exception:
            break
        if body.count('%3C') > 50:      # real server-rendered job body present — stop
            break
    return body


def _taleo_prime_urls(url):
    """The careersection section root + job-search page, to seed a Taleo session before the
    job detail is requested (mirrors a real visitor arriving via the listing)."""
    q = urllib.parse.urlparse(url)
    m = re.match(r'(/careersection/[^/]+/)', q.path)
    if not m:
        return ()
    root = '%s://%s%s' % (q.scheme, q.netloc, m.group(1))
    lang = (urllib.parse.parse_qs(q.query).get('lang') or ['en'])[0]
    return (root + 'jobsearch.ftl?lang=' + lang, root + 'moresearch.ftl?lang=' + lang)


def _is_taleo(url):
    q = urllib.parse.urlparse(url)
    return 'taleo.net' in q.netloc and ('jobdetail' in q.path or 'careersection' in q.path)


def parse_taleo(html_doc):
    """Taleo careersection `jobdetail.ftl`: server-rendered but **URL-encoded** — the job
    body is embedded with `%3C`=`<`, `%24`=`$`, etc., and decoded client-side. So unquote
    BEFORE stripping, or the pay reads as `%2491,700`. Prefer the requisition-description
    block; fall back to the whole decoded page (still carries the pay line)."""
    # No real job body (session shell) → miss, so we never cache page chrome as a
    # "description". The encoded body carries hundreds of `%3C`; a shell has almost none.
    if (html_doc or '').count('%3C') < 30:
        return ''
    dec = urllib.parse.unquote(html_doc)
    m = re.search(r'(?is)id=["\']requisitionDescriptionInterface[^"\']*["\'][^>]*>(.*?)'
                  r'(?:<div[^>]+id=["\']requisitionDescriptionInterface\.[^"\']*bottom|'
                  r'<div[^>]+class=["\'][^"\']*jobfooter)', dec)
    frag = m.group(1) if (m and len(_TAG.sub('', m.group(1))) > 200) else dec
    return html_to_text(frag)


def _is_paylocity(url):
    return 'recruiting.paylocity.com' in urllib.parse.urlparse(url).netloc.lower()


def parse_paylocity(page):
    """Paylocity public job page (`/Recruiting/Jobs/Details/<id>`) is server-rendered, but
    the pay sits in a sibling `<div class="job-listing-header">Salary Description</div>
    <div>$…</div>` AFTER the description container — narrow container extraction would drop
    it. Strip the whole job-preview region (title → apply bar) so the salary is kept."""
    m = re.search(r'(?is)(<div[^>]+class=["\'][^"\']*job-preview[^"\']*["\'].*?)'
                  r'<div[^>]+class=["\'][^"\']*preview-bottom-apply', page)
    frag = m.group(1) if (m and len(_TAG.sub('', m.group(1))) > 200) else page
    return html_to_text(frag)


# ---------- orchestration ----------
def fetch(url, refresh=False):
    """Return {ok, text, source, url, title, cached, error}. Cached on disk with a TTL."""
    url = (url or '').strip()
    if not url or not url.lower().startswith(('http://', 'https://')):
        return {'ok': False, 'text': '', 'source': '', 'url': url, 'cached': False,
                'reason': 'nourl', 'message': 'No link to the original posting was stored '
                'for this job, so there is nothing to read from.',
                'error': 'no usable apply URL'}
    cache = _load_cache()
    hit = cache.get(url)
    if hit and not refresh and (time.time() - hit.get('at', 0) < TTL_SECONDS) and hit.get('text'):
        # Entries cached before the gate existed (or by an older gate) can be metadata.
        # Re-check on read and drop the bad ones so they re-fetch instead of rendering.
        keep, note = assess(hit['text'], url)
        if not keep:
            return {'ok': True, 'text': hit['text'], 'source': hit.get('source', 'cache'),
                    'url': url, 'title': hit.get('title', ''), 'cached': True,
                    'reason': '', 'message': note, 'error': ''}
        cache.pop(url, None)
        _save_cache(cache)

    text, source, title, err = '', '', '', ''
    try:
        # 1. per-host JSON APIs (cleanest)
        api = _greenhouse_api(url)
        if api:
            _, _, body = _http_get(api)
            text, source = parse_greenhouse(body), 'greenhouse'
        if not text:
            api = _lever_api(url)
            if api:
                _, _, body = _http_get(api)
                text, source = parse_lever(body), 'lever'
        if not text:
            api = _eightfold_api(url)
            if api:
                _, _, body = _http_get(api)
                text, source = parse_eightfold(body), 'eightfold'
        if not text:
            api = _workday_api(url)
            if api:
                try:
                    _, _, body = _http_get(api, headers={'Accept': 'application/json'})
                    text, source = parse_workday(body), 'workday'
                except Exception:
                    text = ''
                if not text:
                    # The posting URL answers with a redirect stub naming the canonical
                    # path; the CXS record lives under that path, not this one.
                    try:
                        _, _, body = _http_get(url)
                        nxt = redirect_target(url, body)
                        api2 = _workday_api(nxt) if nxt else None
                        if api2:
                            _, _, body2 = _http_get(api2, headers={'Accept': 'application/json'})
                            text, source = parse_workday(body2), 'workday'
                    except Exception:
                        pass
        # 1b. session-gated / encoded server-rendered ATSes with no clean JSON API.
        if not text and _is_taleo(url):
            text, source = parse_taleo(_session_get(url, _taleo_prime_urls(url))), 'taleo'
        if not text and _is_paylocity(url):
            _, _, page = _http_get(url)
            text, source = parse_paylocity(page), 'paylocity'
        # 2 + 3. fetch the page, try JSON-LD then generic strip
        if not text:
            _, ct, page = _http_get(url)
            if 'application/json' in ct and page.strip().startswith(('{', '[')):
                text, source = html_to_text(page), 'json'
            else:
                text, title = extract_jsonld(page)
                if text:
                    source = 'jsonld'
                else:
                    text, source = extract_generic(page), 'html'
    except Exception as e:
        err = '%s: %s' % (type(e).__name__, e)

    text = (text or '').strip()
    # Gate before caching: a sign-in wall, an expired notice, a redirect blob or bare page
    # chrome is not a description. Return the reason and NO text, so the UI shows a message
    # instead of the metadata, and nothing junk ever reaches the cache.
    reason, message = assess(text, url)
    if reason:
        return {'ok': False, 'text': '', 'source': source, 'url': url, 'title': title,
                'cached': False, 'reason': reason, 'message': message,
                'error': err or 'no readable description found (%s)' % reason}

    cache[url] = {'at': time.time(), 'text': text[:MAX_CHARS], 'source': source, 'title': title}
    _save_cache(cache)
    return {'ok': True, 'text': text[:MAX_CHARS], 'source': source, 'url': url,
            'title': title, 'cached': False, 'reason': '', 'message': message, 'error': ''}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    res = fetch(a.url, refresh=a.refresh)
    if a.json:
        print(json.dumps(res))
    else:
        print('source:', res['source'], '| ok:', res['ok'], '| cached:', res['cached'])
        if res.get('message'):
            print('why:', res['message'])
        if res['error']:
            print('error:', res['error'])
        print('-' * 60)
        print(res['text'])


if __name__ == '__main__':
    main()
