#!/usr/bin/env python3
"""archive.py — monthly archiver for the Top Rat.

Moves APPLIED job entries whose foundDate is older than one month out of the active
tracker (tracking.csv / job_tracker.json) into the Archived page's store (archived.csv),
zips each entry's resume .pdf + .docx into Backups/archived_resumes_<YYYY-MM>.zip, and
records where each resume went in archive_index.csv (resumeFile -> zipFile / pathInZip)
so the dashboard can extract-and-view it on click.

Rules:
  * Only status == 'applied' entries are archived.
  * Cutoff is by the LATER of foundDate / statusDate, against a configurable age.
  * BOOKMARKED entries are NEVER archived until the bookmark is removed.
  * Originals are MOVED (deleted after zipping).
Safe by construction: snapshots first (backup.py), writes every file atomically with verify.

Age (how old an applied job must be before it is archived) is USER-SETTABLE:
  * Schedule page -> the archive job card -> "Archive applied jobs older than [N] [unit]".
    Stored as archive_age / archive_age_unit on the monthly_archive job in schedule.json.
  * CLI override, for a one-off run:  --older-than N  --unit days|weeks|months
  * Falls back to 1 month when neither is set, so the script path never needs config.

Run:  python src/ops/archive.py                       (dry run: prints what WOULD be archived)
      python src/ops/archive.py --apply               (perform the archive, config age)
      python src/ops/archive.py --older-than 2 --unit weeks --apply
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, csv, io, json, time, zipfile, calendar, subprocess
from datetime import date, datetime, timedelta

HERE = _paths.ROOT
def P(*p): return os.path.join(HERE, *p)

from pipelib import (JSON_PATH, TRACKING_CSV, ARCHIVE_CSV, DATA as _DATA, SPLIT as _SPLIT)
def _d_archived():
    return os.path.join(_DATA if _SPLIT else HERE, 'archived.csv')
# Personal state resolves off pipelib's DATA root (falls back to HERE when the data
# repo isn't split out). Backups/ deliberately stays beside the CODE.
TRACKING = TRACKING_CSV
ARCHIVED = _d_archived()
INDEX = ARCHIVE_CSV
TRACKER = JSON_PATH
ZIPDIR = P('Backups')
INDEX_COLS = ['company', 'role', 'status', 'archivedDate', 'foundDate', 'applyUrl',
              'resumeFile', 'zipFile', 'pathInZip']

def load_csv(fp):
    try:
        with open(fp, newline='', encoding='utf-8') as f:
            r = csv.DictReader(f); return list(r), r.fieldnames or []
    except Exception:
        return [], []

def atomic_write(path, text):
    for _ in range(5):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        os.replace(tmp, path)
        try:
            # newline='' so CSV's \r\n survives read-back (universal-newline mode would
            # translate it to \n and the verify would never match).
            with open(path, encoding='utf-8', newline='') as f:
                if f.read() == text: return
        except Exception: pass
        time.sleep(0.3)
    raise IOError('atomic_write could not verify ' + os.path.basename(path))

def write_csv(path, rows, cols):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore')
    w.writeheader()
    for r in rows: w.writerow(r)
    atomic_write(path, buf.getvalue())

def month_cutoff(today, months=1):
    y, m = today.year, today.month - months
    while m <= 0: m += 12; y -= 1
    d = min(today.day, calendar.monthrange(y, m)[1])
    return date(y, m, d)

# ---------------- configurable age ----------------
UNITS = ('days', 'weeks', 'months')
DEFAULT_AGE, DEFAULT_UNIT = 1, 'months'
SCHED_JOB = 'monthly_archive'    # the job in schedule.json that carries the setting

def _sched_age():
    """(value, unit) from schedule.json — user copy shadows the shipped default.

    Read directly rather than through scheduler.load_schedule(): archive.py runs from cron,
    from the scheduler, and by hand, and must not pull in the scheduler's threads to learn
    one number. Same overlay order the scheduler uses: shipped default first, user copy wins.
    """
    val, unit = DEFAULT_AGE, DEFAULT_UNIT
    paths = [P('config', 'schedule.default.json')]
    try:
        from pipelib import cfg as _cfg
        paths.append(_cfg('schedule.json'))
    except Exception:
        paths.append(P('config', 'schedule.json'))
    for p in paths:                       # later file wins
        try:
            with open(p, encoding='utf-8') as f:
                job = ((json.load(f).get('jobs') or {}).get(SCHED_JOB) or {})
        except Exception:
            continue
        if 'archive_age' in job: val = job['archive_age']
        if 'archive_age_unit' in job: unit = job['archive_age_unit']
    return val, unit

def resolve_age(argv):
    """CLI flags override the configured age. Returns a sane (value, unit)."""
    val, unit = _sched_age()
    if '--older-than' in argv:
        try: val = argv[argv.index('--older-than') + 1]
        except IndexError: pass
    if '--unit' in argv:
        try: unit = argv[argv.index('--unit') + 1]
        except IndexError: pass
    try: val = int(val)
    except (TypeError, ValueError): val = DEFAULT_AGE
    unit = str(unit or '').strip().lower()
    if not unit.endswith('s'): unit += 's'          # "month" -> "months"
    if unit not in UNITS: unit = DEFAULT_UNIT
    if val < 0: val = DEFAULT_AGE                   # 0 is legal: archive everything applied
    return val, unit

def age_cutoff(today, value, unit):
    if unit == 'days':  return today - timedelta(days=value)
    if unit == 'weeks': return today - timedelta(weeks=value)
    return month_cutoff(today, value)

def parse_date(s):
    try: return datetime.strptime((s or '')[:10], '%Y-%m-%d').date()
    except Exception: return None

def load_tracker():
    for _ in range(4):
        try:
            with open(TRACKER, encoding='utf-8') as f: return json.load(f), True
        except FileNotFoundError:
            return {}, True
        except Exception:
            time.sleep(0.4)
    return None, False

def main(apply=False, age=None, unit=None):
    today = date.today()
    if age is None or unit is None:
        age, unit = resolve_age(sys.argv[1:])
    cutoff = age_cutoff(today, age, unit)
    label = '%d %s' % (age, unit if age != 1 else unit[:-1])
    rows, cols = load_csv(TRACKING)
    if not cols:
        print('No tracking.csv / no columns — nothing to do.'); return
    tracker, ok = load_tracker()
    if not ok:
        sys.exit('job_tracker.json unreadable — aborting so applied/bookmark data is not lost. '
                 'Restore from git or Backups first.')
    booked = set(tracker.get('bookmarked', [])) if isinstance(tracker, dict) else set()

    to_archive = []
    for r in rows:
        if (r.get('status') or '').strip().lower() != 'applied':
            continue
        # Cutoff uses the LATER of foundDate and appliedDate (statusDate): a job applied to
        # recently is NOT archived even if it was found long ago, and vice-versa.
        dates = [d for d in (parse_date(r.get('foundDate')), parse_date(r.get('statusDate'))) if d]
        latest = max(dates) if dates else None
        if not latest or latest > cutoff:
            continue
        if (r.get('bookmarked') or '').strip().lower() in ('yes', 'true', '1') or r.get('id') in booked:
            continue   # bookmarked -> never archive until unbookmarked
        to_archive.append(r)

    if not to_archive:
        print(f'Nothing to archive: no applied entry is older than {label} '
              f'(newer of found/applied would have to be on/before {cutoff}).')
        return
    print(f'{"WOULD archive" if not apply else "Archiving"} {len(to_archive)} entr'
          f'{"y" if len(to_archive)==1 else "ies"} older than {label} '
          f'(newer of found/applied <= {cutoff}):')
    for r in to_archive:
        print(f'  - {r.get("company","?")} / {r.get("role","?")}  (found {r.get("foundDate","?")}, '
              f'applied {r.get("statusDate","?")})  {os.path.basename(r.get("resumePath",""))}')
    if not apply:
        print('\nDry run — pass --apply to perform the archive.'); return

    # snapshot before mutating the durable stores
    try:
        subprocess.run([sys.executable, _paths.script('backup.py')], cwd=HERE, timeout=120)
    except Exception as e:
        print('(backup snapshot failed, continuing:', e, ')')

    os.makedirs(ZIPDIR, exist_ok=True)
    zip_path = P('Backups', 'archived_resumes_%s.zip' % today.strftime('%Y-%m'))
    zip_rel = os.path.relpath(zip_path, HERE).replace('\\', '/')
    index_rows, _ = load_csv(INDEX)
    arch_rows, arch_cols = load_csv(ARCHIVED)
    if not arch_cols:
        arch_cols = list(cols) + (['archivedDate'] if 'archivedDate' not in cols else [])

    archived_ids, moved_files = set(), 0
    with zipfile.ZipFile(zip_path, 'a', zipfile.ZIP_DEFLATED) as z:
        existing = set(z.namelist())
        for r in to_archive:
            jid = (r.get('id') or '').strip()
            rp = (r.get('resumePath') or '').strip()
            resume_file = os.path.basename(rp) if rp else ''
            stem = os.path.splitext(rp)[0] if rp else ''
            pdf_arc = ''
            for rel in ([rp, stem + '.docx'] if rp else []):
                ap = P(*rel.split('/'))
                if os.path.isfile(ap):
                    arc = os.path.basename(ap)
                    if arc not in existing:
                        z.write(ap, arc); existing.add(arc)
                    if arc.lower().endswith('.pdf'): pdf_arc = arc
                    try: os.remove(ap)
                    except OSError: pass
                    moved_files += 1
            index_rows.append({'company': r.get('company', ''), 'role': r.get('role', ''),
                'status': 'archived', 'archivedDate': today.isoformat(),
                'foundDate': r.get('foundDate', ''), 'applyUrl': r.get('applyUrl', ''),
                'resumeFile': resume_file, 'zipFile': zip_rel,
                'pathInZip': pdf_arc or resume_file})
            ar = dict(r); ar['status'] = 'archived'; ar['archivedDate'] = today.isoformat()
            arch_rows.append(ar)
            archived_ids.add(jid)

    # remove archived rows from the active tracker
    remaining = [r for r in rows if (r.get('id') or '').strip() not in archived_ids]
    write_csv(TRACKING, remaining, cols)
    write_csv(ARCHIVED, arch_rows, arch_cols)
    write_csv(INDEX, index_rows, INDEX_COLS)
    # drop archived ids from job_tracker.json applied (so a dashboard save won't resurrect them)
    if isinstance(tracker, dict):
        applied = tracker.get('applied', {})
        for jid in archived_ids: applied.pop(jid, None)
        tracker['applied'] = applied
        atomic_write(TRACKER, json.dumps(tracker, indent=2))
    print(f'Archived {len(archived_ids)} entries, moved {moved_files} resume files into {zip_rel}.')

if __name__ == '__main__':
    import runlog; runlog.run('archive.py', lambda: main(apply=('--apply' in sys.argv)))
