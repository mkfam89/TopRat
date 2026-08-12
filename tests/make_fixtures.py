#!/usr/bin/env python3
"""Regenerate the golden fixtures in tests/fixtures/ from the LIVE repo state.

    python tests/make_fixtures.py

Read-only over repo data (listings.json, tracking.csv, candidates.csv,
job_tracker.json, config/*); writes ONLY tests/fixtures/*.json.

Goldens are snapshots: they encode current behavior against current config.
If config/skills.json, skill_aliases.json, salary_bands.json or strategy.json
change on purpose, rerun this script and review the fixture diff like code.
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import jobpipe as jp  # noqa: E402

FIX = os.path.join(ROOT, 'tests', 'fixtures')
os.makedirs(FIX, exist_ok=True)


def _load_csv(name):
    with open(os.path.join(ROOT, name), newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _dump(name, obj):
    with open(os.path.join(FIX, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    print('wrote', name)


def depersonalize_classify(cls):
    """Strip the owner's base-resume FILENAME out of a classify() snapshot.

    tests/fixtures/ ships in the public export, and the base resume is named
    after the person ('First_Last_Resume_data_analyst.docx'). A golden exists to
    pin behaviour, and the behaviour here is "CORE picks the CORE base", not
    "the file is called X" — which file fills a slot is this user's private
    data. tests/test_scoring.py::test_golden_classify applies the SAME mapping to
    the live output before comparing, so the assertion still bites.

    Keep this in sync with that test, and never remove it: the resume filenames
    are exactly what release.py's PII gate blocks a ship on.
    """
    cls = dict(cls)
    if cls.get('baseResume'):
        cls['baseResume'] = '<%s base>' % (cls.get('category') or 'UNKNOWN')
    return cls


listings = jp.load_json(os.path.join(ROOT, 'listings.json'), []) or []
tracker = jp.load_json(os.path.join(ROOT, 'job_tracker.json'), {}) or {}
tracking = _load_csv('tracking.csv')
candidates = _load_csv('candidates.csv')

# ---- scoring: classify + skill_match + flag_skills over every live listing ----
scoring = []
for L in listings:
    req = L.get('requiredSkills') or []
    match, flagged = jp.skill_match(req)
    scoring.append({
        'id': L.get('id', ''), 'title': L.get('title', ''),
        'requiredSkills': req,
        'classify': depersonalize_classify(jp.classify(L.get('title', ''), req)),
        'skillMatch': match, 'flagged': flagged,
    })
_dump('scoring.json', scoring)

# ---- applied reconciliation: canon forms + resolve_status over tracking rows ----
applied_map = tracker.get('applied', {}) or {}
skip_set = sorted(set(tracker.get('skipped_jobs', []) or []))
idx = jp.applied_norm_index(applied_map)
canon = []
for k in sorted(applied_map):
    parts = k.split('_', 1)
    canon.append({'key': k,
                  'canonCompany': jp._canon(parts[0]),
                  'roleTokens': sorted(jp._canon_tokens(parts[1] if len(parts) > 1 else ''))})
recon = []
for row in tracking:
    st, dt, src = jp.resolve_status(row['id'], applied_map, set(skip_set), idx,
                                    row['company'], row['role'])
    recon.append({'id': row['id'], 'company': row['company'], 'role': row['role'],
                  'appliedMatch': jp.applied_match(idx, row['company'], row['role']),
                  'status': st, 'statusDate': dt, 'statusSource': src})
# appliedMap is stored as ORDERED [key, date] pairs: applied_match returns the
# first index entry that matches, so when two applied entries cover the same
# company+role (repost marked twice) the map's insertion order decides the date.
_dump('applied.json', {'appliedPairs': list(applied_map.items()), 'skipSet': skip_set,
                       'canon': canon, 'resolve': recon})

# ---- salary: title normalization + band lookup over every distinct live role ----
roles = sorted({r['role'] for r in tracking if r.get('role')} |
               {c['role'] for c in candidates if c.get('role')})
_dump('salary.json', [{'title': t, 'norm': jp._norm_salary_title(t),
                       'lookup': jp.salary_lookup(t)} for t in roles])

# ---- identity: tracker_id derivation from live listings ----
_dump('identity.json', [{'company': L.get('company', ''), 'title': L.get('title', ''),
                         'trackerId': jp.tracker_id(L.get('company', ''), L.get('title', '')),
                         'slugCompany': jp.slug(L.get('company', ''))}
                        for L in listings])

# ---- Houston locality over every distinct live location ----
locs = sorted({L.get('location', '') for L in listings} |
              {c.get('location', '') for c in candidates})
_dump('local.json', [{'location': loc, 'isLocal': jp._is_local(loc)} for loc in locs])

# ---- card parsing: realistic card texts rebuilt from live listings ----
matchable, stems = jp._matchable()
cards = []
for L in listings[:20]:
    text = ' | '.join(x for x in [
        L.get('title', ''), L.get('company', ''), L.get('location', ''),
        L.get('salary', ''), ', '.join(L.get('requiredSkills') or [])] if x)
    cards.append({'text': text, 'parsed': jp._parse_card(text, matchable, stems)})
_dump('cards.json', cards)

print('fixtures regenerated against live data + current config')
