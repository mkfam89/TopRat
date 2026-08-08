"""release.py — the export must never carry personal data.

This is the safety net under the whole publish path. Everything else in the
release pipeline is a convenience; this file is the part that decides whether
pushing to a shared repo is safe.

It does NOT trust release.build() to be correct. Each test builds a real export
into tmp_path and then re-derives the verdict from the files on disk, so a bug in
the allowlist shows up as a failure here rather than as a resume on the internet.

Offline, no network, no LibreOffice. Everything writable goes to tmp_path.
"""
import os

import pytest

import release


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    """One real export, reused by every test in this file (building is not cheap)."""
    out = tmp_path_factory.mktemp('export')
    total, problems = release.build(str(out), quiet=True)
    return str(out), total, problems


# --------------------------------------------------------------------------
# The build produces something, and it is clean
# --------------------------------------------------------------------------

def test_export_is_clean(built):
    _, _, problems = built
    assert problems == [], 'export is not shippable:\n  ' + '\n  '.join(problems)


def test_export_is_not_empty(built):
    _, total, _ = built
    # A broken allowlist that copies nothing would otherwise "pass" every
    # forbidden-content check below, which is the quietest possible failure.
    assert total > 100, 'export has only %d files — the allowlist is probably broken' % total


def test_the_app_actually_ships(built):
    out, _, _ = built
    for must in ('app.py', 'INSTALL.md', 'LICENSE', 'requirements.txt',
                 'Start Here.bat', 'Start Here.command',
                 os.path.join('src', 'web', 'dashboard_server.py'),
                 os.path.join('src', '_version.py')):
        assert os.path.exists(os.path.join(out, must)), 'missing from export: ' + must


# --------------------------------------------------------------------------
# The things that must never be in there
# --------------------------------------------------------------------------

def test_no_personal_data_root(built):
    out, _, _ = built
    strays = [d for d in os.listdir(out) if d.startswith(('user_data', 'job_agent_profile'))]
    assert strays == [], 'personal data root copied into the export: %s' % strays


def test_no_working_folders(built):
    out, _, _ = built
    for d in ('New', 'Applied', 'Skipped', 'Backups', 'logs', 'cache'):
        assert not os.path.isdir(os.path.join(out, d)), \
            '%s/ holds job data and resumes — it must not ship' % d


def test_config_ships_only_templates_and_defaults(built):
    out, _, _ = built
    cfg = os.path.join(out, 'config')
    for fn in os.listdir(cfg):
        assert release._is_config_shippable(fn), \
            'config/%s is a real config file (keys / personal details), not a template' % fn


def test_no_real_resumes(built):
    out, _, _ = built
    for dirpath, _, filenames in os.walk(out):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), out).replace(os.sep, '/')
            if rel.startswith(release.BINARY_EXEMPT_PREFIX):
                continue        # synthetic linter fixtures, generated not committed
            assert not fn.lower().endswith(('.docx', '.pdf', '.doc')), \
                'document file in export: ' + rel


# --------------------------------------------------------------------------
# The guard itself works — a guard that cannot fail is not a guard
# --------------------------------------------------------------------------

def test_verify_catches_a_planted_secret(tmp_path):
    # Assembled at runtime on purpose. Written out literally, this line would be
    # a key-shaped string inside a shipped file, and the scanner would flag its
    # own test — as would GitHub's push protection.
    fake = 'sk-' + 'ant-' + ('a' * 20)
    (tmp_path / 'leak.py').write_text('KEY = "%s"\n' % fake, encoding='utf-8')
    problems = release.verify(str(tmp_path))
    assert any('Anthropic API key' in p for p in problems), problems


def test_verify_catches_a_planted_data_folder(tmp_path):
    d = tmp_path / 'user_data_KP'
    d.mkdir()
    (d / 'job_tracker.json').write_text('{}', encoding='utf-8')
    problems = release.verify(str(tmp_path))
    assert any('forbidden path' in p for p in problems), problems


def test_verify_catches_a_planted_resume(tmp_path):
    (tmp_path / 'Khoa_Pham_Resume_Acme_SRE.docx').write_bytes(b'PK\x03\x04')
    problems = release.verify(str(tmp_path))
    assert any('binary document' in p for p in problems), problems


def test_verify_catches_a_real_config_file(tmp_path):
    cfg = tmp_path / 'config'
    cfg.mkdir()
    (cfg / 'adzuna.json').write_text('{"app_id": "1234"}', encoding='utf-8')
    problems = release.verify(str(tmp_path))
    assert any('forbidden path' in p for p in problems), problems


def test_verify_passes_a_clean_tree(tmp_path):
    (tmp_path / 'ok.py').write_text('print("hello")\n', encoding='utf-8')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'profile.example.json').write_text('{}', encoding='utf-8')
    assert release.verify(str(tmp_path)) == []
