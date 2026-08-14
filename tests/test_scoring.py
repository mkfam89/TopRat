"""classify / skill_match / flag_skills / _skill_hit / norm.

Goldens (tests/fixtures/scoring.json) snapshot every live listing against the
current config/skills.json + skill_aliases.json. Synthetic tests pin the
invariants that must hold regardless of config churn.
"""
import pytest

from conftest import load_fixture

GOLDEN = load_fixture('scoring.json')


# ---- norm ------------------------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('CI/CD', 'cicd'),
    ('Azure DevOps', 'azuredevops'),
    ('  REST APIs ', 'restapis'),
    ('C++', 'c'),
    (None, ''),
    ('', ''),
])
def test_norm(jp, raw, expected):
    assert jp.norm(raw) == expected


# ---- skill_match / flag_skills invariants ----------------------------------

def test_skill_match_empty_returns_none(jp):
    assert jp.skill_match([]) == (None, [])
    # skills that normalize to nothing count as "none extracted"
    assert jp.skill_match(['', '  ', '!!!']) == (None, [])


def test_skill_match_fraction_and_flags_are_consistent(jp):
    k, prior = jp._shrinkage()
    for row in GOLDEN:
        match, flagged = jp.skill_match(row['requiredSkills'])
        req = [r for r in row['requiredSkills'] if jp.norm(r)]
        if not req:
            assert match is None
            continue
        matched = len(req) - len(flagged)
        expected = 0.0 if matched == 0 else round((matched + k * prior) / (len(req) + k), 2)
        assert match == expected
        assert set(flagged) <= set(req)
        # flag_skills and skill_match must agree on the flagged set
        assert flagged == jp.flag_skills(req)


# ---- shrinkage (thin-extraction confidence correction) ---------------------

def test_skill_match_shrinks_thin_extractions(monkeypatch):
    """A 1-of-1 perfect extraction must NOT outrank a thick, fully-matched one."""
    import scoring
    monkeypatch.setattr(scoring, 'flag_skills', lambda req: [])
    monkeypatch.setattr(scoring, '_shrinkage', lambda: (1.0, 0.0))
    one, _ = scoring.skill_match(['SQL'])
    five, _ = scoring.skill_match(['SQL', 'Python', 'Linux', 'Bash', 'Azure'])
    sixteen, _ = scoring.skill_match(['Skill%d' % i for i in range(16)])
    assert one == 0.5
    assert five == 0.83
    assert sixteen == 0.94
    assert one < five < sixteen < 1.0


def test_skill_match_zero_matched_is_zero_even_with_prior(monkeypatch):
    """No overlap at all can never be lifted over the discovery floor by the prior."""
    import scoring
    monkeypatch.setattr(scoring, 'flag_skills', lambda req: list(req))
    monkeypatch.setattr(scoring, '_shrinkage', lambda: (3.0, 0.5))
    assert scoring.skill_match(['zzqqx-not-a-real-tool'])[0] == 0.0


def test_shrinkage_defaults_and_bad_config(monkeypatch):
    import scoring
    monkeypatch.setattr(scoring, 'load_json', lambda *a, **kw: {})
    assert scoring._shrinkage() == (scoring._SHRINK_K, scoring._SHRINK_PRIOR)
    # out-of-range / wrong-typed values fall back to the defaults
    monkeypatch.setattr(scoring, 'load_json',
                        lambda *a, **kw: {'skill_match_shrinkage_k': -1,
                                          'skill_match_prior': 'high'})
    assert scoring._shrinkage() == (scoring._SHRINK_K, scoring._SHRINK_PRIOR)
    # k=0 restores the raw fraction
    monkeypatch.setattr(scoring, 'load_json',
                        lambda *a, **kw: {'skill_match_shrinkage_k': 0})
    assert scoring._shrinkage()[0] == 0.0


def test_shrinkage_reads_live_strategy_config(jp):
    """The shipped config must parse into usable numbers (no silent default fallback)."""
    k, prior = jp._shrinkage()
    assert k >= 0 and 0 <= prior <= 1


