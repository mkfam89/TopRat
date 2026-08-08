"""Tests for ghost_risk.py — the heuristic ghost-job scorer.

The scorer is ADVISORY. These tests pin the calibration (which signals are strong vs.
weak, and the level thresholds) and the salary/repost parsing, so it can't silently
drift back into crying wolf.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ghost_risk as g  # noqa: E402

TODAY = datetime(2026, 7, 21)


def base(**kw):
    row = {'id': 'X_Role', 'company': 'X', 'role': 'Role', 'status': 'candidate',
           'foundDate': '2026-07-20'}
    row.update(kw)
    return row


# ---- salary parsing ------------------------------------------------------

@pytest.mark.parametrize("s,expect", [
    ('$98K-$131K', (98000, 131000)),
    ('$100,000-$130,000', (100000, 130000)),
    ('$90k–$160k', (90000, 160000)),      # en-dash
    ('$120,000', (120000, 120000)),
    ('90-130', (90000, 130000)),          # bare thousands
    ('Undisclosed', None),
    ('', None),
    ('Competitive', None),
])
def test_parse_salary_range(s, expect):
    assert g.parse_salary_range(s) == expect


# ---- individual signals --------------------------------------------------

def test_no_salary_is_weak_only():
    # no salary alone must NOT reach high or medium — it is common, hence weak
    assert g.score_job(base(), today=TODAY)['level'] == 'low'


def test_wide_salary_is_strong():
    r = g.score_job(base(salary='$60,000-$180,000'), today=TODAY)
    assert 'salary_width' in r['signals']
    assert r['level'] == 'medium'   # one strong signal


def test_normal_salary_not_flagged():
    r = g.score_job(base(salary='$120,000-$140,000'), today=TODAY)
    assert 'salary_width' not in r['signals']


def test_remote_hybrid_conflict_is_strong():
    r = g.score_job(base(salary='$120k', location='Remote (Hybrid)'), today=TODAY)
    assert 'remote_hybrid' in r['signals']
    assert r['level'] == 'medium'


def test_plain_remote_not_flagged():
    r = g.score_job(base(salary='$120k', location='Remote - US'), today=TODAY)
    assert 'remote_hybrid' not in r['signals']


def test_repost_is_strong():
    r = g.score_job(base(salary='$120k'), reposted=True, today=TODAY)
    assert 'repost' in r['signals']
    assert r['level'] == 'medium'


def test_stale_active_is_weak():
    r = g.score_job(base(salary='$120k', foundDate='2026-04-01'), today=TODAY)
    assert 'stale_active' in r['signals']
    assert r['level'] == 'low'


def test_stale_ignored_once_applied():
    r = g.score_job(base(salary='$120k', foundDate='2026-01-01', status='applied'), today=TODAY)
    assert 'stale_active' not in r['signals']


# ---- level thresholds ----------------------------------------------------

def test_two_strong_signals_is_high():
    r = g.score_job(base(salary='$50k-$200k', location='Remote / Hybrid'), today=TODAY)
    assert r['level'] == 'high'
    assert set(r['signals']) >= {'salary_width', 'remote_hybrid'}


def test_repost_plus_wide_is_high():
    r = g.score_job(base(salary='$40k-$200k'), reposted=True, today=TODAY)
    assert r['level'] == 'high'


def test_clean_job_is_none():
    r = g.score_job(base(salary='$120,000-$140,000', location='Houston, TX'), today=TODAY)
    assert r['level'] == 'none'
    assert r['signals'] == []


# ---- repost index --------------------------------------------------------

def test_repost_needs_distinct_ids_and_time_gap():
    # same applyUrl, two ids, 30 days apart -> repost
    rows = [
        {'id': 'A_1', 'company': 'A', 'role': 'Eng', 'applyUrl': 'u1', 'foundDate': '2026-06-01'},
        {'id': 'A_2', 'company': 'A', 'role': 'Eng', 'applyUrl': 'u1', 'foundDate': '2026-07-01'},
    ]
    assert g.build_repost_index(rows) == {'A_1', 'A_2'}


def test_same_week_rescrape_is_not_repost():
    rows = [
        {'id': 'B_1', 'company': 'B', 'role': 'R', 'applyUrl': 'u2', 'foundDate': '2026-07-01'},
        {'id': 'B_2', 'company': 'B', 'role': 'R', 'applyUrl': 'u2', 'foundDate': '2026-07-03'},
    ]
    assert g.build_repost_index(rows) == set()


def test_single_listing_is_not_repost():
    rows = [{'id': 'C_1', 'company': 'C', 'role': 'R', 'applyUrl': 'u3', 'foundDate': '2026-07-01'}]
    assert g.build_repost_index(rows) == set()


# ---- score_all + config --------------------------------------------------

def test_score_all_skips_none_level():
    rows = [base(id='clean', salary='$120,000-$140,000', location='Austin, TX'),
            base(id='risky', salary='$40k-$200k', location='Remote / Hybrid')]
    out = g.score_all(rows, {})
    assert 'clean' not in out          # level none is omitted
    assert out['risky']['level'] == 'high'


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(g, 'load_cfg', lambda: dict(g.DEFAULTS, enabled=False))
    assert g.score_all([base(id='risky', salary='$40k-$200k', location='Remote/Hybrid')], {}) == {}


def test_caveat_exists():
    assert 'guess' in g.CAVEAT.lower()
