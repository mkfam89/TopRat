#!/usr/bin/env python3
"""tracker.py — tracker/html/CSV state: applied reconcile, folder scans, status.

Split out of jobpipe.py: read/write_tracker, applied-status normalization,
scan_active_jobs, resolve_status, tracking.csv / archive_index.csv writers,
candidates + simplify + archive + import/export commands. Imported by
jobpipe.py (which re-exports for tests); external callers keep shelling out
to `python src/pipeline/jobpipe.py <cmd>`.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import sys, os, re, json, zipfile
import csv as _csv
from datetime import datetime, timedelta

from pipelib import (BASE, JSON_PATH, HTML_PATH, TRACKING_CSV, ARCHIVE_CSV,
                     CANDIDATES_CSV, TO_PROCESS, TEMPLATES, load_json, data_path,
                     load_listings, _safe_write_text, stem, camel_to_words, tracker_id,
                     is_resume_file)
from scoring import norm, skill_match, score_job, strategy
from blocklist import is_blocked
import reposts  # advisory repost annotation (never excludes)

def read_tracker():
    if os.path.exists(JSON_PATH) and os.path.getsize(JSON_PATH) > 2:
        d = load_json(JSON_PATH, None)
        if d is None:
            import time
            for _ in range(3):
                time.sleep(2)
                d = load_json(JSON_PATH, None)
                if d is not None: break
        if d is None:
            sys.exit('FATAL: job_tracker.json exists but cannot be parsed (mount truncation?). '
                     'Refusing to proceed - restore from git HEAD or Backups/snapshots first.')
        return d
    return {}

def write_tracker(d):
    # ensure_ascii=True keeps job_details ASCII-only (also avoids the rebuild re.sub \\u crash)
    _safe_write_text(JSON_PATH, json.dumps(d, indent=2, ensure_ascii=True), verify_json=True)
def read_html():
    with open(HTML_PATH, encoding='utf-8') as f: return f.read()
def write_html(h):
    _safe_write_text(HTML_PATH, h, verify_json=False)

# ---- applied-status normalization ----------------------------------------
# The applied map (job_tracker.json['applied']) is keyed by an older, hand-
# abbreviated id scheme (e.g. 'Alkami_SrSRERelease'), while the scrape now
# derives deterministic ids from the full posting title
# ('Alkami_SrSiteReliabilityEngineerRelease'). Exact-id matching misses ~90%
# of applied jobs, so they reappear in candidates.csv / notifications. We
# reconcile the two schemes on a normalized company+role key.
_CANON_PHRASES = [('site reliability engineer', 'sre'), ('site reliability', 'sre')]
_CANON_WORDS = {'senior': 'sr', 'junior': 'jr', 'engineer': 'eng', 'engineering': 'eng',
                'infrastructure': 'infra', 'operations': 'ops', 'operation': 'ops',
                'developer': 'dev', 'administrator': 'admin', 'systems': 'sys',
                'system': 'sys', 'services': 'svc', 'service': 'svc',
                'technical': 'tech', 'ii': '2', 'iii': '3', 'iv': '4'}
_CANON_DROP = {'the', 'and', 'of', 'a', 'an', 'remote', 'us', 'usa', 'united',
               'states', 'inc', 'llc', 'corp', 'co'}

def _canon(text):
    s = re.sub(r'[^a-z0-9]+', ' ', camel_to_words(text or '').lower())
    for a, b in _CANON_PHRASES:
        s = s.replace(a, b)
    return ''.join(_CANON_WORDS.get(w, w) for w in s.split() if w not in _CANON_DROP)

def _canon_tokens(text):
    """Order-independent normalized token SET of a role, so 'Engineer Sr, DevOps'
    and 'Sr DevOps Engineer' compare equal, and extra qualifier words in a repost
    ('... - Houston, TX', '... Services') don't defeat the match."""
    s = re.sub(r'[^a-z0-9]+', ' ', camel_to_words(text or '').lower())
    for a, b in _CANON_PHRASES:
        s = s.replace(a, b)
    return frozenset(_CANON_WORDS.get(w, w) for w in s.split() if w not in _CANON_DROP)