# ---- title lane gate -------------------------------------------------------

def test_title_fit_penalizes_off_lane(jp):
    factor, reason = jp.title_fit('Senior Product Manager, Cloud Video')
    assert factor < 1.0
    assert 'product manager' in reason


def test_title_fit_off_lane_beats_in_lane_modifier(jp):
    """'cloud'/'platform' are in-lane MODIFIERS that appear inside off-lane titles.
    Off-lane must win, else the gate waves through the exact job it exists to catch."""
    for title in ('Senior Product Manager, Cloud Video',
                  'Product Manager - Games, Cloud Infrastructure',
                  'Product Owner, Support Platform'):
        assert jp.title_fit(title)[0] < 1.0, title


def test_title_fit_keeps_in_lane_roles_whole(jp):
    for title in ('Site Reliability Engineer', 'Senior DevOps Engineer (Terraform)',
                  'Cloud Support Engineer', 'Data Analyst II', 'Test Engineer'):
        assert jp.title_fit(title)[0] == 1.0, title


def test_title_fit_never_penalizes_unknown_titles(jp):
    """The list may only demote what it RECOGNIZES — an odd title costs nothing."""
    for title in ('Cluster Capacity Eng', 'BMS Data Analysis Engineer',
                  'Azure Dev Ops Engineer', 'Zzqqx Wrangler', '', None):
        assert jp.title_fit(title)[0] == 1.0, title


def test_title_fit_matches_whole_words_only(jp):
    # substring accidents: 'nurse' must not fire on 'nursery', 'welder' not on 'welders'
    assert jp.title_fit('Nursery Systems Analyst')[0] == 1.0
    assert jp.title_fit('Registered Nurse')[0] < 1.0


def test_score_job_applies_factor_to_shrunk_match(jp, monkeypatch):
    import scoring
    monkeypatch.setattr(scoring, 'skill_match', lambda req: (0.50, []))
    monkeypatch.setattr(scoring, 'title_fit', lambda t: (0.35, 'off-lane: product manager'))
    score, flagged, factor, reason = scoring.score_job('Senior Product Manager', ['REST APIs'])
    assert score == 0.17 and factor == 0.35 and 'product manager' in reason


def test_score_job_passes_none_through(jp, monkeypatch):
    """No extracted skills stays None (not 0.0) even for an off-lane title."""
    import scoring
    monkeypatch.setattr(scoring, 'skill_match', lambda req: (None, []))
    assert scoring.score_job('Product Manager - Games', [])[0] is None


def test_score_job_is_a_noop_for_in_lane(jp):
    plain, _ = jp.skill_match(['Kubernetes', 'Terraform', 'Docker'])
    scored = jp.score_job('Site Reliability Engineer', ['Kubernetes', 'Terraform', 'Docker'])
    assert scored[0] == plain and scored[2] == 1.0


def test_title_terms_resolve_from_profile(jp, frozen_config):
    """In-lane terms must actually resolve from profile.json (or a strategy override)."""
    inl, off, factor = jp._title_terms()
    assert inl, 'no in-lane titles resolved - profile.json search.titles missing?'
    assert off and 0 <= factor <= 1


def test_skill_hit_unknown_token_misses(jp):
    matchable, stems = jp._matchable()
    assert not jp._skill_hit('zzqqx-not-a-real-tool', matchable, stems)
    assert not jp._skill_hit('', matchable, stems)


def test_skill_hit_known_skill_hits(jp, frozen_config):
    matchable, stems = jp._matchable()
    # 'SQL' is in config/skills.json; punctuation/case must not matter
    assert jp._skill_hit('sql', matchable, stems)
    assert jp._skill_hit(' S.Q.L ', matchable, stems)


# ---- goldens ---------------------------------------------------------------

