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
    python src/ops/release.py --out TopRat         # build the real thing
    python src/ops/release.py --out TopRat --push  # ... and commit + push it
    python src/ops/release.py --out TopRat --no-python   # ... without the interpreter

Run these from the PROJECT ROOT, not from inside the export: this file reads the
real tree and writes the sanitized one. `TopRat` (not `../TopRat`) — the export
folder lives inside the project folder.

`--push` deliberately refuses to run unless the tree verifies clean. It commits
code only: the `python/` bundle is git-ignored by design and travels in the zip.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import json
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
    # CLAUDE.md deliberately does NOT ship: it is the personal resume-tailoring
    # rulebook (real name, real role history), and it is useless to anyone whose
    # resume isn't this one.
    'DEPENDENCY_MAP.md',
    'SHIP_PLAN.md',
    'pytest.ini',
    '.gitignore',
    'Start Here.bat',
    'Start Here.command',
    'Install Watchdog.bat',
    'Uninstall Watchdog.bat',
    # The mac/linux twins. They existed in the tree from 1.2.0 and were NOT in this
    # list, so every zip shipped without them while INSTALL.md told Mac users to run
    # them - a doc pointing at files the download does not contain (QA 1.2.0, F-2).
    'Install Watchdog.command',
    'Uninstall Watchdog.command',
    'start_dashboard_hidden.vbs',
]
# 'docs' is NOT here on purpose. docs/ is the internal working record — refactor
# playbooks, staging steps, cleanup lists — written in the first person about one
# person's machine, full of C:\Users\<name> paths and real employer history. It is
# notes-to-self, not product documentation; README.md + INSTALL.md are what a user
# of the shipped copy actually needs.
# 'agent_prompts' IS shipped, unlike docs/: it is not notes-to-self but the actual text
# the Settings page hands a user to paste into Claude, so the scheduled-task feature is
# simply dead in a copy without it. The files are de-personalized and placeholder-driven
# (see agent_prompts/README.md), so the PII gate covers them like any other shipped file.
INCLUDE_DIRS = ['src', 'tests', 'tools', 'assets', '.github', 'agent_prompts']
# 'qa' is NOT here, and must not be. qa/ drives a BUILT release from outside —
# it boots Windows Sandbox, unzips dist/TopRat-*.zip on a clean machine and
# checks what the packaged copy does. It imports nothing from src/, so it is
# useless in a clone, and half of what it asserts is about the artifact rather
# than the code. Being absent from this allowlist is what keeps it out: nothing
# had to remember to exclude it. tests/ (the in-process unit suite) DOES ship
# here — a maintainer who clones TopRat wants it and .github/workflows/ci.yml
# runs it — but src/ops/package.py strips it from the user-facing zip, which
# ships no pytest to run it with. Repo and download differ on purpose.

# Names that must be REMOVED from an existing export folder if found. Two kinds:
# things this allowlist used to ship and no longer does, and state files that can
# only have arrived by hand. Explicit rather than "delete anything unexpected",
# so a build can never eat something the user deliberately put in the ship repo.
# Files inside an INCLUDE_DIRS tree that must not ship. Kept deliberately tiny —
# the allowlist is the mechanism, this is for the rare file that has to exist in
# the dev tree but is meaningless (and private) outside it.
#
# content_alkami.json is a REAL tailored resume: name, contact line, five real
# roles. It is the acceptance fixture for test_fidelity_against_alkami, which
# renders it and diffs against the actual New/Alkami/*.docx — a file that never
# ships. So in a clone the fixture can only ever skip, which makes shipping it
# pure leak with zero test value. It loads lazily inside that one test (not at
# import like the other goldens), so removing it costs CI nothing.
PRUNE_RELPATHS = (
    'tests/fixtures/content_alkami.json',
)

