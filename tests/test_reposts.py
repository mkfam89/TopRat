"""Tests for reposts.py — the advisory repost detector.

The detector is ADVISORY: nothing in the pipeline excludes a job because of it. These
tests pin the matching (applyUrl strong-match, company+role fuzzy-match, the time-gap
guard against same-week re-scrapes) and the wording of the note (last-seen date + prior
applied/skipped status + prior user note), so it can't silently drift.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reposts as rp  # noqa: E402

TODAY = datetime(2026, 7, 22)


def hist(**entries):
    """job_details-style history dict: id -> {foundDate, applyUrl, ...}."""
    return entries


# ---- url normalization ---------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ('https://job-boards.greenhouse.io/x/jobs/123', 'http://job-boards.greenhouse.io/x/jobs/123/'),
    ('https://WWW.Acme.com/apply?src=li', 'https://acme.com/apply'),
    ('https://acme.com/apply#form', 'https://acme.com/apply'),
])
def test_norm_url_collapses_equivalents(a, b):
    assert rp.norm_url(a) == rp.norm_url(b)


# ---- strong match: same applyUrl, different id ---------------------------

def test_url_match_flags_repost_ignoring_gap():
    # prior sighting only 2 days old, but a MATCHING applyUrl under a different id is
    # a genuine re-listing -> flagged regardless of the gap.
    jd = hist(Acme_DataAnalyst={'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-20'})
    row = {'id': 'Acme_SeniorDataAnalyst', 'company': 'Acme', 'role': 'Senior Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    out = rp.annotate([row], jd, applied={'Acme_DataAnalyst': '2026-07-21'}, today=TODAY)
    assert 'Acme_SeniorDataAnalyst' in out
    assert out['Acme_SeniorDataAnalyst']['priorStatus'] == 'applied'
    assert out['Acme_SeniorDataAnalyst']['note'].startswith('Repost - last seen 2026-07-20; you applied 2026-07-21')


def test_same_id_is_not_a_repost():
    # the exact same posting re-scraped keeps its id -> never a repost.
    jd = hist(Acme_DataAnalyst={'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-05-01'})
    row = {'id': 'Acme_DataAnalyst', 'company': 'Acme', 'role': 'Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    assert rp.annotate([row], jd, today=TODAY) == {}


# ---- fuzzy company+role match requires the time gap ----------------------

def test_company_role_match_beyond_gap_flags():
    # prior "Data Analyst" reposted as "Senior Data Analyst" (a superset title) under a
    # new id, no applyUrl to lean on -> the fuzzy company+role path catches it past the gap.
    jd = hist(Globex_DataAnalyst={'applyUrl': '', 'foundDate': '2026-06-01'})
    row = {'id': 'Globex_SeniorDataAnalyst', 'company': 'Globex',
           'role': 'Senior Data Analyst', 'applyUrl': '', 'foundDate': '2026-07-22'}
    out = rp.annotate([row], jd, skipped=['Globex_DataAnalyst'], today=TODAY)
    assert 'Globex_SeniorDataAnalyst' in out
    assert 'you skipped it' in out['Globex_SeniorDataAnalyst']['note']


def test_company_role_match_within_gap_is_ignored():
    # no applyUrl to strong-match on, prior only 3 days old -> a fresh re-scrape, not a repost.
    jd = hist(Globex_DataAnalyst={'applyUrl': '', 'foundDate': '2026-07-19'})
    row = {'id': 'Globex_SeniorDataAnalyst', 'company': 'Globex',
           'role': 'Senior Data Analyst', 'applyUrl': '', 'foundDate': '2026-07-22'}
    assert rp.annotate([row], jd, today=TODAY) == {}


def test_single_token_role_does_not_over_match():
    # 'Analyst' must not swallow 'Senior Data Analyst'.
    jd = hist(Acme_Analyst={'applyUrl': '', 'foundDate': '2026-05-01'})
    row = {'id': 'Acme_SeniorDataAnalyst', 'company': 'Acme', 'role': 'Senior Data Analyst',
           'applyUrl': '', 'foundDate': '2026-07-22'}
    assert rp.annotate([row], jd, today=TODAY) == {}


# ---- note content --------------------------------------------------------

def test_note_includes_prior_user_note():
    jd = hist(Acme_DataAnalyst={'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-05-01'})
    row = {'id': 'Acme_LeadDataAnalyst', 'company': 'Acme', 'role': 'Lead Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    out = rp.annotate([row], jd, notes={'Acme_DataAnalyst': 'emailed the recruiter'}, today=TODAY)
    assert 'Prior note: emailed the recruiter' in out['Acme_LeadDataAnalyst']['note']


def test_pending_prior_reads_not_yet_actioned():
    jd = hist(Acme_DataAnalyst={'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-05-01'})
    row = {'id': 'Acme_LeadDataAnalyst', 'company': 'Acme', 'role': 'Lead Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    out = rp.annotate([row], jd, today=TODAY)
    assert 'not yet actioned' in out['Acme_LeadDataAnalyst']['note']


def test_no_history_no_flags():
    row = {'id': 'Acme_DataAnalyst', 'company': 'Acme', 'role': 'Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    assert rp.annotate([row], {}, today=TODAY) == {}


def test_disabled_returns_empty():
    jd = hist(Acme_DataAnalyst={'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-05-01'})
    row = {'id': 'Acme_LeadDataAnalyst', 'company': 'Acme', 'role': 'Lead Data Analyst',
           'applyUrl': 'https://acme.com/jobs/1', 'foundDate': '2026-07-22'}
    assert rp.annotate([row], jd, cfg={'enabled': False, 'gap_days': 7}, today=TODAY) == {}
