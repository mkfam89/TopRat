"""Guards for `jobpipe doctor` (_doctor_collect).

doctor is the session-start health check. These tests exist mainly so the Step 6
module split can't silently break it: they assert the collector returns the expected
shape, degrades gracefully on missing/broken state, and never writes to the repo.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jobpipe  # noqa: E402


def test_collect_returns_expected_shape():
    d = jobpipe._doctor_collect()
    for key in ("ok", "errors", "warnings", "jobs", "tracker", "queue_depth", "last_runs"):
        assert key in d, f"missing key {key!r}"
    assert isinstance(d["ok"], bool)
    assert isinstance(d["errors"], list)
    assert isinstance(d["warnings"], list)


def test_collect_is_read_only(tmp_path):
    """Running the collector must not change any state file's mtime."""
    targets = [jobpipe.JSON_PATH, jobpipe.TRACKING_CSV,
               os.path.join(jobpipe.BASE, "scheduler_state.json")]
    before = {p: os.path.getmtime(p) for p in targets if os.path.exists(p)}
    jobpipe._doctor_collect()
    for p, mt in before.items():
        assert os.path.getmtime(p) == mt, f"doctor mutated {p}"


def test_broken_tracker_is_flagged(monkeypatch, tmp_path):
    """A tracker that exists but won't parse must produce an ERROR, not an exception."""
    bad = tmp_path / "job_tracker.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    monkeypatch.setattr(jobpipe, "JSON_PATH", str(bad))
    d = jobpipe._doctor_collect()
    assert d["ok"] is False
    assert any("job_tracker.json" in e for e in d["errors"])


def test_truncation_cross_check(monkeypatch, tmp_path):
    """A tracker under 50% of the newest backup must be flagged as truncated."""
    tracker = tmp_path / "job_tracker.json"
    tracker.write_text('{"applied": {}}', encoding="utf-8")  # tiny but valid
    snap = tmp_path / "Backups" / "snapshots" / "20260101_000000"
    snap.mkdir(parents=True)
    (snap / "job_tracker.json").write_text("x" * 100_000, encoding="utf-8")
    monkeypatch.setattr(jobpipe, "JSON_PATH", str(tracker))
    monkeypatch.setattr(jobpipe, "BASE", str(tmp_path))
    d = jobpipe._doctor_collect()
    assert any("truncat" in e.lower() for e in d["errors"])
