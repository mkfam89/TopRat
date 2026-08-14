"""_is_local — the Houston-priority gate.

_is_local decides which jobs get the lower local_min_match threshold (0.20)
instead of min_match (0.60) in cmd_process_queue, and sort first. Goldens
snapshot every distinct live location against current config local_keywords.
"""
import pytest

from conftest import load_fixture

GOLDEN = load_fixture('local.json')


def test_houston_is_local(jp):
    assert jp._is_local('Houston, TX') is True
    assert jp._is_local('Greater HOUSTON Area') is True   # case-insensitive substring


def test_non_local(jp):
    assert jp._is_local('Remote (US)') is False
    assert jp._is_local('Austin, TX') is False
    assert jp._is_local('') is False
    assert jp._is_local(None) is False


# The goldens need a populated geo_cache.json — without one every town reads as
# non-local. That cache is now FROZEN (tests/fixtures/config_frozen/geo_cache.frozen.json)
# along with local_keywords and the profile center, so this runs on a fresh clone and a
# re-geocode of your live cache can no longer flip a golden.
def test_golden_locations(jp, frozen_config):
    for row in GOLDEN:
        assert jp._is_local(row['location']) == row['isLocal'], repr(row['location'])


def test_local_threshold_is_below_global(jp):
    # the 0.20-vs-0.60 rule: locals must auto-tailor at a LOWER bar than the
    # global threshold, else Houston priority is silently off
    st = jp.strategy()
    assert 0 < st['local_min_match'] <= st['min_match'] <= 1
