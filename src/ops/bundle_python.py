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
import urllib.error
import urllib.request

HERE = _paths.ROOT

# Pinned minor version. Bump deliberately: it decides what every shipped copy
# runs, and a silent "latest" would change the runtime under users between two
# builds of the same app version.
PY_SERIES = '3.12'

API_LATEST = 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'

# The optional packages pre-installed into every download, so a shipped copy has
# its full feature set and NOTHING is fetched the first time a user opens the app.
# Kept in step with requirements.txt.
#
# python-jobspy is the one exception and it does not belong here — see
# ADDON_PACKAGES. Measured in a clean venv (2026-09-05): docx + pywebview is
# 47 MB, adding jsonschema makes it 51 MB, and adding python-jobspy makes it
# 321 MB. That last 270 MB is tls_client (89), pandas (79), numpy (79) and lxml
# (12), landing on every download on all three platforms, for one job source that
# a user may never enable.
BUNDLED_PACKAGES = ['python-docx', 'pywebview', 'jsonschema']

# Add-ons: shipped as their OWN zip beside the main download, unpacked into an
# existing install by the user. The main zip stays small; whoever wants LinkedIn
# gets it without a pip step either. Same platform rule as the bundle - the
# wheels are chosen by the bundled interpreter, so an add-on is built on, and only
# works on, the platform it was built for.
ADDON_PACKAGES = {'linkedin': ['python-jobspy']}


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

    Sends GITHUB_TOKEN when one is in the environment. Unauthenticated, the API
    allows 60 requests an hour PER IP, and GitHub-hosted runners share their
    addresses with everyone else building on that machine - so the "rare flake"
    this was written off as is really a shared bucket that any busy hour empties,
    and it killed a whole release leg on 2026-09-05 with `403: rate limit
    exceeded`. A token lifts the same call to 1000/hour and costs the workflow one
    `env:` line. No token, no header: a developer running this by hand needs no
    setup, and an invalid one would be worse than none.
    """
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'top-rat-bundler'}
    token = (os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or '').strip()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        # Say which of the two it is. "403" alone sends you looking for a
        # permissions problem that is not there.
        if e.code in (403, 429):
            raise RuntimeError(
                'the GitHub API refused the request (%s). Unauthenticated calls are '
                'capped at 60/hour per IP and CI runners share addresses; set '
                'GITHUB_TOKEN to lift it, or wait and retry.' % e.code)
        raise

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
    """Fetch the release asset. Deliberately NOT authenticated, unlike find_asset():
    the asset URL redirects to object storage, which rejects a request carrying an
    Authorization header it did not issue."""
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


def _sp_relpath(py, root):
    """Where site-packages lives, RELATIVE TO the folder that contains python/.

    Asked of the bundled interpreter rather than assembled from PY_SERIES, because
    the two layouts differ (`python/Lib/site-packages` on Windows,
    `python/lib/python3.12/site-packages` elsewhere) and the patch version floats.
    """
    r = subprocess.run([py, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('could not ask the bundle where site-packages is: ' + (r.stderr or '').strip())
    return os.path.relpath(r.stdout.strip(), root)


def _snapshot(sp):
    """{relative path: (size, mtime)} for everything under site-packages."""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(sp):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, sp)] = (st.st_size, st.st_mtime)
    return out


def build_addon(into, name, out_dir, quiet=False):
    """Build the `name` add-on zip from the bundle already sitting in `into`.

    Installs the add-on's packages INTO the existing bundle and zips the difference.
    Deliberately not `pip install --target` into an empty folder: --target resolves
    against nothing, so it re-downloads its own copy of anything the bundle already
    has and can pin a DIFFERENT version of it, which then overwrites the working one
    on unpack. Installing into the real bundle makes pip resolve against what is
    actually there, and the diff is exactly what the user is missing.

    **Order matters: build the main zip BEFORE calling this.** It mutates the
    bundle on purpose and does not put it back — the add-on's whole point is that
    its packages are not in the main download.

    The zip's entries are rooted at the app folder (`python/...`), so a user
    unpacks it over their install and the files land where the interpreter looks.
    """
    packages = ADDON_PACKAGES[name]
    root = os.path.abspath(into)
    py = python_binary(os.path.join(root, 'python'))
    if not os.path.exists(py):
        raise RuntimeError('no bundle to build an add-on against: ' + py)

    sp_rel = _sp_relpath(py, root)
    sp = os.path.join(root, sp_rel)
    before = _snapshot(sp)

    ok, bad = install_packages(py, packages=packages, quiet=quiet)
    if bad:
        raise RuntimeError('add-on %r could not install: %s' % (name, ', '.join(bad)))

    after = _snapshot(sp)
    changed = sorted(k for k, v in after.items() if before.get(k) != v)
    if not changed:
        raise RuntimeError('add-on %r installed nothing — already in the bundle?' % name)

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, 'TopRat-%s-%s-%s.zip'
                            % (_version_string(), name, _platform_tag()))
    import zipfile
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in changed:
            z.write(os.path.join(sp, rel), os.path.join(sp_rel, rel).replace(os.sep, '/'))
    if not quiet:
        print('  %s  (%.1f MB, %d files)'
              % (os.path.basename(zip_path), os.path.getsize(zip_path) / 1e6, len(changed)))
    return zip_path


def _version_string():
    sys.path.insert(0, os.path.join(HERE, 'src'))
    from _version import VERSION
    return VERSION


def _platform_tag():
    # package.py owns the naming; import it so an add-on can never be labelled
    # differently from the download it belongs to.
    sys.path.insert(0, os.path.join(HERE, 'src', 'ops'))
    import package
    return package.platform_tag()


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
    p.add_argument('--addon', default='', metavar='NAME',
                   help='build an add-on zip (%s) from the bundle already in --into, '
                        'instead of bundling. Run AFTER package.py, not before.'
                        % '/'.join(sorted(ADDON_PACKAGES)))
    p.add_argument('--out', default='dist', help='where --addon writes its zip (default: dist/)')
    p.add_argument('--list', action='store_true', help='print the asset that would be downloaded, then stop')
    a = p.parse_args()

    try:
        triple = a.triple or target_triple()
        if a.list:
            name, url = find_asset(triple)
            print('triple: %s\nasset : %s\nurl   : %s' % (triple, name, url))
            return 0
        if a.addon:
            if a.addon not in ADDON_PACKAGES:
                print('unknown add-on %r. Known: %s' % (a.addon, ', '.join(sorted(ADDON_PACKAGES))))
                return 1
            print('Building the %s add-on' % a.addon)
            build_addon(os.path.abspath(a.into), a.addon, os.path.abspath(a.out))
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
