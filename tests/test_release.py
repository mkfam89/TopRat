"""release.py — the export must never carry personal data.

This is the safety net under the whole publish path. Everything else in the
release pipeline is a convenience; this file is the part that decides whether
pushing to a shared repo is safe.

It does NOT trust release.build() to be correct. Each test builds a real export
into tmp_path and then re-derives the verdict from the files on disk, so a bug in
the allowlist shows up as a failure here rather than as a resume on the internet.

Offline, no network, no LibreOffice. Everything writable goes to tmp_path.
"""
import json
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


def test_rebuild_clears_a_config_subdirectory(tmp_path):
    """A stale config/ SUBDIRECTORY must not survive a rebuild.

    Regression, 2026-08-08. Running the app from inside an export makes the
    export its own data root (no user_data_* present, so resolve_data_root falls
    through to HERE), and setup then writes config/profiles/<name>.json into the
    ship tree. The cleanup sweep only removed non-shippable FILES and skipped
    directories, so that profile — real name, phone, private ntfy topic —
    survived every subsequent rebuild and was staged for push.
    """
    out = tmp_path / 'export'
    (out / 'config' / 'profiles').mkdir(parents=True)
    planted = out / 'config' / 'profiles' / 'someone_profile.json'
    planted.write_text('{"identity": {"full_name": "Real Person"}}', encoding='utf-8')

    release.build(str(out), quiet=True)

    assert not planted.exists(), 'a stale config/profiles/ file survived the rebuild'
    assert not (out / 'config' / 'profiles').exists(), 'config/profiles/ survived the rebuild'


def test_the_beta_badge_cannot_ship(built):
    """The dev copy's BETA chip is instance.json's `label`, not code.

    That is the whole reason release.py needs no "strip the badge" step: there is nothing
    in the tree to strip, so a release cannot forget. Two things make it true, and both are
    asserted here — instance.json is not copied, and an export containing one fails verify.
    Should anyone ever hardcode the badge into a page instead, this test is where that
    decision comes back for review.
    """
    out, _, _ = built
    assert not os.path.exists(os.path.join(out, 'config', 'instance.json'))
    assert not release._is_config_shippable('instance.json')

    planted = os.path.join(out, 'config', 'instance.json')
    with open(planted, 'w', encoding='utf-8') as f:
        f.write('{"label": "BETA"}')
    try:
        assert any('forbidden path' in p for p in release.verify(out)), \
            'a BETA label smuggled into an export would not be caught'
    finally:
        os.remove(planted)          # `built` is module-scoped; leave it as we found it


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


# Every fixture below is ASSEMBLED AT RUNTIME, for the same reason the fake API
# key above is: this file ships, so a literal email / ZIP line / user path written
# here would be flagged by the very scanner it is testing. Same trick, new shapes.
def _licence_noise():
    """The kinds of string CPython's own licences carry."""
    return '\n'.join([
        'j' + 'seward' + '@' + 'acm' + '.org',            # upstream author email
        '252' + '.227-' + '7013',                          # DFARS clause, phone-shaped
        'Santa ' + 'Clara, ' + 'CA ' + '95051',            # city/state/ZIP
    ]) + '\n'


def _user_path():
    return 'C:' + '\\Users\\' + 'somebody' + '\n'


def test_verify_ignores_the_bundled_interpreter(tmp_path):
    """python/ is third-party and exempt BY PATH.

    Regression: add_python_bundle() copies after verify(), which protects only the
    FIRST build — the export folder is reused, so on the second run the previous
    bundle is already sitting there when verify() walks it. That produced 40 failure
    lines from the interpreter's own licences and blocked the release.
    """
    b = tmp_path / release.BUNDLE_DIR / 'Lib'
    b.mkdir(parents=True)
    (tmp_path / release.BUNDLE_DIR / 'LICENSE.txt').write_text(
        _licence_noise(), encoding='utf-8')
    (tmp_path / release.BUNDLE_DIR / 'python312.zip').write_bytes(b'PK\x03\x04')
    (b / 'w.py').write_text(_user_path(), encoding='utf-8')
    assert release.verify(str(tmp_path)) == []


def test_bundle_exemption_is_top_level_only(tmp_path):
    """A folder merely NAMED python/ deeper in the tree is still scanned.

    Otherwise the exemption doubles as a hiding place: src/python/ would ship
    unscanned, which is the opposite of an allowlist.
    """
    d = tmp_path / 'src' / release.BUNDLE_DIR
    d.mkdir(parents=True)
    (d / 'x.py').write_text(_user_path(), encoding='utf-8')
    problems = release.verify(str(tmp_path))
    assert any('src/python/x.py' in p for p in problems), problems


