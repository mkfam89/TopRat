#!/usr/bin/env python3
"""profile_lib.py — turn one plain-language user profile into every derived config.

config/profile.json is the single source of personalization (identity, resume folder,
search filters, skills, notify, git). This module:
  * geocodes the user's city (hiringcafe.com needs coordinates),
  * compiles the full filter set into board-native URLs for LinkedIn + hiringcafe.com,
  * writes config/search_urls.json, config/skills.json, a starter config/skill_aliases.json,
    config/notify.json and config/git.json.

Both the CLI wizard (setup.py) and the dashboard /setup page call regenerate_all().

CLI:
  python src/lib/profile_lib.py --regen              # rebuild derived configs from profile.json
  python src/lib/profile_lib.py --geocode "Austin, TX"
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, urllib.parse, urllib.request, copy, shutil, re, random, unicodedata

HERE = _paths.ROOT
def P(*p): return os.path.join(HERE, *p)          # CODE assets (shipped examples)
# The profile and every config generated FROM it are personal -> pipelib DATA root
# (falls back to HERE when the data repo isn't split out).
from pipelib import CODE_CONFIG, USER_CONFIG, cfg, cfg_write
def C(*p): return os.path.join(USER_CONFIG, *p)   # this user's config dir
PROFILE = cfg_write('profile.json')              # ACTIVE profile — the single source the whole pipeline reads
EXAMPLE = os.path.join(CODE_CONFIG, 'profile.example.json')   # template — ships WITH the code
PROFILES_DIR = C('profiles')                     # per-user named copies (khoa_pham_profile.json, ...)

# ---------------------------------------------------------------- profile I/O
def _strip_notes(obj):
    """Drop _readme/_note/*_note helper keys so they never leak into logic."""
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items()
                if not (k == '_readme' or k == '_note' or k.endswith('_note'))}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj

def load_profile(path=PROFILE, clean=True):
    src = path if os.path.exists(path) else EXAMPLE
    with open(src, encoding='utf-8') as f:
        data = json.load(f)
    return _strip_notes(data) if clean else data

def save_profile(profile, path=PROFILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _rotate_backups(path)   # keep up to 2 older copies of the previous profile
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=2)

def _rotate_backups(path, keep=2):
    """Before overwriting, slide profile.json -> .bak1 -> .bak2 (older backups fall off)."""
    if not os.path.exists(path):
        return
    try:
        for i in range(keep, 1, -1):              # e.g. .bak1 -> .bak2
            older, newer = f'{path}.bak{i}', f'{path}.bak{i-1}'
            if os.path.exists(newer):
                shutil.copy2(newer, older)
        shutil.copy2(path, f'{path}.bak1')        # current profile becomes newest backup
    except Exception:
        pass

# ---------------------------------------------------------- named profiles
# config/profile.json stays the ACTIVE profile the whole pipeline reads. In addition,
# every save keeps a per-user NAMED copy under config/profiles/ so multiple people can
# each own a profile. Naming rule (deterministic, zero-Claude):
#   base filename  = "<first>_<last>_profile.json"      (from identity.full_name)
#   collision       -> the caller (wizard) prompts the user for a name
#   final fallback  = "<first>_<last>_profile<####>.json" (random 4-digit, guaranteed free)
def name_slug(full_name):
    """'Khoa Pham' -> 'khoa_pham'. Uses first + last alnum tokens; 'user' if empty."""
    toks = [re.sub(r'[^a-z0-9]', '', t.lower()) for t in (full_name or '').split()]
    toks = [t for t in toks if t]
    if not toks:
        return 'user'
    return toks[0] if len(toks) == 1 else f'{toks[0]}_{toks[-1]}'

def profile_filename(full_name):
    """Preferred named-profile filename for a user: '<first>_<last>_profile.json'."""
    return f'{name_slug(full_name)}_profile.json'

def initials(full_name):
    """'Khoa Pham' -> 'KP'; 'khoa' -> 'K'; '' -> ''. First + last token only, so a
    middle name does not turn the data folder into user_data_KABP. Names the
    self-contained data folder (pipelib.suggest_user_data_dirname) and the folder
    suggestion the setup wizard shows once the user types their name.

    Accents are FOLDED, not stripped: 'José Álvarez' has to give JA, not JL. The
    folder name still has to be plain ASCII (it is typed into paths, .bat files and
    Git), but dropping 'Á' outright would silently promote the second letter and
    hand the user initials that are not theirs. setup.html's deriveInitials() mirrors
    this exactly — the two are cross-checked in tests/test_initials_parity.py."""
    norm = unicodedata.normalize('NFKD', full_name or '')
    toks = [re.sub(r'[^A-Za-z0-9]', '', t) for t in norm.split()]
    toks = [t for t in toks if t]
    if not toks:
        return ''
    picked = toks if len(toks) == 1 else [toks[0], toks[-1]]
    return ''.join(t[0].upper() for t in picked)

def random_profile_filename(full_name):
    """Collision fallback: '<first>_<last>_profile<####>.json' (4-digit, guaranteed free)."""
    slug = name_slug(full_name)
    while True:
        cand = f'{slug}_profile{random.randint(1000, 9999)}.json'
        if not named_profile_exists(cand):
            return cand

def _sanitize_profile_name(fname):
    """Force a user-supplied profile name to a safe '<...>.json' basename in PROFILES_DIR."""
    base = os.path.basename((fname or '').strip())
    if not base:
        return ''
    if not base.lower().endswith('.json'):
        base += '.json'
    base = re.sub(r'[^A-Za-z0-9_.-]', '_', base)
    return base

def named_profile_exists(fname):
    return bool(fname) and os.path.exists(os.path.join(PROFILES_DIR, _sanitize_profile_name(fname)))

def list_named_profiles():
    try:
        return sorted(f for f in os.listdir(PROFILES_DIR) if f.lower().endswith('.json'))
    except Exception:
        return []

def save_named_profile(profile, fname):
    """Write a per-user named copy into config/profiles/<fname>; returns the filename used."""
    fname = _sanitize_profile_name(fname) or profile_filename(
        (profile.get('identity') or {}).get('full_name', ''))
    os.makedirs(PROFILES_DIR, exist_ok=True)
    with open(os.path.join(PROFILES_DIR, fname), 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=2)
    set_active_profile(fname)
    return fname

# Pointer to the named profile that config/profile.json currently mirrors, so the
# wizard can re-save the same user without re-triggering the collision prompt.
ACTIVE_POINTER = cfg_write('active_profile.json')
def set_active_profile(name):
    try:
        with open(ACTIVE_POINTER, 'w', encoding='utf-8') as f:
            json.dump({'name': name}, f)
    except Exception:
        pass
def get_active_profile():
    try:
        with open(ACTIVE_POINTER, encoding='utf-8') as f:
            return (json.load(f) or {}).get('name', '')
    except Exception:
        return ''

# ---------------------------------------------------------------- geocoding
def geocode_city(query, timeout=15):
    """Resolve a free-text place to {lat, lon, city, state, country, formatted_address,
    population}. Uses OpenStreetMap Nominatim (free, no key). Raises on failure so the
    caller can fall back to manual lat/lon entry."""
    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
        {'q': query, 'format': 'json', 'addressdetails': '1', 'limit': '1', 'extratags': '1'})
    req = urllib.request.Request(url, headers={
        'User-Agent': 'job-pipeline-setup/1.0 (personal job search tool)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        hits = json.load(r)
    if not hits:
        raise ValueError('No geocoding match for %r' % query)
    h = hits[0]
    a = h.get('address', {})
    city = a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or a.get('county') or ''
    state = a.get('state') or ''
    state_code = (a.get('ISO3166-2-lvl4') or '').split('-')[-1] or state
    country_code = (a.get('country_code') or '').upper()
    parts = [p for p in (city, state_code or state, country_code) if p]
    pop = h.get('extratags', {}).get('population')
    try: pop = int(pop) if pop else 0
    except (TypeError, ValueError): pop = 0
    return {
        'query': query,
        'city': city,
        'state': state_code or state,
        'country': country_code or 'US',
        'lat': float(h['lat']),
        'lon': float(h['lon']),
        'population': pop,
        'formatted_address': ', '.join(parts) if parts else h.get('display_name', query),
    }

# ---------------------------------------------------------------- query builders
def _quote(term):
    return '"%s"' % term if ' ' in term.strip() else term

def _boolean_query(titles, exclude, quote=True):
    q = _quote if quote else (lambda x: x)
    inc = ' OR '.join(q(t) for t in titles if t.strip())
    s = '(%s)' % inc if inc else ''
    ex = ' OR '.join(q(t) for t in exclude if t.strip())
    if ex:
        s = (s + ' ' if s else '') + 'NOT (%s)' % ex
    return s

# LinkedIn code maps
_LI_WT = {'onsite': '1', 'remote': '2', 'hybrid': '3'}           # f_WT
_LI_E = {'internship': '1', 'entry level': '2', 'associate': '3',  # f_E
         'mid level': '4', 'senior level': '4', 'director': '5', 'executive': '6'}
_LI_JT = {'full time': 'F', 'part time': 'P', 'contract': 'C',     # f_JT
          'temporary': 'T', 'internship': 'I'}

def _li_salary_bucket(salary_min):
    """LinkedIn f_SB2: 1=$40k .. 9=$200k in $20k steps (coarse min only)."""
    if not salary_min: return None
    b = int((salary_min - 40000) // 20000) + 1
    return str(max(1, min(9, b)))

def build_linkedin_url(search, days):
    loc = search['location']
    kw = _boolean_query(search.get('titles', []), search.get('exclude_terms', []))
    params = [('keywords', kw), ('location', loc.get('query') or loc.get('formatted_address', ''))]
    wt = [_LI_WT[w.lower()] for w in search.get('workplace_types', []) if w.lower() in _LI_WT]
    if wt: params.append(('f_WT', ','.join(dict.fromkeys(wt))))
    e = [_LI_E[s.lower()] for s in search.get('seniority', []) if s.lower() in _LI_E]
    if e: params.append(('f_E', ','.join(dict.fromkeys(e))))
    jt = [_LI_JT[t.lower()] for t in search.get('employment_types', []) if t.lower() in _LI_JT]
    if jt: params.append(('f_JT', ','.join(dict.fromkeys(jt))))
    params.append(('f_TPR', 'r%d' % (int(days) * 86400)))
    sb = _li_salary_bucket(search.get('salary_min'))
    if sb: params.append(('f_SB2', sb))
    if search.get('radius_miles'): params.append(('distance', str(int(search['radius_miles']))))
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return 'https://www.linkedin.com/jobs/search/?' + qs

# hiringcafe.com label maps
_HC_SENIORITY = {'internship': 'Internship', 'entry level': 'Entry Level',
                 'mid level': 'Mid Level', 'senior level': 'Senior Level',
                 'director': 'Director', 'executive': 'Executive'}
_HC_COMMIT = {'full time': 'Full Time', 'part time': 'Part Time', 'contract': 'Contract',
              'temporary': 'Temporary', 'internship': 'Internship'}

def _hc_location(loc, workplace_types):
    """Primary locality object. hiringcafe.com's real place id comes from its own
    autocomplete API; the observed country entry uses a constructed id
    ('United Statescountry'), so we build the locality id the same deterministic way.
    Search is driven by lat/lon + radius, which geocoding provides exactly."""
    city, state, country = loc.get('city', ''), loc.get('state', ''), loc.get('country', 'US')
    ac = []
    if city: ac.append({'long_name': city, 'short_name': city, 'types': ['locality']})
    if state: ac.append({'long_name': state, 'short_name': state,
                         'types': ['administrative_area_level_1']})
    ac.append({'long_name': 'United States' if country == 'US' else country,
               'short_name': country, 'types': ['country']})
    return {
        'id': ''.join(x for x in (city, state, country) if x) + 'locality',
        'types': ['locality'],
        'address_components': ac,
        'geometry': {'location': {'lat': loc.get('lat'), 'lon': loc.get('lon')}},
        'formatted_address': loc.get('formatted_address', ''),
        'population': loc.get('population', 0),
        'workplace_types': workplace_types,
        'options': {'radius': int(loc.get('radius_miles', 50)), 'radius_unit': 'miles',
                    'ignore_radius': False, 'flexible_regions': ['anywhere_in_country']},
    }

def _hc_remote_country(country):
    name = 'United States' if country == 'US' else country
    return {'types': ['country'], 'formatted_address': name,
            'address_components': [{'long_name': name, 'short_name': country, 'types': ['country']}],
            'workplace_types': ['Remote'], 'options': {},
            'id': name + 'country'}

def build_hiringcafe_url(search, days, domain='https://hiringcafe.com/'):
    loc = dict(search['location']); loc['radius_miles'] = search.get('radius_miles', 50)
    wt = search.get('workplace_types', ['Remote', 'Hybrid', 'Onsite'])
    locations = [_hc_location(loc, wt)]
    if search.get('include_remote_anywhere_in_country') and 'Remote' in wt:
        locations.append(_hc_remote_country(loc.get('country', 'US')))
    state = {
        'locations': locations,
        'commitmentTypes': [_HC_COMMIT[t.lower()] for t in search.get('employment_types', [])
                            if t.lower() in _HC_COMMIT],
        'roleYoeRange': search.get('years_experience', [0, 99]),
        'seniorityLevel': [_HC_SENIORITY[s.lower()] for s in search.get('seniority', [])
                           if s.lower() in _HC_SENIORITY],
        'jobTitleQuery': _boolean_query(search.get('titles', []), search.get('exclude_terms', []), quote=True),
        'dateFetchedPastNDays': int(days),
    }
    if search.get('salary_min'):
        state['minCompensationLowEnd'] = str(int(search['salary_min']))
    if search.get('salary_max'):
        state['maxCompensationHighEnd'] = str(int(search['salary_max']))
    for k, key in (('security_clearances', 'securityClearances'),
                   ('air_travel_ok', 'airTravelRequirement'),
                   ('land_travel_ok', 'landTravelRequirement')):
        if search.get(k): state[key] = search[k]
    blob = json.dumps(state, separators=(',', ':'))
    return domain + '?searchState=' + urllib.parse.quote(blob)

# ---------------------------------------------------------------- regeneration
def build_search_urls(profile, hc_domain='https://hiringcafe.com/'):
    s = profile['search']
    presets = s.get('date_presets', {'fresh': 1, 'weekly': 7})
    fresh, weekly = int(presets.get('fresh', 1)), int(presets.get('weekly', 7))
    urls = {}
    if s.get('use_linkedin', True):
        if s.get('linkedin_top_applicant'):
            urls['linkedin_top_applicant'] = 'https://www.linkedin.com/jobs/collections/top-applicant/'
        urls['linkedin_24h'] = build_linkedin_url(s, fresh)
        urls['linkedin_7d'] = build_linkedin_url(s, weekly)
    if s.get('use_hiringcafe', True):
        urls['hiringcafe_24h'] = build_hiringcafe_url(s, fresh, hc_domain)
        urls['hiringcafe_7d'] = build_hiringcafe_url(s, weekly, hc_domain)
    return urls

# common synonyms for a generic starter alias map (only emitted for skills the user has)
_STARTER_ALIASES = {
    'Kubernetes': ['K8s', 'AKS', 'EKS', 'GKE', 'Helm', 'OpenShift', 'Container Orchestration'],
    'Docker': ['Containers', 'Containerization', 'Podman'],
    'Terraform': ['IaC', 'Infrastructure as Code', 'HCL'],
    'CI/CD': ['GitHub Actions', 'GitLab CI', 'Jenkins', 'ArgoCD', 'Continuous Integration', 'Continuous Delivery'],
    'Monitoring': ['Observability', 'Datadog', 'New Relic', 'Splunk', 'Grafana', 'Prometheus', 'CloudWatch'],
    'SQL': ['PostgreSQL', 'MySQL', 'T-SQL', 'MSSQL', 'SQL Server', 'Relational Databases'],
    'Bash': ['Shell', 'Shell Scripting', 'Scripting', 'Unix Shell'],
    'Python': ['Python3', 'Py'],
    'Linux': ['Ubuntu', 'RHEL', 'Red Hat', 'CentOS', 'Unix'],
    'REST APIs': ['REST', 'RESTful', 'API', 'APIs', 'Web Services', 'OpenAPI', 'Swagger'],
    'Git': ['GitHub', 'GitLab', 'Bitbucket', 'Version Control', 'Source Control'],
    'Azure': ['Microsoft Azure', 'Azure AD', 'Entra ID', 'M365', 'Microsoft 365'],
    'Data Quality': ['Data Validation', 'Data Cleansing', 'Data Cleaning', 'Data Integrity'],
}

def _load_json(path, default=None):
    """Best-effort read of an existing config file (so regen can preserve hand-tuned values)."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def regenerate_all(profile, hc_domain='https://hiringcafe.com/'):
    """Write every derived config from the profile. Returns a list of written paths."""
    os.makedirs(C(), exist_ok=True)
    written = []

    def _w(rel, data):
        with open(C(*rel[1:]), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        written.append(os.path.join(*rel))

    # search_urls.json
    _w(['config', 'search_urls.json'], build_search_urls(profile, hc_domain))

    # skills.json + skill_aliases.json.
    # skills.json: the skills LIST is derived from the profile (updates when you edit skills), but a
    # hand-written note is preserved. skill_aliases.json is treated like summary_rules.json — it is
    # generated ONLY when missing, and NEVER overwritten once it exists, so a hand-tuned alias map
    # (aliases + substringStems) survives every wizard Save instead of being wiped back to a starter.
    have = profile.get('skills', {}).get('have', [])
    if have:
        prev_skills = _load_json(cfg('skills.json'))
        note = (prev_skills or {}).get('note') or \
            'User-declared claimable skills. flag-skills reports required tools NOT in this list.'
        _w(['config', 'skills.json'], {'skills': have, 'note': note})
        aliases_out = C('skill_aliases.json')
        if not os.path.exists(aliases_out):
            have_lower = {h.lower(): h for h in have}
            starter = {have_lower[k.lower()]: v for k, v in _STARTER_ALIASES.items()
                       if k.lower() in have_lower}
            _w(['config', 'skill_aliases.json'],
               {'_note': 'GENERIC starter alias map (common synonyms only). Hand-tune: add posting '
                         'synonyms that map to YOUR skills, and never map tools you lack. This file is '
                         'preserved across regen once it exists — delete it to regenerate a fresh starter.',
                'aliases': starter,
                'substringStems': [k.lower().replace(' ', '').replace('/', '') for k in have]})

    # summary_rules.json — fill placeholders from the template, but NEVER overwrite an
    # existing (hand-tuned) file; only generate one for a fresh user.
    sr_out = C('summary_rules.json')
    sr_tpl = os.path.join(CODE_CONFIG, 'summary_rules.example.json')
    if not os.path.exists(sr_out) and os.path.exists(sr_tpl):
        ident = profile.get('identity', {})
        with open(sr_tpl, encoding='utf-8') as f:
            sr = _strip_notes(json.load(f))
        titles = ident.get('real_titles') or ['{{REAL_TITLES}}']
        companies = ident.get('past_companies') or ['{{PAST_COMPANIES}}']
        name = ident.get('full_name') or '{{FULL_NAME}}'
        def _fill(v):
            if isinstance(v, str):
                return v.replace('{{FULL_NAME}}', name).replace(
                    '{{REAL_TITLES}}', ', '.join(titles)).replace(
                    '{{PAST_COMPANIES}}', ', '.join(companies))
            if isinstance(v, list):
                out = []
                for x in v:
                    if x == '{{REAL_TITLES}}': out.extend(titles)
                    elif x == '{{PAST_COMPANIES}}': out.extend(companies)
                    else: out.append(_fill(x))
                return out
            if isinstance(v, dict):
                return {k: _fill(x) for k, x in v.items()}
            return v
        _w(['config', 'summary_rules.json'], _fill(sr))

    # notify.json
    n = profile.get('notify')
    if n: _w(['config', 'notify.json'], n)

    # git.json
    g = profile.get('git')
    if g: _w(['config', 'git.json'], g)

    return written

# ---------------------------------------------------------------- CLI
def _cli():
    if '--geocode' in sys.argv:
        q = sys.argv[sys.argv.index('--geocode') + 1]
        print(json.dumps(geocode_city(q), indent=2))
        return
    if '--regen' in sys.argv:
        prof = load_profile()
        written = regenerate_all(prof)
        print('Regenerated:\n  ' + '\n  '.join(written))
        return
    print(__doc__)

if __name__ == '__main__':
    _cli()
