#!/usr/bin/env python3
"""The data-folder name is derived TWICE — keep the two derivations identical.

`profile_lib.initials()` (Python, used by the server and by migrate_data_root.py to
name the folder) and `deriveInitials()` (JavaScript, inside src/web/setup.html, which
paints the grey suggestion as the user types) must agree on every name. If they drift,
the wizard shows one folder and the app creates another — the kind of bug that only
surfaces on somebody else's name, long after the change that caused it.

The JS is not imported; it is extracted from setup.html by regex and run under node.
No node on this machine => skipped, never failed: the suite stays offline and
dependency-free (tests/conftest.py).

Also pins the resolution rules that pipelib.nested_user_data() is relied on for.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_HTML = os.path.join(ROOT, 'src', 'web', 'setup.html')

# Spread over the cases that actually differ between the two languages: unicode
# normalisation, runs of whitespace, punctuation inside a token, single tokens,
# empty/blank, and a middle name (which must NOT reach the initials).
NAMES = [
    'Khoa Pham', 'khoa', '', '   ', 'Khoa Anh Bao Pham', '  Khoa   Pham  ',
    'Mary-Jane Watson', 'Khoa\tPham', "O'Brien Smith", 'José Álvarez',
    'X', 'Jean-Luc Picard Jr', '123 456', 'Khoa Pham ', 'Ægir Østergaard',
    'van der Berg', 'ANNA MARIA ROSSI',
]


def _py_initials(names):
    import profile_lib
    return [profile_lib.initials(n) for n in names]


def _extract_js():
    """Pull deriveInitials() out of setup.html exactly as the browser sees it."""
    html = open(SETUP_HTML, encoding='utf-8').read()
    m = re.search(r'const deriveInitials\s*=.*?\n};', html, re.S)
    assert m, 'deriveInitials() not found in setup.html — was it renamed?'
    return m.group(0)


def test_initials_match_between_python_and_setup_page():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed — JS side unverified on this machine')
    script = _extract_js() + '\nconst names=%s;\n' % json.dumps(NAMES) + \
        'console.log(JSON.stringify(names.map(deriveInitials)));'
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, 'node failed: ' + out.stderr
    js = json.loads(out.stdout.strip())
    py = _py_initials(NAMES)
    mismatches = [(n, p, j) for n, p, j in zip(NAMES, py, js) if p != j]
    assert not mismatches, 'python vs setup.html disagree on: %r' % (mismatches,)


def test_initials_rules():
    import profile_lib as pl
    assert pl.initials('Khoa Pham') == 'KP'
    assert pl.initials('Khoa Anh Bao Pham') == 'KP', 'middle names must not appear'
    assert pl.initials('khoa') == 'K'
    assert pl.initials('') == ''
    assert pl.initials(None) == ''
    # Accents FOLD to their base letter. Stripping them instead would promote the
    # next letter and give the user initials that are not theirs (JL, not JA).
    assert pl.initials('José Álvarez') == 'JA'
    # Whatever comes out has to be safe to put in a path, a .bat file and a Git ref.
    for name in NAMES:
        assert re.fullmatch(r'[A-Z0-9]*', pl.initials(name)), name


def test_suggest_user_data_dirname():
    import pipelib
    assert pipelib.suggest_user_data_dirname('KP') == 'user_data_KP'
    assert pipelib.suggest_user_data_dirname('') == 'user_data_ME'
    assert pipelib.suggest_user_data_dirname(None) == 'user_data_ME'
    assert pipelib.suggest_user_data_dirname('k.p') == 'user_data_KP'


def test_nested_user_data_needs_exactly_one_match(tmp_path):
    import pipelib
    assert pipelib.nested_user_data(str(tmp_path)) == '', 'no folder -> no answer'

    (tmp_path / 'user_data_KP').mkdir()
    assert pipelib.nested_user_data(str(tmp_path)).endswith('user_data_KP')

    # Two candidates is ambiguous. Picking one would point the pipeline at somebody
    # else's tracker, so resolution must fall through to the later rungs instead.
    (tmp_path / 'user_data_AB').mkdir()
    assert pipelib.nested_user_data(str(tmp_path)) == '', 'ambiguous -> no answer'

    shutil.rmtree(tmp_path / 'user_data_AB')
    (tmp_path / 'user_data_NOTADIR').write_text('x')
    assert pipelib.nested_user_data(str(tmp_path)).endswith('user_data_KP'), \
        'a FILE named user_data_* must not count as a candidate'


def test_missing_directory_is_not_an_error():
    import pipelib
    assert pipelib.nested_user_data(os.path.join(ROOT, 'no_such_folder_xyz')) == ''
