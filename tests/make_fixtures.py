#!/usr/bin/env python3
"""Regenerate the golden fixtures in tests/fixtures/ from live INPUTS + FROZEN config.

    python tests/make_fixtures.py

Read-only over repo data (listings.json, tracking.csv, candidates.csv,
job_tracker.json); writes ONLY tests/fixtures/*.json.

TWO SOURCES, AND THE SPLIT IS THE POINT
  * INPUTS are live — real listings, real titles, real locations, real tracker rows.
    Synthetic inputs would not exercise the messy strings these parsers exist to survive.
  * CONFIG is FROZEN — tests/fixtures/config_frozen/, via tests/frozen_root.py. The
    config-derived goldens (scoring, cards, local) are computed with the frozen dials in
    effect, because that is what the suite checks them against. Generating them against
    the live config instead would bake in values every test then disagrees with, which is
    the drift this mechanism exists to stop. See tests/frozen_root.py for the full why.

So: changing your live skills.json does NOT require rerunning this. Changing the FROZEN
dials, the scoring code, or salary_bands.json does — rerun, then review the diff like code.
"""
import csv
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, 'tests')
# The modules live in src/<group>/; src/_paths.py puts every one of them on sys.path so
# they import by plain name. Mirrors tests/conftest.py — this script used to do a bare
# `sys.path.insert(ROOT)` + `import jobpipe`, which stopped resolving when the code moved.
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, HERE)
import _paths  # noqa: E402,F401
import jobpipe as jp  # noqa: E402
import pipelib  # noqa: E402
import frozen_root  # noqa: E402

# Live state moved OUT of the repo root into the data root; these used to be read as
# ROOT/listings.json etc, which silently produced empty goldens after the split.
DATA = pipelib.DATA

FIX = os.path.join(HERE, 'fixtures')
os.makedirs(FIX, exist_ok=True)


def _load_csv(name):
    with open(os.path.join(DATA, name), newline='', encoding='utf-8') as f:
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


listings = jp.load_json(os.path.join(DATA, 'listings.json'), []) or []
tracker = jp.load_json(os.path.join(DATA, 'job_tracker.json'), {}) or {}
tracking = _load_csv('tracking.csv')
candidates = _load_csv('candidates.csv')

# The inputs above were read from the LIVE data root, on purpose: the frozen root holds
# config only, so reading listings through it would find nothing.
import tempfile  # noqa: E402

_frozen_dir = frozen_root.materialize(tempfile.mkdtemp(prefix='make_fixtures_frozen_'))

# ---- scoring: classify + skill_match + flag_skills over every live listing ----
#
# The two halves of this fixture need DIFFERENT roots, and getting that backwards is a
# silent corruption rather than an error:
#   * skill_match  -> FROZEN. It is pure dials (skills.json + skill_aliases.json), which
#                     is exactly what test_golden_skill_match now pins.
#   * classify     -> LIVE. It resolves the base resume out of resume_template/, and the
#                     frozen root has no templates — computing it there would write ''
#                     into every row and quietly blank the golden. This is also why
#                     test_golden_classify keeps needs_userdata and its sibling does not.
# depersonalize_classify then strips the resume FILENAME, which is the user's private data.
classified = {}
for L in listings:
    classified[id(L)] = depersonalize_classify(
        jp.classify(L.get('title', ''), L.get('requiredSkills') or []))

with frozen_root.activate(_frozen_dir):
    scoring = []
    for L in listings:
        req = L.get('requiredSkills') or []
        match, flagged = jp.skill_match(req)
        scoring.append({
            'id': L.get('id', ''), 'title': L.get('title', ''),
            'requiredSkills': req,
            'classify': classified[id(L)],
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
# FROZEN: local_keywords + the profile center + geo_cache all come from the frozen root,
# so a re-geocode of the live cache cannot flip a golden.
locs = sorted({L.get('location', '') for L in listings} |
              {c.get('location', '') for c in candidates})
with frozen_root.activate(_frozen_dir):
    _dump('local.json', [{'location': loc, 'isLocal': jp._is_local(loc)} for loc in locs])

# ---- card parsing: realistic card texts rebuilt from live listings ----
# FROZEN: the card TEXTS are live, but the skill set that segments them is not.
with frozen_root.activate(_frozen_dir):
    matchable, stems = jp._matchable()
    cards = []
    for L in listings[:20]:
        text = ' | '.join(x for x in [
            L.get('title', ''), L.get('company', ''), L.get('location', ''),
            L.get('salary', ''), ', '.join(L.get('requiredSkills') or [])] if x)
        cards.append({'text': text, 'parsed': jp._parse_card(text, matchable, stems)})
_dump('cards.json', cards)

shutil.rmtree(_frozen_dir, ignore_errors=True)
print('fixtures regenerated: live inputs + frozen config (tests/fixtures/config_frozen)')
