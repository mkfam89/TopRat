#!/usr/bin/env python3
"""
jobpipe.py — deterministic engine for the job-alert pipeline.

The LLM drives the browser scrape and writes the tailored resume prose (and the
per-job questionnaire); everything below is pure logic run as a script.

Subcommands (run from the project folder):
  jobpipe.py urls                          # print scrape URLs (config/search_urls.json)
  jobpipe.py classify --title "..."        # CORE / TECH_SUPPORT / ANALYST + base resume
  jobpipe.py flag-skills --required a,b,c   # required skills NOT in config/skills.json
  jobpipe.py match --required a,b,c         # skill-match fraction + flagged list
  jobpipe.py filter --listings f.json [--cap 20] [--min-match 0.70]
        # title-exclude + dedup + skip-list + QUALITY skill-match filter + classify
  jobpipe.py update --details d.json [--processed p.json] [--run r.json]
  jobpipe.py rebuild [--manual m.json]     # reconcile folders + rebuild JOBS/ARCHIVED + persist skip/bookmark
  jobpipe.py archive [--days 7]            # zip+remove stale resumes (bookmarked protected)
  jobpipe.py parse-cards --raw cards.json [--out listings.json]
        # deterministic hiringcafe.com card-text parser -> listings.json + "review" list of ambiguous cards
  jobpipe.py salary --title "..."          # salary band lookup (config/salary_bands.json)
  jobpipe.py salary-set --family X [--range "$A-$B (est.)"] [--senior-range "..."] [--match kw1,kw2]
  jobpipe.py simplify [--raw raw.json] [--mark id[=date],id2]
        # deterministic Simplify Applied matcher -> updates tracker['applied'] + html + CSVs;
        # prints unmatched entries for the LLM to resolve by hand (via --mark)

Listings JSON (from the scrape) is a list of:
  {"source":"hiring_cafe|linkedin_search|linkedin_top_applicant",
   "id":"<hc_ or numeric scrape id>","title":"...","company":"...",
   "location":"...","salary":"...","applyUrl":"...","requiredSkills":[...]}

Quality strategy: `filter` drops any posting whose skill match < min_match
(config/strategy.json, default 0.70). Each surfaced job carries "skillMatch" (0-1).
job_details may also hold "questionnaire" (list of questions the LLM wrote for that
job); rebuild carries skillMatch + questionnaire into the dashboard JOBS array.

Module layout (split 2026-07-21; jobpipe.py is the single CLI entrypoint and the
ONLY supported import surface — external callers shell out to its subcommands):
  pipelib.py         shared path constants + io/text helpers
  scoring.py         classify / skill match / strategy + card parser
  tracker.py         tracker/html/CSV state, applied reconcile, candidates/simplify/archive
  salary.py          salary bands, probe, resolution chain
  dashboard_build.py update + rebuild (dashboard HTML generation)
jobpipe.py re-exports their public + underscore names so tests and monkeypatches
keep resolving jobpipe.<name>.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import sys, os, re, json, argparse

# Shared path constants + io/text helpers live in pipelib.py; imported here so
# tests (and monkeypatching) keep resolving them as jobpipe.<name>.
from pipelib import (HERE, BASE, DATA, SPLIT, JSON_PATH, HTML_PATH, CONFIG, cfg, cfg_write,
                     SALARY_QUOTA, TEMPLATE_DIR,
                     TRACKING_CSV, ARCHIVE_CSV, CANDIDATES_CSV, TO_PROCESS,
                     SALARY_BANDS, SALARY_CACHE, template_files, TEMPLATES,
                     load_json, data_path, load_listings,
                     _safe_write_text, stem, camel_to_words, slug,
                     tracker_id, clean_company, clean_role,
                     resume_prefix, reset_resume_prefix, is_resume_file)
from scoring import (norm, known_norms, _matchable, _skill_hit, flag_skills,
                     skill_match, _shrinkage, _SHRINK_K, _SHRINK_PRIOR,
                     title_fit, score_job, _title_norm, _title_terms,
                     _TITLE_OFFLANE, _OFFLANE_FACTOR,
                     strategy, INFRA_SIGNAL, INFRA_MIN, _pick_base,
                     classify, _ROLE_WORDS, _LOC_RE, _SAL_RE, _parse_card,
                     cmd_parse_cards)
from tracker import (read_tracker, write_tracker, read_html, write_html,
                     _CANON_PHRASES, _CANON_WORDS, _CANON_DROP, _canon,
                     _canon_tokens, applied_norm_index, applied_match,
                     simplify_applied_set, TRACKING_COLS, ARCHIVE_COLS,
                     html_simplify_map, seed_applied, _prefer_pdf,
                     scan_active_jobs, resolve_status, write_tracking_csv,
                     write_archive_index, refresh_html_status, cmd_export_csv,
                     cmd_import_csv, CANDIDATE_COLS, _load_csv, cmd_candidates,
                     cmd_archive, ROLE_ALIASES, _ROLE_STOP, _role_tokens,
                     _known_jobs, cmd_simplify)
from salary import (_probe_salaries, _SALARY_TITLE_FOLD, _norm_salary_title,
                    salary_lookup, cmd_salary, cmd_salary_apply, cmd_salary_set, cmd_salaries)
from salary import _resolve_salary as _salary_resolve
from dashboard_build import (snapshot_backup, cmd_update, move_preserve_ts,
                             cmd_rebuild, prune_empty_dirs)
from blocklist import (is_blocked, block_employer, unblock_employer,
                       blocked_employers, TACTICS)

TITLE_EXCLUDE = re.compile(r'\b(principal|staff|manager|director)\b', re.I)

# ---------- filter ----------
def cmd_filter(a):
    # Same data-root resolution as cmd_candidates — a bare "--listings listings.json"
    # from a saved schedule step must not resolve off the cwd (see pipelib.data_path).
    listings = load_listings(a.listings, 'filter')
    tracker = read_tracker()
    processed = set(tracker.get('processed_jobs', []))
    skip_set = set(tracker.get('skipped_jobs', []))
    applied = simplify_applied_set()
    min_match = a.min_match if a.min_match is not None else strategy().get('min_match', 0.70)
    order = {'hiring_cafe': 0, 'linkedin_top_applicant': 1, 'linkedin_search': 2}
    listings = sorted(listings, key=lambda j: order.get(j.get('source'), 9))
    process, skipped_titles, skipped_manual, skipped_low_match, seen = [], [], [], [], set()
    for j in listings:
        title, company = j.get('title', ''), j.get('company', '')
        scrapeId = j.get('id') or j.get('hcId') or j.get('numericId') or ''
        tid = j.get('trackerId') or tracker_id(company, title)
        if TITLE_EXCLUDE.search(title):
            skipped_titles.append(f"{title} @ {company}"); continue
        if tid in skip_set:
            skipped_manual.append(tid); continue
        if scrapeId and scrapeId in processed and tid in applied:
            continue  # already processed AND applied
        pct, flagged = skill_match(j.get('requiredSkills', []) or [])
        if pct is not None and pct < min_match:
            skipped_low_match.append({'trackerId': tid, 'title': title, 'company': company,
                                      'matchPct': pct, 'flaggedSkills': flagged}); continue
        if tid in seen: continue
        seen.add(tid)
        cls = classify(title, j.get('requiredSkills', []) or [])
        process.append({**j, 'scrapeId': scrapeId, 'trackerId': tid,
                        'category': cls['category'], 'baseResume': cls['baseResume'],
                        'baseType': cls['baseType'], 'skillMatch': pct, 'flaggedSkills': flagged})
        if len(process) >= a.cap: break
    print(json.dumps({
        'minMatch': min_match,
        'process': process,
        'skipped_titles': skipped_titles,
        'skipped_manual': skipped_manual,
        'skipped_low_match': skipped_low_match,
        'counts': {'scanned': len(listings), 'process': len(process),
                   'skipped_titles': len(skipped_titles), 'skipped_manual': len(skipped_manual),
                   'skipped_low_match': len(skipped_low_match)}
    }, indent=2))

def _is_local(location):
    # keyword pass + cached-geo pass (locality.py): a job inside the profile's
    # search radius counts as local even if its city isn't in local_keywords
    # (e.g. "Spring, TX" for a Houston profile). Offline-safe — no network here.
    import locality
    return locality.is_local(location, strategy())

def cmd_process_queue(a):
    """Emit the jobs the user queued from the GUI (to_process.json) that still need a resume,
    as a 'process' list for the LLM run to tailor. Also merges in anything >= threshold if --auto."""
    cands = {c['id']: c for c in _load_csv(CANDIDATES_CSV)}
    tracker = read_tracker()
    done = set((tracker.get('applied') or {}).keys()) | set(tracker.get('skipped_jobs') or [])
    q = load_json(TO_PROCESS, {}) or {}
    ids = [i for i in q.get('ids', []) if i not in done]
    # Optional per-id tailoring notes the user typed in the dashboard's Tailor dialog
    # ({id: text}, written by /api/queue). Passed through to the emitted item as
    # `userNotes` for the LLM pass; absent/empty on the deterministic template path.
    qnotes = q.get('notes', {})
    if not isinstance(qnotes, dict):
        qnotes = {}
    _queued = set(ids)   # explicitly hand-queued ids — never subject to the auto-tailor cap
    st = strategy()
    thr = a.threshold if a.threshold is not None else st.get('min_match', 0.6)
    local_thr = st.get('local_min_match', thr)
    if a.auto or getattr(a, 'hourly', False):
        local_only = getattr(a, 'local_only', False)
        for cid, c in cands.items():
            try: sm = float(c.get('skillMatch') or 0)
            except Exception: sm = 0
            loc = _is_local(c.get('location'))
            if local_only and not loc:
                continue  # auto-tailor ONLY Houston-metro jobs; remote jobs wait for the GUI queue
            eff = local_thr if loc else thr
            if sm >= eff and c.get('hasResume') != 'yes' and cid not in ids and cid not in done: ids.append(cid)
    cust_thr = st.get('resume_customize_threshold', 0.8)
    # AUTO-PROBE: before emitting, chase a real salary for any queued job that doesn't list one
    # and hasn't been probed yet. On by default so the tailoring run gets it with no extra step;
    # --no-probe for callers that must not block on network (e.g. a UI-triggered process-queue).
    if not getattr(a, 'no_probe', False):
        _cache = load_json(SALARY_CACHE, {}) or {}
        # A cached stage-2-pending miss (probed stage-1-only by the hourly radar, or before
        # creds existed) counts as un-probed once Adzuna creds exist, mirroring
        # salary_probe.retryable_miss, so those jobs aren't stuck at 'none' forever.
        _adz = bool((load_json(cfg('adzuna.json'), {}) or {}).get('app_id'))
        def _probed(cid):
            e = _cache.get(cid)
            if e is None:
                return False
            if _adz and not e.get('salary'):
                js = e.get('jobsworthStatus') or ''
                if js in ('', 'no-adzuna-key') or js.startswith('quota-exhausted'):
                    return False
            return True
        _need = [cid for cid in ids
                 if (cands.get(cid) or {}).get('hasResume') != 'yes'
                 and not ((cands.get(cid, {}).get('salary') or '').strip()
                          and (cands.get(cid, {}).get('salary') or '').strip().lower()
                          not in ('undisclosed', 'n/a', 'none', '-'))
                 and not _probed(cid)]
        if _need:
            _probe_salaries(_need)
    process = []
    _blocked_skipped = 0
    for cid in ids:
        c = cands.get(cid)
        # Defense in depth: a job queued before its employer was banned must still
        # not get tailored. cmd_candidates already excludes blocked employers, but
        # to_process.json can hold ids from before the ban.
        if c and is_blocked(c.get('company', '')):
            _blocked_skipped += 1
            continue
        if c and c.get('hasResume') != 'yes':
            cls = classify(c.get('role', ''), c.get('requiredSkills'))
            sal = (c.get('salary') or '').strip()
            est = salary_lookup(c.get('role', '')) if (not sal or sal.lower() in ('undisclosed', 'n/a')) else {}
            try: smv = float(c.get('skillMatch') or 0)
            except Exception: smv = 0.0
            # customize (LLM-tailor) only when skills DON'T already cover the job well;
            # otherwise the run should copy the classified template as-is (token saver).
            customize = smv < cust_thr
            fin, fin_src = _resolve_salary(cid, sal, est)
            process.append({**c, 'trackerId': cid, 'category': cls['category'],
                            'baseResume': cls['baseResume'], 'baseType': cls['baseType'],
                            'isLocal': _is_local(c.get('location')),
                            'userNotes': str(qnotes.get(cid, '') or ''),
                            'customize': customize, 'action': 'customize' if customize else 'template',
                            'salaryEst': est.get('range', ''), 'salaryFamily': est.get('family', ''),
                            'salaryFinal': fin, 'salarySource': fin_src,
                            'requiredSkills': [s.strip() for s in (c.get('requiredSkills') or '').split(';') if s.strip()]})
    def _sm(p):
        try: return float(p.get('skillMatch') or 0)
        except Exception: return 0.0
    # Always prioritize: LOCAL first, then best skill match. Then AUTO-TAILOR AT MOST N — the
    # configurable cap in config/gui_settings.json: dailyAutoTailorCap (--auto daily run, default 20)
    # or hourlyAutoTailorCap (--hourly run, default 10). --cap overrides. Queue runs (neither flag)
    # are uncapped — you tailor exactly what you queued.
    #
    # THE CAP COUNTS AI RESUMES ONLY. It exists to bound token spend, and only the
    # `customize` path (skillMatch < resume_customize_threshold — see cust_thr above) spends
    # tokens. A `template` item is a deterministic file copy: free, instant, and capping it
    # would drop resumes for no saving at all.
    process.sort(key=lambda p: (not p['isLocal'], -_sm(p)))
    _gui = load_json(cfg('gui_settings.json'), {}) or {}
    if a.cap is not None:
        _cap = a.cap
    elif a.auto:
        try: _cap = int(_gui.get('dailyAutoTailorCap', 20))
        except Exception: _cap = 20
    elif getattr(a, 'hourly', False):
        try: _cap = int(_gui.get('hourlyAutoTailorCap', 10))
        except Exception: _cap = 10
    else:
        _cap = None
    _total = len(process)
    _totalAi = sum(1 for p in process if p.get('customize'))
    if _cap is not None and _cap >= 0:
        _kept, _auton = [], 0
        for p in process:
            if p.get('trackerId') in _queued:
                _kept.append(p)                     # hand-picked: always tailor, never capped
            elif not p.get('customize'):
                _kept.append(p)                     # template copy: zero tokens, never capped
            elif _auton < _cap:
                _kept.append(p); _auton += 1        # top-N AI-tailored, in priority order
        process = _kept
    _capped = len(process) < _total
    print(json.dumps({'process': process, 'count': len(process), 'totalEligible': _total,
                      # How many AI resumes were eligible before the cap — the cap counts these
                      # only, so a caller comparing count vs totalEligible would misread it.
                      'totalAiEligible': _totalAi,
                      'cap': _cap, 'capped': _capped,
                      'blockedSkipped': _blocked_skipped,
                      'localCount': sum(1 for p in process if p['isLocal']),
                      'customizeCount': sum(1 for p in process if p.get('customize')),
                      'templateCount': sum(1 for p in process if not p.get('customize'))}, indent=2))

# ---------- employer blocklist (config/employer_blocklist.json) ----------
def cmd_block_employer(a):
    rec = block_employer(a.name, reason=a.reason or '', tactic=a.tactic, example=a.example or '')
    print(json.dumps({'blocked': rec['display'], 'tactic': rec['tactic'],
                      'reason': rec.get('reason', '')}, indent=2))

def cmd_unblock_employer(a):
    rec = unblock_employer(a.name)
    print(json.dumps({'unblocked': rec['display']} if rec else
                     {'unblocked': None, 'note': f'"{a.name}" was not on the blocklist'}, indent=2))

def cmd_list_blocked(a):
    rows = blocked_employers()
    if getattr(a, 'json', False):
        print(json.dumps([r for _, r in rows], indent=2)); return
    if not rows:
        print('blocklist empty'); return
    for _, r in rows:
        ex = f'  (e.g. {r["examples"][0]})' if r.get('examples') else ''
        print(f'  [{r["tactic"]:11}] {r["display"]} — {r.get("reason", "")}{ex}')
    print(f'\n{len(rows)} employer(s) blocked')

# ---------- salary bands (config/salary_bands.json) ----------
def _resolve_salary(cid, posted, est):
    """Thin wrapper over salary._resolve_salary that passes this module's
    SALARY_CACHE through, so monkeypatching jobpipe.SALARY_CACHE (tests) and
    the historical jobpipe-global behavior both keep working."""
    return _salary_resolve(cid, posted, est, cache_path=SALARY_CACHE)

def _doctor_collect():
    """Gather session-start health facts. READ-ONLY: never writes anything.

    Returns a dict the CLI renders as text or JSON. Every field is best-effort —
    a missing/unreadable file degrades to a note, never an exception, so `doctor`
    can always run even when the repo is in a broken state (which is exactly when
    it's most useful)."""
    import csv as _csv, glob as _glob
    from datetime import datetime as _dt

    def _age(iso):
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                delta = _dt.now() - _dt.strptime(iso[:26], fmt)
                secs = delta.total_seconds()
                if secs < 3600: return f'{int(secs//60)}m ago'
                if secs < 86400: return f'{secs/3600:.1f}h ago'
                return f'{secs/86400:.1f}d ago'
            except (ValueError, TypeError):
                continue
        return iso or '?'

    out = {'ok': True, 'errors': [], 'warnings': []}

    # 1. tracking.csv counts by status
    counts = {}
    try:
        with open(TRACKING_CSV, encoding='utf-8') as f:
            rows = list(_csv.DictReader(f))
        for r in rows:
            counts[r.get('status', '?')] = counts.get(r.get('status', '?'), 0) + 1
        out['jobs'] = {'total': len(rows), 'by_status': counts}
    except Exception as e:
        out['jobs'] = {'error': str(e)}
        out['warnings'].append('tracking.csv unreadable')

    # 2. job_tracker.json parse + truncation cross-check against newest backup
    tracker = {'size': None, 'parses': False, 'applied': None}
    try:
        tracker['size'] = os.path.getsize(JSON_PATH)
        data = load_json(JSON_PATH, None)
        tracker['parses'] = data is not None
        if data is not None:
            tracker['applied'] = len(data.get('applied', {}))
        else:
            out['errors'].append('job_tracker.json exists but does NOT parse (mount truncation?)')
        backups = _glob.glob(os.path.join(BASE, 'Backups', 'snapshots', '*', 'job_tracker.json'))
        if backups and tracker['size'] is not None:
            newest = max(backups, key=os.path.getmtime)
            bsize = os.path.getsize(newest)
            tracker['backup_size'] = bsize
            if bsize and tracker['size'] < bsize * 0.5:
                out['errors'].append(
                    f'job_tracker.json ({tracker["size"]}B) is <50% of newest backup '
                    f'({bsize}B) — likely truncated')
    except Exception as e:
        out['warnings'].append(f'tracker check failed: {e}')
    out['tracker'] = tracker

    # 3. queue depth (to_process.json — absent = empty queue)
    q = load_json(TO_PROCESS, None)
    out['queue_depth'] = len(q) if isinstance(q, (list, dict)) else 0

    # 4. last scheduled runs
    state = load_json(os.path.join(BASE, 'scheduler_state.json'), {}) or {}
    runs = {}
    for name, info in (state.get('jobs', {}) or {}).items():
        runs[name] = {'when': _age(info.get('lastRunAt', '')),
                      'result': info.get('lastResult', '?')}
        if str(info.get('lastResult', '')).startswith('fail'):
            out['warnings'].append(f'last {name} run failed')
    out['last_runs'] = runs

    # 5. last tailoring run
    run = load_json(os.path.join(BASE, 'run.json'), {}) or {}
    if run:
        out['last_tailor'] = {'when': _age(run.get('timestamp', '')),
                              'resumes': run.get('resumes_created'),
                              'pushed': run.get('git_pushed')}

    # 6. Adzuna quota (gitignored; often absent)
    quota = load_json(SALARY_QUOTA, None)
    if quota:
        out['adzuna'] = {'day': quota.get('day'), 'exhausted': quota.get('exhausted', False)}
        if quota.get('exhausted'):
            out['warnings'].append('Adzuna quota exhausted — salaries fall back to bands')

    # 7. stale lock files
    locks = []
    for lk in _glob.glob(os.path.join(BASE, '.~lock.*#')):
        locks.append(os.path.basename(lk))
    git_lock = os.path.join(BASE, '.git', 'index.lock')
    if os.path.exists(git_lock):
        locks.append('.git/index.lock')
        out['warnings'].append('.git/index.lock present — a git commit/push may be stuck or crashed')
    out['locks'] = locks

    # 8. lint error count over New/ (the actionable, not-yet-sent set) — fast
    try:
        import subprocess
        script = _paths.script('lint_resume.py')
        r = subprocess.run([sys.executable, script, '--dir', os.path.join(BASE, 'New'),
                            '--json', '--quiet'], capture_output=True, timeout=60)
        rep = json.loads(r.stdout.decode('utf-8', 'replace') or '{}')
        # Count resumes with >=1 ERROR — the actionable number. Raw error count is
        # inflated because R01 (font floor) fires once per offending line.
        bad = sum(1 for fl in rep.get('files', [])
                  if any(x['level'] == 'ERROR' for x in fl.get('findings', [])))
        out['lint_new'] = {'checked': rep.get('checked', 0),
                           'files_with_errors': bad, 'errors': rep.get('errors', 0)}
        if bad:
            out['warnings'].append(f'{bad} of {rep.get("checked", 0)} New/ resume(s) '
                                   f'fail lint — run: jobpipe lint-resume --dir New --errors-only')
    except Exception as e:
        out['lint_new'] = {'error': str(e)}

    out['ok'] = not out['errors']
    return out


def cmd_doctor(a):
    """Print a compact session-start health block. READ-ONLY."""
    d = _doctor_collect()
    if getattr(a, 'json', False):
        print(json.dumps(d, indent=2))
        return 0 if d['ok'] else 1

    j = d.get('jobs', {})
    if 'by_status' in j:
        parts = ' '.join(f'{k} {v}' for k, v in sorted(j['by_status'].items()))
        print(f'jobs        {j["total"]} total  ({parts})')
    t = d.get('tracker', {})
    flag = 'OK' if t.get('parses') else 'PARSE FAIL'
    size = f'{t["size"]//1024}KB' if t.get('size') else '?'
    print(f'tracker     {flag}  {size}  applied={t.get("applied")}')
    print(f'queue       {d.get("queue_depth", 0)} waiting to tailor')
    for name, info in d.get('last_runs', {}).items():
        print(f'  {name:16} {info["result"]:6} {info["when"]}')
    if 'last_tailor' in d:
        lt = d['last_tailor']
        print(f'last tailor {lt["resumes"]} resumes, {lt["when"]}, pushed={lt["pushed"]}')
    if 'adzuna' in d:
        print(f'adzuna      day={d["adzuna"]["day"]} exhausted={d["adzuna"]["exhausted"]}')
    ln = d.get('lint_new', {})
    if 'files_with_errors' in ln:
        print(f'lint (New/) {ln["files_with_errors"]} of {ln["checked"]} resume(s) failing')
    if d.get('locks'):
        print(f'locks       {", ".join(d["locks"])}')
    if d['errors'] or d['warnings']:
        print()
        for e in d['errors']:
            print(f'  ERROR  {e}')
        for w in d['warnings']:
            print(f'  warn   {w}')
    else:
        print('\nall clear')
    return 0 if d['ok'] else 1


def cmd_lint_resume(argv):
    """Delegate to lint_resume.py, passing every argument through verbatim.

    Shelled out rather than imported so python-docx is only required when
    actually linting. Dispatched before argparse runs, because
    argparse.REMAINDER silently drops leading --flags.
    """
    import subprocess
    script = _paths.script('lint_resume.py')
    if not os.path.exists(script):
        print('lint_resume.py not found beside jobpipe.py', file=sys.stderr)
        return 2
    return subprocess.run([sys.executable, script] + list(argv)).returncode

def main():
    # Pass-through subcommand: handled before argparse so flags reach the delegate intact.
    if len(sys.argv) > 1 and sys.argv[1] == 'lint-resume':
        sys.exit(cmd_lint_resume(sys.argv[2:]))
    p = argparse.ArgumentParser(description='Deterministic engine for the job pipeline.')
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('urls')
    c = sub.add_parser('classify'); c.add_argument('--title', required=True)
    f = sub.add_parser('flag-skills'); f.add_argument('--required', required=True)
    mt = sub.add_parser('match'); mt.add_argument('--required', required=True)
    mt.add_argument('--title', default='', help='apply the title-relevance factor too')
    fl = sub.add_parser('filter'); fl.add_argument('--listings', required=True)
    fl.add_argument('--cap', type=int, default=20); fl.add_argument('--min-match', type=float, default=None, dest='min_match')
    u = sub.add_parser('update'); u.add_argument('--details', required=True); u.add_argument('--processed'); u.add_argument('--run')
    r = sub.add_parser('rebuild'); r.add_argument('--manual')
    ar = sub.add_parser('archive'); ar.add_argument('--days', type=int, default=7)
    sub.add_parser('export-csv')
    ic = sub.add_parser('import-csv'); ic.add_argument('--file', default=None)
    cd = sub.add_parser('candidates'); cd.add_argument('--listings', required=True); cd.add_argument('--threshold', type=float, default=None)
    pq = sub.add_parser('process-queue'); pq.add_argument('--threshold', type=float, default=None); pq.add_argument('--auto', action='store_true'); pq.add_argument('--local-only', action='store_true', dest='local_only'); pq.add_argument('--hourly', action='store_true'); pq.add_argument('--cap', type=int, default=None); pq.add_argument('--no-probe', action='store_true', dest='no_probe')
    sa = sub.add_parser('salary'); sa.add_argument('--title', required=True)
    sub.add_parser('salaries', help='resolved {id:{value,source,kind}} salary map for all visible jobs (dashboard)')
    sap = sub.add_parser('salary-apply'); sap.add_argument('--id', required=True)
    sap.add_argument('--value', default=''); sap.add_argument('--source', default='')
    ss = sub.add_parser('salary-set'); ss.add_argument('--family', required=True)
    ss.add_argument('--range', default=None); ss.add_argument('--senior-range', dest='senior_range', default=None)
    ss.add_argument('--match', default=None)
    sp = sub.add_parser('simplify'); sp.add_argument('--raw', default=None); sp.add_argument('--mark', default=None); sp.add_argument('--add-unmatched', action='store_true', dest='add_unmatched')
    pc = sub.add_parser('parse-cards'); pc.add_argument('--raw', required=True); pc.add_argument('--out', default=None)
    sub.add_parser('lint-resume', help='check tailored resumes against the CLAUDE.md rules')
    dr = sub.add_parser('doctor', help='compact read-only session-start health check')
    dr.add_argument('--json', action='store_true')
    be = sub.add_parser('block-employer', help='ban an employer (ghost/salary/bait-switch); auto-skips their jobs')
    be.add_argument('--name', required=True)
    be.add_argument('--reason', default='')
    be.add_argument('--tactic', choices=sorted(TACTICS), default='other')
    be.add_argument('--example', default='', help='a job id or URL that prompted the ban')
    ube = sub.add_parser('unblock-employer'); ube.add_argument('--name', required=True)
    lb = sub.add_parser('list-blocked'); lb.add_argument('--json', action='store_true')
    a = p.parse_args()
    if a.cmd == 'urls':
        print(json.dumps(load_json(cfg('search_urls.json'), {}), indent=2))
    elif a.cmd == 'classify':
        print(json.dumps(classify(a.title), indent=2))
    elif a.cmd == 'flag-skills':
        print(json.dumps(flag_skills([s.strip() for s in a.required.split(',') if s.strip()]), indent=2))
    elif a.cmd == 'match':
        req = [s.strip() for s in a.required.split(',') if s.strip()]
        pct, flagged, tfit, treason = score_job(getattr(a, 'title', '') or '', req)
        raw, _ = skill_match(req)
        print(json.dumps({'skillMatch': pct, 'skillMatchBeforeTitle': raw,
                          'titleFit': tfit, 'titleReason': treason,
                          'flaggedSkills': flagged}, indent=2))
    elif a.cmd == 'filter': cmd_filter(a)
    elif a.cmd == 'update': cmd_update(a)
    elif a.cmd == 'rebuild': cmd_rebuild(a)
    elif a.cmd == 'archive': cmd_archive(a)
    elif a.cmd == 'export-csv': cmd_export_csv(a)
    elif a.cmd == 'import-csv': cmd_import_csv(a)
    elif a.cmd == 'candidates': cmd_candidates(a)
    elif a.cmd == 'process-queue': cmd_process_queue(a)
    elif a.cmd == 'salary': cmd_salary(a)
    elif a.cmd == 'salaries': cmd_salaries(a)
    elif a.cmd == 'salary-set': cmd_salary_set(a)
    elif a.cmd == 'salary-apply': cmd_salary_apply(a)
    elif a.cmd == 'simplify': cmd_simplify(a)
    elif a.cmd == 'parse-cards': cmd_parse_cards(a)
    elif a.cmd == 'doctor': sys.exit(cmd_doctor(a))
    elif a.cmd == 'block-employer': cmd_block_employer(a)
    elif a.cmd == 'unblock-employer': cmd_unblock_employer(a)
    elif a.cmd == 'list-blocked': cmd_list_blocked(a)
    else: p.print_help()

if __name__ == '__main__':
    main()