# Snapshotted against tests/fixtures/config_frozen/, NOT the owner's live config — so
# editing your real skills.json can no longer redden this, and a fresh clone can run it.
# Regenerate with tests/make_fixtures.py after an intentional change to the frozen dials
# or to the scoring code, and review the diff like code.
def test_golden_skill_match(jp, frozen_config):
    for row in GOLDEN:
        match, flagged = jp.skill_match(row['requiredSkills'])
        assert match == row['skillMatch'], row['id']
        assert flagged == row['flagged'], row['id']


# Golden values were snapshotted against the owner's live config (skills.json,
# skill_aliases.json, strategy.json, blocklist). A clone with no data root reads an
# empty config and scores everything differently, so this fails for want of data.
def _slot(cls):
    """classify() output with the base-resume FILENAME reduced to its slot.

    The golden cannot carry 'First_Last_Resume_data_analyst.docx' — tests/fixtures
    ships publicly and release.py's PII gate blocks on exactly that string. What
    the golden is actually pinning is that a title lands on the right base, so
    compare the slot. Mirrors make_fixtures.depersonalize_classify(); change one
    and change the other. Which real file fills the slot is covered by
    test_pick_base_prefers_docx_over_its_pdf_twin.
    """
    cls = dict(cls)
    if cls.get('baseResume'):
        cls['baseResume'] = '<%s base>' % (cls.get('category') or 'UNKNOWN')
    return cls


@pytest.mark.needs_userdata
def test_golden_classify(jp):
    for row in GOLDEN:
        assert _slot(jp.classify(row['title'], row['requiredSkills'])) == row['classify'], row['id']


# ---- classify invariants ---------------------------------------------------

@pytest.mark.parametrize('title, category', [
    ('Data Analyst', 'ANALYST'),
    ('Business Data Analyst', 'ANALYST'),
    ('Technical Support Specialist', 'TECH_SUPPORT'),
    ('Support Analyst', 'TECH_SUPPORT'),   # 'support' outranks 'analyst'
    ('Site Reliability Engineer', 'CORE'),
    ('DevOps Engineer', 'CORE'),
    ('', 'CORE'),
])
def test_classify_title_routing(jp, title, category):
    assert jp.classify(title)['category'] == category


def test_classify_infra_override_beats_title(jp):
    # >= 3 INFRA_SIGNAL markers route to CORE even with a support/analyst title
    got = jp.classify('Application Support Analyst',
                      ['Kubernetes', 'Terraform', 'Docker', 'SQL'])
    assert got['category'] == 'CORE'
    # accepts a semicolon/comma string form too
    got = jp.classify('Support Analyst', 'kubernetes; terraform; docker')
    assert got['category'] == 'CORE'


def test_classify_two_markers_do_not_override(jp):
    got = jp.classify('Application Support Analyst', ['Kubernetes', 'Terraform', 'SQL'])
    assert got['category'] == 'TECH_SUPPORT'


def test_classify_returns_existing_base_resume(jp):
    import os
    if not os.path.isdir(jp.TEMPLATE_DIR) or not jp.template_files():
        pytest.skip('no resume_template/ on this machine (data root not present)')
    for title in ('Data Analyst', 'Support Engineer', 'SRE'):
        cls = jp.classify(title)
        # baseResume is a relative identifier; the file lives under TEMPLATE_DIR,
        # which follows the data root (code/data split) — same resolution tailor_local uses.
        assert cls['baseResume'], cls
        assert os.path.exists(os.path.join(jp.TEMPLATE_DIR,
                                           os.path.basename(cls['baseResume']))), cls
        assert cls['baseType'] in ('docx', 'pdf')