def applied_norm_index(applied_map):
    """Index applied-map entries as (canonCompany, roleTokenSet, date) for fuzzy reconcile."""
    out = []
    for k, v in (applied_map or {}).items():
        parts = k.split('_', 1)
        out.append((_canon(parts[0]), _canon_tokens(parts[1] if len(parts) > 1 else ''), v))
    return out

def applied_match(idx, company, role):
    """Date if this company+role was applied to (normalized), else None.
    Company must match (equal or containment >=4 chars) AND the shorter role token set
    must be a subset of the longer (order-independent). Single-token roles must match
    exactly, to avoid e.g. applied 'Analyst' swallowing every 'Senior X Analyst'."""
    cc, cr = _canon(company), _canon_tokens(role)
    if not cr:
        return None
    for ac, ar, dt in idx:
        if not ar:
            continue
        if not (ac == cc or (len(ac) >= 4 and len(cc) >= 4 and (ac in cc or cc in ac))):
            continue
        small, large = (ar, cr) if len(ar) <= len(cr) else (cr, ar)
        if (small == large) if len(small) == 1 else (small <= large):
            return dt
    return None

def simplify_applied_set():
    h = read_html()
    m = re.search(r'const SIMPLIFY_APPLIED = (\{.*?\});', h, re.S)
    return set(json.loads(m.group(1)).keys()) if m else set()

# ---------- CSV source-of-truth + archive index ----------
TRACKING_COLS = ['id', 'company', 'role', 'status', 'statusDate', 'statusSource',
                 'bookmarked', 'foundDate', 'source', 'salary', 'skillMatch',
                 'flaggedSkills', 'applyUrl', 'resumePath', 'notes', 'answers']
ARCHIVE_COLS = ['company', 'role', 'status', 'archivedDate', 'foundDate',
                'applyUrl', 'resumeFile', 'zipFile', 'pathInZip']

def html_simplify_map():
    """Read the SIMPLIFY_APPLIED {id: date} map currently in job_tracker.html."""
    try:
        m = re.search(r'const SIMPLIFY_APPLIED = (\{.*?\});', read_html(), re.S)
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}

def seed_applied(tracker):
    """Migrate: applied state lives in tracker['applied'] {id:date}. Seed once from html."""
    if 'applied' not in tracker:
        tracker['applied'] = html_simplify_map()
    return tracker['applied']

def _prefer_pdf(jobs):
    """One row per job id; prefer the .pdf when a .docx twin exists (kept for ATS uploads)."""
    by_id = {}
    for j in jobs:
        prev = by_id.get(j['id'])
        if prev is None or (j['resume'].endswith('.pdf') and not prev['resume'].endswith('.pdf')):
            by_id[j['id']] = j
    return list(by_id.values())

def scan_active_jobs(tracker):
    """Same scan rebuild uses: one row per resume in New/Applied/Skipped (pdf preferred)."""
    EXCLUDE = set(TEMPLATES)
    job_details = tracker.get('job_details', {})
    archived_ids = {x['id'] for x in tracker.get('archived', [])}
    seen, jobs = set(), []
    for folder in ['New', 'Applied', 'Skipped']:
        fp = os.path.join(BASE, folder)
        if not os.path.isdir(fp): continue
        for company in sorted(os.listdir(fp)):
            cp = os.path.join(fp, company)
            if not os.path.isdir(cp): continue
            for fname in sorted(os.listdir(cp)):
                if fname.startswith('~$') or fname.startswith('PREVIEW') or fname in EXCLUDE: continue
                if not is_resume_file(fname): continue
                s = stem(fname)
                if s in archived_ids: continue
                seen.add(s)
                parts = s.split('_', 1); d = job_details.get(s, {})
                jobs.append({'id': s, 'company': camel_to_words(parts[0] if parts else s),
                             'role': camel_to_words(parts[1] if len(parts) > 1 else ''),
                             'resume': f'{folder}/{company}/{fname}', 'foundDate': d.get('foundDate', ''),
                             'applyUrl': d.get('applyUrl', ''), 'salary': d.get('salary', ''),
                             'source': d.get('source', ''), 'skillMatch': d.get('skillMatch', ''),
                             'flaggedSkills': d.get('flaggedSkills', [])})
    return _prefer_pdf(jobs)

