#!/usr/bin/env python3
"""migrate_data_root.py — move the personal data folder INTO the app folder.

Old layout (data beside the code)          New layout (self-contained)
    Projects/                                  auto customize resume/
      auto customize resume/   <- code           src/ config/ tools/     <- code
      job_agent_profile_KP/    <- data           user_data_KP/           <- data
                                                   resume_template/

Why: one folder is then the whole install — copy it, back it up, or hand it to
another machine and the tracker, the profile and the base resumes come along.
pipelib.resolve_data_root() finds a nested ``user_data_*`` folder on its own
(rung 3), so the layout also survives config/instance.json being deleted.

WHAT IT DOES  (in this order, stopping at the first failure)
  1. copy every file from the current data root into <app>/user_data_<INITIALS>
  2. re-read each copy and compare SHA-256 against the source
  3. fold the resume folder in, if it is still outside the new data root
  4. point config/instance.json at the new folder

WHAT IT NEVER DOES
  * delete or modify anything in the source folder — the old folder stays exactly
    as it is, so a bad migration costs nothing but disk. Remove it by hand once
    the dashboard has restarted and the board looks right.
  * overwrite a destination file that already differs from its source without
    saying so (--force re-copies; the default stops and reports).

USAGE
    python src/ops/migrate_data_root.py              # dry run: prints the plan, writes nothing
    python src/ops/migrate_data_root.py --apply      # do it
    python src/ops/migrate_data_root.py --apply --dest user_data_KP
    python src/ops/migrate_data_root.py --apply --from "C:\\path\\to\\old_data"

Restart the dashboard afterwards: DATA is resolved once at import time.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, shutil, hashlib, argparse

import pipelib as pl

HERE = _paths.ROOT
# Junk that is regenerated on the next run. Copying it wastes time and, worse, carries a
# stale cache into what should be a clean layout. Matched case-insensitively on basename.
SKIP_DIRS = {'__pycache__', '.git', 'cache', 'logs', '.pytest_cache'}
SKIP_FILES = {'.write_probe', 'thumbs.db', '.ds_store'}


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def walk_files(root):
    """(relative_path, absolute_path) for every file worth copying, skipping junk."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for name in filenames:
            if name.lower() in SKIP_FILES or name.startswith('~$') or name.endswith('.pyc'):
                continue
            src = os.path.join(dirpath, name)
            yield os.path.relpath(src, root), src


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.0f %s' % (n, unit) if unit == 'B' else '%.1f %s' % (n, unit)
        n /= 1024.0


def resolve_initials():
    """Initials for the folder name, from the profile the app is using right now."""
    try:
        import profile_lib as plib
        prof = plib.load_profile() or {}
        return plib.initials((prof.get('identity') or {}).get('full_name') or '')
    except Exception:
        return ''


def plan(src_root, dest_root, force):
    """(copies, identical, conflicts, total_bytes) without touching the disk."""
    copies, identical, conflicts, total = [], [], [], 0
    for rel, src in walk_files(src_root):
        dest = os.path.join(dest_root, rel)
        if os.path.exists(dest):
            try:
                same = os.path.getsize(dest) == os.path.getsize(src) and sha256(dest) == sha256(src)
            except OSError:
                same = False
            if same:
                identical.append(rel)
                continue
            if not force:
                conflicts.append(rel)
                continue
        copies.append((rel, src, dest))
        try:
            total += os.path.getsize(src)
        except OSError:
            pass
    return copies, identical, conflicts, total


