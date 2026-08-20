#!/usr/bin/env python3
"""package.py — build the release zip a user actually downloads.

Why this exists separately from release.py
------------------------------------------
`release.py` produces the sanitized SOURCE tree that goes in the git repo.
That is the wrong thing to hand a non-technical user for two reasons:

  1. The repo is private. "Download the ZIP from GitHub" needs an account and a
     collaborator invite, which most people you would give this to do not have.
     A Release asset has a plain download URL.
  2. A clone has no interpreter, so the user is back to installing Python.

The two artifacts therefore differ on purpose, and in one place: `ZIP_EXCLUDE`
below drops `tests/` and `pytest.ini` from the download. The repo keeps them —
its CI runs them — while the download, which ships no pytest, does not carry a
suite nobody can run. See the comment on ZIP_EXCLUDE for the full reasoning.

So a release zip = the sanitized tree + a private `python/` bundle. The bundle is
built here and NEVER committed: it is ~40 MB of platform-specific binaries, it
would sit in git history on every clone forever, and it differs per OS. Release
assets are the right vehicle; the repo stays small and cross-platform.

Usage
-----
    python src/ops/package.py                       # zip for THIS platform, with Python
    python src/ops/package.py --no-python           # small zip, user installs Python
    python src/ops/package.py --python-from ./python  # reuse a bundle you already built
    python src/ops/package.py --out dist

Run it on the platform you are building for — see bundle_python.py for why.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import os
import platform
import shutil
import sys
import tempfile
import zipfile

import release

HERE = _paths.ROOT
DIST = os.path.join(HERE, 'dist')


def _version():
    try:
        from _version import VERSION
        return VERSION
    except Exception:
        return '0.0.0'


def platform_tag(system=None, machine=None):
    """'windows-x64' / 'macos-arm64' / 'linux-x64' — what goes in the zip name.

    Deliberately friendlier than the build triple: this string is read by someone
    choosing a download, not by a package manager.
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    osname = {'darwin': 'macos', 'windows': 'windows', 'linux': 'linux'}.get(system, system)
    arch = 'arm64' if machine in ('arm64', 'aarch64') else (
        'x64' if machine in ('x86_64', 'amd64') else machine)
    return '%s-%s' % (osname, arch)


# Developer-only packages that tools\run_tests.bat installs into the working
# python/ bundle (requirements-dev.txt). They are not imported by the app, so a
# downloaded release must not carry them: it is dead weight, and shipping a test
# runner invites someone to run the suite against a tree that has no fixtures.
# Names are top-level import names / console scripts; dist-info and .pyc dirs are
# matched by prefix so pytest-8.4.1.dist-info goes too.
DEV_ONLY = ('pytest', '_pytest', 'pluggy', 'iniconfig', 'py.test')

# The same reasoning, one level up: the TEST SUITE itself.
#
# release.py exports tests/ to the TopRat git repo on purpose — someone who
# clones it to maintain the thing wants the safety net, and the exported
# .github/workflows/ci.yml exists to run it. A zip is a different audience: it
# goes to a person who double-clicks Start Here.bat, and for them the suite is
# ~2 MB of files they cannot execute, because DEV_ONLY above has already stripped
# the only thing that could run them. Shipping a test suite with no test runner is
# not a safety net, it is a puzzle.
#
# So the split is by ARTIFACT, not by allowlist: repo gets tests, download does
# not. qa/ is in neither — it is excluded from the export by release.py's
# allowlist and therefore never reaches this function at all.
ZIP_EXCLUDE = ('tests', 'pytest.ini')


