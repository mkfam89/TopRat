#!/usr/bin/env python3
"""release.py — build the sanitized, shippable copy of this project.

Why it exists
-------------
The public/handoff repo (TopRat) is NOT a branch of this one: this repo's
history carries tracking data, job descriptions and real resumes, so the ship
copy has to be a fresh TREE built from an allowlist, with its own history.

Doing that by hand works exactly once. The second time, a new file lands in
src/ and nobody remembers to copy it, or a personal config file gets swept in
because the copy used a blocklist that day. So the allowlist lives HERE, in
code, and every release runs the same function.

Deterministic and zero-token, like everything else on the script path: stdlib
only, no network unless you ask for --push, no Claude anywhere.

Allowlist, not blocklist
------------------------
`INCLUDE` names what ships. Anything not named is left behind — the default is
EXCLUDE. A blocklist gets this backwards: it ships every new file by default and
only omits what someone remembered to forbid, which is the wrong failure
direction when the thing you are forbidding is your own address book.

Usage
-----
    python src/ops/release.py --check              # build to a temp dir, verify, report, discard
    python src/ops/release.py --out ../TopRat     # build the real thing
    python src/ops/release.py --out ../TopRat --push      # ... and commit + push it

`--push` deliberately refuses to run unless the tree verifies clean.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = _paths.ROOT

# --------------------------------------------------------------------------
# What ships. Directories are copied whole (minus PRUNE_DIRS / PRUNE_SUFFIXES);
# files are copied verbatim. Add to this list when you add a top-level file, or
# it silently will not ship.
# --------------------------------------------------------------------------
INCLUDE_FILES = [
    'app.py',
    'requirements.txt',
    'README.md',
    'INSTALL.md',
    'LICENSE',
    'CLAUDE.md',
    'DEPENDENCY_MAP.md',
    'SHIP_PLAN.md',
    'pytest.ini',
    '.gitignore',
    'Start Here.bat',
    'Start Here.command',
    'Install Watchdog.bat',
    'Uninstall Watchdog.bat',
    'start_dashboard_hidden.vbs',
]
INCLUDE_DIRS = ['src', 'tests', 'tools', 'assets', 'docs', '.github']

# Never copied, wherever they appear inside an included directory.
PRUNE_DIRS = {'__pycache__', '.pytest_cache', '.git', 'node_modules', '.venv', 'venv'}
PRUNE_SUFFIXES = ('.pyc', '.pyo', '.log', '.tmp', '.bak', '.corrupt')

# config/ is special: ONLY the shipped defaults and the .example templates go.
# A real adzuna.json / git.json / profile.json holds keys and personal details.
CONFIG_ALLOW_EXACT = {'salary_bands.json', 'schedule.default.json', 'strategy.json'}


def _is_config_shippable(name):
    """True for '*.example*' templates and the handful of shipped defaults."""
    return '.example' in name or name in CONFIG_ALLOW_EXACT


# --------------------------------------------------------------------------
# The guard. build() is only half the job — this is the half that makes the
# result trustworthy, and tests/test_release.py runs it on every CI pass.
# --------------------------------------------------------------------------

# Paths that must never exist in an export, as regexes against the RELATIVE path.
FORBIDDEN_PATHS = [
    r'(^|/)user_data[^/]*/',              # the personal data root, whatever the initials
    r'(^|/)job_agent_profile[^/]*/',      # the split-out profile folder
    r'(^|/)(New|Applied|Skipped|Backups|logs|cache)/',
    r'(^|/)\.git/',
    r'(^|/)job_tracker\.(json|html)$',
    r'(^|/)(tracking|candidates|archive_index|archived)\.csv$',
    r'(^|/)(to_process|listings|details_manual|geo_cache|salary_cache|notified)\.json$',
    r'(^|/)(raw_applied|scrape_meta|scrape_raw_sample|scheduler_state)\.json$',
    r'(^|/)config/(adzuna|git|notify|anthropic|instance|profile|search_urls|skills|'
    r'skill_aliases|active_profile|ghost_flags|gui_settings|summary_rules)\.json$',
]

# Binary document types. tests/fixtures/docx is the ONE exception: those are
# synthetic linter fixtures, and .gitignore keeps them out of git anyway.
FORBIDDEN_SUFFIXES = ('.docx', '.pdf', '.doc', '.zip')
BINARY_EXEMPT_PREFIX = 'tests/fixtures/'

# Strings that mean a secret got copied. Checked inside text files.
SECRET_PATTERNS = [
    (r'sk-ant-[A-Za-z0-9_\-]{10,}', 'Anthropic API key'),
    (r'ghp_[A-Za-z0-9]{20,}', 'GitHub personal access token'),
    (r'github_pat_[A-Za-z0-9_]{20,}', 'GitHub fine-grained token'),
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', 'private key'),
    (r'"app_key"\s*:\s*"[A-Za-z0-9]{8,}"', 'Adzuna app_key'),
]

TEXT_SUFFIXES = ('.py', '.md', '.json', '.txt', '.yml', '.yaml', '.html', '.css',
                 '.js', '.bat', '.command', '.sh', '.vbs', '.ini', '.cfg')


def verify(root):
    """Return a list of problems with a built export. Empty list == clean.

    Deliberately re-derived from the tree on disk rather than from what build()
    thinks it copied: the point is to catch the case where build() is wrong.
    """
    problems = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, '/')

            for pat in FORBIDDEN_PATHS:
                if re.search(pat, rel):
                    problems.append('forbidden path: %s' % rel)
                    break

            if rel.lower().endswith(FORBIDDEN_SUFFIXES) and not rel.startswith(BINARY_EXEMPT_PREFIX):
                problems.append('binary document: %s' % rel)

            if rel.lower().endswith(TEXT_SUFFIXES):
                try:
                    with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                        body = fh.read()
                except OSError:
                    continue
                for pat, label in SECRET_PATTERNS:
                    if re.search(pat, body):
                        problems.append('%s in %s' % (label, rel))
    return sorted(set(problems))


# --------------------------------------------------------------------------
# The build
# --------------------------------------------------------------------------

def _copy_tree(src, dst):
    """Copy a directory, skipping pruned folders and junk suffixes. Returns file count."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        rel = os.path.relpath(dirpath, src)
        out = dst if rel == '.' else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for fn in filenames:
            if fn.endswith(PRUNE_SUFFIXES):
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(out, fn))
            n += 1
    return n


