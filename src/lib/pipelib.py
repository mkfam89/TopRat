#!/usr/bin/env python3
"""pipelib.py — shared path constants, io helpers, and tiny text utilities.

Single home for everything the jobpipe modules (scoring / tracker / salary /
dashboard_build) and jobpipe.py itself all need. No business logic lives here.
Imported BY jobpipe.py and its modules only — external callers keep shelling
out to `python src/pipeline/jobpipe.py <cmd>`.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, re, json

HERE = _paths.ROOT
BASE = HERE

# ---------------------------------------------------------------- data root --
# PERSONAL data (tracker state, profile, generated config, resume content) lives
# in ONE folder that is never the code folder, so the code repo carries none of it.
# Resolution order, first hit wins:
#   1. $TOP_RAT_DATA  (or the legacy $JOB_AGENT_DATA)
#   2. config/instance.json  ->  "data_root"   (relative paths resolve off HERE)
#   3. nested  ./user_data_<INITIALS>          (the DEFAULT — self-contained install)
#   4. ~/Documents/my_job_agent                (legacy default location)
#   5. sibling ../job_agent_profile_KP         (legacy repo-beside-repo layout)
#   6. nested  ./job_agent_profile_KP          (transitional, while migrating)
#   7. HERE                                    (legacy single-folder install)
#
# 3-6 only win if the directory actually exists, so step 7 is load-bearing: an
# install that never splits behaves EXACTLY as before, and the deterministic
# zero-token script path keeps working with no data folder and no env var set
# (CLAUDE.md — every feature degrades gracefully). To relocate the data later,
# set "data_root" in config/instance.json (or $TOP_RAT_DATA per-shell) — the
# defaults below are only reached when neither override is set.
#
# Why rung 3 is a GLOB and not a fixed name: the folder is named from the user's
# initials, and the initials live in profile.json — which lives INSIDE the data
# root we are still trying to find. Reading it here would be circular. Globbing
# for the folder breaks the cycle: setup writes the chosen path into
# instance.json (rung 2), and rung 3 is the zero-config fallback that finds it
# again if instance.json is ever deleted, reset, or never written.
USER_DATA_PREFIX = 'user_data_'
_DATA_DIRNAME = 'job_agent_profile_KP'
DEFAULT_DATA = os.path.join(os.path.expanduser('~'), 'Documents', 'my_job_agent')


def nested_user_data(root=None):
    """The self-contained data folder inside the app dir: <app>/user_data_<INITIALS>.

    Returns '' unless EXACTLY ONE user_data_* directory is present. Two of them
    (a second person set up on the same copy, or a half-finished migration) is
    genuinely ambiguous, and silently picking the alphabetically-first one would
    point the pipeline at somebody else's tracker. Ambiguity falls through to the
    later rungs instead, where the user is asked. Never raises."""
    base = root or HERE
    try:
        hits = sorted(n for n in os.listdir(base)
                      if n.lower().startswith(USER_DATA_PREFIX)
                      and os.path.isdir(os.path.join(base, n)))
    except OSError:
        return ''
    return os.path.join(base, hits[0]) if len(hits) == 1 else ''


def suggest_user_data_dirname(initials):
    """'KP' -> 'user_data_KP'. Falls back to a generic name for an empty/odd value."""
    safe = re.sub(r'[^A-Za-z0-9]', '', (initials or '')).upper()
    return USER_DATA_PREFIX + (safe or 'ME')


def _read_json_quiet(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def env(suffix, default=''):
    """Read TOP_RAT_<suffix>, falling back to the old JOB_AGENT_<suffix>.

    The app was renamed to Top Rat, but the old variable names may already be set
    in a Windows scheduled task, a .bat, or a shell profile — places nobody thinks
    to update, and where a rename fails SILENTLY (the variable is simply unset, so
    the code takes its default and the user gets a working app pointed at the wrong
    data root). Both spellings are honoured for exactly that reason.

    New name wins when both are set. Returns a stripped string.
    """
    for name in ('TOP_RAT_' + suffix, 'JOB_AGENT_' + suffix):
        v = (os.environ.get(name) or '').strip()
        if v:
            return v
    return default


def env_truthy(suffix):
    """env() read as a flag: 1/true/yes/on."""
    return env(suffix).lower() in ('1', 'true', 'yes', 'on')


def resolve_data_root():
    """Absolute path of the personal-data root. Never raises."""
    d = env('DATA')
    if d:
        return os.path.abspath(os.path.expanduser(d))
    d = str(_read_json_quiet(os.path.join(HERE, 'config', 'instance.json'))
            .get('data_root') or '').strip()
    if d:
        d = os.path.expanduser(d)
        # A Windows path ("C:\...") read on a non-Windows box is not a path at all there:
        # '\' and ':' are ordinary characters, so os.makedirs() would happily create ONE
        # folder literally named 'C:\Users\you\...\job_agent_profile_XX' inside the repo, and
        # the run would write its state into that junk instead of the real data root.
        # Ignore the pointer instead and fall through to the platform defaults.
        if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', d):
            d = ''
    if d:
        return os.path.abspath(d if os.path.isabs(d) else os.path.join(HERE, d))
    # Self-contained layout: the data folder sits INSIDE the app folder, so a copied
    # or moved install keeps its data without an absolute path to fix up.
    nested_user = nested_user_data()
    if nested_user:
        return os.path.abspath(nested_user)
    if os.path.isdir(DEFAULT_DATA):
        return DEFAULT_DATA
    sibling = os.path.join(os.path.dirname(HERE), _DATA_DIRNAME)
    if os.path.isdir(sibling):
        return sibling
    nested = os.path.join(HERE, _DATA_DIRNAME)   # transitional: data repo not yet moved out
    if os.path.isdir(nested):
        return nested
    return HERE


DATA = resolve_data_root()
# True when data actually lives outside the code repo. Everything below keys off
# this, so the pre-split layout takes the identical code path it always did.
SPLIT = os.path.normcase(os.path.abspath(DATA)) != os.path.normcase(os.path.abspath(HERE))

CODE_CONFIG = os.path.join(HERE, 'config')   # shipped defaults + *.example.json (versioned WITH the code)
DATA_CONFIG = os.path.join(DATA, 'config')   # this user's private config (never committed to the code repo)
USER_CONFIG = DATA_CONFIG if SPLIT else CODE_CONFIG   # where user config is WRITTEN
CONFIG = CODE_CONFIG                         # legacy alias: the SHIPPED config dir. Per-file reads go through cfg().


# BOOTSTRAP file — always beside the CODE, never in the data root, because the data
# root is resolved FROM it (chicken-and-egg). Per-instance/per-worktree by design.
INSTANCE_JSON = os.path.join(CODE_CONFIG, 'instance.json')


def _overlay(*parts):
    """Read path: the data root wins when the file is really there, shipped copy is the fallback."""
    p = os.path.join(DATA, *parts)
    return p if os.path.exists(p) else os.path.join(HERE, *parts)


def cfg(name):
    """READ path for a config file — user's private copy shadows the shipped default."""
    return _overlay('config', name)


