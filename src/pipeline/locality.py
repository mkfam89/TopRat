#!/usr/bin/env python3
"""locality.py — geographic "is this job local?" gate. SCRIPT-LEVEL, zero Claude tokens.

Decides local-priority (lower skill floor, processed first) in two passes:
  1. keyword pass — strategy.json local_keywords substring match (legacy behavior,
     always available, fully offline);
  2. geo pass — the job's "City, ST" location is looked up in geo_cache.json
     (pre-geocoded lat/lon) and haversine-checked against the profile's
     search.location center within search.radius_miles. So "Spring, TX" or
     "Conroe, TX" count as local for a Houston profile without listing every
     suburb — and it works for ANY user's city, driven by config/profile.json.

The hot path (is_local) NEVER touches the network: unknown locations simply fall
back to keyword-only until the cache learns them. The cache is populated by the
scheduled scrape (scrape.py calls refresh_from_files() best-effort after writing
listings.json) or manually:  python src/pipeline/locality.py --refresh
Geocoding uses profile_lib.geocode_city (OpenStreetMap Nominatim, free, no key),
throttled ~1 req/sec and capped per run. Failures are cached too (retried after
FAIL_RETRY_DAYS) so a bad string never re-hits the network every run.

Design principle: everything here runs without Claude — pure stdlib + config.

Config knobs (strategy.json / gui_settings.json):
  local_enabled / localEnabled    master switch (existing)
  local_keywords                  keyword pass list (existing)
  local_geo_enabled               geo pass switch (default true)
Profile (config/profile.json, Settings tab): search.location{lat,lon} + search.radius_miles.

CLI:
  python src/pipeline/locality.py --refresh [--cap N]   # geocode uncached locations from listings+candidates
  python src/pipeline/locality.py --check "Spring, TX"  # explain the verdict for one location
  python src/pipeline/locality.py                       # cache stats
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, csv, json, math, re, time, argparse

from pipelib import BASE, DATA, CONFIG, cfg, GEO_CACHE, CANDIDATES_CSV, load_json, _safe_write_text

# GEO_CACHE comes from pipelib (data root) — do NOT redefine it off BASE here.
LISTINGS = os.path.join(DATA, 'listings.json')
FAIL_RETRY_DAYS = 30
_NON_GEO = {'remote', 'united states', 'usa', 'us', 'anywhere', 'hybrid', 'onsite', 'on-site'}

# lazy module caches (scripts are short-lived processes)
_strat = None
_cache = None
_center = None


def _merged_strategy():
    """strategy.json with the gui_settings.json overrides locality cares about
    (mirrors scoring.strategy for local_enabled so notify.py needs no jobpipe import)."""
    global _strat
    if _strat is None:
        st = load_json(cfg('strategy.json'), {}) or {}
        g = load_json(cfg('gui_settings.json'), {}) or {}
        if isinstance(g.get('localEnabled'), bool):
            st['local_enabled'] = g['localEnabled']
        _strat = st
    return _strat


def profile_center():
    """(lat, lon, radius_miles) from config/profile.json search.location — the same
    center+radius the hiring.cafe scrape uses, so 'scraped because in radius' and
    'treated as local' finally agree. None if no usable profile."""
    global _center
    if _center is None:
        try:
            import profile_lib as pl
            prof = pl.load_profile(clean=True) or {}
            s = prof.get('search') or {}
            loc = s.get('location') or {}
            lat, lon = loc.get('lat'), loc.get('lon')
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                _center = (float(lat), float(lon), float(s.get('radius_miles', 50)))
            else:
                _center = ()
        except Exception:
            _center = ()
    return _center or None


def haversine_miles(lat1, lon1, lat2, lon2):
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((rlat2 - rlat1) / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def geo_key(location):
    """Normalized cache key for a geo-eligible location string, or None.
    Eligible = looks like 'City, ST...' (has a comma, city part isn't a
    remote/country marker, no 'remote' anywhere in the string)."""
    loc = re.sub(r'\s+', ' ', (location or '').strip())
    if ',' not in loc:
        return None
    low = loc.lower()
    if 'remote' in low or 'hybrid' in low:
        return None  # remote/hybrid postings never get geo local-priority
    city = low.split(',', 1)[0].strip()
    if not city or city in _NON_GEO:
        return None
    return low


def _load_cache():
    global _cache
    if _cache is None:
        _cache = load_json(GEO_CACHE, {}) or {}
    return _cache


def is_local(location, strat=None):
    """Keyword pass, then cached-geo pass. Offline-safe, zero network."""
    st = strat if strat is not None else _merged_strategy()
    if not st.get('local_enabled', True):
        return False
    kws = [k.lower() for k in (st.get('local_keywords') or ['houston'])]
    loc = (location or '').lower()
    if any(k in loc for k in kws):
        return True
    if not st.get('local_geo_enabled', True):
        return False
    center = profile_center()
    key = geo_key(location)
    if not center or not key:
        return False
    ent = _load_cache().get(key)
    if not isinstance(ent, dict) or 'lat' not in ent:
        return False
    lat0, lon0, radius = center
    return haversine_miles(lat0, lon0, ent['lat'], ent['lon']) <= radius


def refresh_cache(locations, cap=20, sleep_s=1.1, force=False):
    """Geocode uncached geo-eligible locations (network!). Caps calls per run and
    throttles for Nominatim. Caches failures with a timestamp; retries them after
    FAIL_RETRY_DAYS. Returns (geocoded, failed, skipped_cached)."""
    import profile_lib as pl
    cache = dict(_load_cache())
    now = time.time()
    keys, seen = [], set()
    for loc in locations:
        k = geo_key(loc)
        if not k or k in seen:
            continue
        seen.add(k)
        ent = cache.get(k)
        if ent and not force:
            if 'lat' in ent:
                continue
            if now - float(ent.get('failedAt', 0)) < FAIL_RETRY_DAYS * 86400:
                continue
        keys.append(k)
    ok = fail = 0
    for k in keys[:cap]:
        try:
            g = pl.geocode_city(k)
            cache[k] = {'lat': g['lat'], 'lon': g['lon'],
                        'resolved': g.get('formatted_address', '')}
            ok += 1
        except Exception as e:
            cache[k] = {'failedAt': now, 'error': str(e)[:120]}
            fail += 1
        time.sleep(sleep_s)
    if ok or fail:
        _safe_write_text(GEO_CACHE, json.dumps(cache, indent=1, sort_keys=True),
                         verify_json=True)
        global _cache
        _cache = cache
    return ok, fail, len(seen) - len(keys)


def _known_locations():
    locs = []
    for x in (load_json(LISTINGS, []) or []):
        if isinstance(x, dict) and x.get('location'):
            locs.append(x['location'])
    try:
        with open(CANDIDATES_CSV, encoding='utf-8') as f:
            locs += [r.get('location', '') for r in csv.DictReader(f)]
    except Exception:
        pass
    return locs


def refresh_from_files(cap=20):
    """Best-effort hook for scrape.py: geocode anything new from listings+candidates."""
    try:
        return refresh_cache(_known_locations(), cap=cap)
    except Exception as e:
        print('locality: refresh skipped: %s' % e, file=sys.stderr)
        return 0, 0, 0


def main():
    ap = argparse.ArgumentParser(description='geo local-priority cache')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--cap', type=int, default=20)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--check', metavar='LOCATION')
    a = ap.parse_args()
    if a.refresh:
        ok, fail, cached = refresh_cache(_known_locations(), cap=a.cap, force=a.force)
        print('locality: geocoded %d, failed %d, already cached %d (cache: %s)'
              % (ok, fail, cached, GEO_CACHE))
        return
    if a.check:
        st = _merged_strategy()
        kws = [k.lower() for k in (st.get('local_keywords') or ['houston'])]
        kw_hit = any(k in a.check.lower() for k in kws)
        key = geo_key(a.check)
        ent = _load_cache().get(key or '')
        center = profile_center()
        dist = (haversine_miles(center[0], center[1], ent['lat'], ent['lon'])
                if center and isinstance(ent, dict) and 'lat' in ent else None)
        print(json.dumps({'location': a.check, 'isLocal': is_local(a.check),
                          'keywordHit': kw_hit, 'geoKey': key,
                          'cached': bool(ent), 'distanceMiles': round(dist, 1) if dist is not None else None,
                          'radiusMiles': center[2] if center else None,
                          'profileCenter': bool(center)}, indent=1))
        return
    c = _load_cache()
    hits = sum(1 for v in c.values() if isinstance(v, dict) and 'lat' in v)
    print('locality: %d cached locations (%d resolved, %d failed); center=%s'
          % (len(c), hits, len(c) - hits, profile_center()))


if __name__ == '__main__':
    import runlog
    runlog.run('locality.py', main)
