"""Tests for the pay floor — profile.json search.salary_min applied to the RESULT.

Two halves, mirroring test_blocklist.py: payfilter.py in isolation (pure functions, no
file access — the cache and the minimum are passed in), then the enforcement hook in
tracker.cmd_candidates.

The rule under test, in one line: drop a pending job only when a REAL source (posted /
posting / jobsworth) puts the TOP of its range under the minimum. Most of these cases
exist to prove the filter does NOTHING — a band guess, silence, an hourly rate and a job
already decided are all reasons to keep, and each has its own test because each was a
deliberate choice rather than an oversight.
"""
import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import payfilter  # noqa: E402
import tracker  # noqa: E402

MIN = 105000


def row(jid='X_Y', salary='', status='candidate', hasResume='no', **kw):
    r = {'id': jid, 'salary': salary, 'status': status, 'hasResume': hasResume}
    r.update(kw)
    return r


def cache(jid, salary, source):
    return {jid: {'salary': salary, 'source': source}}


# ---- top_figure: read the ceiling, or refuse to read at all ---------------

@pytest.mark.parametrize("text,want", [
    ('$95K-$130K', 130000),
    ('$56,000-$71,000 (est.)', 71000),
    ('$106K', 106000),
    ('$98,000 - $131,000', 131000),
    ('$120,000', 120000),
    ('$130', 130000),          # bare 3-digit shorthand: no salary is $130/yr
    ('', None),
    ('Undisclosed', None),
    ('competitive', None),
    ('$60/hr', None),          # per-hour cannot be compared without assuming hours
    ('$45 per hour', None),
    ('$8,500 per month', None),
])
def test_top_figure(text, want):
    assert payfilter.top_figure(text) == want


def test_top_figure_takes_the_ceiling_not_the_floor():
    """The whole point of the edge rule: a range that STARTS low is not a low job."""
    assert payfilter.top_figure('$60,000-$180,000') == 180000


# ---- resolve: source comes from the cache, never from the string ----------

def test_posted_column_wins_over_the_probe():
    r = row('A', '$120,000')
    assert payfilter.resolve(r, cache('A', '$50,000-$60,000 (est.)', 'jobsworth')) == \
        ('$120,000', 'posted')


def test_est_in_the_column_is_a_band_price_not_posted_pay():
    """A band figure written back by an earlier run carries '(est.)' in the same column
    real pay uses. It must fall through to the cache, or a band guess would be treated
    as the posting's own statement."""
    assert payfilter.resolve(row('A', '$80K-$95K (est.)'), {}) == ('$80K-$95K (est.)', 'band')


def test_adzuna_est_suffix_is_not_mistaken_for_a_band():
    """Adzuna's own string also ends in '(est.)' — 307 of 429 cached entries do. Sniffing
    the string would misclassify every one of them; the source field is authoritative."""
    val, src = payfilter.resolve(row('A'), cache('A', '$56,000-$71,000 (est.)', 'jobsworth'))
    assert (val, src) == ('$56,000-$71,000 (est.)', 'jobsworth')


def test_no_figure_anywhere_resolves_to_none():
    assert payfilter.resolve(row('A'), {}) == ('', 'none')


# ---- verdict: who gets dropped -------------------------------------------

def test_drops_on_posted_pay_below_the_minimum():
    keep, why = payfilter.verdict(row('A', '$70,000-$90,000'), MIN, {})
    assert keep is False and '$105,000' in why


def test_drops_on_an_adzuna_prediction_below_the_minimum():
    keep, _ = payfilter.verdict(row('A'), MIN, cache('A', '$56,000-$71,000 (est.)', 'jobsworth'))
    assert keep is False


def test_keeps_when_the_top_of_the_range_clears():
    keep, _ = payfilter.verdict(row('A', '$95,000-$130,000'), MIN, {})
    assert keep is True


def test_keeps_a_band_guess_however_low():
    """A salary_bands.json family average is this pipeline's own guess. Dropping a job on
    it would be the tool arguing with itself."""
    keep, _ = payfilter.verdict(row('A', '$40K-$50K (est.)'), MIN, {})
    assert keep is True


def test_keeps_a_job_with_no_pay_information():
    keep, _ = payfilter.verdict(row('A'), MIN, {})
    assert keep is True


def test_keeps_an_hourly_rate_rather_than_guessing_the_hours():
    keep, _ = payfilter.verdict(row('A', '$60/hr'), MIN, {})
    assert keep is True


@pytest.mark.parametrize("field,value", [
    ('status', 'applied'), ('status', 'tailored'), ('status', 'skipped'),
    ('status', 'blocked'), ('hasResume', 'yes'),
    ('queued', 'yes'), ('bookmarked', 'yes'),
])
def test_never_drops_a_decided_or_worked_on_job(field, value):
    keep, _ = payfilter.verdict(row('A', '$40,000', **{field: value}), MIN, {})
    assert keep is True


def test_a_zero_minimum_turns_the_whole_filter_off():
    keep, _ = payfilter.verdict(row('A', '$10,000'), 0, {})
    assert keep is True


def test_exactly_the_minimum_is_not_below_it():
    keep, _ = payfilter.verdict(row('A', '$105,000'), MIN, {})
    assert keep is True


