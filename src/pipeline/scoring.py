#!/usr/bin/env python3
"""scoring.py — classify / skill-match / strategy + the hiringcafe card parser.

Pure scoring logic split out of jobpipe.py. Imported by jobpipe.py (which
re-exports everything so tests keep resolving jobpipe.<name>); external callers
still go through `python src/pipeline/jobpipe.py <cmd>`.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, re, json

from pipelib import CONFIG, cfg, load_json, data_path, template_files

def _pick_base(*keywords):
    """Resolve a base resume from resume_template/ by keyword match on the
    filename, so the pipeline follows whatever files live there rather than a
    hardcoded name. Skips non-resume content (e.g. 'star' stories). Falls back
    to any resume file, preferring .docx.

    Returns ('', '') when the folder holds no base resume at all. It must NOT
    name a file here: an earlier version returned a legacy default
    ('PHAM_KHOA_RESUME.docx') whenever template_files() came back empty, which
    made every downstream message report a specific missing FILE for a folder
    that was empty (or unresolvable — a data_root that does not exist reads the
    same as an empty one). The caller sees no base and says so."""
    resumes = [f for f in template_files() if 'star' not in f.lower()]
    def rel(f):
        return os.path.join('resume_template', f), ('pdf' if f.lower().endswith('.pdf') else 'docx')
    for kw in keywords:
        hits = [f for f in resumes if kw in f.lower()]
        if hits:
            # A base usually exists as BOTH a .docx and its .pdf twin (e.g.
            # PHAM_KHOA_RESUME.docx + .pdf). Prefer the .docx: it is the editable
            # source the render path needs, and the template path copies whatever it
            # is handed to a `.docx` filename — a .pdf winning here only because
            # os.listdir happened to return it first produced a PDF wearing a .docx
            # extension, which Word and every ATS then choked on.
            return rel(next((f for f in hits if f.lower().endswith('.docx')), hits[0]))
    for f in resumes:
        if f.lower().endswith('.docx'):
            return rel(f)
    if resumes:
        return rel(resumes[0])
    return '', ''

# ---------- classify ----------
# Strong SRE/infra skill markers. A role carrying >= INFRA_MIN of these is treated
# as SRE/infra work and routed to the CORE base even if its TITLE says "Support"/
# "Analyst" (e.g. "Infrastructure Support Engineer" is an SRE role, not tech support).
INFRA_SIGNAL = {
    'kubernetes', 'k8s', 'terraform', 'ansible', 'infrastructure as code', 'iac',
    'ci/cd', 'cicd', 'sre', 'site reliability', 'observability', 'prometheus',
    'grafana', 'helm', 'argo cd', 'argocd', 'azure devops', 'rundeck', 'docker',
}
INFRA_MIN = 3

def classify(title, skills=None):
    t = (title or '').lower()
    # Skill-aware override: dominantly-infra roles use the CORE/SRE base regardless
    # of the title keyword, since the title alone misroutes infra "support" roles.
    if skills:
        sk = re.split(r'[;,]', skills) if isinstance(skills, str) else list(skills)
        norm_set = {s.strip().lower() for s in sk if s and s.strip()}
        if len(norm_set & INFRA_SIGNAL) >= INFRA_MIN:
            base = _pick_base('pham_khoa', 'core')
            return {'category': 'CORE', 'baseResume': base[0], 'baseType': base[1]}
    if 'analyst' in t and 'support' not in t: cat = 'ANALYST'
    elif 'support' in t: cat = 'TECH_SUPPORT'
    else: cat = 'CORE'
    if cat == 'ANALYST':
        base = _pick_base('analyst', 'data')
    elif cat == 'TECH_SUPPORT':
        base = _pick_base('support', 'tangible', 'techsupport')
    else:
        base = _pick_base('pham_khoa', 'core')
    return {'category': cat, 'baseResume': base[0], 'baseType': base[1]}

# ---------- skills ----------
def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
def known_norms():
    return {norm(k) for k in load_json(cfg('skills.json'), {}).get('skills', [])}
def _matchable():
    """Expanded set of normalized skill tokens: canonical skills + their aliases/synonyms.
    (config/skill_aliases.json) plus a list of unambiguous substring stems. Lets the scorer
    count transferable capability (e.g. AKS->Kubernetes, Azure Repos->Azure DevOps) instead of
    penalizing every granular sub-tool a posting happens to name."""
    known = known_norms()
    al = load_json(cfg('skill_aliases.json'), {}) or {}
    m = set(known)
    for canon, variants in (al.get('aliases') or {}).items():
        if norm(canon) in known:
            for v in variants: m.add(norm(v))
    return m, [s for s in (al.get('substringStems') or []) if s]
def _skill_hit(req, matchable, stems):
    rn = norm(req)
    if not rn: return False
    if rn in matchable: return True
    return any(st in rn for st in stems)
def flag_skills(required):
    matchable, stems = _matchable()
    return [r for r in required if norm(r) and not _skill_hit(r, matchable, stems)]
_SHRINK_K, _SHRINK_PRIOR = 1.0, 0.0
def _shrinkage():
    """(k, prior) for the skill-match confidence correction, from strategy.json.
    Read per call (like _matchable) so a settings edit takes effect without a restart."""
    st = load_json(cfg('strategy.json'), {}) or {}
    k, p = st.get('skill_match_shrinkage_k'), st.get('skill_match_prior')
    k = float(k) if isinstance(k, (int, float)) and not isinstance(k, bool) and k >= 0 else _SHRINK_K
    p = float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1 else _SHRINK_PRIOR
    return k, p
def skill_match(required):
    """Return (matchFraction or None, flagged_list). None when no skills were extracted.

    The fraction is SHRUNK toward `prior` with weight `k` — (matched + k*prior)/(n + k) —
    because the raw matched/n is meaningless at small n: a posting tagged with a single
    skill you happen to have scored a perfect 1.00 and outranked a 16-skill SRE role.
    Defaults (k=1, prior=0) act as one phantom unmatched skill: 1-of-1 -> 0.50,
    5-of-5 -> 0.83, 16-of-16 -> 0.94. Zero matched skills always returns 0.00 so a
    no-overlap job can't be lifted over discovery_min_match by a nonzero prior."""
    req = [r for r in required if norm(r)]
    if not req: return (None, [])
    flagged = flag_skills(req)
    matched = len(req) - len(flagged)
    if matched == 0: return (0.0, flagged)
    k, prior = _shrinkage()
    return (round((matched + k * prior) / (len(req) + k), 2), flagged)

