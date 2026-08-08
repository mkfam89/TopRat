#!/usr/bin/env python3
"""setup.py — first-run onboarding wizard for the job pipeline.

Run once (or any time to change your search): `python src/ops/setup.py`
It asks plain-language questions, geocodes your city, and writes config/profile.json
plus every derived config (search_urls.json for LinkedIn + hiringcafe.com, skills.json,
skill_aliases.json, notify.json, git.json). Nothing here is board-specific — the
generators apply each filter only to the boards that support it.

Re-running pre-fills answers from your existing profile.json (press Enter to keep).
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, copy
import profile_lib as pl

WT_CHOICES = ['Remote', 'Hybrid', 'Onsite', 'Field']
SENIORITY = ['Internship', 'Entry Level', 'Mid Level', 'Senior Level', 'Director', 'Executive']
EMPLOYMENT = ['Full Time', 'Part Time', 'Contract', 'Temporary', 'Internship']

def ask(prompt, default=None):
    d = '' if default is None else ' [%s]' % default
    try:
        v = input('%s%s: ' % (prompt, d)).strip()
    except EOFError:
        v = ''
    return v or (default if default is not None else '')

def ask_list(prompt, default):
    d = ', '.join(default) if default else ''
    v = ask(prompt + ' (comma-separated)', d)
    return [x.strip() for x in v.split(',') if x.strip()]

def ask_bool(prompt, default=True):
    v = ask(prompt + ' (y/n)', 'y' if default else 'n').lower()
    return v.startswith('y')

def ask_int(prompt, default):
    while True:
        v = ask(prompt, str(default))
        try: return int(str(v).replace(',', '').replace('$', ''))
        except ValueError: print('  Enter a number.')

def ask_choices(prompt, options, default):
    print(prompt)
    for i, o in enumerate(options, 1):
        mark = 'x' if o in default else ' '
        print('   [%s] %d) %s' % (mark, i, o))
    raw = ask('   Pick numbers (comma-separated), or press Enter to keep',
              ','.join(str(options.index(d) + 1) for d in default if d in options))
    picked = []
    for tok in raw.split(','):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= len(options):
            picked.append(options[int(tok) - 1])
    return picked or default

def resolve_location(existing):
    while True:
        query = ask('Target city (e.g. "Austin, TX" or "Remote, US")',
                    existing.get('query', 'Houston, TX'))
        try:
            print('  geocoding %r ...' % query)
            loc = pl.geocode_city(query)
            print('  -> %s  (%.4f, %.4f)' % (loc['formatted_address'], loc['lat'], loc['lon']))
            if ask_bool('  Use this location?', True):
                return loc
        except Exception as e:
            print('  The geocoding failed (%s).' % e)
            if ask_bool('  Enter the coordinates manually instead?', True):
                return {
                    'query': query, 'city': ask('  City', existing.get('city', '')),
                    'state': ask('  State/region code', existing.get('state', '')),
                    'country': ask('  Country code', existing.get('country', 'US')) or 'US',
                    'lat': float(ask('  Latitude', existing.get('lat', 0)) or 0),
                    'lon': float(ask('  Longitude', existing.get('lon', 0)) or 0),
                    'population': existing.get('population', 0),
                    'formatted_address': ask('  Display address', existing.get('formatted_address', query)),
                }

def _mask(k):
    k = k or ''
    return ('*' * max(0, len(k) - 4) + k[-4:]) if k else '(none)'


def _report_claude_cli():
    """Report whether AI tailoring is available. There is nothing to configure or store.

    Tailoring goes through the Claude Code CLI (`claude -p`), which authenticates with the
    user's own `claude login` and bills against their Claude PLAN. This app holds no API key,
    so this step only looks and tells — it never asks for a secret.
    """
    try:
        import llm_tailor
    except Exception as e:
        print('  (Could not check now: %s)' % e)
        return
    binp = llm_tailor.find_claude()
    if not binp:
        print('  Claude Code was not found. AI tailoring is off; "customize" jobs will get')
        print('  the template copy. To turn it on, install Claude Code from')
        print('  https://docs.claude.com/en/docs/claude-code and run `claude login`.')
        return
    print('  Found Claude Code at %s' % binp)
    if os.environ.get('ANTHROPIC_API_KEY'):
        print('  Note: ANTHROPIC_API_KEY is set in your environment. This app removes it')
        print('  before calling Claude Code, so tailoring stays on your plan, not metered API.')
    print('  Checking the login (this asks Claude one short question)...')
    try:
        ok, msg = llm_tailor.check()
        print('  ' + ('Connected. ' if ok else 'Not ready: ') + msg)
    except Exception as e:
        print('  (Could not verify now: %s)' % e)


def main():
    print('=' * 64)
    print('  Job Pipeline — setup')
    print('=' * 64)
    prof = pl.load_profile(clean=False)          # keep any existing values as defaults
    prof = pl._strip_notes(prof)
    ident = prof.setdefault('identity', {})
    resume = prof.setdefault('resume', {})
    skills = prof.setdefault('skills', {})
    s = prof.setdefault('search', {})
    notify = prof.setdefault('notify', {})
    git = prof.setdefault('git', {})

    print('\n-- Your resume files --')
    ident['full_name'] = ask('Your full name', ident.get('full_name', ''))
    default_prefix = (ident['full_name'].replace(' ', '_') + '_Resume_') if ident.get('full_name') else (ident.get('resume_prefix') or 'Resume_')
    ident['resume_prefix'] = ask('Tailored-resume filename prefix', default_prefix)
    ident['real_titles'] = ask_list('Real job titles you have held', ident.get('real_titles', []))
    ident['past_companies'] = ask_list('Past companies (optional)', ident.get('past_companies', []))
    resume['template_dir'] = ask('Folder that holds your base resumes', resume.get('template_dir', 'resume_template'))

    print('\n-- Preferences --')
    s['titles'] = ask_list('Target role keywords', s.get('titles', ['data analyst']))
    s['exclude_terms'] = ask_list('Exclude these title words', s.get('exclude_terms', ['manager', 'director']))
    s['location'] = resolve_location(s.get('location', {}))
    s['radius_miles'] = ask_int('Search radius (miles)', s.get('radius_miles', 50))
    s['workplace_types'] = ask_choices('Workplace types:', WT_CHOICES, s.get('workplace_types', ['Remote', 'Hybrid', 'Onsite']))
    s['include_remote_anywhere_in_country'] = ask_bool('Also include country-wide remote roles?', s.get('include_remote_anywhere_in_country', True))
    s['seniority'] = ask_choices('Seniority levels:', SENIORITY, s.get('seniority', ['Mid Level', 'Senior Level']))
    yoe = s.get('years_experience', [0, 15])
    s['years_experience'] = [ask_int('Minimum years experience', yoe[0]), ask_int('Maximum years experience', yoe[1] if len(yoe) > 1 else 15)]
    s['employment_types'] = ask_choices('Employment types:', EMPLOYMENT, s.get('employment_types', ['Full Time']))
    s['salary_min'] = ask_int('Minimum salary ($/yr)', s.get('salary_min', 80000))
    s['salary_max'] = ask_int('Maximum salary ($/yr, target ceiling)', s.get('salary_max', 150000))
    # advanced hiringcafe.com-only filters keep sensible defaults unless already set
    s.setdefault('security_clearances', ['None'])
    s.setdefault('air_travel_ok', ['Minimal', 'None'])
    s.setdefault('land_travel_ok', ['Minimal', 'None'])
    s.setdefault('date_presets', {'fresh': 1, 'weekly': 7})
    s['use_linkedin'] = ask_bool('Search LinkedIn?', s.get('use_linkedin', True))
    if s['use_linkedin']:
        s['linkedin_top_applicant'] = ask_bool('  Include LinkedIn Top-Applicant feed? (needs an active LinkedIn Premium session in your browser; the app cannot authenticate to it)', s.get('linkedin_top_applicant', True))
    s['use_hiringcafe'] = ask_bool('Search hiringcafe.com?', s.get('use_hiringcafe', True))

    print('\n-- Your real skills (never-invent list) --')
    skills['have'] = ask_list('Skills you can genuinely claim', skills.get('have', []))

    print('\n-- Phone alerts (optional) --')
    notify['service'] = 'ntfy'
    notify['enabled'] = ask_bool('Enable phone alerts through ntfy?', notify.get('enabled', True))
    if notify['enabled']:
        notify['topic'] = ask('  ntfy topic. Pick a long random string. Subscribe to it in the ntfy app', notify.get('topic', ''))

    print('\n-- Daily auto-commit identity (optional) --')
    git['name'] = ask('Git commit name', git.get('name') or ident.get('full_name', ''))
    git['email'] = ask('Git commit email', git.get('email', ''))
    git['remote'] = ask('Git remote URL (blank = do not push)', git.get('remote', ''))

    print('\n-- AI resume tailoring (optional) --')
    print('  Tailoring runs through Claude Code on your Claude plan. Nothing metered, no API key.')
    _report_claude_cli()

    pl.save_profile(prof)
    written = pl.regenerate_all(prof)
    print('\nSaved config/profile.json')
    print('Generated:\n  ' + '\n  '.join(written))
    print('\nDone. To edit these values, run `python src/ops/setup.py` again, or open the /setup page of the dashboard.')

if __name__ == '__main__':
    main()
