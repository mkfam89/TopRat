#!/usr/bin/env python3
"""The FROZEN config root — one definition, shared by the tests and the generator.

WHY THIS EXISTS
The config-derived goldens (skill_match, _parse_card, _is_local, the title lane) used
to be computed against the OWNER's live config. That made them snapshots of an opinion
rather than of behaviour: pruning one entry from skills.json turned the suite red with a
failure that pointed at scoring.py and had nothing to do with scoring.py. Real case —
dropping 'Change Management' moved a golden 0.92 -> 0.85 and read exactly like a
regression. Frozen dials mean editing your real config can no longer redden a test,
while changing the scoring CODE still does, which is the point. They also let these
tests run on a fresh clone and in CI, where there is no data root at all.

WHY BOTH SIDES IMPORT IT
tests/conftest.py reads the goldens through this root and tests/make_fixtures.py WRITES
them through it. If those two ever disagreed about which config is in effect, every
regeneration would quietly bake live values into fixtures the suite then checks against
frozen ones — the exact drift this whole mechanism exists to stop. So the mapping and
the activation live here and are imported by both, never re-implemented.

WHY THE '.frozen.json' NAMES
The files are materialized under the names the code looks for, but they cannot be
COMMITTED under those names. Two independent guards reject them: .gitignore excludes
'geo_cache.json' by bare name (so it would never be committed at all), and release.py's
FORBIDDEN_PATHS blocks geo_cache.json plus config/{profile,skills,skill_aliases,
gui_settings}.json ANYWHERE in an export tree. Both guards are worth more than the
convenience of matching names, so the rename lives here rather than as an exception
carved into either one. Same reason the frozen copies carry no personal notes: the PII
gate scans them like any other shipped file.

NOT FROZEN, DELIBERATELY
Anything needing the owner's real CONTENT — the bullet bank (test_render_resume) and
resume_template/ (test_golden_classify). Those keep @pytest.mark.needs_userdata. Frozen
dials fix drift; they cannot conjure a resume to render.
"""
import contextlib
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN_SRC = os.path.join(HERE, 'fixtures', 'config_frozen')

# committed name -> path inside the materialized data root
FROZEN_FILES = {
    'skills.frozen.json': 'config/skills.json',
    'skill_aliases.frozen.json': 'config/skill_aliases.json',
    'strategy.frozen.json': 'config/strategy.json',
    'gui_settings.frozen.json': 'config/gui_settings.json',
    'profile.frozen.json': 'config/profile.json',
    'geo_cache.frozen.json': 'geo_cache.json',
}


def materialize(dest):
    """Build a real data root at `dest` from the committed frozen copies.

    gui_settings.json is present and EMPTY on purpose: scoring.strategy() lets the
    dashboard sliders override strategy.json, so an ABSENT file would fall through
    pipelib._overlay() to the shipped default and quietly re-open the door to live
    values moving a golden. Present-and-empty closes it.
    """
    os.makedirs(os.path.join(dest, 'config'), exist_ok=True)
    for src, rel in FROZEN_FILES.items():
        shutil.copyfile(os.path.join(FROZEN_SRC, src), os.path.join(dest, *rel.split('/')))
    return dest


@contextlib.contextmanager
def activate(root):
    """Redirect config reads to `root` for the duration of the block, then restore.

    pipelib._overlay() reads the module-global DATA at CALL time, so patching that one
    attribute redirects every cfg() lookup with no module reload — unlike the scratch
    roots in test_backlog.py, which reload because they change paths latched at import.
    The three latched things that do NOT follow DATA are handled by hand: pipelib.GEO_CACHE,
    locality.GEO_CACHE (imported by value) and profile_lib.PROFILE. locality also memoizes
    strategy/cache/center in module globals, so those are cleared on the way in AND on the
    way out — left warm, they would hand the frozen config to everything that ran later.
    """
    import pipelib
    import profile_lib
    import locality

    geo = os.path.join(root, 'geo_cache.json')
    saved = [
        (pipelib, 'DATA', pipelib.DATA), (pipelib, 'GEO_CACHE', pipelib.GEO_CACHE),
        (profile_lib, 'PROFILE', profile_lib.PROFILE),
        (locality, 'GEO_CACHE', locality.GEO_CACHE),
        (locality, '_strat', locality._strat), (locality, '_cache', locality._cache),
        (locality, '_center', locality._center),
    ]
    pipelib.DATA = root
    pipelib.GEO_CACHE = geo
    profile_lib.PROFILE = os.path.join(root, 'config', 'profile.json')
    locality.GEO_CACHE = geo
    locality._strat = locality._cache = locality._center = None
    try:
        yield root
    finally:
        for obj, name, val in saved:
            setattr(obj, name, val)