def resolve_status(jid, applied_map, skip_set, applied_idx=None, company='', role=''):
    """Single-valued status. Applied wins over skip (matches html manual-applied precedence).
    Falls back to a normalized company+role match so applied jobs stored under the older
    abbreviated id scheme are still recognized."""
    if jid in applied_map: return 'applied', applied_map.get(jid, ''), 'tracked'
    if applied_idx is not None:
        dt = applied_match(applied_idx, company, role)
        if dt is not None: return 'applied', dt, 'tracked-norm'
    if jid in skip_set: return 'skipped', '', 'tracked'
    # Blocked employers are auto-skipped (applied still wins above, so a job you
    # applied to before banning the employer stays 'applied'). Source 'blocked'
    # keeps them distinguishable from user-skipped jobs on the board.
    if is_blocked(company): return 'skipped', '', 'blocked'
    return 'pending', '', ''

def write_tracking_csv(tracker):
    applied = seed_applied(tracker)
    applied_idx = applied_norm_index(applied)
    skip_set = set(tracker.get('skipped_jobs', []))
    book = set(tracker.get('bookmarked', []))
    notes = tracker.get('notes', {}); answers = tracker.get('answers', {})
    jobs = scan_active_jobs(tracker)
    have = {j['id'] for j in jobs}
    for jid, d in (tracker.get('job_details') or {}).items():
        if jid in have or not d.get('trackedOnly'): continue
        parts = jid.split('_', 1)
        jobs.append({'id': jid, 'company': camel_to_words(parts[0] if parts else jid),
                     'role': camel_to_words(parts[1] if len(parts) > 1 else ''), 'resume': '',
                     'foundDate': d.get('foundDate', ''), 'applyUrl': d.get('applyUrl', ''),
                     'salary': d.get('salary', ''), 'source': d.get('source', ''),
                     'skillMatch': d.get('skillMatch', ''), 'flaggedSkills': d.get('flaggedSkills', [])})
    with open(TRACKING_CSV, 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=TRACKING_COLS); w.writeheader()
        for j in jobs:
            st, dt, src = resolve_status(j['id'], applied, skip_set, applied_idx, j['company'], j['role'])
            w.writerow({'id': j['id'], 'company': j['company'], 'role': j['role'],
                        'status': st, 'statusDate': dt, 'statusSource': src,
                        'bookmarked': 'yes' if j['id'] in book else 'no',
                        'foundDate': j['foundDate'], 'source': j['source'], 'salary': j['salary'],
                        'skillMatch': j['skillMatch'],
                        'flaggedSkills': '; '.join(j['flaggedSkills']) if isinstance(j['flaggedSkills'], list) else (j['flaggedSkills'] or ''),
                        'applyUrl': j['applyUrl'], 'resumePath': j['resume'],
                        'notes': notes.get(j['id'], ''), 'answers': answers.get(j['id'], '')})
    return len(jobs)

def write_archive_index(tracker):
    rows = tracker.get('archived', [])
    applied = tracker.get('applied', {}); skip_set = set(tracker.get('skipped_jobs', []))
    with open(ARCHIVE_CSV, 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=ARCHIVE_COLS); w.writeheader()
        for r in rows:
            st, _, _ = resolve_status(r.get('id', ''), applied, skip_set)
            w.writerow({'company': r.get('companyDisplay', ''), 'role': r.get('role', ''),
                        'status': st, 'archivedDate': r.get('archivedDate', ''),
                        'foundDate': r.get('foundDate', ''), 'applyUrl': r.get('applyUrl', ''),
                        'resumeFile': os.path.basename(r.get('zipPath', '')),
                        'zipFile': r.get('zipFile', ''), 'pathInZip': r.get('zipPath', '')})
    return len(rows)

