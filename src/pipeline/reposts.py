#!/usr/bin/env python3
"""reposts.py — deterministic repost detector. ADVISORY ONLY, zero Claude tokens.

A job board re-listing gets a FRESH id, so a repost of a role you already saw would
otherwise show up as a brand-new posting. This module matches a fresh listing/candidate
against the tracker's own history and, when it recognizes one, produces a short note:

    "Repost - last seen 2026-06-15; you applied 2026-06-20. Prior note: emailed recruiter."

It is ADVISORY: nothing in the pipeline skips, filters, or reclassifies a job because of
this. Reposts are NEVER excluded from search — the note just lets the user reuse an already
tailored resume or ban the employer (blocklist.py) if a listing that never seems to close
looks like a ghost job. Only the manual employer blocklist can actually skip jobs.

Identity keys, strongest first:
  1. normalized applyUrl (the real ATS URL — same greenhouse/workday job = same URL)
  2. normalized requisition id (reqId, when both sides carry one)
  3. employer + role-token match (company normalized via blocklist.norm_employer;
     role compared as an order-independent token set, like the applied-status reconcile)

A match only counts as a repost when the prior sighting is a DISTINCT tracker id whose
foundDate is at least `gap_days` older than the current row (so the same posting
re-scraped a few days apart, which keeps its id, is not mistaken for a re-listing).

Pure/no-network. `annotate()` is the single public entry point; both jobpipe's
`candidates` builder and the dashboard's /api/data call it.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os
import re
from datetime import datetime

from pipelib import CONFIG, cfg, camel_to_words, load_json
from blocklist import norm_employer

REPOST_CFG = cfg('reposts.json')

DEFAULTS = {
    'gap_days': 7,        # a prior sighting must be at least this many days older to count
    'enabled': True,
}

# Role-token normalization mirrors tracker.py's applied-status reconcile so that
# "Sr Site Reliability Engineer" and "Senior SRE - Houston" collapse to the same set.
_CANON_PHRASES = [('site reliability engineer', 'sre'), ('site reliability', 'sre')]
_CANON_WORDS = {'senior': 'sr', 'junior': 'jr', 'engineer': 'eng', 'engineering': 'eng',
                'infrastructure': 'infra', 'operations': 'ops', 'operation': 'ops',
                'developer': 'dev', 'administrator': 'admin', 'systems': 'sys',
                'system': 'sys', 'services': 'svc', 'service': 'svc',
                'technical': 'tech', 'ii': '2', 'iii': '3', 'iv': '4'}
_CANON_DROP = {'the', 'and', 'of', 'a', 'an', 'remote', 'us', 'usa', 'united',
               'states', 'inc', 'llc', 'corp', 'co'}


def load_cfg():
    cfg = dict(DEFAULTS)
    disk = load_json(REPOST_CFG, None)
    if isinstance(disk, dict):
        cfg.update({k: disk[k] for k in disk if k in DEFAULTS})
    return cfg


def _role_tokens(text):
    s = re.sub(r'[^a-z0-9]+', ' ', camel_to_words(text or '').lower())
    for a, b in _CANON_PHRASES:
        s = s.replace(a, b)
    return frozenset(_CANON_WORDS.get(w, w) for w in s.split() if w not in _CANON_DROP)


def norm_url(url):
    """Canonical apply key: scheme/query/fragment/trailing-slash stripped, lowercased."""
    u = str(url or '').strip().lower()
    if not u:
        return ''
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    u = u.split('?', 1)[0].split('#', 1)[0]
    return u.rstrip('/')


def _norm_req(req):
    r = re.sub(r'[^a-z0-9]+', '', str(req or '').lower())
    # ignore trivially short / obviously non-unique ids
    return r if len(r) >= 4 else ''


def _company_role(jid):
    """Recover ('Company', 'Role words') from a tracker id like 'Alkami_SrSRERelease'."""
    parts = str(jid).split('_', 1)
    company = camel_to_words(parts[0]) if parts else str(jid)
    role = camel_to_words(parts[1]) if len(parts) > 1 else ''
    return company, role


def _emp_match(a, b):
    if not a or not b:
        return False
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


def _role_match(a, b):
    """Order-independent token subset (single-token roles must match exactly, so
    'Analyst' does not swallow every 'Senior X Analyst')."""
    if not a or not b:
        return False
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    if len(small) == 1:
        return small == large
    return small <= large


def _parse_date(s):
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(str(s)[:10], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _status_of(jid, applied, skipped):
    if jid in (applied or {}):
        return 'applied', (applied or {}).get(jid, '')
    if jid in (skipped or set()):
        return 'skipped', ''
    return 'pending', ''


def _build_history(job_details, applied, skipped, notes):
    """List of prior sightings from job_details:
    {id, date, url, req, empKey, roleTokens, status, statusDate, note}."""
    hist = []
    for jid, d in (job_details or {}).items():
        d = d or {}
        company, role = _company_role(jid)
        status, sdate = _status_of(jid, applied, skipped)
        hist.append({
            'id': jid,
            'date': _parse_date(d.get('foundDate')),
            'url': norm_url(d.get('applyUrl')),
            'req': _norm_req(d.get('reqId')),
            'empKey': norm_employer(company),
            'roleTokens': _role_tokens(role),
            'status': status,
            'statusDate': sdate,
            'note': str((notes or {}).get(jid, '') or '').strip(),
        })
    return hist


def _short_note(prior):
    """Compact human note for a matched prior sighting."""
    last = prior['date'].strftime('%Y-%m-%d') if prior['date'] else 'earlier'
    if prior['status'] == 'applied':
        phrase = 'you applied' + (' ' + prior['statusDate'] if prior['statusDate'] else '')
    elif prior['status'] == 'skipped':
        phrase = 'you skipped it'
    else:
        phrase = 'not yet actioned'
    note = 'Repost - last seen %s; %s.' % (last, phrase)
    pnote = prior['note']
    if pnote:
        if len(pnote) > 70:
            pnote = pnote[:67].rstrip() + '...'
        note += ' Prior note: ' + pnote
    return note


def annotate(rows, job_details=None, applied=None, skipped=None, notes=None,
             cfg=None, today=None):
    """{ id: {'note', 'lastSeen', 'priorStatus', 'priorId'} } for rows that look reposted.

    `rows`   - current listings/candidates/tracking rows (need id, company, role,
               applyUrl; foundDate + reqId optional).
    `job_details` / `applied` / `skipped` / `notes` - the tracker's history + user state.

    Advisory: an empty dict is returned when disabled, so callers need no flag of their own.
    """
    cfg = cfg or load_cfg()
    if not cfg.get('enabled', True):
        return {}
    gap = cfg.get('gap_days', 7)
    today = today or datetime.now()
    skipped = set(skipped or [])
    hist = _build_history(job_details, applied, skipped, notes)

    out = {}
    for r in rows:
        rid = r.get('id') or r.get('trackerId')
        if not rid:
            continue
        r_url = norm_url(r.get('applyUrl'))
        r_req = _norm_req(r.get('reqId'))
        r_emp = norm_employer(r.get('company') or _company_role(rid)[0])
        r_role = _role_tokens(r.get('role') or _company_role(rid)[1])
        r_date = _parse_date(r.get('foundDate')) or today

        best = None
        for h in hist:
            if h['id'] == rid:
                continue  # same posting, not a re-listing
            if h['url'] and r_url and h['url'] == r_url:
                strong = True
            elif h['req'] and r_req and h['req'] == r_req:
                strong = True
            elif _emp_match(h['empKey'], r_emp) and _role_match(h['roleTokens'], r_role):
                strong = False  # fuzzy company+role match
            else:
                continue
            # require a real time gap so a same-week re-scrape under a new id (or a
            # candidate row vs its own job_details entry) is not called a repost
            if h['date'] is not None and (r_date - h['date']).days < gap and not strong:
                continue
            if best is None or (h['date'] and best['date'] and h['date'] > best['date']) \
                    or (h['date'] and not best['date']):
                best = h
        if best is not None:
            out[rid] = {'note': _short_note(best), 'priorId': best['id'],
                        'priorStatus': best['status'],
                        'lastSeen': best['date'].strftime('%Y-%m-%d') if best['date'] else ''}
    return out


CAVEAT = ('Recognized from a prior sighting in your tracker - not excluded from search. '
          'Reuse the tailored resume, or ban the employer if it looks like a ghost job.')