def test_pick_base_prefers_docx_over_its_pdf_twin(jp, monkeypatch):
    """A keyword that matches both twins must resolve to the .docx.

    Regression: the keyword pass returned the first os.listdir hit, so a folder that
    listed the .pdf first handed tailor_local a PDF, which it copied to a .docx
    filename — a PDF with the wrong extension.
    """
    import scoring
    monkeypatch.setattr(scoring, 'template_files',
                        lambda: ['CORE_RESUME.pdf', 'CORE_RESUME.docx'])
    cls = scoring.classify('Site Reliability Engineer')
    assert cls['baseResume'].endswith('CORE_RESUME.docx'), cls
    assert cls['baseType'] == 'docx'
    # a keyword with only a .pdf on disk still resolves — preference, not a filter
    monkeypatch.setattr(scoring, 'template_files', lambda: ['CORE_RESUME.pdf'])
    cls = scoring.classify('Site Reliability Engineer')
    assert cls['baseResume'].endswith('CORE_RESUME.pdf') and cls['baseType'] == 'pdf', cls


def test_classify_names_no_file_when_template_dir_is_empty(jp, monkeypatch):
    """An empty resume_template/ must yield NO base resume name.

    Regression: _pick_base used to fall back to a hardcoded owner-specific filename
    when the folder was empty or unresolvable, so the board and the queue both
    reported a specific missing FILE for a folder that simply had nothing in it.
    """
    import scoring
    monkeypatch.setattr(scoring, 'template_files', lambda: [])
    for title in ('Data Analyst', 'Support Engineer', 'SRE'):
        cls = scoring.classify(title)
        assert cls['baseResume'] == '', cls
        assert cls['baseType'] == ''
    # the skill-driven infra override takes the same path
    cls = scoring.classify('Application Support Analyst', ['Kubernetes', 'Terraform', 'Docker'])
    assert cls['category'] == 'CORE' and cls['baseResume'] == ''


# ---------------------------------------------------------------- CORE base resolution
# The CORE base used to be keyed on the first user's own surname, hardcoded in the router.
# It is now "the base with no specialization keyword", which works for any name.

def test_core_base_is_the_unspecialized_resume(jp, monkeypatch):
    """CORE picks the plain base, not a specialized variant, for a user with any name.

    Regression guard for the tempting-but-wrong fix — deriving the keyword from
    identity.full_name. resume_prefix puts the user's name in EVERY variant's filename,
    so a name keyword matches 'Jane_Doe_Resume_data_analyst.docx' first and CORE silently
    resolves to the ANALYST resume."""
    import scoring
    monkeypatch.setattr(scoring, 'template_files',
                        lambda: ['Jane_Doe_Resume_data_analyst.docx',
                                 'Jane_Doe_Resume.docx',
                                 'Jane_Doe_Resume_support.docx'])
    assert scoring.classify('Site Reliability Engineer')['baseResume'].endswith('Jane_Doe_Resume.docx')
    # the specialized categories still route to their own variants
    assert scoring.classify('Data Analyst')['baseResume'].endswith('Jane_Doe_Resume_data_analyst.docx')
    assert scoring.classify('Support Engineer')['baseResume'].endswith('Jane_Doe_Resume_support.docx')


def test_core_base_honours_an_explicit_generic_name(jp, monkeypatch):
    """An explicitly-named general resume wins over the no-keyword rule."""
    import scoring
    monkeypatch.setattr(scoring, 'template_files',
                        lambda: ['Something_Else.docx', 'Master_Resume.docx'])
    assert scoring.classify('SRE')['baseResume'].endswith('Master_Resume.docx')


def test_core_base_when_the_base_is_named_after_its_owner(jp, monkeypatch):
    """The layout that shipped first: a base named after the person, plus two variants.

    No personal alias is needed for this — a base named after its owner carries no
    specialization keyword, so the no-keyword rule resolves it. That is why the alias
    could be deleted rather than kept as backward compatibility (it was a real name in
    shipped code, which release.py's PII gate rejects). Prefers the .docx over its twin."""
    import scoring
    monkeypatch.setattr(scoring, 'template_files',
                        lambda: ['A_Person_Resume_data_analyst.docx',
                                 'A_Person_Resume_Technical_support.docx',
                                 'A_PERSON_RESUME.docx', 'A_PERSON_RESUME.pdf'])
    cls = scoring.classify('Site Reliability Engineer')
    assert cls['baseResume'].endswith('A_PERSON_RESUME.docx'), cls