def refresh_html_status(tracker):
    """Write SIMPLIFY_APPLIED (from tracker['applied']) + PIPELINE_SKIPPED into the html."""
    applied = seed_applied(tracker)
    h = read_html()
    blk = '// Applied state (source of truth: tracking.csv -> job_tracker.json["applied"]) — synced ' + \
          datetime.now().strftime('%Y-%m-%d') + '\nconst SIMPLIFY_APPLIED = ' + json.dumps(applied, indent=2) + ';'
    if 'const SIMPLIFY_APPLIED' in h:
        h = re.sub(r'(// [^\n]*\n)?const SIMPLIFY_APPLIED = \{.*?\};', blk, h, count=1, flags=re.S)
    else:
        h = re.sub(r'(const BOOKMARKED = \[.*?\];)', r'\1\n' + blk, h, count=1, flags=re.S)
    write_html(h)

def cmd_export_csv(a):
    tracker = read_tracker()
    n = write_tracking_csv(tracker); m = write_archive_index(tracker)
    write_tracker(tracker)  # persist any seeded 'applied'
    print(f"tracking.csv: {n} active jobs | archive_index.csv: {m} archived resumes")

def cmd_import_csv(a):
    """CSV is the source of truth: fold tracking.csv statuses back into job_tracker.json + html."""
    path = a.file or TRACKING_CSV
    tracker = read_tracker(); seed_applied(tracker)
    applied = tracker['applied']; skip = set(tracker.get('skipped_jobs', []))
    book = set(tracker.get('bookmarked', [])); notes = tracker.setdefault('notes', {})
    answers = tracker.setdefault('answers', {}); changed = 0
    with open(path, newline='', encoding='utf-8') as f:
        for row in _csv.DictReader(f):
            jid = (row.get('id') or '').strip()
            if not jid: continue
            st = (row.get('status') or '').strip().lower()
            if st == 'applied':
                applied[jid] = (row.get('statusDate') or '').strip() or datetime.now().strftime('%Y-%m-%d')
                skip.discard(jid)
            elif st == 'skipped':
                skip.add(jid); applied.pop(jid, None)
            else:
                applied.pop(jid, None); skip.discard(jid)
            if (row.get('bookmarked') or '').strip().lower() in ('yes', 'true', '1'): book.add(jid)
            else: book.discard(jid)
            if row.get('notes'): notes[jid] = row['notes']
            if row.get('answers'): answers[jid] = row['answers']
            changed += 1
    tracker['applied'] = applied; tracker['skipped_jobs'] = sorted(skip); tracker['bookmarked'] = sorted(book)
    write_tracker(tracker)
    refresh_html_status(tracker)
    write_tracking_csv(tracker); write_archive_index(tracker)
    print(f"imported {changed} rows from {os.path.basename(path)} -> job_tracker.json + html refreshed")

CANDIDATE_COLS = ['id', 'company', 'role', 'skillMatch', 'flaggedSkills', 'hasResume',
                  'status', 'source', 'foundDate', 'location', 'salary', 'applyUrl',
                  'repost', 'repostNote', 'titleFit', 'requiredSkills']

def _load_csv(fp):
    try:
        with open(fp, newline='', encoding='utf-8') as f: return list(_csv.DictReader(f))
    except Exception: return []