_STALE_TOP_LEVEL = (
    'CLAUDE.md', 'docs',                       # un-shipped 2026-08-08
    'qa', 'dist',                              # never shipped; harness + build output
    'scheduler_state.json', 'schedule_log.txt', 'job_tracker.json', 'job_tracker.html',
    'to_process.json', 'listings.json', 'details_manual.json',
    'geo_cache.json', 'salary_cache.json', 'notified.json',
    'raw_applied.json', 'scrape_meta.json', 'scrape_raw_sample.json',
    'tracking.csv', 'candidates.csv', 'archive_index.csv', 'archived.csv',
    'New', 'Applied', 'Skipped', 'Backups', 'logs', 'cache',
)

# Never copied, wherever they appear inside an included directory.
PRUNE_DIRS = {'__pycache__', '.pytest_cache', '.git', 'node_modules', '.venv', 'venv'}

# The portable interpreter. Named here because THREE places need to agree on it:
# verify() skips it, the file count excludes it, and add_python_bundle() copies it.
BUNDLE_DIR = 'python'
PRUNE_SUFFIXES = ('.pyc', '.pyo', '.log', '.tmp', '.bak', '.corrupt')

# config/ is special: ONLY the shipped defaults and the .example templates go.
# A real adzuna.json / git.json / profile.json holds keys and personal details.
#
# This is also why the BETA chip on a dev copy needs no "strip the badge" step here: the
# badge is not in the code at all. It is instance.json's `label`, and instance.json is
# neither in this allowlist nor (see FORBIDDEN_PATHS) permitted to exist in an export — so
# a release renders no badge by construction rather than by remembering to remove one.
# tests/test_release.py asserts both halves.
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
    r'(^|/)schedule_log\.txt$',
    r'(^|/)config/(adzuna|git|notify|anthropic|instance|runtime|profile|search_urls|skills|'
    r'skill_aliases|active_profile|ghost_flags|gui_settings|summary_rules)\.json$',
    # config/profiles/ — saved per-person profiles. build() deletes the whole
    # directory, but the guard must catch it independently: build() is the thing
    # under test, and this exact file (real name, phone, ntfy topic) reached the
    # staged ship tree once already because the sweep skipped directories.
    r'(^|/)config/profiles/',
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

# Extension-less text files. Without this the scanner silently skipped LICENSE,
# .gitignore and friends — a whole class of shipped file that was never read,
# by either the secret check or the PII check. Found 2026-08-08 by a test that
# planted a phone number in LICENSE and got a clean verdict back.
TEXT_NAMES = {'LICENSE', 'LICENCE', 'NOTICE', 'COPYING', 'README', 'AUTHORS',
              'Dockerfile', 'Makefile', 'Procfile',
              '.gitignore', '.gitattributes', '.editorconfig', '.dockerignore'}


def _is_text(rel):
    """True if the scanner should read this file's contents."""
    base = rel.rsplit('/', 1)[-1]
    return rel.lower().endswith(TEXT_SUFFIXES) or base in TEXT_NAMES