# ---------- title lane ----------
# Explicit OFF-LANE role phrases: professions/functions that are not this user's line of
# work no matter how many tool keywords the posting happens to share. Deliberately made of
# SPECIFIC multi-word role names (or unambiguous single professions) — never a bare modifier
# like "cloud" or "sales", which show up inside legitimate titles.
_TITLE_OFFLANE = [
    'product manager', 'product owner', 'product designer', 'program manager',
    'project manager', 'scrum master', 'account executive', 'account manager',
    'sales representative', 'sales manager', 'business development',
    'ux designer', 'ui designer', 'graphic designer', 'web designer',
    'recruiter', 'talent acquisition', 'human resources', 'marketing manager',
    'social media', 'content writer', 'copywriter', 'nurse', 'physician',
    'pharmacist', 'therapist', 'dentist', 'veterinarian', 'attorney', 'paralegal',
    'teacher', 'professor', 'truck driver', 'delivery driver', 'warehouse associate',
    'cashier', 'barista', 'security guard', 'real estate', 'insurance agent',
    'financial advisor', 'loan officer', 'bookkeeper', 'social worker',
    'welder', 'electrician', 'plumber',
]
_OFFLANE_FACTOR = 0.35
def _title_norm(s):
    """Lowercase, punctuation->space, padded with spaces so `' phrase ' in title` is a
    whole-word phrase test ('cloud' won't match 'clouds', 'ops' won't match 'devops')."""
    return ' ' + re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip() + ' '
def _title_terms():
    """(in_lane, off_lane, factor). In-lane defaults to the SAME titles the searches use
    (config/profile.json search.titles) so it stays per-user; strategy.json may override
    with `title_lane_terms` and tune `title_offlane_terms` / `title_offlane_factor`."""
    st = load_json(cfg('strategy.json'), {}) or {}
    prof = load_json(cfg('profile.json'), {}) or {}
    inl = st.get('title_lane_terms')
    if not isinstance(inl, list):
        inl = ((prof.get('search') or {}).get('titles') or [])
    off = st.get('title_offlane_terms')
    if not isinstance(off, list): off = _TITLE_OFFLANE
    f = st.get('title_offlane_factor')
    f = float(f) if isinstance(f, (int, float)) and not isinstance(f, bool) and 0 <= f <= 1 else _OFFLANE_FACTOR
    clean = lambda L: [t for t in L if isinstance(t, str) and t.strip()]
    return clean(inl), clean(off), f
def title_fit(title):
    """Return (factor, reason) — a multiplier applied to skillMatch for role relevance.

    1.0 = in lane OR unrecognized. Unknown titles are NEVER penalized: the list can only
    demote what it explicitly recognizes, so a mis-worded title costs nothing.
    < 1.0 only when an explicit off-lane phrase matches.

    OFF-LANE WINS over in-lane on purpose. The in-lane list is full of bare modifiers
    ('cloud', 'platform', 'support') that appear inside off-lane titles — "Senior Product
    Manager, Cloud Video" contains 'cloud', so an in-lane-first rule would wave it through,
    which is exactly the bug this gate exists to fix."""
    t = _title_norm(title)
    if not t.strip(): return (1.0, '')
    inl, off, factor = _title_terms()
    for term in off:
        if _title_norm(term) in t:
            return (factor, 'off-lane: ' + term.strip().lower())
    for term in inl:
        if _title_norm(term) in t:
            return (1.0, 'in-lane: ' + term.strip().lower())
    return (1.0, '')
