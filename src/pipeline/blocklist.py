#!/usr/bin/env python3
"""blocklist.py — deterministic employer ban list.

A manually-curated list of employers to auto-skip: ghost-job posters, unrealistic
salary bait, and remote->hybrid switch-bait (Felix being the motivating case).

This is the DETERMINISTIC half of fake-job handling — 100% reliable, no false
positives. It is the opposite of the heuristic ghost-risk scorer (advisory, guesses):
once you ban an employer, every future posting from them is skipped and tagged with
the tactic you recorded, no judgement involved.

Storage: config/employer_blocklist.json
    {"version": 1,
     "employers": {
        "<normkey>": {"display", "reason", "tactic", "added", "examples": [...]}}}

tactic in {ghost, salary, bait-switch, other}
  ghost       — posts roles it has no intent to fill / never closes / reposts
  salary      — unrealistic or bait salary expectations
  bait-switch — advertises remote, then moves to hybrid/onsite after you engage
  other       — anything else worth a permanent skip
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os
import re
import json
from datetime import datetime

from pipelib import CONFIG, cfg, cfg_write, load_json, _safe_write_text, camel_to_words

BLOCKLIST = cfg('employer_blocklist.json')

TACTICS = {'ghost', 'salary', 'bait-switch', 'other'}

# Corporate suffixes / noise stripped before matching, so "Felix", "Felix, Inc.",
# and "felix technologies" all collapse to the same key.
_SUFFIX = {'inc', 'llc', 'corp', 'co', 'ltd', 'plc', 'gmbh', 'group', 'holdings',
           'technologies', 'technology', 'labs', 'software', 'solutions', 'the'}


def norm_employer(name):
    """Normalized match key for a company name (lowercase, suffix-stripped)."""
    s = re.sub(r'[^a-z0-9]+', ' ', camel_to_words(name or '').lower())
    toks = [t for t in s.split() if t not in _SUFFIX]
    return ''.join(toks) or ''.join(s.split())


def load_blocklist():
    d = load_json(BLOCKLIST, None) or {'version': 1, 'employers': {}}
    d.setdefault('employers', {})
    return d


def is_blocked(company):
    """Return the block record for a company, or None. Cheap enough to call per job."""
    if not company:
        return None
    return load_blocklist()['employers'].get(norm_employer(company))


def block_employer(name, reason='', tactic='other', example=''):
    """Add or update an employer ban. Returns the stored record."""
    if not (name or '').strip():
        raise ValueError('employer name required')
    tactic = tactic if tactic in TACTICS else 'other'
    d = load_blocklist()
    key = norm_employer(name)
    rec = d['employers'].get(key) or {
        'display': name.strip(),
        'added': datetime.now().strftime('%Y-%m-%d'),
        'examples': [],
    }
    if reason:
        rec['reason'] = reason
    rec.setdefault('reason', '')
    rec['tactic'] = tactic
    if example and example not in rec['examples']:
        rec['examples'].append(example)
    d['employers'][key] = rec
    _safe_write_text(BLOCKLIST, json.dumps(d, indent=2), verify_json=True)
    return rec


def unblock_employer(name):
    """Remove an employer ban. Returns the removed record, or None if not present."""
    d = load_blocklist()
    key = norm_employer(name)
    rec = d['employers'].pop(key, None)
    if rec is not None:
        _safe_write_text(BLOCKLIST, json.dumps(d, indent=2), verify_json=True)
    return rec


def blocked_employers():
    """List of (key, record) sorted by display name — for `list-blocked`."""
    d = load_blocklist()
    return sorted(d['employers'].items(), key=lambda kv: kv[1].get('display', kv[0]).lower())
