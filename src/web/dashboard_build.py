#!/usr/bin/env python3
"""dashboard_build.py — tracker update + dashboard (job_tracker.html) rebuild.

Split out of jobpipe.py: cmd_update (fold details/processed/run into
job_tracker.json, with a backup.py snapshot first) and cmd_rebuild (reconcile
New/Applied/Skipped folders, regenerate the JOBS/ARCHIVED arrays in the html,
persist skip/bookmark state, re-emit CSVs). Imported by jobpipe.py; external
callers keep shelling out to `python src/pipeline/jobpipe.py <cmd>`.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import sys, os, re, json, shutil
from datetime import datetime

from pipelib import BASE, TEMPLATES, load_json, data_path, stem, camel_to_words, is_resume_file
from tracker import (read_tracker, write_tracker, read_html, write_html,
                     simplify_applied_set, seed_applied, _prefer_pdf,
                     refresh_html_status, write_tracking_csv, write_archive_index)

# ---------- update (Step 8) ----------
def snapshot_backup():
    """Run backup.py once (5-snapshot rotation of code + tracking data)."""
    try:
        import subprocess
        bp = _paths.script('backup.py')
        if os.path.exists(bp): subprocess.run([sys.executable, bp], check=False)
    except Exception as e:
        print(f"(backup snapshot skipped: {e})")

def cmd_update(a):
    snapshot_backup()  # one durable snapshot per pipeline run, before mutating state
    tracker = read_tracker()
    jd = tracker.setdefault('job_details', {})
    for k, v in (load_json(data_path(a.details), {}) or {}).items():
        if k not in jd: jd[k] = v          # never overwrite prior entries
        else: jd[k].update({kk: vv for kk, vv in v.items() if kk not in jd[k]})
    pj = tracker.setdefault('processed_jobs', [])
    for pid in (load_json(data_path(a.processed), []) or []):
        if pid not in pj: pj.append(pid)
    if a.run:
        run = load_json(data_path(a.run), None)
        if run is not None: tracker.setdefault('runs', []).append(run)
    tracker['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    write_tracker(tracker)
    print(f"job_details: {len(jd)} | processed_jobs: {len(pj)} | runs: {len(tracker.get('runs', []))}")

# ---------- rebuild (Step 9 B/C/D) ----------
def move_preserve_ts(src, dst):
    st = os.stat(src); shutil.copy2(src, dst); os.utime(dst, (st.st_atime, st.st_mtime)); os.remove(src)

def cmd_rebuild(a):
    manual = (load_json(data_path(a.manual), {}) if a.manual else {}) or {}
    m_applied = set(manual.get('manualApplied', []))
    m_skipped = set(manual.get('manualSkipped', []))
    m_unskip = set(manual.get('manualUnskip', []))
    m_book = set(manual.get('manualBookmarked', []))
    m_unbook = set(manual.get('manualUnbookmark', []))
    simplify = simplify_applied_set()
    EXCLUDE = set(TEMPLATES)
    def _id_in_dir(sid, dpath, ext):
        return os.path.isdir(dpath) and any(
            is_resume_file(f) and stem(f) == sid and f.endswith(ext) for f in os.listdir(dpath))
    newdir = os.path.join(BASE, 'New')
    if os.path.isdir(newdir):
        for company in os.listdir(newdir):
            cp = os.path.join(newdir, company)
            if not os.path.isdir(cp): continue
            for fname in list(os.listdir(cp)):
                if fname in EXCLUDE or fname.startswith('~$') or fname.startswith('PREVIEW'): continue
                if not (fname.endswith('.pdf') or fname.endswith('.docx')): continue
                s = stem(fname); src = os.path.join(cp, fname)
                dest = 'Skipped' if s in m_skipped else ('Applied' if (s in m_applied or (s in simplify and s not in m_skipped)) else None)
                if not dest: continue
                dd = os.path.join(BASE, dest, company)  # reuse existing [Company] folder for this employer
                if _id_in_dir(s, dd, os.path.splitext(fname)[1]):                    # already have this job's resume there -> New copy is redundant
                    try: os.remove(src)
                    except Exception: pass
                    continue
                os.makedirs(dd, exist_ok=True)
                try: move_preserve_ts(src, os.path.join(dd, fname))
                except Exception: pass
    # prune_empty_dirs() below removes any New/[Company] folders emptied by the moves above
    tracker = read_tracker()
    job_details = tracker.get('job_details', {})
    archived_list = tracker.get('archived', [])
    archived_ids = {x['id'] for x in archived_list}
    jobs = []
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
                parts = s.split('_', 1); d = job_details.get(s, {})
                jobs.append({'id': s, 'companyDisplay': camel_to_words(parts[0] if parts else s),
                             'role': camel_to_words(parts[1] if len(parts) > 1 else ''),
                             'resume': f'{folder}/{company}/{fname}',
                             'foundDate': d.get('foundDate', ''), 'applyUrl': d.get('applyUrl', ''),
                             'salary': d.get('salary', ''), 'location': d.get('location', ''),
                             'source': d.get('source', ''), 'flaggedSkills': d.get('flaggedSkills', []),
                             'skillMatch': d.get('skillMatch', None), 'questionnaire': d.get('questionnaire', [])})
    jobs = _prefer_pdf(jobs)
    h = read_html()
    # Replacements are passed as literal functions: json.dumps emits \uXXXX for non-ASCII
    # job_details, and re.sub would otherwise parse those (and any trailing backslash) as
    # replacement escapes and crash ("bad escape \u"). A lambda returns the string verbatim.
    h = re.sub(r'const JOBS = \[.*?\];', lambda _m, _r='const JOBS = ' + json.dumps(jobs, indent=2) + ';': _r, h, flags=re.S)
    h = re.sub(r'const ARCHIVED_JOBS = \[.*?\];', lambda _m, _r='const ARCHIVED_JOBS = ' + json.dumps(archived_list, indent=2) + ';': _r, h, flags=re.S)
    # SAFE: single-line, brace-bounded, NO re.S. The old pattern used re.S with a greedy
    # `(// .*atime.*\n)?` prefix, so `.` matched newlines and a match starting at any earlier
    # `//` (even `https://` inside a JOBS URL) would swallow every line down to this const —
    # which silently deleted the UI-state declarations and blanked the dashboard. Keep it local.
    h = re.sub(r'const FILE_ACCESSED_APPLIED = \{[^{}]*\};',
               'const FILE_ACCESSED_APPLIED = {};', h)
    write_html(h)
    skip_all = (set(tracker.get('skipped_jobs', [])) | m_skipped) - m_unskip
    book_all = (set(tracker.get('bookmarked', [])) | m_book) - m_unbook
    tracker['skipped_jobs'] = sorted(skip_all); tracker['bookmarked'] = sorted(book_all)
    write_tracker(tracker)
    h = read_html()
    h = re.sub(r'const PIPELINE_SKIPPED = \[.*?\];', lambda _m, _r='const PIPELINE_SKIPPED = ' + json.dumps(sorted(skip_all)) + ';': _r, h, flags=re.S)
    h = re.sub(r'const BOOKMARKED = \[.*?\];', lambda _m, _r='const BOOKMARKED = ' + json.dumps(sorted(book_all)) + ';': _r, h, flags=re.S)
    # Guard: job_tracker.html references SIMPLIFY_APPLIED but rebuild never emits it.
    # If the definition is missing, inject an empty one right after BOOKMARKED so the
    # dashboard never throws a ReferenceError. Existing entries are preserved (only
    # injected when absent); Step 7 fills it from Simplify.
    if 'const SIMPLIFY_APPLIED' not in h:
        h = re.sub(r'(const BOOKMARKED = \[.*?\];)',
                   r'\1\nconst SIMPLIFY_APPLIED = {};', h, count=1, flags=re.S)
    write_html(h)
    pruned = prune_empty_dirs()
    # Centralize applied state in job_tracker.json['applied'] and re-emit SIMPLIFY_APPLIED from it,
    # then regenerate the durable CSVs (tracking.csv = source of truth; archive_index.csv).
    tracker = read_tracker(); seed_applied(tracker); write_tracker(tracker)
    refresh_html_status(tracker)
    n_csv = write_tracking_csv(tracker); n_arc = write_archive_index(tracker)
    print(f"rebuilt JOBS: {len(jobs)} | skipped: {len(skip_all)} | bookmarked: {len(book_all)} | pruned empty dirs: {pruned}")
    print(f"tracking.csv: {n_csv} jobs | archive_index.csv: {n_arc} archived")

def prune_empty_dirs():
    """Remove empty [Company] subfolders under New/Applied/Skipped."""
    n = 0
    for folder in ['New', 'Applied', 'Skipped']:
        fp = os.path.join(BASE, folder)
        if not os.path.isdir(fp): continue
        for company in os.listdir(fp):
            cp = os.path.join(fp, company)
            if os.path.isdir(cp) and not os.listdir(cp):
                try: os.rmdir(cp); n += 1
                except Exception: pass
    return n
