"""Tests for the deterministic employer blocklist and its enforcement.

Covers blocklist.py in isolation (monkeypatched to a tmp file so the real
config/employer_blocklist.json is never touched) plus the two enforcement hooks in
tracker.py: resolve_status and cmd_candidates.
"""
import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import blocklist  # noqa: E402
import tracker  # noqa: E402


@pytest.fixture
def tmp_blocklist(tmp_path, monkeypatch):
    """Point blocklist storage at a throwaway file for the duration of a test."""
    path = tmp_path / "employer_blocklist.json"
    monkeypatch.setattr(blocklist, "BLOCKLIST", str(path))
    return path


# ---- normalization -------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Felix", "felix"),
    ("Felix", "Felix, Inc."),
    ("Felix", "FELIX Technologies"),
    ("Acme Labs", "acme"),
    ("Big Co LLC", "big"),
])
def test_norm_collapses_variants(a, b):
    assert blocklist.norm_employer(a) == blocklist.norm_employer(b)


def test_norm_does_not_overmatch():
    assert blocklist.norm_employer("Felix") != blocklist.norm_employer("Felixx")


# ---- block / unblock roundtrip ------------------------------------------

def test_block_then_is_blocked(tmp_blocklist):
    blocklist.block_employer("Felix", reason="bait-switch remote->hybrid",
                             tactic="bait-switch", example="Felix_SrEng")
    rec = blocklist.is_blocked("felix, inc.")
    assert rec is not None
    assert rec["tactic"] == "bait-switch"
    assert "Felix_SrEng" in rec["examples"]


def test_unblock_removes(tmp_blocklist):
    blocklist.block_employer("Felix", tactic="ghost")
    assert blocklist.is_blocked("Felix")
    removed = blocklist.unblock_employer("Felix")
    assert removed is not None
    assert blocklist.is_blocked("Felix") is None


def test_unblock_absent_is_none(tmp_blocklist):
    assert blocklist.unblock_employer("Nobody") is None


def test_invalid_tactic_falls_back_to_other(tmp_blocklist):
    rec = blocklist.block_employer("Weird", tactic="not-a-real-tactic")
    assert rec["tactic"] == "other"


def test_empty_name_rejected(tmp_blocklist):
    with pytest.raises(ValueError):
        blocklist.block_employer("   ")


def test_reblock_updates_without_duplicating(tmp_blocklist):
    blocklist.block_employer("Felix", tactic="ghost", example="a")
    blocklist.block_employer("Felix", tactic="salary", example="a")  # same example
    rows = blocklist.blocked_employers()
    assert len(rows) == 1
    assert rows[0][1]["tactic"] == "salary"
    assert rows[0][1]["examples"] == ["a"]


# ---- enforcement: resolve_status ----------------------------------------

def test_resolve_status_blocks_employer(tmp_blocklist):
    blocklist.block_employer("Felix", tactic="bait-switch")
    st, dt, src = tracker.resolve_status("Felix_SrEng", {}, set(), None, "Felix", "Sr Engineer")
    assert st == "skipped"
    assert src == "blocked"


def test_applied_beats_block(tmp_blocklist):
    """Banning an employer must never rewrite a job already applied to."""
    blocklist.block_employer("Felix", tactic="ghost")
    st, dt, src = tracker.resolve_status(
        "Felix_SrEng", {"Felix_SrEng": "2026-07-01"}, set(), None, "Felix", "Sr Engineer")
    assert st == "applied"


def test_unblocked_employer_is_pending(tmp_blocklist):
    st, dt, src = tracker.resolve_status("Acme_Eng", {}, set(), None, "Acme", "Engineer")
    assert st == "pending"


# ---- enforcement: cmd_candidates ----------------------------------------

# Golden values were snapshotted against the owner's live config (skills.json,
# skill_aliases.json, strategy.json, blocklist). A clone with no data root reads an
# empty config and scores everything differently, so this fails for want of data.
@pytest.mark.needs_userdata
def test_candidates_excludes_blocked_from_ready(tmp_blocklist, tmp_path, monkeypatch):
    blocklist.block_employer("Felix", tactic="bait-switch")
    listings = [
        {"title": "Senior Engineer", "company": "Felix", "requiredSkills": ["Python"],
         "location": "Remote", "salary": "$150k", "source": "test"},
        {"title": "Senior Engineer", "company": "Acme", "requiredSkills": ["Python"],
         "location": "Remote", "salary": "$150k", "source": "test"},
    ]
    lpath = tmp_path / "listings.json"
    lpath.write_text(json.dumps(listings), encoding="utf-8")
    cpath = tmp_path / "candidates.csv"
    monkeypatch.setattr(tracker, "CANDIDATES_CSV", str(cpath))
    monkeypatch.setattr(tracker, "read_tracker", lambda: {})
    # Hermetic: keep the real job_tracker.html / New/ folder state from leaking in
    # via seed_applied()/scan_active_jobs() and fuzzy-matching a synthetic job.
    monkeypatch.setattr(tracker, "seed_applied", lambda t: {})
    monkeypatch.setattr(tracker, "scan_active_jobs", lambda t: [])
    # force both jobs above the discovery floor regardless of real skill config
    monkeypatch.setattr(tracker, "skill_match", lambda skills: (0.95, []))

    class A:
        listings = str(lpath)
        threshold = 0.6
    tracker.cmd_candidates(A())

    rows = list(csv.DictReader(open(cpath, encoding="utf-8")))
    by_company = {r["company"]: r for r in rows}
    assert by_company["Felix"]["status"] == "blocked"
    assert by_company["Acme"]["status"] in ("candidate", "tailored")
    # the blocked row must carry hasResume handling but never be "ready" — status gate
    assert by_company["Felix"]["status"] == "blocked"