def test_verify_catches_a_planted_data_folder(tmp_path):
    d = tmp_path / 'user_data_KP'
    d.mkdir()
    (d / 'job_tracker.json').write_text('{}', encoding='utf-8')
    problems = release.verify(str(tmp_path))
    assert any('forbidden path' in p for p in problems), problems


# --------------------------------------------------------------------------
# The PII gate
#
# Every fixture below is a FAKE persona (Robin Vale / Northwind). Writing the
# real values here would defeat the point: this test file ships, so a realistic
# test case is itself a leak. If a future test needs the real strings to prove
# something, that is the signal the design is wrong, not the test.
# --------------------------------------------------------------------------

def _s(*parts):
    """Assemble a PII-shaped sample at runtime.

    Spelled out literally, every sample below would be a PII-shaped string
    living in a shipped file, and the gate would flag its own test suite. Same
    reason test_verify_catches_a_planted_secret assembles its API key from
    fragments — applied to the persona.
    """
    return ''.join(parts)


NAME = _s('Robin', ' ', 'Vale')
PREFIX = _s('Robin_Vale_', 'Resume_')
EMAIL = _s('robin.vale', '@', 'mailhost.test')
TOPIC = _s('robin-jobradar-', 'deadbeef99')

PERSONA = {
    'identity': {
        'full_name': NAME,
        'resume_prefix': PREFIX,
        'past_companies': [_s('Northwind', ' Traders')],
        'real_titles': ['Reliability Engineer'],
    },
    'notify': {'topic': TOPIC},
    'git': {'name': NAME, 'email': EMAIL},
}


def _persona_file(tmp_path):
    p = tmp_path / 'persona.json'
    p.write_text(json.dumps(PERSONA), encoding='utf-8')
    return str(p)


@pytest.mark.parametrize('label,parts', [
    ('phone number',             ('CONTACT = "(713) ', '478-', '1234"')),
    ('phone number',             ('CONTACT = "713', '-478-', '1234"')),
    ('email address',            ('OWNER = "r.vale89', '@', 'mailhost.test"')),
    ('LinkedIn profile',         ('URL = "linkedin.com', '/in/', 'robin-vale-cloud"')),
    ('Windows user path',        ('PROJ = r"C:', '\\\\Users\\\\', 'rvale\\\\Projects"')),
    ('postal address',           ('HOME = "Houston, Texas ', '770', '62"')),
    ('personal resume filename', ('BASE = "Robin_Vale_', 'Resume_', 'data_analyst.docx"')),
])
def test_structural_pii_is_caught(tmp_path, label, parts):
    """Layer 1: shape-based, so it works in CI with no profile.json present."""
    (tmp_path / 'leak.py').write_text(_s(*parts) + '\n', encoding='utf-8')
    problems = release.verify(str(tmp_path), identity=[])
    assert any(p.startswith(label) for p in problems), (label, problems)


@pytest.mark.parametrize('line', [
    '"full_name": "Jane Doe"',
    '"email": "jane.doe@example.com"',
    '"query": "Houston, Texas"',          # a city with no ZIP is a default, not an identity
    '"city": "Houston", "state": "TX"',
    '"resume_prefix": "Jane_Doe_Resume_"',
    'SUPPORT = "noreply@example.org"',
    r'PROJ = r"C:\Users\<you>\Projects"',
    'PHONE_HINT = "(555) 555-5555"',
])
def test_placeholders_are_not_flagged(tmp_path, line):
    """A gate that cries wolf gets bypassed. Generic examples must stay silent."""
    (tmp_path / 'sample.json').write_text(line + '\n', encoding='utf-8')
    assert release.verify(str(tmp_path), identity=[]) == []


def test_identity_layer_catches_a_bare_name(tmp_path):
    """Layer 2: a bare name in prose has no PII shape — only the profile knows."""
    (tmp_path / 'notes.md').write_text(
        'Standing rules for customizing %s\'s resumes.\n' % NAME, encoding='utf-8')
    identity = release._local_identity(_persona_file(tmp_path))
    problems = release.verify(str(tmp_path), identity=identity)
    assert any('real name' in p for p in problems), problems


def test_identity_layer_catches_underscored_name_and_employer(tmp_path):
    (tmp_path / 'code.py').write_text(
        'BASE = "%score.docx"\nROLES = ["%s SRE"]\n' % (PREFIX, _s('Northwind', ' Traders')),
        encoding='utf-8')
    identity = release._local_identity(_persona_file(tmp_path))
    problems = release.verify(str(tmp_path), identity=identity)
    assert any('real name' in p for p in problems), problems
    assert any('real employer' in p for p in problems), problems


def test_identity_layer_catches_the_private_notify_topic(tmp_path):
    """Anyone who reads the ntfy topic can subscribe to the user's job alerts."""
    (tmp_path / 'cfg.json').write_text('{"topic": "%s"}\n' % TOPIC, encoding='utf-8')
    identity = release._local_identity(_persona_file(tmp_path))
    assert any('ntfy' in p for p in release.verify(str(tmp_path), identity=identity))