def cfg_write(name):
    """WRITE path for a config file — user config always lands in the data root."""
    os.makedirs(USER_CONFIG, exist_ok=True)
    return os.path.join(USER_CONFIG, name)


def _d(name):
    """State file that is READ AND WRITTEN — unambiguously in the data root once split."""
    return os.path.join(DATA if SPLIT else HERE, name)


def data_path(p):
    """Resolve a file path given on the COMMAND LINE (e.g. --listings listings.json).

    Bare relative names in saved schedule.json steps ('listings.json') used to resolve
    off the process cwd, which scheduler.py sets to the CODE root (HERE). After the data
    split scrape.py writes listings.json into the DATA root, so `jobpipe.py candidates
    --listings listings.json` read a nonexistent file, got [], and silently overwrote
    candidates.csv with a header — the board went empty while the scrape logged 100+ hits.

    Order: absolute/user-expanded as given; otherwise data root, then code root, then cwd.
    Returns the first existing candidate, else the data-root path (so error messages name
    the location the file is actually supposed to live in)."""
    p = os.path.expanduser(str(p or ''))
    if not p:
        return p
    if os.path.isabs(p):
        return p
    for root in (DATA, HERE, os.getcwd()):
        cand = os.path.join(root, p)
        if os.path.exists(cand):
            return cand
    return os.path.join(DATA, p)