def cmd_candidates(a):
    """Discovery: score EVERY scraped job (no filtering) and write candidates.csv.
    hasResume=yes for jobs already tailored (a resume exists / it's in tracking.csv)."""
    # data_path/load_listings, not a bare relative open: scheduler.py runs steps with
    # cwd=<code root> while scrape.py writes listings.json into the DATA root, so the
    # saved schedule's "--listings listings.json" resolved to nothing and this command
    # silently rewrote candidates.csv empty.
    listings = load_listings(a.listings, 'candidates')
    if not listings:
        print('candidates.csv: UNCHANGED - the scrape returned 0 listings '
              '(not overwriting the existing pool with an empty file)')
        return
    tracker = read_tracker()
    _active = scan_active_jobs(tracker)
    tailored = {j['id'] for j in _active}
    applied_map = seed_applied(tracker)
    applied_idx = applied_norm_index(applied_map)
    skipped = set(tracker.get('skipped_jobs', []))
    # Normalized (company+role) fallbacks so a job whose resume was tailored/skipped
    # under the OLDER long-id scheme is still recognized once tracker_id() shortens
    # the id — otherwise discovery would treat it as untailored and re-tailor a short-
    # named DUPLICATE. Same subset-match the applied reconciler uses.
    tailored_idx = applied_norm_index({j['id']: '' for j in _active})
    skipped_idx = applied_norm_index({s: '' for s in skipped})
    today = datetime.now().strftime('%Y-%m-%d')
    # Discovery floor (dashboard slider -> gui_settings.discoveryFloor): keep low-match
    # jobs out of candidates.csv entirely. Applies to ALL jobs, local included. Only
    # already-tailored/applied jobs and anything the user queued are kept regardless.
    floor = strategy().get('discovery_min_match', 0.10)
    queued = set((load_json(TO_PROCESS, {}) or {}).get('ids', []))
    seen, rows, dropped = set(), [], 0
    for j in listings:
        title, company = j.get('title', ''), j.get('company', '')
        tid = j.get('trackerId') or tracker_id(company, title)
        if not tid or tid in seen: continue
        seen.add(tid)
        # score_job = shrunk skill match * title-relevance factor. An off-lane role
        # (product manager, recruiter, nurse...) that happens to share tool keywords is
        # demoted, not dropped — it stays visible in candidates.csv with the reason.
        pct, flagged, tfit, treason = score_job(title, j.get('requiredSkills', []) or [])
        has = (tid in tailored) or (applied_match(tailored_idx, company, title) is not None)
        is_applied = (tid in applied_map) or (applied_match(applied_idx, company, title) is not None)
        smf = pct if isinstance(pct, (int, float)) else 0.0
        is_skipped = (tid in skipped) or (applied_match(skipped_idx, company, title) is not None)
        blocked = is_blocked(company) is not None and not is_applied
        if not (has or is_applied or is_skipped or blocked or tid in queued or smf >= floor):
            dropped += 1; continue  # below discovery floor (applies to local jobs too)
        # Precedence: applied > blocked > skipped > tailored > candidate. A blocked
        # employer's job is kept (so you can see it was auto-skipped) but never enters
        # the tailoring-ready set below. Applied still wins — banning an employer never
        # rewrites a job you already applied to.
        status = ('applied' if is_applied else 'blocked' if blocked
                  else 'skipped' if is_skipped else 'tailored' if has else 'candidate')
        rows.append({'id': tid, 'company': company, 'role': title,
                     'skillMatch': pct if pct is not None else '',
                     'flaggedSkills': '; '.join(flagged), 'hasResume': 'yes' if (has or is_applied) else 'no',
                     'status': status, 'source': j.get('source', ''),
                     'foundDate': j.get('foundDate', today), 'location': j.get('location', ''),
                     'salary': j.get('salary', ''), 'applyUrl': j.get('applyUrl', ''),
                     'repost': '', 'repostNote': '',
                     'titleFit': ('%.2f %s' % (tfit, treason)) if tfit < 1.0 else '',
                     'requiredSkills': '; '.join(j.get('requiredSkills', []) or [])})
    # Advisory repost annotation: recognize a fresh listing whose posting we've seen
    # before (by applyUrl or company+role) and attach a short note (last seen + prior
    # applied/skipped status + prior note). NEVER excludes — reposts stay in the feed
    # so the user can reuse the tailored resume or ban a suspected ghost employer.
    try:
        rp = reposts.annotate(rows, tracker.get('job_details', {}), applied_map,
                              skipped, tracker.get('notes', {}))
        for r in rows:
            info = rp.get(r['id'])
            if info:
                r['repost'] = 'yes'
                r['repostNote'] = info['note']
    except Exception:
        pass  # advisory only — a detector hiccup must never break discovery
    rows.sort(key=lambda r: -(r['skillMatch'] if isinstance(r['skillMatch'], float) else 0))
    with open(CANDIDATES_CSV, 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=CANDIDATE_COLS); w.writeheader(); w.writerows(rows)
    thr = a.threshold if a.threshold is not None else strategy().get('min_match', 0.6)
    ready = [r for r in rows if isinstance(r['skillMatch'], float) and r['skillMatch'] >= thr
             and r['hasResume'] != 'yes' and r['status'] != 'blocked']
    nblocked = sum(1 for r in rows if r['status'] == 'blocked')
    bnote = f", {nblocked} blocked-employer" if nblocked else ""
    print(f"candidates.csv: {len(rows)} kept ({dropped} low-match remote dropped, floor {int(floor*100)}%{bnote}) | >= {int(thr*100)}% needing a resume: {len(ready)}")

