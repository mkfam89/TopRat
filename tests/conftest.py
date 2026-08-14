"""Shared test setup: repo root on sys.path + fixture loader.

The suite is read-only over the repo: golden fixtures live in tests/fixtures/,
anything writable goes to pytest's tmp_path. No network, no LibreOffice.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The modules live in src/<group>/ folders. src/_paths.py knows how to put every
# one of them on sys.path, so tests keep importing them by plain name (import jobpipe).
sys.path.insert(0, os.path.join(ROOT, 'src'))
import _paths  # noqa: E402,F401

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')


# --- the docx lint fixtures are BUILT, not committed -----------------------------
#
# .gitignore excludes *.docx repo-wide (tailored resumes are binaries; the repo commits
# code only), which also excludes tests/fixtures/docx/. A fresh clone — CI's included —
# therefore has the folder empty, and every lint_resume test failed with R00
# "could not read document: PackageNotFoundError" until this hook existed.
#
# So build them at session start when they are missing. tests/build_docx_fixtures.py is
# the same generator you run by hand; it is deterministic and offline, and the whole set
# takes well under a second. If python-docx is not installed we stay silent: the lint
# tests will say so themselves, and the rest of the suite has no reason to care.
_DOCX_FIXTURES = os.path.join(FIXTURES, 'docx')


def pytest_sessionstart(session):
    import glob
    import subprocess
    if glob.glob(os.path.join(_DOCX_FIXTURES, '*.docx')):
        return
    builder = os.path.join(ROOT, 'tests', 'build_docx_fixtures.py')
    try:
        subprocess.run([sys.executable, builder], check=True,
                       capture_output=True, text=True, timeout=120)
    except Exception as exc:  # missing python-docx, or the builder itself broke
        print('\ncould not build tests/fixtures/docx (%s); lint_resume tests will fail'
              % exc)


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='session')
def jp():
    import jobpipe
    return jobpipe


# --- the FROZEN config root ------------------------------------------------------
#
# See tests/frozen_root.py for why this exists and why the files are committed under
# '.frozen.json' names. The mechanics live there rather than here because
# tests/make_fixtures.py WRITES the goldens through the same root these fixtures READ
# them through; two implementations would eventually disagree, and a regeneration that
# baked live values into fixtures the suite checks against frozen ones is precisely the
# drift being prevented.
import frozen_root as _frozen  # noqa: E402


@pytest.fixture(scope='session')
def frozen_root(tmp_path_factory):
    """A real data root on disk, built once per session from the committed frozen copies."""
    return _frozen.materialize(str(tmp_path_factory.mktemp('frozen_data_root')))


@pytest.fixture
def frozen_config(frozen_root):
    """Point config reads at the frozen root for the duration of one test.

    Restores on the way out, so the autouse _data_root_restored guard below stays
    satisfied and no later test inherits the frozen dials.
    """
    with _frozen.activate(frozen_root) as root:
        yield root


# --- the data root must come back ------------------------------------------------
#
# pipelib.DATA is resolved once, at import, and blocklist / backlog / scheduler latch
# onto it the same way. A test that points them at a scratch root (test_backlog does,
# on purpose) has to point them back, and for a long time one did not: the tmp_path
# stayed in effect for the rest of the session, every later test read its config out of
# a directory pytest had already deleted, and 8 config-derived goldens failed in a full
# run while passing alone. Nothing pointed at the culprit, because the failures were all
# in other files.
#
# The check runs at SETUP, so the blame lands on the test that runs right after the leak
# rather than on whichever golden noticed first, and it is ordering-proof: it cannot race
# another fixture's teardown.
import pipelib as _pipelib  # noqa: E402

_ORIGINAL_DATA = _pipelib.DATA

_LEAK_MSG = (
    'the data root leaked out of an earlier test: pipelib.DATA is\n'
    '  %s\nbut this session started on\n  %s\n'
    'A test that reloads pipelib (or blocklist / backlog / scheduler) under a scratch\n'
    'root must reload it back — see the teardown in tests/test_backlog.py::board.'
)


@pytest.fixture(autouse=True)
def _data_root_restored():
    if _pipelib.DATA != _ORIGINAL_DATA:
        raise AssertionError(_LEAK_MSG % (_pipelib.DATA, _ORIGINAL_DATA))
    yield


def pytest_sessionfinish(session, exitstatus):
    """Catch a leak from the very LAST test, which no setup check would see."""
    if _pipelib.DATA != _ORIGINAL_DATA:
        print('\n' + _LEAK_MSG % (_pipelib.DATA, _ORIGINAL_DATA))