def load_listings(p, cmd=''):
    """Load a scrape output file for a command that consumes it (candidates/filter).

    Resolves via data_path() and FAILS LOUDLY on missing/empty/malformed input. The old
    silent `load_json(a.listings, []) or []` turned a path mistake into a normal-looking
    run that reported '0 kept' and blanked candidates.csv, so a broken pipeline read as
    'no jobs today'. Returns the listings list (never None)."""
    fp = data_path(p)
    tag = f'{cmd}: ' if cmd else ''
    if not os.path.exists(fp):
        sys.exit(f'FATAL: {tag}listings file not found: {fp}\n'
                 f'       (arg was "{p}"; searched the data root, the code folder, and the cwd)\n'
                 '       Refusing to run — writing an empty candidates.csv would wipe the board.')
    data = load_json(fp, None)
    if data is None:
        sys.exit(f'FATAL: {tag}{fp} exists but cannot be parsed (truncated write?). '
                 'Re-run scrape.py before scoring.')
    if not isinstance(data, list):
        sys.exit(f'FATAL: {tag}{fp} is {type(data).__name__}, expected a list of listings.')
    # An empty list is NOT fatal — a real scrape can legitimately find nothing in a 24h
    # window. The caller must skip its write instead, so an empty scrape never blanks a
    # pool built by earlier runs.
    return data


JSON_PATH = _d('job_tracker.json')
HTML_PATH = _d('job_tracker.html')

def _profile_template_dir():
    """Folder the user picked in Settings — profile.json  resume.template_dir.

    The field has been written since the first setup wizard but nothing read it, so a
    path chosen there was silently ignored. Now it WINS when it names a real folder:
    absolute as typed/browsed, relative resolved against the data root then the code dir
    (so the shipped default value "resume_template" lands exactly where it always did).
    Returns '' for unset/missing/unreadable — the caller falls back to the built-in
    location, so the zero-token script path still runs with no profile at all."""
    prof = _read_json_quiet(cfg('profile.json'))
    res = prof.get('resume') if isinstance(prof, dict) else None
    d = str((res or {}).get('template_dir') or '').strip() if isinstance(res, dict) else ''
    if not d:
        return ''
    d = os.path.expanduser(d)
    if os.path.isabs(d):
        return os.path.abspath(d) if os.path.isdir(d) else ''
    for root in (DATA, HERE):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            return os.path.abspath(p)
    return ''


# ---------------------------------------------------------------- resume prefix
# Every tailored resume is named "<prefix><Company>_<Role>.docx". The prefix used to be a
# hardcoded literal carrying the FIRST user's name, in six places, so a second user got
# resumes with someone else's name in the filename. It now comes from profile.json
# identity.resume_prefix (the field setup.py / the /setup page have always written but
# nothing ever read).
#
# Three-step fallback, so the zero-token script path still runs with NO profile at all:
#   1. identity.resume_prefix           — what the wizard stored
#   2. derived from identity.full_name  — "Jane Doe" -> "Jane_Doe_Resume_"
#   3. _DEFAULT_RESUME_PREFIX           — anonymous last resort
# Step 3 is deliberately GENERIC rather than the original owner's literal prefix. Keeping
# that literal as the default read like harmless backward compatibility, but it is exactly
# the string release.py's PII gate exists to keep out of a shipped copy — and it was dead
# weight anyway, since any configured install sets step 1.
_DEFAULT_RESUME_PREFIX = 'Resume_'

