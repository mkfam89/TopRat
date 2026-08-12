#!/usr/bin/env python3
"""bundle_python.py — put a private Python interpreter inside the app folder.

Why
---
A shipped copy must not depend on what the user happens to have installed.
"Install Python 3.8+, and tick Add to PATH" is the single biggest drop-off in
INSTALL.md, and on macOS the system Python is either absent or the wrong one.
With a bundle present the launchers find it first and the user installs nothing.

Both launchers ALREADY prefer a bundle — this only supplies what they look for:

    Windows          python\\python.exe        (Start Here.bat, line 1 of its search)
    macOS / Linux    python/bin/python3       (Start Here.command)

Source: python-build-standalone (astral-sh), the same redistributable runtime uv
ships. Its `install_only` tarballs unpack to exactly the two layouts above, which
is why one script covers every platform. The older Windows-only
`tools\\Make Portable Python.bat` uses python.org's *embeddable* zip instead —
that build disables `import site` and omits pip, so it needs a `._pth` rewrite
and a get-pip bootstrap. This does not, so prefer this script.

RUN IT ON THE PLATFORM YOU ARE BUILDING FOR. The download could be fetched
anywhere, but the pip step compiles/selects native wheels (pywebview pulls in
pyobjc on macOS), and those cannot be chosen correctly from a different OS.

Usage
-----
    python src/ops/bundle_python.py                    # bundle into ./python
    python src/ops/bundle_python.py --into dist/TopRat # bundle into that folder
    python src/ops/bundle_python.py --triple aarch64-apple-darwin --no-pip
    python src/ops/bundle_python.py --list             # show what would be downloaded

Network: this is the ONE script here that needs it (GitHub API + release asset).
Everything else on the script path stays offline.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = _paths.ROOT

# Pinned minor version. Bump deliberately: it decides what every shipped copy
# runs, and a silent "latest" would change the runtime under users between two
# builds of the same app version.
PY_SERIES = '3.12'

API_LATEST = 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'

# The optional packages worth pre-installing, so a bundled copy has the app's
# full feature set with no pip step for the user. Kept in step with
# requirements.txt; jsonschema and python-jobspy are omitted on purpose — the
# first is a nicety and the second is a large tree for one job source.
BUNDLED_PACKAGES = ['python-docx', 'pywebview']


def target_triple(system=None, machine=None):
    """The python-build-standalone platform triple for this machine.

    Raises rather than guessing: a wrong triple downloads an interpreter that
    cannot run, and the failure would not surface until a user double-clicks.
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arm = machine in ('arm64', 'aarch64')
    intel = machine in ('x86_64', 'amd64')

    if system == 'darwin':
        if arm:
            return 'aarch64-apple-darwin'
        if intel:
            return 'x86_64-apple-darwin'
    elif system == 'windows':
        if intel:
            return 'x86_64-pc-windows-msvc'
    elif system == 'linux':
        if intel:
            return 'x86_64-unknown-linux-gnu'
        if arm:
            return 'aarch64-unknown-linux-gnu'
    raise RuntimeError('no python-build-standalone build for %s/%s — bundle by hand '
                       'or run with --triple' % (system, machine))


def python_binary(root):
    """Path of the interpreter inside a bundle at `root`, per platform layout."""
    win = os.path.join(root, 'python.exe')
    nix = os.path.join(root, 'bin', 'python3')
    return win if os.path.exists(win) else nix


def find_asset(triple, series=PY_SERIES, api=API_LATEST):
    """(name, url) of the install_only tarball for this triple. Raises if absent.

    Resolved from the API rather than a hardcoded URL: the release tag is a date
    that changes every few weeks, so a pinned link rots and 404s. The PYTHON
    version stays pinned by `series` — it is the tag that floats, not the runtime.
    """
    req = urllib.request.Request(api, headers={'Accept': 'application/vnd.github+json',
                                               'User-Agent': 'top-rat-bundler'})
    with urllib.request.urlopen(req, timeout=60) as r:
        rel = json.load(r)

    want_mid = '-%s-install_only.tar.gz' % triple
    hits = [a for a in rel.get('assets', [])
            if a.get('name', '').startswith('cpython-%s.' % series)
            and a.get('name', '').endswith(want_mid)]
    if not hits:
        raise RuntimeError('release %s has no cpython-%s.* %s asset'
                           % (rel.get('tag_name', '?'), series, triple))
    # Highest patch version, read from 'cpython-3.12.10+2026...' .
    def patch(a):
        try:
            return int(a['name'].split('-')[1].split('+')[0].split('.')[2])
        except (IndexError, ValueError):
            return -1
    best = sorted(hits, key=patch)[-1]
    return best['name'], best['browser_download_url']


