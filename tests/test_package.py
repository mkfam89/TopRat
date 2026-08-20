"""package.py / bundle_python.py — the release zip must be safe and usable.

Offline. Nothing here downloads an interpreter: the network paths are exercised
by monkeypatching, because CI has no business pulling 40 MB and the bundle can
only be built correctly on the target platform anyway.
"""
import os
import zipfile

import pytest

import bundle_python
import package
import release


# --------------------------------------------------------------------------
# Platform resolution — a wrong triple ships an interpreter that cannot run,
# and nobody finds out until a user double-clicks.
# --------------------------------------------------------------------------

@pytest.mark.parametrize('system,machine,expected', [
    ('Darwin', 'arm64', 'aarch64-apple-darwin'),
    ('Darwin', 'x86_64', 'x86_64-apple-darwin'),
    ('Windows', 'AMD64', 'x86_64-pc-windows-msvc'),
    ('Linux', 'x86_64', 'x86_64-unknown-linux-gnu'),
    ('Linux', 'aarch64', 'aarch64-unknown-linux-gnu'),
])
def test_target_triple(system, machine, expected):
    assert bundle_python.target_triple(system, machine) == expected


def test_unsupported_platform_raises_rather_than_guessing():
    with pytest.raises(RuntimeError):
        bundle_python.target_triple('Windows', 'arm64')


@pytest.mark.parametrize('system,machine,expected', [
    ('Darwin', 'arm64', 'macos-arm64'),
    ('Windows', 'AMD64', 'windows-x64'),
    ('Linux', 'x86_64', 'linux-x64'),
])
def test_platform_tag_is_human_readable(system, machine, expected):
    assert package.platform_tag(system, machine) == expected


def test_python_binary_layout(tmp_path):
    """The two layouts the launchers already search for."""
    nix = tmp_path / 'nixbundle' / 'bin'
    nix.mkdir(parents=True)
    (nix / 'python3').write_text('', encoding='utf-8')
    assert bundle_python.python_binary(str(tmp_path / 'nixbundle')).endswith(os.path.join('bin', 'python3'))

    win = tmp_path / 'winbundle'
    win.mkdir()
    (win / 'python.exe').write_text('', encoding='utf-8')
    assert bundle_python.python_binary(str(win)).endswith('python.exe')


# --------------------------------------------------------------------------
# The zip
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def built_zip(tmp_path_factory):
    out = tmp_path_factory.mktemp('dist')
    path, problems = package.build_zip(out_dir=str(out), with_python=False, quiet=True)
    return path, problems


def test_zip_builds_clean(built_zip):
    path, problems = built_zip
    assert problems == [], 'package refused to build:\n  ' + '\n  '.join(problems)
    assert os.path.exists(path)


def test_zip_unpacks_into_exactly_one_folder(built_zip):
    """Otherwise an unzip sprays 150 files across the user's Downloads folder."""
    path, _ = built_zip
    roots = {n.split('/')[0] for n in zipfile.ZipFile(path).namelist()}
    assert len(roots) == 1, 'zip has %d top-level entries: %s' % (len(roots), roots)


@pytest.mark.parametrize('rel, host_mode, wants_exec', [
    # NTFS reports 0o666 for everything, so a Windows build used to emit these
    # non-executable and a mac user got "permission denied" with no explanation.
    ('Start Here.command', 0o100666, True),
    (os.path.join('python', 'bin', 'python3'), 0o100666, True),
    ('install.sh', 0o100666, True),
    # ...and a Linux/macOS build must not start marking data files executable
    ('app.py', 0o100644, False),
    ('INSTALL.md', 0o100666, False),
    ('Start Here.bat', 0o100644, False),
])
def test_zip_mode_decides_exec_from_the_path_not_the_host(rel, host_mode, wants_exec):
    """The exec bit must survive a build on a filesystem that has no exec bit."""
    mode = package._zip_mode(rel, host_mode)
    assert bool(mode & 0o111) is wants_exec, oct(mode)
    assert mode & 0o170000 == 0o100000, 'lost the regular-file bits: %o' % mode


def test_zip_keeps_the_executable_bit(built_zip):
    """A zip drops POSIX mode by default, and then Start Here.command will not run."""
    path, _ = built_zip
    z = zipfile.ZipFile(path)
    hits = [n for n in z.namelist() if n.endswith('/Start Here.command')]
    assert hits, 'the macOS launcher is not in the zip'
    mode = (z.getinfo(hits[0]).external_attr >> 16) & 0o777
    assert mode & 0o100, 'Start Here.command is not executable in the zip (mode %o)' % mode


def test_zip_contains_what_a_user_needs(built_zip):
    path, _ = built_zip
    names = zipfile.ZipFile(path).namelist()
    for want in ('app.py', 'INSTALL.md', 'LICENSE', 'Start Here.bat', 'Start Here.command'):
        assert any(n.endswith('/' + want) for n in names), 'missing from the zip: ' + want