def test_pii_in_a_filename_is_caught(tmp_path):
    """A binary file has no scannable text — its NAME is the whole leak.

    Regression: a linter fixture named after the owner sat in tests/fixtures/docx/,
    which is binary-exempt, so no check in verify() ever looked at it.
    """
    (tmp_path / ('%sAcme_DataEngineer.docx' % PREFIX)).write_bytes(b'PK\x03\x04')
    identity = release._local_identity(_persona_file(tmp_path))
    problems = release.verify(str(tmp_path), identity=identity)
    assert any('resume filename prefix' in p or 'real name' in p for p in problems), problems


def test_identity_layer_is_absent_without_a_profile(tmp_path):
    """CI has no profile.json. That must degrade to layer 1, not crash."""
    assert release._local_identity(str(tmp_path / 'nope.json')) == []


def test_problems_are_line_numbered(tmp_path):
    (tmp_path / 'a.py').write_text('x = 1\ny = 2\nMAIL = "%s"\n' % EMAIL, encoding='utf-8')
    problems = release.verify(str(tmp_path), identity=[])
    assert any(':3 ' in p for p in problems), problems


# --------------------------------------------------------------------------
# The LICENSE carve-out — narrow on purpose
# --------------------------------------------------------------------------

def test_license_copyright_line_is_exempt(tmp_path):
    (tmp_path / 'LICENSE').write_text(
        'MIT License\n\nCopyright (c) 2026 %s\n\nPermission is hereby granted...\n' % NAME,
        encoding='utf-8')
    identity = release._local_identity(_persona_file(tmp_path))
    problems = [p for p in release.verify(str(tmp_path), identity=identity)
                if 'LICENSE' in p]
    assert problems == [], 'attribution is what a copyright line is for: %s' % problems


def test_license_exemption_does_not_cover_the_rest_of_the_file(tmp_path):
    """One line, not one file — a phone number in LICENSE is still a phone number.

    Also the regression test for extension-less files being skipped outright:
    before TEXT_NAMES, LICENSE was never opened by the scanner at all.
    """
    (tmp_path / 'LICENSE').write_text(
        'Copyright (c) 2026 %s\nQuestions: %s\n' % (NAME, _s('(713) ', '478-', '1234')),
        encoding='utf-8')
    problems = release.verify(str(tmp_path), identity=[])
    assert any('phone number' in p for p in problems), problems


def test_license_exemption_is_scoped_to_that_filename(tmp_path):
    (tmp_path / 'README.md').write_text('Copyright (c) 2026 %s\n' % NAME, encoding='utf-8')
    identity = release._local_identity(_persona_file(tmp_path))
    assert any('real name' in p for p in release.verify(str(tmp_path), identity=identity))


# --------------------------------------------------------------------------
# Un-shipping actually un-ships
# --------------------------------------------------------------------------

def test_internal_docs_do_not_ship(built):
    """CLAUDE.md is the personal resume rulebook; docs/ is notes-to-self."""
    out, _, _ = built
    assert not os.path.exists(os.path.join(out, 'CLAUDE.md'))
    assert not os.path.isdir(os.path.join(out, 'docs'))


def test_build_removes_stale_content_from_an_existing_export(tmp_path):
    """Dropping a name from the allowlist stops the copy. It does not remove what
    a previous release already put there — build() has to, or un-shipping is a
    no-op on every existing clone."""
    out = tmp_path / 'export'
    (out / 'config').mkdir(parents=True)
    (out / 'CLAUDE.md').write_text('personal rules\n', encoding='utf-8')
    (out / 'docs').mkdir()
    (out / 'docs' / 'PLAYBOOK.md').write_text('notes\n', encoding='utf-8')
    (out / 'scheduler_state.json').write_text('{}', encoding='utf-8')
    (out / 'config' / 'profile.json').write_text('{"identity": {}}', encoding='utf-8')
    (out / 'config' / 'notify.json').write_text('{"topic": "private"}', encoding='utf-8')

    release.build(str(out), quiet=True)

    for gone in ('CLAUDE.md', 'docs', 'scheduler_state.json',
                 'config/profile.json', 'config/notify.json'):
        assert not os.path.exists(os.path.join(str(out), *gone.split('/'))), \
            'stale %s survived a rebuild' % gone
    assert os.path.exists(os.path.join(str(out), 'config', 'profile.example.json')), \
        'the pruner ate the shipped templates'


def test_verify_catches_a_planted_resume(tmp_path):
    # Persona filename, not the real one: the gate now reads this file too.
    (tmp_path / ('%sAcme_SRE.docx' % PREFIX)).write_bytes(b'PK\x03\x04')
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