# Matches the configured prefix AND any legacy/other-user one. Filenames already on disk
# were written under whatever prefix was current when they were tailored; the scan filters
# and stem() must keep resolving those, or every previously-tailored resume in New/ and
# Applied/ silently stops matching its tracker id. Non-greedy: '_Resume_' occurs once.
_ANY_PREFIX_RE = re.compile(r'^.+?_Resume_')

_resume_prefix_cache = None

def _derive_resume_prefix(full_name):
    parts = re.findall(r'[A-Za-z0-9]+', full_name or '')
    return ('_'.join(parts) + '_Resume_') if parts else ''

def resume_prefix():
    """Filename prefix for tailored resumes, from profile.json (cached per process)."""
    global _resume_prefix_cache
    if _resume_prefix_cache is None:
        prof = _read_json_quiet(cfg('profile.json'))
        ident = prof.get('identity') if isinstance(prof, dict) else None
        ident = ident if isinstance(ident, dict) else {}
        p = str(ident.get('resume_prefix') or '').strip()
        if not p:
            p = _derive_resume_prefix(ident.get('full_name'))
        # A prefix without the separator would collide with the stem, so normalize it.
        if p and not p.endswith('_'):
            p += '_'
        _resume_prefix_cache = p or _DEFAULT_RESUME_PREFIX
    return _resume_prefix_cache

def reset_resume_prefix():
    """Drop the cache — for tests and for the /setup save path."""
    global _resume_prefix_cache
    _resume_prefix_cache = None

def is_resume_file(fname):
    """Is this a tailored-resume file (any prefix, current or legacy)?

    Used by every folder scan. Deliberately prefix-AGNOSTIC rather than an equality
    check on resume_prefix(): the moment a user edits the prefix in Settings, an
    equality check would orphan every resume already on disk."""
    # Extension check stays case-SENSITIVE to match the scans this replaced (and stem(),
    # which strips a lowercase extension). Loosening it here would admit a '.PDF' that
    # stem() then fails to strip, producing an id with the extension still attached.
    #
    # '~$' is rejected HERE, not just by the callers. The old literal-prefix test excluded
    # a Word lock file ('~$<prefix>Acme_SRE.docx') for free because it did not start with
    # the prefix; the regex matches it, and dashboard_build._id_in_dir has no separate '~$'
    # guard — a stale lock file would have read as "this job's resume is already filed here"
    # and deleted the real copy out of New/.
    if fname.startswith('~$') or fname.startswith('PREVIEW'):
        return False
    return fname.endswith(('.pdf', '.docx')) and bool(_ANY_PREFIX_RE.match(fname))


_DATA_TEMPLATES = os.path.join(DATA, 'resume_template')
TEMPLATE_DIR = (_profile_template_dir()
                or (_DATA_TEMPLATES if SPLIT and os.path.isdir(_DATA_TEMPLATES)
                    else os.path.join(HERE, 'resume_template')))
BULLETS = os.path.join(TEMPLATE_DIR, 'bullets.json')
BULLETS_SCHEMA = os.path.join(TEMPLATE_DIR, 'bullets.schema.json')

TRACKING_CSV = _d('tracking.csv')
ARCHIVE_CSV = _d('archive_index.csv')
CANDIDATES_CSV = _d('candidates.csv')
TO_PROCESS = _d('to_process.json')
# Why a tailoring run produced no resume, per job id. Written by tailor_local.py (the only
# thing that builds resumes), read by dashboard_server for the board's error tag. Every
# failure used to print to the console and leave the job silently queued, so the board
# showed a state ("Templated") that no longer matched what the queue could actually do.
TAILOR_ERRORS = _d('tailor_errors.json')
SALARY_BANDS = cfg('salary_bands.json')                  # reference data — ships with the code
SALARY_CACHE = _d('salary_cache.json')                   # written by salary_probe.py
SALARY_QUOTA = _d('salary_quota.json')
GEO_CACHE = _d('geo_cache.json')
SCRAPE_META = _d('scrape_meta.json')
NOTIFIED = _d('notified.json')
RAW_APPLIED = _d('raw_applied.json')

