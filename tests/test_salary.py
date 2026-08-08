"""salary_lookup / _norm_salary_title / _resolve_salary.

The precedence chain (posted -> posting -> jobsworth -> band -> none) is tested
synthetically with SALARY_CACHE pointed at tmp_path, so no live cache churn can
break it. Goldens (tests/fixtures/salary.json) snapshot title normalization +
band lookup for every distinct live role against config/salary_bands.json.
"""
import json

import pytest

from conftest import load_fixture

GOLDEN = load_fixture('salary.json')


# ---- _norm_salary_title ----------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('Sr DevOps Eng', 'senior devops engineer'),
    ('Dev Ops Engineer II', 'devops engineer'),        # fold + Engineer II strip
    ('Dev Sec Ops Engineer', 'devsecops engineer'),
    ('SRE', 'site reliability engineer'),
    ('Infra Sys Admin', 'infrastructure systems administrator'),
    ('Engineering Manager', 'engineer manager'),
    ('', ''),
    (None, ''),
])
def test_norm_salary_title(jp, raw, expected):
    assert jp._norm_salary_title(raw) == expected


# ---- salary_lookup ---------------------------------------------------------

def test_lookup_senior_flag(jp):
    assert jp.salary_lookup('Senior DevOps Engineer')['senior'] is True
    assert jp.salary_lookup('Sr. DevOps Engineer')['senior'] is True
    assert jp.salary_lookup('DevOps Engineer III')['senior'] is True
    assert jp.salary_lookup('DevOps Engineer')['senior'] is False


def test_lookup_abbreviated_title_matches_same_family(jp):
    # the whole point of _norm_salary_title: spelling variants land in one family
    assert jp.salary_lookup('Dev Ops Engineer')['family'] == \
        jp.salary_lookup('DevOps Engineer')['family']


def test_lookup_unknown_title_returns_empty_family(jp):
    got = jp.salary_lookup('Zzqqx Wrangler')
    assert got['family'] == '' and got['range'] == ''


def test_golden_salary(jp):
    for row in GOLDEN:
        assert jp._norm_salary_title(row['title']) == row['norm'], row['title']
        assert jp.salary_lookup(row['title']) == row['lookup'], row['title']


# ---- _resolve_salary precedence chain --------------------------------------

@pytest.fixture
def cache(jp, tmp_path, monkeypatch):
    """Point SALARY_CACHE at a controlled tmp file; return a setter."""
    p = tmp_path / 'salary_cache.json'

    def write(data):
        p.write_text(json.dumps(data), encoding='utf-8')

    write({})
    monkeypatch.setattr(jp, 'SALARY_CACHE', str(p))
    return write


BAND = {'range': '$110,000 - $135,000 (est.)', 'family': 'devops'}


def test_resolve_posted_wins_over_everything(jp, cache):
    cache({'J1': {'salary': '$1', 'source': 'posting'}})
    assert jp._resolve_salary('J1', '$120,000 - $140,000', BAND) == \
        ('$120,000 - $140,000', 'posted')


@pytest.mark.parametrize('posted', ['', '  ', 'Undisclosed', 'N/A', 'none', '-'])
def test_resolve_placeholder_posted_falls_through(jp, cache, posted):
    cache({'J1': {'salary': '$100,000 - $115,000', 'source': 'posting'}})
    assert jp._resolve_salary('J1', posted, BAND) == \
        ('$100,000 - $115,000', 'posting')


def test_resolve_jobsworth_from_cache(jp, cache):
    cache({'J1': {'salary': '$105,000 (est.)', 'source': 'jobsworth'}})
    assert jp._resolve_salary('J1', '', BAND) == ('$105,000 (est.)', 'jobsworth')


def test_resolve_band_last_resort(jp, cache):
    cache({})
    assert jp._resolve_salary('J1', '', BAND) == (BAND['range'], 'band')


def test_resolve_nothing_available(jp, cache):
    cache({})
    assert jp._resolve_salary('J1', '', {}) == ('', 'none')
    assert jp._resolve_salary('J1', 'Undisclosed', None) == ('', 'none')


def test_resolve_never_returns_undisclosed(jp, cache):
    cache({})
    for posted in ('Undisclosed', 'N/A', '-', ''):
        value, _ = jp._resolve_salary('J1', posted, {})
        assert value.lower() not in ('undisclosed', 'n/a', '-')
