"""locality.py — geo-aware local-priority gate.

Offline by design: the geo pass reads only geo_cache.json (never the network),
so tests inject a fake cache + profile center via the module's lazy caches.
"""
import pytest

import locality

HOUSTON = (29.76328, -95.36327, 55.0)   # profile center used by the geo pass
ST = {'local_enabled': True, 'local_geo_enabled': True,
      'local_keywords': ['houston', 'katy', 'sugar land', 'the woodlands', 'pearland']}


@pytest.fixture
def geo(monkeypatch):
    """Fake profile center + geo cache; no profile.json / geo_cache.json needed."""
    monkeypatch.setattr(locality, '_center', HOUSTON)
    monkeypatch.setattr(locality, '_cache', {
        'spring, tx': {'lat': 30.0799, 'lon': -95.4172},          # ~23 mi — inside
        'conroe, tx': {'lat': 30.3119, 'lon': -95.4561},          # ~38 mi — inside
        'austin, tx': {'lat': 30.2672, 'lon': -97.7431},          # ~146 mi — outside
        'dallas, tx': {'failedAt': 0, 'error': 'x'},              # cached failure
    })


def test_haversine_sanity():
    assert locality.haversine_miles(*HOUSTON[:2], *HOUSTON[:2]) == 0
    d = locality.haversine_miles(29.76328, -95.36327, 30.2672, -97.7431)  # Houston→Austin
    assert 140 < d < 155


def test_geo_key_guards():
    assert locality.geo_key('Spring, TX') == 'spring, tx'
    assert locality.geo_key('  Spring,   TX ') == 'spring, tx'    # whitespace-normalized
    assert locality.geo_key('Remote (US)') is None                # no comma / remote
    assert locality.geo_key('Remote - Spring, TX') is None        # remote anywhere in string
    assert locality.geo_key('Hybrid - Spring, TX') is None
    assert locality.geo_key('Texas') is None                      # bare state, no comma
    assert locality.geo_key('United States, Remote') is None
    assert locality.geo_key('') is None and locality.geo_key(None) is None


def test_keyword_pass_unchanged(geo):
    assert locality.is_local('Houston, TX', ST) is True
    assert locality.is_local('Greater HOUSTON Area', ST) is True
    assert locality.is_local('The Woodlands, TX', ST) is True
    assert locality.is_local('', ST) is False
    assert locality.is_local(None, ST) is False


def test_geo_pass_in_radius(geo):
    assert locality.is_local('Spring, TX', ST) is True            # suburb not in keywords
    assert locality.is_local('Conroe, TX', ST) is True
    assert locality.is_local('Austin, TX', ST) is False           # outside radius
    assert locality.is_local('Dallas, TX', ST) is False           # cached failure = no verdict
    assert locality.is_local('Nowhere, ZZ', ST) is False          # uncached = keyword-only


def test_geo_pass_switches(geo):
    off = dict(ST, local_geo_enabled=False)
    assert locality.is_local('Spring, TX', off) is False          # geo pass off → keyword only
    assert locality.is_local('Houston, TX', off) is True
    disabled = dict(ST, local_enabled=False)
    assert locality.is_local('Houston, TX', disabled) is False    # master switch


def test_no_profile_center(geo, monkeypatch):
    monkeypatch.setattr(locality, '_center', ())                  # no usable profile
    assert locality.is_local('Spring, TX', ST) is False           # degrades to keyword-only
    assert locality.is_local('Houston, TX', ST) is True