def download(url, dest, quiet=False):
    if not quiet:
        print('  downloading %s' % os.path.basename(dest))
    req = urllib.request.Request(url, headers={'User-Agent': 'top-rat-bundler'})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, 'wb') as fh:
        shutil.copyfileobj(r, fh)
    return dest


def _safe_extract(tar, path):
    """Extract, refusing any member that would escape `path`.

    A tarball is arbitrary data from the network; without this check a crafted
    '../..' member writes anywhere the user can. Python 3.12 has `filter='data'`
    for this, but the script must also run on the 3.8 the app supports.
    """
    base = os.path.realpath(path)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(path, m.name))
        if not (target == base or target.startswith(base + os.sep)):
            raise RuntimeError('refusing tar member outside the target: ' + m.name)
        if m.issym() or m.islnk():
            link = os.path.realpath(os.path.join(os.path.dirname(target), m.linkname))
            if not (link == base or link.startswith(base + os.sep)):
                raise RuntimeError('refusing link outside the target: ' + m.name)
    tar.extractall(path)


def install_packages(py, packages=BUNDLED_PACKAGES, quiet=False):
    """pip install into the bundle. Best effort — returns (ok_list, failed_list).

    Never fatal: the app runs with none of these (see requirements.txt), so a
    package that will not build must not cost the user a working interpreter.
    """
    ok, bad = [], []
    for pkg in packages:
        if not quiet:
            print('  pip install %s' % pkg)
        r = subprocess.run([py, '-m', 'pip', 'install', '--quiet',
                            '--disable-pip-version-check', pkg],
                           capture_output=True, text=True)
        (ok if r.returncode == 0 else bad).append(pkg)
    return ok, bad


def bundle(into, triple=None, with_pip=True, quiet=False):
    """Create <into>/python. Returns the interpreter path. Replaces any existing bundle."""
    triple = triple or target_triple()
    name, url = find_asset(triple)
    if not quiet:
        print('  %s' % name)

    target = os.path.join(into, 'python')
    tmp = tempfile.mkdtemp(prefix='toprat_py_')
    try:
        tarball = download(url, os.path.join(tmp, name), quiet=quiet)
        with tarfile.open(tarball, 'r:gz') as tf:
            _safe_extract(tf, tmp)
        unpacked = os.path.join(tmp, 'python')
        if not os.path.isdir(unpacked):
            raise RuntimeError('tarball did not contain a python/ directory')

        # Replace last: an interrupted download must not leave the app with a
        # half-written interpreter, which fails far more confusingly than none.
        if os.path.isdir(target):
            shutil.rmtree(target)
        os.makedirs(into, exist_ok=True)
        shutil.move(unpacked, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    py = python_binary(target)
    if not os.path.exists(py):
        raise RuntimeError('no interpreter at the expected path: ' + py)
    if os.name != 'nt':
        os.chmod(py, 0o755)

    r = subprocess.run([py, '-c', 'import sys; print(sys.version.split()[0])'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('the bundled interpreter does not run: ' + (r.stderr or '').strip())
    if not quiet:
        print('  bundled Python %s at %s' % (r.stdout.strip(), target))

    if with_pip:
        ok, bad = install_packages(py, quiet=quiet)
        if not quiet and bad:
            print('  could not install: %s (the app still runs without them)' % ', '.join(bad))
    return py


def main():
    p = argparse.ArgumentParser(description='Bundle a private Python into the app folder.')
    p.add_argument('--into', default=HERE, help='folder to create python/ inside (default: the app folder)')
    p.add_argument('--triple', default='', help='override the platform triple')
    p.add_argument('--no-pip', action='store_true', help='skip installing the optional packages')
    p.add_argument('--list', action='store_true', help='print the asset that would be downloaded, then stop')
    a = p.parse_args()

    try:
        triple = a.triple or target_triple()
        if a.list:
            name, url = find_asset(triple)
            print('triple: %s\nasset : %s\nurl   : %s' % (triple, name, url))
            return 0
        print('Bundling Python %s (%s)' % (PY_SERIES, triple))
        bundle(os.path.abspath(a.into), triple=triple, with_pip=not a.no_pip)
        print('\nDone. The launchers will now use this copy instead of the system Python.')
        return 0
    except Exception as e:
        print('\nCould not bundle Python: %s' % e)
        print('The app still works — users just have to install Python themselves.')
        return 1


if __name__ == '__main__':
    sys.exit(main() or 0)