# --------------------------------------------------------------------------
# The PII gate
# --------------------------------------------------------------------------
# Two layers, for one reason: THIS FILE SHIPS. A denylist spelling out the real
# name, phone and email would publish the exact strings it exists to suppress —
# on the first release, forever, in git history. So no real value appears below,
# and the gate flags this file too if one ever gets pasted in.
#
#   Layer 1 (PII_PATTERNS) describes the SHAPE of identifying data, so no real
#   value appears here. Runs everywhere, CI included.
#   Layer 2 (_local_identity) reads the real values from the git-ignored
#   config/profile.json on the machine doing the release. Present when the owner
#   ships, absent in CI — which is fine, because shipping is what it guards.
#
# Generic placeholders are explicitly allowed: "Houston" with no street or ZIP is
# a default location, not an identity. A ZIP, a phone, an email, a LinkedIn
# handle or a personal resume filename is not.
PII_PATTERNS = [
    (r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}', 'phone number'),
    (r'(?<![\d.])\d{3}[-.]\d{3}[-.]\d{4}(?![\d.])', 'phone number'),
    (r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', 'email address'),
    (r'linkedin\.com/in/[A-Za-z0-9\-]+', 'LinkedIn profile'),
    (r'[Cc]:[\\/]{1,2}Users[\\/]{1,2}[A-Za-z0-9._\-]+', 'Windows user path'),
    (r'\b[A-Z][a-zA-Z]+,\s*(?:[A-Z][a-z]+|[A-Z]{2})\s+\d{5}(?:-\d{4})?\b', 'postal address'),
    (r'\b[A-Z][a-z]+_[A-Z][a-z]+_Resume[_.]', 'personal resume filename'),
    (r'\b[A-Z]{3,}_[A-Z]{3,}_RESUME\b', 'personal resume filename'),
]

# Deliberate placeholders. Compared case-insensitively against the MATCHED TEXT
# only, so allowing 'example.com' does not whitelist a whole file.
PII_ALLOW = (
    'jane doe', 'jane_doe', 'jane.doe', 'john doe', 'john_doe', 'john.doe',
    'first_last', 'first last', 'firstname', 'first_name',
    'example.com', 'example.org', 'example.net', '@example', 'user@', 'someone@',
    'noreply@', 'no-reply@', 'you@', 'name@', 'email@', 'test@',
    'change-me', 'changeme', 'yourname', 'your.name', 'youruser', 'your-handle',
    '(555)', '555-555', '123-456-7890', '(123)',
    'linkedin.com/in/your', 'linkedin.com/in/jane', 'linkedin.com/in/john',
    'c:/users/<', 'c:\\users\\<', 'users/username', 'users\\username',
    'users/youruser', 'users\\youruser', 'users/<', 'users\\<',
)

# The one file where a real name is the point. Attribution is what a copyright
# line is FOR — scrubbing it would defeat the licence, not protect anyone.
PII_EXEMPT_LINES = {
    'LICENSE': re.compile(r'^\s*Copyright\s*\(c\)', re.I),
}

# profile.json fields that hold identity worth scrubbing, as (dotted path, label).
IDENTITY_FIELDS = [
    ('identity.full_name', 'real name'),
    ('identity.resume_prefix', 'resume filename prefix'),
    ('git.name', 'real name'),
    ('git.email', 'personal email'),
    ('notify.topic', 'private ntfy topic'),
]
# List-valued fields: every entry becomes its own needle.
IDENTITY_LIST_FIELDS = [
    ('identity.past_companies', 'real employer'),
    ('identity.real_titles', 'real job title'),
]


def _dig(d, dotted):
    for part in dotted.split('.'):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def _profile_path():
    """Where the real profile.json lives on THIS machine, or '' if there isn't one.

    Honours the data-root split (pipelib.cfg overlays <data>/config on the code
    dir). pipelib is imported defensively: release.py must keep working in CI,
    where the data root is absent and pipelib may not resolve one.
    """
    env = os.environ.get('JOBAGENT_PROFILE', '')
    if env and os.path.isfile(env):
        return env
    try:
        import pipelib
        p = pipelib.cfg('profile.json')
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    p = os.path.join(HERE, 'config', 'profile.json')
    return p if os.path.isfile(p) else ''


def _local_identity(profile_path=None):
    """[(needle, label)] of real identity strings to scrub. Empty list in CI.

    Short and generic values are dropped: a one-word company like 'HP' would
    match inside 'HTTP' and turn the gate into noise, and a gate that cries wolf
    gets bypassed, which is worse than not having one.
    """
    path = profile_path if profile_path is not None else _profile_path()
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            prof = json.load(fh)
    except (OSError, ValueError):
        return []

    needles = []

    def add(val, label):
        val = (val or '').strip()
        if len(val) < 4 or val.lower() in PII_ALLOW:
            return
        needles.append((val, label))
        if label == 'real name' and ' ' in val:
            # A full name must also be caught as a filename token: it ships far
            # more often underscored, inside a resume filename, than as prose.
            needles.append((val.replace(' ', '_'), label))
            # ...and prose almost never uses the full name. Comments say "runs on
            # <First>'s machine" and "per <First>", which is still the owner's
            # identity and which full-name matching sailed straight past — 19
            # occurrences survived the first pass this way. Word-bounded and
            # length-gated, so a short or common token cannot carpet the report.
            for part in val.split():
                if len(part) >= 4:
                    needles.append((part, 'real name (given/family)'))

    for dotted, label in IDENTITY_FIELDS:
        add(_dig(prof, dotted), label)
    for dotted, label in IDENTITY_LIST_FIELDS:
        for item in (_dig(prof, dotted) or []):
            add(item if isinstance(item, str) else '', label)

    seen, out = set(), []
    for needle, label in needles:
        if needle.lower() not in seen:
            seen.add(needle.lower())
            out.append((needle, label))
    return out


# Usernames that are obviously stand-ins, checked against the trailing component
# of a Windows path. Substring-matching these through PII_ALLOW would be far too
# loose — 'you' appears inside plenty of real strings — so the path patterns get
# a dedicated, exact-match placeholder list instead.
PLACEHOLDER_USERS = {'you', 'youruser', 'yourname', 'your-name', 'username',
                     'user', 'name', 'me', '...', '<you>', '%username%'}

_WIN_USER = re.compile(r'[Cc]:[\\/]{1,2}Users[\\/]{1,2}([A-Za-z0-9._\-]+)')


def _allowed(match_text):
    low = match_text.lower()
    if any(tok in low for tok in PII_ALLOW):
        return True
    m = _WIN_USER.match(match_text)
    return bool(m) and m.group(1).lower() in PLACEHOLDER_USERS


_needle_cache = {}


def _needle_re(needle):
    """Case-insensitive, word-bounded matcher for one identity needle.

    Bounded so a family name cannot fire inside an unrelated word. The edges are
    conditional on purpose: a prefix needle ends in '_' ('First_Last_Resume_'),
    and a trailing boundary there would refuse to match the very thing it is for
    — 'First_Last_Resume_Acme.docx'. So the guard is only applied on an edge that
    is actually alphanumeric.
    """
    rx = _needle_cache.get(needle)
    if rx is None:
        pat = re.escape(needle)
        if needle[:1].isalnum():
            pat = r'(?<![A-Za-z0-9])' + pat
        if needle[-1:].isalnum():
            pat = pat + r'(?![A-Za-z0-9])'
        rx = _needle_cache[needle] = re.compile(pat, re.I)
    return rx


def scan_pii(rel, body, identity):
    """Problems for one text file. Line-numbered, because 'somewhere in this
    2000-line map' is not an actionable failure message."""
    problems = []
    exempt = PII_EXEMPT_LINES.get(rel)
    for lineno, line in enumerate(body.splitlines(), 1):
        if exempt and exempt.search(line):
            continue
        for pat, label in PII_PATTERNS:
            for m in re.finditer(pat, line):
                if not _allowed(m.group(0)):
                    problems.append('%s: %s:%d (%s)' % (label, rel, lineno, m.group(0)[:60]))
        for needle, label in identity:
            if _needle_re(needle).search(line):
                problems.append('%s: %s:%d (%s)' % (label, rel, lineno, needle))
    return problems


def verify(root, identity=None):
    """Return a list of problems with a built export. Empty list == clean.

    Deliberately re-derived from the tree on disk rather than from what build()
    thinks it copied: the point is to catch the case where build() is wrong.

    `identity` overrides the profile-derived needles (tests pass a fake persona;
    None means "read this machine's profile.json", which is what a real run does).
    """
    problems = []
    if identity is None:
        identity = _local_identity()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        # The bundled interpreter is third-party, not ours, and is exempt BY PATH
        # rather than by any of the content rules below. Scanning it is not a
        # near-miss, it is guaranteed noise: CPython + pip + setuptools ship ~30
        # upstream author emails, DFARS clause numbers shaped like phone numbers,
        # a US city/state/ZIP line in three licences, and the stdlib .zip, which
        # trips the binary-document rule. None of it belongs to the person
        # shipping this, none of it can leak, and 40 lines of it bury a real hit.
        # (Written without quoting the offending strings on purpose - this file
        # ships, so an example here would flag the scanner in its own scan.)
        #
        # This must live HERE, not only in the copy order. add_python_bundle()
        # runs after verify() on a FIRST build, but the export folder is reused:
        # on every later run the previous bundle is already sitting in it when
        # verify() walks the tree. (Found the hard way, 2026-08-08.)
        if os.path.abspath(dirpath) == os.path.abspath(root):
            dirnames[:] = [d for d in dirnames if d != BUNDLE_DIR]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, '/')

            for pat in FORBIDDEN_PATHS:
                if re.search(pat, rel):
                    problems.append('forbidden path: %s' % rel)
                    break

            if rel.lower().endswith(FORBIDDEN_SUFFIXES) and not rel.startswith(BINARY_EXEMPT_PREFIX):
                problems.append('binary document: %s' % rel)

            # The PATH is PII too, and it is the one place a BINARY file can leak
            # it — a linter fixture called '<First>_<Last>_Resume_Acme.docx' has no
            # scannable text but publishes the name in the directory listing.
            # Found 2026-08-08: exactly that file sat in tests/fixtures/docx/,
            # binary-exempt and therefore invisible to every other check here.
            problems.extend(scan_pii(rel, rel, identity))

            if _is_text(rel):
                try:
                    with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                        body = fh.read()
                except OSError:
                    continue
                for pat, label in SECRET_PATTERNS:
                    if re.search(pat, body):
                        problems.append('%s in %s' % (label, rel))
                problems.extend(scan_pii(rel, body, identity))
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

    # Stale content from an EARLIER release, or hand-copied in. INCLUDE_DIRS are
    # rmtree'd above so they self-clean, but config/ is filtered rather than
    # replaced, and dropping a name from INCLUDE_FILES (CLAUDE.md) or INCLUDE_DIRS
    # (docs/) only stops the copy — it does not remove what is already sitting in
    # the export folder. Without this, un-shipping a file leaves it shipped.
    # (Found for real: a hand-built TopRat/ still held nine live config files and
    # scheduler_state.json, ignored by git and therefore invisible to `git status`.)
    for rel in PRUNE_RELPATHS:
        doomed = os.path.join(out_dir, *rel.split('/'))
        if os.path.isfile(doomed):
            os.remove(doomed)

    for name in list(_STALE_TOP_LEVEL):
        stale = os.path.join(out_dir, name)
        if os.path.isdir(stale):
            shutil.rmtree(stale, ignore_errors=True)
        elif os.path.isfile(stale):
            os.remove(stale)

    # A nested export folder and a stray release zip. Both are what an earlier run
    # leaves behind when release.py (or the tar step) is invoked from INSIDE the
    # export: `--out TopRat` with the cwd already TopRat builds TopRat/TopRat, and
    # the zip command lands the archive next to it. Neither is tracked by git, so
    # `git status` looks clean and nothing reaches GitHub - but verify() walks the
    # DISK, so the nested tree's own python/ bundle, its .docx fixtures and its
    # LICENSE copyright line are all scanned as export content and the gate fails
    # on ~60 problems that are not real. Self-healing here beats a runbook warning
    # nobody re-reads. (Found 2026-08-19, left by the 1.1.0 build.)
    #
    # Only the export ROOT is swept: a .zip deeper in the tree is someone's fixture,
    # and the interpreter's own python/python312.zip must survive.
    nested = os.path.join(out_dir, os.path.basename(os.path.abspath(out_dir)))
    if os.path.isdir(nested):
        shutil.rmtree(nested, ignore_errors=True)
    for fn in os.listdir(out_dir):
        full = os.path.join(out_dir, fn)
        if fn.lower().endswith('.zip') and os.path.isfile(full):
            os.remove(full)

    cfg_src = os.path.join(source, 'config')
    cfg_dst = os.path.join(out_dir, 'config')
    if os.path.isdir(cfg_dst):
        for fn in os.listdir(cfg_dst):
            full = os.path.join(cfg_dst, fn)
            if os.path.isdir(full):
                # NOTHING nested is ever shippable — the copy below is a flat
                # listdir of files. A subdirectory here can only have arrived by
                # hand or, far more likely, because the app was RUN from inside
                # the export: with no user_data_* folder present,
                # pipelib.resolve_data_root() falls all the way through to HERE,
                # so the export becomes its own data root and setup writes
                # config/profiles/<name>.json straight into the ship tree.
                # The old file-only sweep skipped directories, so that survived
                # every rebuild — real name, phone, ntfy topic and all.
                shutil.rmtree(full, ignore_errors=True)
            elif os.path.isfile(full) and not _is_config_shippable(fn):
                os.remove(full)
    os.makedirs(cfg_dst, exist_ok=True)
    kept = 0
    if os.path.isdir(cfg_src):
        for fn in sorted(os.listdir(cfg_src)):
            if os.path.isfile(os.path.join(cfg_src, fn)) and _is_config_shippable(fn):
                shutil.copy2(os.path.join(cfg_src, fn), os.path.join(cfg_dst, fn))
                kept += 1
    say('  %-14s %d files (templates + shipped defaults only)' % ('config/', kept))

    # Counts OUR files. A bundle left by an earlier build would otherwise add ~1700
    # and make the total meaningless as a "did the export change?" signal.
    bundle = os.path.abspath(os.path.join(out_dir, BUNDLE_DIR))
    total = sum(len(f) for d, _, f in os.walk(out_dir)
                if not os.path.abspath(d).startswith(bundle))
    return total, verify(out_dir)