def _strip_zip_only(stage, quiet=False):
    """Remove the repo-only paths from a staged tree on its way into a zip.

    Runs on the STAGED copy, never on the source tree — the same staged copy
    release.verify() is about to re-scan, so anything removed here is provably
    absent from the artifact rather than merely un-listed.
    """
    gone = []
    for name in ZIP_EXCLUDE:
        target = os.path.join(stage, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            gone.append(name + '/')
        elif os.path.isfile(target):
            os.remove(target)
            gone.append(name)
    if gone and not quiet:
        print('  %-14s %s (in the repo export, not in the download)'
              % ('stripped', ', '.join(gone)))
    return gone


def _is_dev_only(name):
    """True for a site-packages entry that belongs to the dev-only install."""
    stem = name.lower()
    for ext in ('.exe', '.py', '.pyc', '.dist-info', '.egg-info'):
        if stem.endswith(ext):
            stem = stem[:-len(ext)]
    stem = stem.split('-')[0]          # pytest-8.4.1.dist-info -> pytest
    return stem in DEV_ONLY


def _prune_dev(_dirpath, names):
    """shutil.copytree ignore-callback: drop the dev-only packages."""
    return [n for n in names if _is_dev_only(n)]


def _copy_python(src, dst_root, quiet=False):
    """Copy an existing python/ bundle into the staged tree. Returns file count.

    The working bundle doubles as the developer's test interpreter, so pytest
    may be sitting in its site-packages. Strip it on the way out — see DEV_ONLY.
    """
    dst = os.path.join(dst_root, 'python')
    # symlinks=True matters on macOS: the standalone runtime uses symlinked
    # bin/python3 -> python3.12, and resolving those would double the size and
    # can break the framework layout.
    shutil.copytree(src, dst, symlinks=True, ignore=_prune_dev)
    n = sum(len(f) for _, _, f in os.walk(dst))
    if not quiet:
        print('  python/        %d files (bundled interpreter)' % n)
    return n


# Paths that MUST extract executable, whatever the build host thinks. NTFS has no
# POSIX exec bit — os.stat() reports 0o666 for every file on Windows — so copying
# the host's mode straight through means a zip built on Windows ships
# `Start Here.command` and `python/bin/python3` non-executable, and the person who
# unpacks it on macOS gets "permission denied" with nothing to go on. Decide from
# the path, not from the filesystem we happen to be standing on.
EXEC_SUFFIXES = ('.command', '.sh')
EXEC_DIRS = ('python/bin/',)


def _zip_mode(rel, st_mode):
    """The POSIX mode to record for `rel`. Keeps the file-type bits (S_IFREG)."""
    posix = rel.replace(os.sep, '/')
    perm = st_mode & 0o777
    if posix.endswith(EXEC_SUFFIXES) or posix.startswith(EXEC_DIRS):
        perm = 0o755
    elif not perm:
        perm = 0o644                    # a filesystem that reported nothing useful
    return (st_mode & 0xFFFF & ~0o777) | perm


def _zip_tree(root, zip_path, quiet=False):
    """Zip `root` with every path prefixed by the zip's own stem.

    The prefix is what stops an unzip from spraying 300 files into Downloads —
    the user gets one folder named after the app, which is also the folder the
    launchers expect to sit in.
    """
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    os.makedirs(os.path.dirname(zip_path) or '.', exist_ok=True)
    n = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in release.PRUNE_DIRS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if os.path.islink(full):
                    continue        # see note below; links are re-made on extract
                rel = os.path.relpath(full, root)
                zi = zipfile.ZipInfo.from_file(full, os.path.join(stem, rel))
                zi.compress_type = zipfile.ZIP_DEFLATED
                # Preserve the executable bit. A zip drops POSIX mode by default,
                # so an extracted Start Here.command / python3 would not run and
                # the user would get "permission denied" with no idea why.
                zi.external_attr = _zip_mode(rel, os.stat(full).st_mode) << 16
                with open(full, 'rb') as fh:
                    z.writestr(zi, fh.read())
                n += 1
    if not quiet:
        print('  %d files -> %s (%.1f MB)' % (n, zip_path, os.path.getsize(zip_path) / 1e6))
    return n


def build_zip(out_dir=DIST, with_python=True, python_from='', tag='', quiet=False):
    """Build the release zip. Returns (zip_path, problems).

    A non-empty `problems` means NOTHING was written: the sanitized tree failed
    its own guard, and a zip is exactly the artifact you cannot un-publish.
    """
    say = (lambda *a: None) if quiet else print
    tag = tag or platform_tag()
    stage = tempfile.mkdtemp(prefix='toprat_pkg_')
    try:
        say('Building the sanitized tree')
        _, problems = release.build(stage, quiet=quiet)
        if problems:
            return '', problems

        # Straight after build(), before the interpreter goes in: the tree is
        # still small and the removal is cheap to reason about here.
        _strip_zip_only(stage, quiet=quiet)

        if with_python:
            src = python_from or os.path.join(HERE, 'python')
            if os.path.isdir(src):
                _copy_python(src, stage, quiet=quiet)
            else:
                import bundle_python
                say('No python/ found — downloading one')
                bundle_python.bundle(stage, quiet=quiet)

        name = 'TopRat-%s-%s.zip' % (_version(), tag)
        zip_path = os.path.join(out_dir, name)
        # Re-verify the STAGED tree, not the earlier return value: the python
        # bundle was added after that check, and this is the last look before
        # something leaves the machine.
        late = release.verify(stage)
        if late:
            return '', late
        _zip_tree(stage, zip_path, quiet=quiet)
        return zip_path, []
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description='Build the downloadable release zip.')
    p.add_argument('--out', default=DIST, help='where to write the zip (default: dist/)')
    p.add_argument('--no-python', action='store_true', help='do not bundle an interpreter')
    p.add_argument('--python-from', default='', help='use this existing python/ bundle')
    p.add_argument('--tag', default='', help='override the platform tag in the filename')
    a = p.parse_args()

    zip_path, problems = build_zip(out_dir=os.path.abspath(a.out),
                                   with_python=not a.no_python,
                                   python_from=a.python_from, tag=a.tag)
    if problems:
        print('\nFAILED — nothing was written. The tree is not shippable:')
        for x in problems[:25]:
            print('  * ' + x)
        if len(problems) > 25:
            print('  ... and %d more' % (len(problems) - 25))
        return 1

    print('\nAttach this to a GitHub Release:\n  %s' % zip_path)
    if a.no_python:
        print('No interpreter bundled — users must install Python (see INSTALL.md).')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