def template_files():
    """All base-resume files present in resume_template/ (any filename).
    Supplementary content (e.g. STAR stories) is skipped as a base resume."""
    try:
        return [f for f in os.listdir(TEMPLATE_DIR)
                if f.lower().endswith(('.docx', '.pdf')) and not f.startswith('~$')]
    except OSError:
        return []

# Exclude every file that lives in resume_template/ (by basename) from job-folder scans.
#
# There used to be a hardcoded set of four legacy basenames here from the original
# single-user install. They were personal filenames living in shipped code — the thing
# release.py's PII gate rejects — and they were also the wrong mechanism: any base resume
# the user actually has is already covered by template_files(), and a legacy name that is
# NO LONGER in the template folder describes a file this install has no other reason to
# know about. A user who still has such a stray in a job folder can delete it, which is
# what they would have to do for any other stray anyway.
TEMPLATES = set(template_files())

def load_json(p, default=None):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception: return default

def _safe_write_text(path, text, verify_json=False, tries=6):
    # Atomic, verified write to defend against intermittent mount write-truncation.
    # Writes to a temp file, fsyncs, reads it back and checks it byte-for-byte
    # (and parses it if JSON), then os.replace()s into place and re-verifies.
    # Retries on any mismatch; raises rather than leaving a truncated file.
    import time
    expect = text.encode('utf-8')
    tmp = path + '.tmp'
    last = None
    for _ in range(tries):
        try:
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
                f.flush()
                try: os.fsync(f.fileno())
                except OSError: pass
            with open(tmp, 'rb') as f: got = f.read()
            if got != expect:
                last = 'tmp %d/%d bytes' % (len(got), len(expect)); time.sleep(1); continue
            if verify_json: json.loads(got.decode('utf-8'))
            os.replace(tmp, path)
            with open(path, 'rb') as f: fin = f.read()
            if fin != expect:
                last = 'final %d/%d bytes' % (len(fin), len(expect)); time.sleep(1); continue
            if verify_json: json.loads(fin.decode('utf-8'))
            return
        except Exception as e:
            last = repr(e); time.sleep(1)
    try:
        if os.path.exists(tmp): os.remove(tmp)
    except OSError: pass
    raise IOError('safe_write failed for %s after %d tries (%s)' % (path, tries, last))

def stem(fname):
    # Strip the CONFIGURED prefix first, then fall back to any '<name>_Resume_' prefix, so
    # files tailored under an older prefix (or by a previous owner of the install) still
    # resolve to their tracker id. Order matters: the configured prefix is exact, the
    # regex is a best-effort rescue for everything else.
    p = resume_prefix()
    if p and fname.startswith(p):
        fname = fname[len(p):]
    else:
        fname = _ANY_PREFIX_RE.sub('', fname, count=1)
    return fname.replace('.pdf', '').replace('.docx', '')
def camel_to_words(s):
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    s = re.sub(r'([a-z\d])([A-Z])', r'\1 \2', s)
    return s.strip()
def slug(text):
    return ''.join(w[:1].upper() + w[1:] for w in re.findall(r'[A-Za-z0-9]+', text or ''))

# --- filename/id shorteners --------------------------------------------------
# Recruiters read the resume filename; an over-long auto-generated name reads as
# bot-applied. tracker_id() (and render_resume's output filename) run the raw
# company/role through these FIRST so the on-disk name is just "<Company>_<Role>"
# core, not the full posting title with every location/timezone/tech-stack tail.
# slug() itself stays pure — its round-trip (camel_to_words∘slug) and the applied
# reconciler depend on it — so only the *inputs* to the id are trimmed.