def build(out_dir, source=HERE, quiet=False):
    """Build the sanitized export at out_dir. Returns (file_count, problems).

    Never deletes out_dir wholesale — it may be a git working copy whose .git
    must survive. Tracked content is replaced; .git and anything else already
    there is left alone.
    """
    say = (lambda *a: None) if quiet else print
    os.makedirs(out_dir, exist_ok=True)

    for name in INCLUDE_DIRS:
        src = os.path.join(source, name)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(out_dir, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        say('  %-14s %d files' % (name + '/', _copy_tree(src, dst)))

    for name in INCLUDE_FILES:
        src = os.path.join(source, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out_dir, name))

    cfg_src = os.path.join(source, 'config')
    cfg_dst = os.path.join(out_dir, 'config')
    os.makedirs(cfg_dst, exist_ok=True)
    kept = 0
    if os.path.isdir(cfg_src):
        for fn in sorted(os.listdir(cfg_src)):
            if os.path.isfile(os.path.join(cfg_src, fn)) and _is_config_shippable(fn):
                shutil.copy2(os.path.join(cfg_src, fn), os.path.join(cfg_dst, fn))
                kept += 1
    say('  %-14s %d files (templates + shipped defaults only)' % ('config/', kept))

    total = sum(len(f) for _, _, f in os.walk(out_dir))
    return total, verify(out_dir)


# --------------------------------------------------------------------------
# Optional publish step
# --------------------------------------------------------------------------

def _git(out_dir, *args, timeout=180):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    return subprocess.run(['git', '-C', out_dir] + list(args),
                          capture_output=True, text=True, env=env, timeout=timeout)


def publish(out_dir, remote, message, branch='main'):
    """Commit everything in out_dir and push. Returns (ok, message).

    Assumes the tree already verified clean — main() enforces that, because a
    push is the one step in this file that cannot be undone quietly.
    """
    if not os.path.isdir(os.path.join(out_dir, '.git')):
        _git(out_dir, 'init')
        _git(out_dir, 'branch', '-M', branch)
        if remote:
            _git(out_dir, 'remote', 'add', 'origin', remote)
    _git(out_dir, 'add', '-A')
    if not _git(out_dir, 'status', '--porcelain').stdout.strip():
        return True, 'nothing changed since the last release'
    r = _git(out_dir, 'commit', '-m', message)
    if r.returncode != 0:
        return False, 'commit failed: ' + (r.stderr or r.stdout).strip()
    r = _git(out_dir, 'push', '-u', 'origin', branch)
    if r.returncode != 0:
        return False, 'push failed: ' + (r.stderr or r.stdout).strip()
    return True, 'pushed to %s (%s)' % (remote or 'origin', branch)


def _version():
    try:
        from _version import VERSION
        return VERSION
    except Exception:
        return '0.0.0'


def main():
    p = argparse.ArgumentParser(description='Build the sanitized shippable copy.')
    p.add_argument('--out', help='where to build (e.g. ../TopRat)')
    p.add_argument('--check', action='store_true',
                   help='build to a temp folder, verify, report, discard')
    p.add_argument('--push', action='store_true', help='commit + push after a clean build')
    p.add_argument('--remote', default='', help='git remote for --push on a first run')
    p.add_argument('--message', default='', help='commit message for --push')
    a = p.parse_args()

    if not a.check and not a.out:
        p.error('give --out DIR, or --check to dry-run')

    tmp = tempfile.mkdtemp(prefix='ja_release_') if a.check else None
    out = tmp or os.path.abspath(a.out)
    try:
        print('Building export -> %s' % ('(temporary)' if a.check else out))
        total, problems = build(out)
        print('  %-14s %d files total' % ('', total))

        if problems:
            print('\nFAILED — the export is not clean:')
            for x in problems:
                print('  * ' + x)
            print('\nFix the allowlist in src/ops/release.py, then run again.')
            return 1

        print('\nClean: no personal data, no secrets, no binaries.')
        if a.check:
            print('(--check, so nothing was written)')
            return 0

        if a.push:
            msg = a.message or ('Top Rat %s' % _version())
            ok, note = publish(out, a.remote, msg)
            print(('Published: ' if ok else 'Publish FAILED: ') + note)
            return 0 if ok else 1

        print('Next: cd %s && git add -A && git commit && git push' % out)
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main() or 0)