def main():
    ap = argparse.ArgumentParser(description='Move the data folder inside the app folder.')
    ap.add_argument('--apply', action='store_true', help='actually copy (default is a dry run)')
    ap.add_argument('--dest', default='', help='destination folder NAME inside the app folder')
    ap.add_argument('--from', dest='src', default='', help='source data root (default: the active one)')
    ap.add_argument('--force', action='store_true',
                    help='re-copy destination files that differ from the source')
    ap.add_argument('--keep-resume-folder', action='store_true',
                    help='leave the resume folder where it is instead of folding it in')
    args = ap.parse_args()

    src_root = os.path.abspath(os.path.expanduser(args.src)) if args.src else os.path.abspath(pl.DATA)
    dest_name = args.dest or pl.suggest_user_data_dirname(resolve_initials())
    dest_root = os.path.abspath(os.path.join(HERE, dest_name))

    print('app folder :', HERE)
    print('data now   :', src_root)
    print('data after :', dest_root)
    print()

    if not os.path.isdir(src_root):
        sys.exit('The source folder does not exist: ' + src_root)
    if os.path.normcase(src_root) == os.path.normcase(dest_root):
        sys.exit('Already migrated — the data root IS ' + dest_root)
    if os.path.normcase(src_root) == os.path.normcase(os.path.abspath(HERE)):
        # Data has never been split out; every state file sits loose among the code and
        # there is no reliable way to tell a tracker file from a source file. Refuse.
        sys.exit('The data root is the app folder itself. Run the setup wizard and choose a\n'
                 'data folder first, then re-run this script.')
    if os.path.normcase(dest_root).startswith(os.path.normcase(src_root) + os.sep):
        sys.exit('The destination is inside the source. Pick a different --dest.')

    copies, identical, conflicts, total = plan(src_root, dest_root, args.force)

    print('to copy    : %d files (%s)' % (len(copies), human(total)))
    if identical:
        print('already ok : %d files (identical, skipped)' % len(identical))
    if conflicts:
        print('CONFLICTS  : %d files exist at the destination and DIFFER:' % len(conflicts))
        for rel in conflicts[:20]:
            print('   ', rel)
        if len(conflicts) > 20:
            print('    ... and %d more' % (len(conflicts) - 20))
        print('\nNothing was written. Inspect those, then re-run with --force to overwrite them.')
        return 1

    # The resume folder rides along only if it is not already inside the source tree.
    tpl = os.path.abspath(pl.TEMPLATE_DIR)
    tpl_inside = os.path.normcase(tpl).startswith(os.path.normcase(src_root) + os.sep)
    tpl_move = None
    if not args.keep_resume_folder and not tpl_inside and os.path.isdir(tpl):
        tpl_move = (tpl, os.path.join(dest_root, 'resume_template'))
        print('resume dir : %s\n             -> %s' % tpl_move)

    if not args.apply:
        print('\nDRY RUN — nothing written. Re-run with --apply to do it.')
        return 0

    print('\ncopying...')
    os.makedirs(dest_root, exist_ok=True)
    for i, (rel, src, dest) in enumerate(copies, 1):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        if i % 25 == 0 or i == len(copies):
            print('  %d/%d' % (i, len(copies)))

    # Verify by RE-READING both sides. copy2 returning cleanly is not proof: a truncated
    # write on a network/synced folder reports success and leaves a short file behind.
    print('verifying...')
    bad = []
    for rel, src, dest in copies:
        try:
            if sha256(src) != sha256(dest):
                bad.append(rel)
        except OSError as e:
            bad.append('%s (%s)' % (rel, e))
    if bad:
        print('\nCHECKSUM MISMATCH on %d file(s) — instance.json NOT repointed:' % len(bad))
        for rel in bad[:20]:
            print('   ', rel)
        print('\nThe source folder is untouched. Fix the cause and re-run with --force.')
        return 1
    print('  all %d files verified byte-for-byte.' % len(copies))

    if tpl_move:
        if not os.path.isdir(tpl_move[1]):
            shutil.copytree(tpl_move[0], tpl_move[1], dirs_exist_ok=True)
            print('resume folder copied in.')
        # Copying the folder is not enough. profile.json still holds the OLD absolute
        # template_dir, and pipelib._profile_template_dir() honours an absolute path that
        # still exists — so the app would keep reading the folder we just moved away from,
        # and the copy inside the data root would sit there unused. Rewrite it to the
        # RELATIVE name, which resolves against the data root wherever that ends up.
        prof_path = os.path.join(dest_root, 'config', 'profile.json')
        try:
            with open(prof_path, 'r', encoding='utf-8') as fh:
                prof = json.load(fh)
            if isinstance(prof.get('resume'), dict):
                old = prof['resume'].get('template_dir') or ''
                prof['resume']['template_dir'] = 'resume_template'
                tmp_p = prof_path + '.tmp'
                with open(tmp_p, 'w', encoding='utf-8') as fh:
                    json.dump(prof, fh, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_p, prof_path)
                print('profile.json resume.template_dir: %r -> "resume_template"' % old)
        except FileNotFoundError:
            pass
        except Exception as e:
            print('WARNING: could not rewrite resume.template_dir in %s (%s).' % (prof_path, e))
            print('         Set the resume folder again in Settings after restarting.')

    # Repoint LAST: until this line the app still reads the old folder, so an abort at any
    # earlier point leaves a working install rather than a half-migrated one.
    inst = os.path.join(HERE, 'config', 'instance.json')
    try:
        with open(inst, 'r', encoding='utf-8') as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {}
    cfg['data_root'] = dest_root
    os.makedirs(os.path.dirname(inst), exist_ok=True)
    tmp = inst + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, inst)
    print('config/instance.json now points at', dest_root)

    print('\nDone. Restart the dashboard — the data root is read once at startup.')
    print('The old folder is untouched at:\n  ' + src_root)
    print('Delete it by hand once the board looks right.')
    return 0


if __name__ == '__main__':
    try:
        import runlog
        sys.exit(runlog.run('migrate_data_root.py', main) or 0)
    except ImportError:
        sys.exit(main() or 0)
