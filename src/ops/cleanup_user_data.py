#!/usr/bin/env python3
"""cleanup_user_data.py — remove leftover PERSONAL data from the CODE folder.

One-shot migration helper for the code/data split. The code now reads personal
data from the data root (default: ./user_data_<INITIALS> inside the app folder —
see pipelib.resolve_data_root). This script deletes the stale copies still sitting
in the code folder, but ONLY after proving the data root has that file:

  - identical content (sha256)          -> safe, delete
  - exists but differs                  -> KEPT + warned (never guesses which is newer)
  - missing from the data root          -> KEPT + warned

Dry-run by default; nothing is touched without --apply.

  python src/ops/cleanup_user_data.py            # report what would happen
  python src/ops/cleanup_user_data.py --apply    # actually delete verified copies

Zero tokens, stdlib only, standalone — nothing imports it. Safe to re-run;
delete it (or keep it) once the code folder is clean.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import hashlib
import os
import sys

import pipelib

HERE = pipelib.HERE
DATA = pipelib.DATA

# Personal files that may linger in the code folder. Relative paths, '/'-separated;
# directories end with '/'. Secrets are listed too: they are just as personal, and
# by now they live (gitignored) in the data root.
PERSONAL = [
    # tracker state
    'tracking.csv', 'job_tracker.json', 'job_tracker.html', 'candidates.csv',
    'archive_index.csv', 'archived.csv', 'raw_applied.json', 'geo_cache.json',
    'scrape_meta.json', 'scheduler_state.json', 'details_manual.json',
    'listings.json', 'to_process.json', 'notified.json',
    'salary_cache.json', 'salary_quota.json',
    # profile + generated config
    'config/profile.json', 'config/profile.json.bak1', 'config/profile.json.bak2',
    'config/profiles/', 'config/active_profile.json', 'config/search_urls.json',
    'config/skills.json', 'config/skill_aliases.json', 'config/summary_rules.json',
    # runtime settings
    'config/gui_settings.json', 'config/schedule.json', 'config/employer_blocklist.json',
    # secrets
    'config/git.json', 'config/adzuna.json', 'config/notify.json',
    # resume source content
    'resume_template/',
]

# NEVER touched, listed only so the report shows the boundary explicitly.
STAYS = ['config/strategy.json', 'config/salary_bands.json', 'config/instance.json',
         'config/*.example.json (all)', 'config/ghost_risk.json', 'config/reposts.json']


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def files_under(rel_dir):
    """All files below HERE/rel_dir, as '/'-relative paths."""
    base = os.path.join(HERE, rel_dir)
    for root, _dirs, files in os.walk(base):
        for f in files:
            yield os.path.relpath(os.path.join(root, f), HERE).replace(os.sep, '/')


def classify(rel):
    """-> ('match'|'differs'|'missing', src, dst)"""
    src = os.path.join(HERE, rel)
    dst = os.path.join(DATA, rel)
    if not os.path.exists(dst):
        return 'missing', src, dst
    if os.path.getsize(src) == os.path.getsize(dst) and sha256(src) == sha256(dst):
        return 'match', src, dst
    return 'differs', src, dst


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true',
                    help='actually delete verified copies (default: dry-run report)')
    a = ap.parse_args()

    if not pipelib.SPLIT:
        print('ABORT: pipelib resolves the data root to the code folder itself\n'
              f'  DATA = {DATA}\n'
              'Deleting here would destroy the only copy. Set up the data folder first\n'
              '(default ./user_data_<INITIALS>, or set data_root in config/instance.json).')
        return 1

    print(f'code folder : {HERE}')
    print(f'data root   : {DATA}')
    print(f'mode        : {"APPLY — deleting verified copies" if a.apply else "dry-run (use --apply to delete)"}\n')

    todo = []
    for rel in PERSONAL:
        if rel.endswith('/'):
            d = os.path.join(HERE, rel.rstrip('/'))
            if os.path.isdir(d):
                todo.extend(sorted(files_under(rel.rstrip('/'))))
        elif os.path.exists(os.path.join(HERE, rel)):
            todo.append(rel)

    if not todo:
        print('Nothing personal left in the code folder. Already clean.')
        return 0

    deleted = kept_differs = kept_missing = 0
    for rel in todo:
        state, src, _dst = classify(rel)
        if state == 'match':
            if a.apply:
                os.remove(src)
                # verify the delete actually happened (mount weirdness paranoia)
                if os.path.exists(src):
                    print(f'  !! FAILED to delete {rel} — still present'); continue
            print(f'  {"deleted " if a.apply else "would delete"}  {rel}')
            deleted += 1
        elif state == 'differs':
            print(f'  KEPT (DIFFERS from data-root copy — reconcile by hand)  {rel}')
            kept_differs += 1
        else:
            print(f'  KEPT (missing from data root — copy it there first)     {rel}')
            kept_missing += 1

    if a.apply:  # sweep now-empty personal dirs
        for rel in ('config/profiles', 'resume_template'):
            d = os.path.join(HERE, rel)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                print(f'  removed empty dir  {rel}/')

    print(f'\n{"deleted" if a.apply else "deletable"}: {deleted}   '
          f'kept (differs): {kept_differs}   kept (missing from data root): {kept_missing}')
    print('\nstays with the code (by design): ' + ', '.join(STAYS))
    if kept_differs or kept_missing:
        print('\nRe-run after reconciling the kept files. Nothing was deleted without a verified copy.')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