# ---------- archive (Step 11) ----------
def cmd_archive(a):
    backups = os.path.join(BASE, 'Backups'); os.makedirs(backups, exist_ok=True)
    today = datetime.now(); today_str = today.strftime('%Y-%m-%d'); cutoff = today - timedelta(days=a.days)
    zip_name = f'job_backup_{today.strftime("%Y%m%d")}.zip'
    zip_path = os.path.join(backups, zip_name); zip_rel = f'Backups/{zip_name}'
    tracker = read_tracker()
    job_details = tracker.get('job_details', {})
    archived = tracker.get('archived', [])
    already = {x['id'] for x in archived}
    bookmarked = set(tracker.get('bookmarked', []))
    EXCLUDE = set(TEMPLATES)
    to_archive = []
    for folder in ['New', 'Applied', 'Skipped']:
        fp = os.path.join(BASE, folder)
        if not os.path.isdir(fp): continue
        for company in os.listdir(fp):
            cp = os.path.join(fp, company)
            if not os.path.isdir(cp): continue
            for fname in os.listdir(cp):
                if fname in EXCLUDE or fname.startswith('~$') or fname.startswith('PREVIEW'): continue
                if not (fname.endswith('.pdf') or fname.endswith('.docx')): continue
                fpath = os.path.join(cp, fname); s = stem(fname)
                if s in already or s in bookmarked: continue
                fd = job_details.get(s, {}).get('foundDate', '')
                try: age = datetime.strptime(fd, '%Y-%m-%d') if fd else datetime.fromtimestamp(os.path.getmtime(fpath))
                except Exception: age = datetime.fromtimestamp(os.path.getmtime(fpath))
                if age < cutoff: to_archive.append((fpath, folder, company, fname, s))
    new_entries = []
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for t in ['job_tracker.html', 'job_tracker.json']:
            tp = os.path.join(BASE, t)
            if os.path.exists(tp): zf.write(tp, t)
        for (fpath, folder, company, fname, s) in to_archive:
            zi = f'{folder}/{company}/{fname}'
            try:
                zf.write(fpath, zi); parts = s.split('_', 1); d = job_details.get(s, {})
                new_entries.append({'id': s, 'companyDisplay': camel_to_words(parts[0] if parts else s),
                                    'role': camel_to_words(parts[1] if len(parts) > 1 else ''),
                                    'originalFolder': folder, 'archivedDate': today_str,
                                    'foundDate': d.get('foundDate', ''), 'applyUrl': d.get('applyUrl', ''),
                                    'salary': d.get('salary', ''), 'location': d.get('location', ''),
                                    'source': d.get('source', ''), 'flaggedSkills': d.get('flaggedSkills', []),
                                    'zipFile': zip_rel, 'zipPath': zi})
            except Exception as e:
                print(f'Warning: could not archive {fpath}: {e}')
    for (fpath, folder, company, fname, s) in to_archive:
        try: os.remove(fpath)
        except Exception: pass
    if new_entries:
        ded = {}
        for e in new_entries:
            prev = ded.get(e['id'])
            if prev is None or (e['zipPath'].endswith('.pdf') and not prev['zipPath'].endswith('.pdf')):
                ded[e['id']] = e
        new_entries = list(ded.values())
        tracker['archived'] = archived + new_entries; write_tracker(tracker)
    n_arc = write_archive_index(tracker)
    print(f"Archived {len(new_entries)} resumes -> {zip_rel} (bookmarked protected: {len(bookmarked)})")
    print(f"archive_index.csv: {n_arc} archived resumes indexed")

# ---------- Simplify Applied matcher ----------
ROLE_ALIASES = {'sre': ['site', 'reliability', 'engineer'], 'sr': ['senior'], 'srs': ['senior'],
                'devops': ['devops'], 'swe': ['software', 'engineer'], 'qa': ['quality', 'assurance'],
                'ba': ['business', 'analyst'], 'da': ['data', 'analyst'], 'infra': ['infrastructure'],
                'eng': ['engineer'], 'engr': ['engineer'], 'mgr': ['manager'], 'ops': ['operations'],
                'admin': ['administrator'], 'sys': ['systems'], 'tech': ['technical']}