def score_job(title, required):
    """Full discovery score: shrunk skill match * title-relevance factor.

    Returns (score, flagged, factor, reason). This is what the discovery path should call —
    `skill_match` alone knows nothing about the ROLE, so an off-lane posting that happens to
    share tool keywords used to rank as high as a real one."""
    pct, flagged = skill_match(required)
    factor, reason = title_fit(title)
    if pct is None or factor >= 1.0:
        return (pct, flagged, factor, reason)
    return (round(pct * factor, 2), flagged, factor, reason)
def strategy():
    st = load_json(cfg('strategy.json'), {}) or {}
    g = load_json(cfg('gui_settings.json'), {}) or {}
    if isinstance(g.get('threshold'), (int, float)): st['min_match'] = g['threshold']
    if isinstance(g.get('localEnabled'), bool): st['local_enabled'] = g['localEnabled']
    # Skill-match floors are driven by the dashboard sliders (gui_settings.json), falling
    # back to strategy.json when unset. globalFloor = hard cutoff for candidates.csv;
    # remoteFloor / localFloor filter the dashboard + phone alerts by location.
    if isinstance(g.get('globalFloor'), (int, float)): st['discovery_min_match'] = g['globalFloor']
    elif isinstance(g.get('discoveryFloor'), (int, float)): st['discovery_min_match'] = g['discoveryFloor']  # back-compat
    if isinstance(g.get('remoteFloor'), (int, float)): st['remote_min_match'] = g['remoteFloor']
    if isinstance(g.get('localFloor'), (int, float)): st['local_min_match'] = g['localFloor']
    elif isinstance(g.get('localThreshold'), (int, float)): st['local_min_match'] = g['localThreshold']  # back-compat
    # Resume customization trigger: AI-tailor only when skillMatch < this; else use the template.
    if isinstance(g.get('resumeCustomizeThreshold'), (int, float)): st['resume_customize_threshold'] = g['resumeCustomizeThreshold']
    return st

# ---------- hiringcafe.com card parser ----------
_ROLE_WORDS = re.compile(r'\b(engineer(ing)?|analyst|developer|administrator|architect|specialist|'
                         r'support|manager|consultant|technician|sre|devops|scientist|lead)\b', re.I)
# Keywords are case-insensitive via the scoped (?i:...) group ("Remote" as the site
# renders it); the state-code alternative stays case-SENSITIVE on purpose, else
# ",\s*[a-z]{2}" would claim segments like "Acme, inc" as locations.
_LOC_RE = re.compile(r'(?i:\b(remote|hybrid|on-?site|united states|usa)\b)|,\s*[A-Z]{2}\b')
_SAL_RE = re.compile(r'\$\s?\d|\b\d{2,3}\s?k\b', re.I)

def _parse_card(text, matchable, stems):
    segs = [s.strip() for s in (text or '').split('|') if s.strip()]
    out = {'title': '', 'company': '', 'location': '', 'salary': '', 'requiredSkills': []}
    used = set()
    for i, s in enumerate(segs):          # salary
        if _SAL_RE.search(s) and len(s) < 60: out['salary'] = s; used.add(i); break
    for i, s in enumerate(segs):          # skills line: >=2 known-skill hits among list tokens
        if i in used: continue
        toks = [t.strip() for t in re.split(r'[,•·;]', s) if t.strip()]
        if len(toks) >= 2 and sum(1 for t in toks if _skill_hit(t, matchable, stems)) >= 2:
            out['requiredSkills'] = toks; used.add(i); break
    for i, s in enumerate(segs):          # location
        if i in used: continue
        if _LOC_RE.search(s) and len(s) < 60: out['location'] = s; used.add(i); break
    for i, s in enumerate(segs):          # title: first role-word segment
        if i in used: continue
        if _ROLE_WORDS.search(s) and len(s) < 90: out['title'] = s; used.add(i); break
    for i, s in enumerate(segs):          # company: first remaining short segment
        if i in used: continue
        if 0 < len(s) < 60 and not _SAL_RE.search(s): out['company'] = s; used.add(i); break
    if not out['title'] and segs: out['title'] = segs[0]
    return out

def cmd_parse_cards(a):
    """cards.json = [{"slug","text"}] from the one-shot Chrome JS extraction. Writes listings.json.
    Prints a 'review' list of cards missing title/company/skills — the LLM fixes ONLY those."""
    cards = load_json(data_path(a.raw), []) or []
    matchable, stems = _matchable()
    listings, review = [], []
    for c in cards:
        sl = c.get('slug', '')
        parsed = _parse_card(c.get('text', ''), matchable, stems)
        listings.append({'source': 'hiring_cafe', 'id': 'hc_' + sl[:48],
                         'applyUrl': 'https://hiringcafe.com/job/' + sl, **parsed})
        missing = [k for k in ('title', 'company', 'requiredSkills') if not parsed[k]]
        if missing:
            review.append({'slug': sl, 'missing': missing, 'text': c.get('text', '')[:200]})
    out = a.out or 'listings.json'
    with open(out, 'w', encoding='utf-8') as f: json.dump(listings, f, indent=2)
    print(json.dumps({'written': out, 'count': len(listings),
                      'reviewCount': len(review), 'review': review}, indent=2))
