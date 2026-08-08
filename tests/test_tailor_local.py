"""Guards for the render_resume wiring in tailor_local.py.

The key property: when NO content.json is present (today's default), behavior is
unchanged (verbatim template path). When one IS present, _resolve_content finds it so
the render path can pick it up. Full main() is not exercised here — it shells out to
jobpipe and writes to New/ — but the decision helper is.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tailor_local as tl  # noqa: E402


def test_inline_content_wins():
    content = {"company": "Acme", "role": "Eng"}
    assert tl._resolve_content({"content": content}, "Acme_Eng", "Acme") == content


def test_no_content_returns_none(tmp_path, monkeypatch):
    # nothing inline, nothing on disk -> None (=> caller uses verbatim template path)
    monkeypatch.setattr(tl, "P", lambda *p: os.path.join(str(tmp_path), *p))
    assert tl._resolve_content({}, "Foo_Bar", "Foo") is None


def test_content_file_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "P", lambda *p: os.path.join(str(tmp_path), *p))
    os.makedirs(os.path.join(str(tmp_path), "New", "Foo"))
    content = {"company": "Foo", "role": "Bar", "summary": "x"}
    with open(os.path.join(str(tmp_path), "New", "Foo", "Foo_Bar.content.json"), "w") as f:
        json.dump(content, f)
    assert tl._resolve_content({}, "Foo_Bar", "Foo") == content


def test_bad_content_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "P", lambda *p: os.path.join(str(tmp_path), *p))
    p = os.path.join(str(tmp_path), "Foo_Bar.content.json")
    with open(p, "w") as f:
        f.write("{ not valid json")
    assert tl._resolve_content({}, "Foo_Bar", "Foo") is None