def test_zip_carries_no_personal_data(built_zip):
    """The guard runs on the STAGED tree; this asserts it on the artifact itself.

    Reuses release.FORBIDDEN_PATHS rather than inventing looser rules — an
    ad-hoc substring check flags `src/ops/cleanup_user_data.py` (a script name,
    not a data folder) and the synthetic `tests/fixtures/docx/*` linter fixtures,
    which is how a guard trains people to ignore it.
    """
    import re
    path, _ = built_zip
    # Paths inside the zip are prefixed with the folder the user unpacks into.
    rels = [n.split('/', 1)[1] for n in zipfile.ZipFile(path).namelist() if '/' in n]

    bad = [r for r in rels if any(re.search(p, r) for p in release.FORBIDDEN_PATHS)]
    bad += [r for r in rels
            if r.lower().endswith(release.FORBIDDEN_SUFFIXES)
            and not r.startswith(release.BINARY_EXEMPT_PREFIX)]
    assert sorted(set(bad)) == [], 'personal data in the release zip: %s' % sorted(set(bad))[:5]


def test_zip_carries_no_test_suite(built_zip):
    """The download is for someone who double-clicks, not someone who clones.

    tests/ IS exported to the TopRat git repo (test_release.py asserts that from
    the other side) but stripped from the zip here: DEV_ONLY already removes
    pytest from the bundled interpreter, so a suite in the download is ~2 MB the
    user cannot run. Shipping tests with no runner is a puzzle, not a safety net.
    """
    path, _ = built_zip
    rels = [n.split('/', 1)[1] for n in zipfile.ZipFile(path).namelist() if '/' in n]
    assert not [r for r in rels if r.startswith('tests/')], \
        'the download carries a test suite it cannot run'
    assert 'pytest.ini' not in rels


def test_strip_is_scoped_to_the_staged_copy(tmp_path):
    """_strip_zip_only must only ever delete inside the stage it is handed.

    It runs `rmtree` on paths named by a module constant. Pointed at the wrong
    root — the project folder, say — that deletes the real test suite. This pins
    that it takes the stage as an argument and touches nothing else, so the day
    someone 'simplifies' it to use HERE, this fails.
    """
    stage = tmp_path / 'stage'
    (stage / 'tests' / 'fixtures').mkdir(parents=True)
    (stage / 'tests' / 'test_x.py').write_text('', encoding='utf-8')
    (stage / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    (stage / 'src').mkdir()
    (stage / 'src' / 'app.py').write_text('', encoding='utf-8')
    sibling = tmp_path / 'tests'          # must survive: outside the stage
    sibling.mkdir()
    (sibling / 'keepme.py').write_text('', encoding='utf-8')

    gone = package._strip_zip_only(str(stage), quiet=True)

    assert sorted(gone) == ['pytest.ini', 'tests/']
    assert not (stage / 'tests').exists()
    assert not (stage / 'pytest.ini').exists()
    assert (stage / 'src' / 'app.py').exists(), 'the strip ate something it should not have'
    assert (sibling / 'keepme.py').exists(), 'the strip escaped the staged copy'


def test_strip_is_silent_when_there_is_nothing_to_strip(tmp_path):
    """A second call, or a tree built without tests, must not raise."""
    stage = tmp_path / 'stage'
    stage.mkdir()
    assert package._strip_zip_only(str(stage), quiet=True) == []


def test_a_dirty_tree_produces_no_zip(tmp_path, monkeypatch):
    """The refusal must happen BEFORE anything is written.

    A zip is the one artifact that cannot be un-published, so 'build it then
    check' is the wrong order and this pins the right one.
    """
    monkeypatch.setattr(release, 'verify', lambda root: ['forbidden path: user_data_XX/tracking.csv'])
    out = tmp_path / 'dist'
    path, problems = package.build_zip(out_dir=str(out), with_python=False, quiet=True)
    assert path == ''
    assert problems
    assert not out.exists() or not list(out.glob('*.zip')), 'a zip was written despite a dirty tree'


def test_bundled_python_is_never_committed():
    """python/ must not be in the release allowlist — it is a per-OS binary blob.

    ~40 MB of platform-specific binaries in git history on every clone forever,
    and wrong for two of the three platforms. It belongs in a Release asset,
    which is what package.py builds.
    """
    assert 'python' not in release.INCLUDE_DIRS
    assert 'python' not in release.INCLUDE_FILES


def test_dev_only_packages_are_stripped_from_the_bundle(tmp_path):
    """tools\\run_tests.bat installs pytest into python\\; a release must not ship it.

    The working bundle doubles as the developer's test interpreter, so its
    site-packages picks up requirements-dev.txt. _copy_python is the one place
    that decides what a downloaded copy contains.
    """
    sp = tmp_path / 'bundle' / 'Lib' / 'site-packages'
    sp.mkdir(parents=True)
    for name in ('pytest', '_pytest', 'pluggy', 'iniconfig',
                 'pytest-8.4.1.dist-info', 'docx', 'lxml',
                 'python_docx-1.2.0.dist-info'):
        (sp / name).mkdir()
        (sp / name / '__init__.py').write_text('', encoding='utf-8')
    scripts = tmp_path / 'bundle' / 'Scripts'
    scripts.mkdir()
    for name in ('pytest.exe', 'pip.exe'):
        (scripts / name).write_text('', encoding='utf-8')

    package._copy_python(str(tmp_path / 'bundle'), str(tmp_path / 'stage'), quiet=True)
    out = tmp_path / 'stage' / 'python'
    got = {p.name for p in (out / 'Lib' / 'site-packages').iterdir()}
    assert got == {'docx', 'lxml', 'python_docx-1.2.0.dist-info'}, got
    assert {p.name for p in (out / 'Scripts').iterdir()} == {'pip.exe'}
