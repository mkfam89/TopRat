"""applied_match / applied_norm_index / _canon / _canon_tokens / resolve_status.

Goldens (tests/fixtures/applied.json) snapshot the live tracker's applied map,
its canonical forms, and resolve_status over every tracking.csv row. Synthetic
tests pin the fuzzy-reconcile rules (old abbreviated ids vs new full-title ids).
"""
import pytest

from conftest import load_fixture

GOLDEN = load_fixture('applied.json')


# ---- _canon / _canon_tokens ------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('Senior Site Reliability Engineer', 'srsre'),   # phrase fold, then word map
    ('SrSiteReliabilityEngineer', 'srsre'),          # camel ids canon the same
    ('Alkami', 'alkami'),
    ('Acme, Inc.', 'acme'),                          # corp suffixes dropped
    ('', ''),
    (None, ''),
])
def test_canon(jp, raw, expected):
    assert jp._canon(raw) == expected


def test_canon_tokens_order_independent(jp):
    a = jp._canon_tokens('Sr DevOps Engineer')
    b = jp._canon_tokens('Engineer Sr, DevOps')
    assert a == b


def test_canon_tokens_camel_equals_spaced(jp):
    assert jp._canon_tokens('SrSRERelease') == jp._canon_tokens(
        'Sr Site Reliability Engineer, Release')


def test_canon_tokens_drops_noise_words(jp):
    assert jp._canon_tokens('Systems Engineer II - Remote, United States') == \
        jp._canon_tokens('Sys Eng 2')


# ---- applied_norm_index / applied_match ------------------------------------

def _idx(jp, applied):
    return jp.applied_norm_index(applied)


def test_applied_match_old_abbreviated_id(jp):
    # the exact miss the normalizer exists for: hand id vs full-title id
    idx = _idx(jp, {'Alkami_SrSRERelease': '2026-05-01'})
    assert jp.applied_match(idx, 'Alkami',
                            'Sr Site Reliability Engineer, Release') == '2026-05-01'


def test_applied_match_subset_role(jp):
    # extra qualifier words in a repost must not defeat the match
    idx = _idx(jp, {'Acme_DevOpsEngineer': '2026-06-01'})
    assert jp.applied_match(idx, 'Acme',
                            'DevOps Engineer - Houston, TX') == '2026-06-01'


def test_applied_match_single_token_must_be_exact(jp):
    # applied 'Analyst' must NOT swallow every 'Senior X Analyst'
    idx = _idx(jp, {'Acme_Analyst': '2026-06-01'})
    assert jp.applied_match(idx, 'Acme', 'Senior Data Analyst') is None
    assert jp.applied_match(idx, 'Acme', 'Analyst') == '2026-06-01'


def test_applied_match_company_containment(jp):
    idx = _idx(jp, {'Alkami_DevOpsEngineer': '2026-06-01'})
    assert jp.applied_match(idx, 'Alkami Technology', 'DevOps Engineer') == '2026-06-01'
    # short-company containment (<4 chars) must not match
    idx = _idx(jp, {'Abc_DevOpsEngineer': '2026-06-01'})
    assert jp.applied_match(idx, 'Abcdef', 'DevOps Engineer') is None


def test_applied_match_wrong_company_or_empty_role(jp):
    idx = _idx(jp, {'Alkami_DevOpsEngineer': '2026-06-01'})
    assert jp.applied_match(idx, 'Globex', 'DevOps Engineer') is None
    assert jp.applied_match(idx, 'Alkami', '') is None


def test_applied_norm_index_shape(jp):
    idx = _idx(jp, {'Acme_DevOpsEngineer': '2026-06-01', 'Solo': 'd'})
    assert len(idx) == 2
    for company, tokens, date in idx:
        assert isinstance(company, str) and isinstance(date, str)
        assert isinstance(tokens, frozenset)
    # key without a role part -> empty token set (never matches)
    assert frozenset() in {t for _, t, _ in idx}


# ---- resolve_status --------------------------------------------------------

def test_resolve_status_applied_beats_skip(jp):
    st = jp.resolve_status('X_Y', {'X_Y': '2026-01-02'}, {'X_Y'})
    assert st == ('applied', '2026-01-02', 'tracked')


def test_resolve_status_norm_fallback(jp):
    idx = _idx(jp, {'Alkami_SrSRERelease': '2026-05-01'})
    st = jp.resolve_status('Alkami_SrSiteReliabilityEngineerRelease', {}, set(),
                           idx, 'Alkami', 'Sr Site Reliability Engineer Release')
    assert st == ('applied', '2026-05-01', 'tracked-norm')


def test_resolve_status_skipped_and_pending(jp):
    assert jp.resolve_status('A_B', {}, {'A_B'}) == ('skipped', '', 'tracked')
    assert jp.resolve_status('A_B', {}, set()) == ('pending', '', '')


# ---- goldens ---------------------------------------------------------------

def test_applied_match_first_entry_wins_on_duplicates(jp):
    # documents the order-dependence directly: same company+role, two dates
    idx = _idx(jp, {'Acme_DevOpsEngineer': '2026-07-08',
                    'Acme_EngineerDevOps': '2026-07-10'})
    assert jp.applied_match(idx, 'Acme', 'DevOps Engineer') == '2026-07-08'


def test_golden_canon_forms(jp):
    for row in GOLDEN['canon']:
        parts = row['key'].split('_', 1)
        assert jp._canon(parts[0]) == row['canonCompany'], row['key']
        assert sorted(jp._canon_tokens(parts[1] if len(parts) > 1 else '')) == \
            row['roleTokens'], row['key']


def test_golden_resolve_status(jp):
    # rebuild in recorded order — applied_match takes the FIRST index hit, so
    # insertion order decides which date wins when two entries cover one role
    applied = dict(GOLDEN['appliedPairs'])
    skip_set = set(GOLDEN['skipSet'])
    idx = jp.applied_norm_index(applied)
    for row in GOLDEN['resolve']:
        st, dt, src = jp.resolve_status(row['id'], applied, skip_set, idx,
                                        row['company'], row['role'])
        assert (st, dt, src) == (row['status'], row['statusDate'],
                                 row['statusSource']), row['id']
        assert jp.applied_match(idx, row['company'], row['role']) == \
            row['appliedMatch'], row['id']