# --------------------------------------------------------------------------
# The bundled interpreter
# --------------------------------------------------------------------------

def add_python_bundle(out_dir, source=HERE):
    """Copy the portable interpreter into a built export. Returns a status line.

    Why this is NOT an entry in INCLUDE_DIRS, which would have been the obvious
    place for it:

      * verify() rejects binaries, and the bundle is 800+ files of .exe/.dll/.pyd.
        Copied before the scan it would fail the gate on content that is not ours,
        cannot contain anyone's data, and would have to be exempted anyway.
      * `--check` discards its output, so a dry run would spend its time copying
        51 MB into a temp folder for nothing.

    So it runs after the export has verified clean, on real builds only.

    How it reaches a user: the ZIP of this folder, never git. `.gitignore` ships
    with the export and lists `python/`, so neither repo carries an interpreter
    that rewrites every file on each rebuild — while the folder actually handed
    over is complete and needs nothing installed.
    """
    src = os.path.join(source, BUNDLE_DIR)
    if not os.path.isdir(src):
        return ('  %-14s SKIPPED - no bundle here. Build one with\n'
                '                 tools\\Make Portable Python.bat, then re-run, or the\n'
                '                 export needs Python already installed.' % (BUNDLE_DIR + '/',))
    dst = os.path.join(out_dir, BUNDLE_DIR)
    # Replaced wholesale, not merged: a stale copy would keep the OLD package set
    # (an un-upgraded pywebview, a removed dependency) silently alive in the ship
    # folder, and that is exactly the kind of drift nobody goes looking for.
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    n = _copy_tree(src, dst)
    return '  %-14s %d files (bundled interpreter; git-ignored, ships in the zip)' % (
        BUNDLE_DIR + '/', n)


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
    p.add_argument('--out', help='where to build, relative to the project root (e.g. TopRat)')
    p.add_argument('--check', action='store_true',
                   help='build to a temp folder, verify, report, discard')
    p.add_argument('--push', action='store_true', help='commit + push after a clean build')
    p.add_argument('--remote', default='', help='git remote for --push on a first run')
    p.add_argument('--message', default='', help='commit message for --push')
    p.add_argument('--no-python', action='store_true',
                   help='do not copy the python/ bundle into the export '
                        '(default is to copy it, so the export needs nothing installed)')
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

        # After the gate, never before — see add_python_bundle().
        if not a.no_python:
            print(add_python_bundle(out))

        if a.push:
            msg = a.message or ('Top Rat %s' % _version())
            ok, note = publish(out, a.remote, msg)
            print(('Published: ' if ok else 'Publish FAILED: ') + note)
            return 0 if ok else 1

        print('Next: cd %s && git add -A && git commit && git push' % out)
        print('      (git carries the code; zip the folder for the interpreter)')
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main() or 0)