# ---- filter_rows / sweep -------------------------------------------------

def test_filter_rows_splits_and_explains():
    rows = [row('low', '$60,000'), row('ok', '$150,000'), row('quiet')]
    kept, dropped = payfilter.filter_rows(rows, MIN, {})
    assert [r['id'] for r in kept] == ['ok', 'quiet']
    assert len(dropped) == 1 and dropped[0][0]['id'] == 'low' and '$60,000' in dropped[0][1]


def test_sweep_rewrites_the_csv_and_keeps_the_columns(tmp_path, monkeypatch):
    p = tmp_path / 'candidates.csv'
    cols = ['id', 'company', 'role', 'status', 'hasResume', 'salary', 'skillMatch']
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(dict(zip(cols, ['low', 'A', 'r', 'candidate', 'no', '$60,000', '0.9'])))
        w.writerow(dict(zip(cols, ['ok', 'B', 'r', 'candidate', 'no', '$150,000', '0.5'])))
    monkeypatch.setattr(payfilter, 'min_expected', lambda *a, **k: MIN)
    monkeypatch.setattr(payfilter, 'load_json', lambda *a, **k: {})
    res = payfilter.sweep(str(p), verbose=False)
    assert (res['scanned'], res['kept'], res['dropped']) == (2, 1, 1)
    with open(p, encoding='utf-8') as f:
        rd = csv.DictReader(f)
        assert rd.fieldnames == cols
        assert [r['id'] for r in rd] == ['ok']


def test_sweep_does_not_touch_the_file_when_nothing_is_dropped(tmp_path, monkeypatch):
    """A no-op run must leave the mtime alone — the dashboard watches this file."""
    p = tmp_path / 'candidates.csv'
    p.write_text('id,status,hasResume,salary\nok,candidate,no,$150000\n', encoding='utf-8')
    monkeypatch.setattr(payfilter, 'min_expected', lambda *a, **k: MIN)
    monkeypatch.setattr(payfilter, 'load_json', lambda *a, **k: {})
    before = p.stat().st_mtime_ns
    res = payfilter.sweep(str(p), verbose=False)
    assert res['dropped'] == 0 and p.stat().st_mtime_ns == before


def test_sweep_is_a_no_op_when_the_minimum_is_zero(tmp_path, monkeypatch):
    p = tmp_path / 'candidates.csv'
    p.write_text('id,status,hasResume,salary\nlow,candidate,no,$10000\n', encoding='utf-8')
    monkeypatch.setattr(payfilter, 'min_expected', lambda *a, **k: 0)
    res = payfilter.sweep(str(p), verbose=False)
    assert res['dropped'] == 0 and 'low' in p.read_text(encoding='utf-8')


def test_missing_candidates_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(payfilter, 'min_expected', lambda *a, **k: MIN)
    assert payfilter.sweep(str(tmp_path / 'nope.csv'), verbose=False)['scanned'] == 0


# ---- enforcement inside discovery ----------------------------------------

def _run_candidates(tmp_path, monkeypatch, listings, cache_map, minimum=MIN):
    """Drive tracker.cmd_candidates over a throwaway data root and return its rows."""
    out = tmp_path / 'candidates.csv'
    monkeypatch.setattr(tracker, 'CANDIDATES_CSV', str(out))
    monkeypatch.setattr(tracker, 'read_tracker', lambda: {})
    monkeypatch.setattr(tracker, 'load_listings', lambda *a, **k: listings)
    monkeypatch.setattr(tracker, 'strategy', lambda: {'discovery_min_match': 0.0, 'min_match': 0.6})
    monkeypatch.setattr(payfilter, 'min_expected', lambda *a, **k: minimum)
    monkeypatch.setattr(payfilter, 'load_json', lambda *a, **k: cache_map)

    class A:
        listings, threshold = 'x.json', None
    tracker.cmd_candidates(A())
    with open(out, encoding='utf-8') as f:
        return {r['id']: r for r in csv.DictReader(f)}


def test_cmd_candidates_drops_the_underpaid_job(tmp_path, monkeypatch):
    rows = _run_candidates(tmp_path, monkeypatch, [
        {'title': 'Data Analyst', 'company': 'Poor Co', 'salary': '$60,000 - $80,000',
         'requiredSkills': ['sql'], 'applyUrl': 'u'},
        {'title': 'Data Analyst', 'company': 'Rich Co', 'salary': '$120,000 - $150,000',
         'requiredSkills': ['sql'], 'applyUrl': 'u'},
    ], {})
    # Assert on the company column, not the id: clean_company() strips "Co" as a legal
    # suffix, so the ids are Poor_DataAnalyst / Rich_DataAnalyst.
    assert sorted(r['company'] for r in rows.values()) == ['Rich Co']


def test_cmd_candidates_leaves_everything_when_no_minimum_is_set(tmp_path, monkeypatch):
    rows = _run_candidates(tmp_path, monkeypatch, [
        {'title': 'Data Analyst', 'company': 'Poor Co', 'salary': '$60,000 - $80,000',
         'requiredSkills': ['sql'], 'applyUrl': 'u'},
    ], {}, minimum=0)
    assert len(rows) == 1