_ROLE_STOP = {'the', 'a', 'an', 'of', 'and', 'or', 'i', 'ii', 'iii', 'iv', 'v', 'remote', 'hybrid'}

def _role_tokens(s):
    toks = []
    for w in re.findall(r"[A-Za-z0-9']+", (s or '').lower()):
        toks.extend(ROLE_ALIASES.get(w, [w]))
    return {t for t in toks if t not in _ROLE_STOP}

def _known_jobs(tracker):
    """{id: (normalized company, role token set)} for every active + archived job."""
    out = {}
    ids = ({j['id'] for j in scan_active_jobs(tracker)} | {x.get('id', '') for x in tracker.get('archived', [])}
           | set(tracker.get('job_details', {}).keys()) | set(tracker.get('applied', {}).keys()))
    for jid in ids:
        if not jid: continue
        parts = jid.split('_', 1)
        out[jid] = (norm(camel_to_words(parts[0] if parts else '')),
                    _role_tokens(camel_to_words(parts[1] if len(parts) > 1 else '')))
    return out

def cmd_simplify(a):
    """Deterministically fold Simplify's Applied column into tracker['applied'].
    --raw raw.json: list of {"company","role"} (or "Company - Role" strings) scraped from the page.
    --mark id[=date],id2: force-mark ids applied (for entries the matcher couldn't resolve).
    Preserves existing applied entries; refreshes html + tracking.csv + archive_index.csv."""
    tracker = read_tracker(); seed_applied(tracker)
    applied = tracker['applied']
    today = datetime.now().strftime('%Y-%m-%d')
    matched, unmatched, already = [], [], []
    if a.mark:
        for pair in a.mark.split(','):
            jid, _, dt = pair.partition('=')
            jid = jid.strip()
            if not jid: continue
            if jid in applied: already.append(jid)
            else: applied[jid] = dt.strip() or today; matched.append(jid)
    if a.raw:
        known = _known_jobs(tracker)
        for entry in (load_json(data_path(a.raw), []) or []):
            if isinstance(entry, str):
                m = re.split(r'\s*[|–—-]\s*', entry, maxsplit=1)
                comp_raw, role_raw = (m[0], m[1]) if len(m) == 2 else (entry, '')
            else:
                comp_raw = entry.get('company', ''); role_raw = entry.get('role', '') or entry.get('title', '')
            nc, rt = norm(comp_raw), _role_tokens(role_raw)
            best, best_score = None, 0.0
            for jid, (kc, kr) in known.items():
                if not nc or not kc: continue
                if nc != kc and nc not in kc and kc not in nc: continue
                if rt and kr: score = len(rt & kr) / min(len(rt), len(kr))
                else: score = 0.5  # company matched, role info missing on one side
                if score > best_score: best, best_score = jid, score
            if best and best_score >= 0.5:
                if best in applied: already.append(best)
                else: applied[best] = today; matched.append(best)
            else:
                unmatched.append({'company': comp_raw, 'role': role_raw})
    added_track_only = []
    if getattr(a, 'add_unmatched', False) and unmatched:
        jd = tracker.setdefault('job_details', {})
        still = []
        for u in unmatched:
            tid = tracker_id(u.get('company', ''), u.get('role', ''))
            if not tid or tid == '_':
                still.append(u); continue
            if tid not in applied: applied[tid] = today
            if tid not in jd:
                jd[tid] = {'foundDate': today, 'applyUrl': '', 'salary': '', 'location': '',
                           'source': 'simplify_manual', 'flaggedSkills': [], 'skillMatch': None,
                           'questionnaire': [], 'trackedOnly': True}
            added_track_only.append(tid)
        unmatched = still
    tracker['applied'] = applied; write_tracker(tracker)
    refresh_html_status(tracker); write_tracking_csv(tracker); write_archive_index(tracker)
    print(json.dumps({'matched': sorted(matched), 'alreadyApplied': sorted(set(already)),
                      'addedTrackOnly': sorted(added_track_only),
                      'unmatched': unmatched, 'appliedTotal': len(applied)}, indent=2))
