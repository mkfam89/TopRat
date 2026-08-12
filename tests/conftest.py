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


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='session')
def jp():
    import jobpipe
    return jobpipe


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