# Location / employment-type / timezone qualifier words dropped from a role tail.
_ROLE_HEADS = {
    'engineer', 'engineering', 'developer', 'analyst', 'manager', 'consultant',
    'administrator', 'admin', 'specialist', 'architect', 'scientist', 'technician',
    'lead', 'designer', 'coordinator', 'representative', 'associate', 'intern',
    'director', 'officer', 'owner', 'strategist', 'advisor', 'generalist',
    'recruiter', 'accountant', 'controller', 'supervisor', 'agent', 'programmer',
    'tester', 'evangelist', 'ambassador', 'sre', 'devops',
}
_ROLE_LEVEL = re.compile(
    r'i{1,3}|iv|v|[1-4]|sr|senior|jr|junior|lead|staff|principal|associate', re.I)
# Corporate / location suffix tokens stripped from a company name.
_CO_SUFFIX = {
    'inc', 'incorporated', 'llc', 'llp', 'lp', 'ltd', 'limited', 'corp',
    'corporation', 'co', 'company', 'plc', 'gmbh', 'sa', 'nv', 'ag', 'group',
    'holding', 'holdings', 'technologies', 'technology', 'labs', 'solutions',
    'systems', 'software', 'services', 'international', 'worldwide', 'global',
    'usa', 'us', 'na', 'division', 'div', 'the',
}
_CO_PHRASE = re.compile(r'\bnorth\s+america\b', re.I)

def clean_company(name):
    """Human-short company for the filename: drop parentheticals, legal/location
    suffixes, and cap to the first few significant words.
    'ENGIE North America Inc' -> 'ENGIE'; 'EPAM Systems Inc' -> 'EPAM'."""
    s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', name or '')
    if ' ' not in s.strip():
        s = camel_to_words(s)              # split slugged/camelCase inputs (render passes these)
    s = _CO_PHRASE.sub(' ', s)             # 'North America' (now space-separated)
    s = s.split(',')[0]                    # drop a ', ND Division'-style trailing clause
    words = re.findall(r'[A-Za-z0-9&]+', s)
    kept = [w for w in words if w.lower() not in _CO_SUFFIX]
    kept = (kept or words)[:3]             # never empty out; cap length
    return ' '.join(kept)

def clean_role(role):
    """Core job title only: drop parentheticals, trailing team/specialty, location,
    timezone, remote flags and tech-stack tails; keep seniority + level. The title
    ends at the last role-head word (Engineer/Analyst/...) plus a trailing level.
    'Site Reliability Engineer (US, Central/Eastern)' -> 'Site Reliability Engineer';
    'Software Engineer III Power Platform' -> 'Software Engineer III'."""
    s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', role or '')
    # Cut a trailing clause after a separator: spaced dash/slash ('A / B' dual titles)
    # or comma/pipe/colon. A TIGHT slash ('DevOps/Platform') is preserved.
    s = re.split(r'\s[-–—/]\s|[,|:·]', s)[0]
    if ' ' not in s.strip():
        s = camel_to_words(s)              # split slugged/camelCase inputs
    words = re.findall(r'[A-Za-z0-9+#]+', s)
    if not words:
        return ''
    heads = [i for i, w in enumerate(words) if w.lower() in _ROLE_HEADS]
    if not heads:
        return ' '.join(words[:5])          # no head noun — keep a short prefix
    end = heads[-1] + 1
    while end < len(words) and _ROLE_LEVEL.fullmatch(words[end]):
        end += 1
    return ' '.join(words[:end])

def tracker_id(company, role):
    return (slug(clean_company(company)) + '_' + slug(clean_role(role)))[:80]


