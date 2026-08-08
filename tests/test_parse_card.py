"""_parse_card — the deterministic hiringcafe.com card-text parser.

Goldens (tests/fixtures/cards.json) snapshot card texts rebuilt from live
listings. Synthetic tests pin segment routing: salary -> skills -> location ->
title -> company, in that claim order.
"""
import pytest

from conftest import load_fixture

GOLDEN = load_fixture('cards.json')


@pytest.fixture(scope='module')
def ms(jp):
    return jp._matchable()


# Golden values were snapshotted against the owner's live config (skills.json,
# skill_aliases.json, strategy.json, blocklist). A clone with no data root reads an
# empty config and scores everything differently, so this fails for want of data.
@pytest.mark.needs_userdata
def test_full_card(jp, ms):
    matchable, stems = ms
    got = jp._parse_card(
        'Site Reliability Engineer | Acme Corp | Houston, TX | '
        '$120K - $150K | Kubernetes, Terraform, Docker, Python',
        matchable, stems)
    assert got == {'title': 'Site Reliability Engineer', 'company': 'Acme Corp',
                   'location': 'Houston, TX', 'salary': '$120K - $150K',
                   'requiredSkills': ['Kubernetes', 'Terraform', 'Docker', 'Python']}


# Golden values were snapshotted against the owner's live config (skills.json,
# skill_aliases.json, strategy.json, blocklist). A clone with no data root reads an
# empty config and scores everything differently, so this fails for want of data.
@pytest.mark.needs_userdata
def test_segment_order_does_not_matter(jp, ms):
    matchable, stems = ms
    got = jp._parse_card(
        '$120K - $150K | Kubernetes, Terraform, Docker | Acme Corp | '
        'Houston, TX | DevOps Engineer', matchable, stems)
    assert got['salary'] == '$120K - $150K'
    assert got['requiredSkills'] == ['Kubernetes', 'Terraform', 'Docker']
    assert got['location'] == 'Houston, TX'
    assert got['title'] == 'DevOps Engineer'
    assert got['company'] == 'Acme Corp'


def test_capitalized_remote_is_a_location(jp, ms):
    # regression guard: _LOC_RE keywords are (?i:...) so "Remote" as
    # hiringcafe.com renders it is recognized (fixed 2026-07-21)
    matchable, stems = ms
    got = jp._parse_card('DevOps Engineer | Acme Corp | Remote', matchable, stems)
    assert got['location'] == 'Remote'


def test_lowercase_remote_is_a_location(jp, ms):
    matchable, stems = ms
    got = jp._parse_card('DevOps Engineer | Acme Corp | remote', matchable, stems)
    assert got['location'] == 'remote'


def test_state_code_stays_case_sensitive(jp):
    # the (?i:) fix must NOT loosen the ", XX" alternative: lowercase pairs
    # like "Acme, inc" / "Acme, Co" are companies, not locations
    assert jp._LOC_RE.search('Houston, TX')
    assert not jp._LOC_RE.search('Acme, inc')
    assert not jp._LOC_RE.search('Acme, Co')


def test_skills_need_two_known_hits(jp, ms):
    matchable, stems = ms
    # one known skill + unknowns: not enough evidence to claim the segment
    got = jp._parse_card('DevOps Engineer | Kubernetes, Zzqqx, Wxyzz',
                         matchable, stems)
    assert got['requiredSkills'] == []


def test_empty_and_minimal_cards(jp, ms):
    matchable, stems = ms
    empty = jp._parse_card('', matchable, stems)
    assert empty == {'title': '', 'company': '', 'location': '', 'salary': '',
                     'requiredSkills': []}
    assert jp._parse_card(None, matchable, stems) == empty
    # a single unrecognizable segment falls back to title
    got = jp._parse_card('Acme Corp', matchable, stems)
    assert got['company'] == 'Acme Corp' or got['title'] == 'Acme Corp'


def test_salary_k_form(jp, ms):
    matchable, stems = ms
    got = jp._parse_card('Systems Engineer | 120k - 140k | Globex', matchable, stems)
    assert got['salary'] == '120k - 140k'


# Golden values were snapshotted against the owner's live config (skills.json,
# skill_aliases.json, strategy.json, blocklist). A clone with no data root reads an
# empty config and scores everything differently, so this fails for want of data.
@pytest.mark.needs_userdata
def test_golden_cards(jp, ms):
    matchable, stems = ms
    for row in GOLDEN:
        assert jp._parse_card(row['text'], matchable, stems) == row['parsed'], \
            row['text'][:80]
