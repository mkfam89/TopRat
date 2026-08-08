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
