"""Guards that the dashboard wires the blocklist + ghost-risk endpoints without breaking.

A real HTTP round-trip is exercised manually / via curl; here we keep it hermetic and
fast: confirm the server module imports with its new optional deps, and that the block
endpoint path actually persists through blocklist.py (the thin-wrapper contract).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_dashboard_imports_with_new_modules():
    import dashboard_server as ds
    # the new optional modules must have loaded (not swallowed by the try/except)
    assert ds.bl is not None, "blocklist module failed to import into dashboard"
    assert ds.gr is not None, "ghost_risk module failed to import into dashboard"


def _queue_post(tmp_path, monkeypatch, body):
    """Drive the /api/queue POST handler against a temp to_process.json.

    The handler is inline in do_POST, so we call it through a stub request object rather
    than opening a socket — hermetic, and it still exercises the real merge / remove /
    note-retention branches.
    """
    import json
    import dashboard_server as ds

    qfile = tmp_path / "to_process.json"
    monkeypatch.setattr(ds, "TO_PROCESS", str(qfile))
    raw = json.dumps(body).encode()

    class _Rfile:
        def read(self, n): return raw

    h = ds.Handler.__new__(ds.Handler)
    h.path = "/api/queue"
    h.headers = {"Content-Length": str(len(raw))}
    h.rfile = _Rfile()
    sent = {}
    h._send = lambda code, payload, *a, **k: sent.update(code=code, body=json.loads(payload))
    ds.Handler.do_POST(h)
    return sent["body"], json.loads(qfile.read_text(encoding="utf-8"))


def test_queue_add_remove_and_note_retention(tmp_path, monkeypatch):
    """Queue → unqueue → re-queue, the contract the card board's tailor menu relies on.

    The customization note must SURVIVE an unqueue (so it can be edited and re-queued
    without retyping), and only an explicit empty note deletes it.
    """
    res, q = _queue_post(tmp_path, monkeypatch, {"ids": ["a1"], "notes": {"a1": "lead with SRE"}})
    assert res["queued"] == 1 and q["ids"] == ["a1"]
    assert q["notes"]["a1"] == "lead with SRE"

    # unqueue: the id leaves the queue, the note stays behind for later editing
    res, q = _queue_post(tmp_path, monkeypatch, {"remove": ["a1"]})
    assert res["removed"] == 1 and q["ids"] == []
    assert q["notes"]["a1"] == "lead with SRE"

    # editing the note without ids must not re-queue the job
    res, q = _queue_post(tmp_path, monkeypatch, {"notes": {"a1": "edited"}})
    assert q["ids"] == [] and q["notes"]["a1"] == "edited"

    # an empty note is the delete path
    res, q = _queue_post(tmp_path, monkeypatch, {"notes": {"a1": ""}})
    assert "notes" not in q or "a1" not in q.get("notes", {})

    # re-queue works after the round trip
    res, q = _queue_post(tmp_path, monkeypatch, {"ids": ["a1"]})
    assert q["ids"] == ["a1"]


def test_block_endpoint_contract(tmp_path, monkeypatch):
    """The /api/block-employer handler is a thin wrapper: calling block_employer with
    the same body must persist to the blocklist file."""
    import blocklist
    monkeypatch.setattr(blocklist, "BLOCKLIST", str(tmp_path / "bl.json"))
    rec = blocklist.block_employer("Dashboard Test Co", reason="via api", tactic="ghost")
    assert rec["tactic"] == "ghost"
    assert blocklist.is_blocked("dashboard test co") is not None