# ---- employment type (deterministic, zero-token; used by the card badge) ----
# A job is assumed full-time W2 unless a signal says otherwise. Only NON-default
# arrangements get a badge, so a normal salaried role stays unadorned. Order matters:
# the first matching row wins, so more specific arrangements are listed before broader
# ones (e.g. contract-to-hire before plain contract, internship before part-time).
_EMPLOYMENT_PATTERNS = [
    ('Contract-to-hire', r'\bcontract[\s-]*to[\s-]*(hire|perm|permanent)\b|\bc2h\b|\btemp[\s-]*to[\s-]*(hire|perm)\b|\bright[\s-]*to[\s-]*hire\b'),
    ('Internship',       r'\binternship\b|\bintern\b|\bco[\s-]?op\b|\bindustrial placement\b'),
    ('Apprenticeship',   r'\bapprentice(ship)?\b|\btrainee\b'),
    ('Fellowship',       r'\bfellowship\b|\bfellow\b'),
    ('Seasonal',         r'\bseasonal\b'),
    ('Per diem',         r'\bper[\s-]?diem\b|\blocum\b|\bprn\b'),
    ('Temporary',        r'\btemporary\b|\btemp\b|\bfixed[\s-]*term\b|\binterim\b|\bcontingent\b'),
    ('Freelance',        r'\bfreelance\b|\bfreelancer\b'),
    ('Contract',         r'\bcontract(or)?\b|\bc2c\b|\bcorp[\s-]*to[\s-]*corp\b|\b1099\b|\bw2\s*contract\b|\bindependent contractor\b|\bstaff\s*aug(mentation)?\b'),
    ('Part-time',        r'\bpart[\s-]?time\b|\bpart[\s-]?time\b'),
]
# Contexts where "contract" etc. is NOT an employment arrangement — e.g. "contract
# management", "government contracts", "smart contracts". Suppresses a false badge when
# the word is only part of a phrase like these and no other signal fires.
_EMPLOYMENT_NOISE = re.compile(
    r'contract\s+(management|manager|negotiation|law|lifecycle|administration|drafting|review)|'
    r'smart\s+contract|government\s+contract|contract\s+vehicle|social\s+contract|'
    r'permanent\s+(residen|contract)', re.I)
# Explicit full-time / permanent wording — lets a page that says both "contract role" in a
# boilerplate benefits blurb AND "this is a full-time position" avoid a wrong badge is NOT
# our goal; instead this only clears the hourly-alone weak signal (see below).
_FULLTIME_HINT = re.compile(r'\bfull[\s-]?time\b|\bfull[\s-]?time\s+employee\b|\bfte\b|\bpermanent\b|\bw2\s+(employee|full)', re.I)


def detect_employment_type(title='', location='', description='', salary=''):
    """Return a short badge label (e.g. 'Contract', 'Part-time', 'Internship') when the
    role is clearly NOT a standard full-time W2 position, else ''.

    Deterministic and side-effect free so both scrape.py and the dashboard can call it and
    tests can pin it. Signals, strongest first: explicit arrangement words in the title or
    location (most reliable — recruiters put "(Contract)"/"Part-Time" right in the title),
    then the same words anywhere in the description. An hourly-only pay figure with no other
    signal is left unlabeled: plenty of full-time roles quote an hourly wage."""
    title = title or ''; location = location or ''; description = description or ''
    # Title + location carry the least noise, so trust them first and scan the full text second.
    strong = '%s ‹› %s' % (title, location)
    body = (description or '')[:6000]
    for scope in (strong, body):
        low = scope.lower()
        for label, pat in _EMPLOYMENT_PATTERNS:
            if re.search(pat, low, re.I):
                # In the description scope only, ignore a lone "contract*" that is really a
                # phrase like "contract management" with no standalone arrangement wording.
                if (scope is body and label in ('Contract', 'Contract-to-hire')
                        and _EMPLOYMENT_NOISE.search(low)
                        and not re.search(r'\bthis is a\b[^.]*\bcontract\b|\bcontract (role|position|opportunity|assignment|basis|engagement)\b|\bon a contract\b|\bw2 contract\b|\bc2c\b|\b1099\b', low)):
                    continue
                return label
    return ''
