#!/usr/bin/env python3
"""ghostflags.py — the USER's ghost-job flags (deterministic, zero tokens).

Two different things wear the ghost hat in this pipeline, and they must not be mixed:

  * `ghost_risk.py` GUESSES. It scores every job off heuristics (salary width, reposts,
    staleness) and is advisory — nothing downstream reads it.
  * this module RECORDS. The user pressed 👻 on a specific posting because they believe
    it's a ghost job. That's evidence, not a guess, so it is stored, counted per
    employer, and used to offer a ban once a second posting from the same employer is
    flagged (the blocklist is the deterministic half of fake-job handling).

Flagging deliberately does NOT change a job's status, queue state or filtering — the user
decides what to do with a flagged job himself. The only behaviour it drives is the
count and the ban offer.

Storage: config/ghost_flags.json
    {"version": 1,
     "employers": {
        "<normkey>": {"display": "Felix Pago",
                      "banPrompt": "" | "declined",
                      "jobs": {"<job id>": {"role": "...", "flagged": "YYYY-MM-DD"}}}}}

Employer keys come from `blocklist.norm_employer`, so a flag counts against the same
identity a ban would use ("Felix", "Felix, Inc." and "felix technologies" collapse).
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import json
from datetime import datetime

from pipelib import cfg, cfg_write, load_json, _safe_write_text
from blocklist import norm_employer

FLAGS_NAME = 'ghost_flags.json'


def flags_path():
    # Resolved PER CALL, not at import: the file usually doesn't exist yet on first run, and a
    # module-level cfg() would then pin the code-dir path for the life of the dashboard process
    # and never see the copy the first write puts in the data root.
    return cfg(FLAGS_NAME)


def load_flags():
    d = load_json(flags_path(), None) or {'version': 1, 'employers': {}}
    d.setdefault('employers', {})
    return d


def _save(d):
    # cfg_write resolves the WRITE copy (always the data root when one is set); flags_path()
    # above resolves the READ copy, which that write then shadows.
    _safe_write_text(cfg_write(FLAGS_NAME), json.dumps(d, indent=2), verify_json=True)


def flagged_ids():
    """Set of every job id the user has flagged — one pass for the whole board."""
    out = set()
    for rec in load_flags()['employers'].values():
        out.update((rec.get('jobs') or {}).keys())
    return out


def employer_counts():
    """{normkey: number of flagged postings} — drives the card tag and the ban offer."""
    return {k: len(v.get('jobs') or {}) for k, v in load_flags()['employers'].items()}


def declined_employers():
    """Employers whose ban offer the user explicitly turned down — never ask again."""
    return {k for k, v in load_flags()['employers'].items() if v.get('banPrompt') == 'declined'}


def is_flagged(job_id):
    return bool(job_id) and job_id in flagged_ids()


def flag_count(company):
    """How many postings from this employer are flagged."""
    if not company:
        return 0
    rec = load_flags()['employers'].get(norm_employer(company)) or {}
    return len(rec.get('jobs') or {})


def set_flag(job_id, company, role='', flagged=True):
    """Flag / unflag one posting. Returns {flagged, count, key, declined, display}.

    Unflagging drops the employer entry once its last posting is cleared — unless the
    user declined a ban for them, which is a decision worth keeping.
    """
    job_id = (job_id or '').strip()
    if not job_id:
        raise ValueError('job id required')
    if not (company or '').strip():
        raise ValueError('company required')
    d = load_flags()
    key = norm_employer(company)
    rec = d['employers'].get(key) or {'display': company.strip(), 'banPrompt': '', 'jobs': {}}
    rec.setdefault('jobs', {})
    rec.setdefault('banPrompt', '')
    rec['display'] = rec.get('display') or company.strip()
    if flagged:
        rec['jobs'][job_id] = {'role': (role or '').strip(),
                               'flagged': datetime.now().strftime('%Y-%m-%d')}
    else:
        rec['jobs'].pop(job_id, None)
    if rec['jobs'] or rec.get('banPrompt'):
        d['employers'][key] = rec
    else:
        d['employers'].pop(key, None)
    _save(d)
    return {'flagged': bool(flagged), 'count': len(rec['jobs']), 'key': key,
            'declined': rec.get('banPrompt') == 'declined', 'display': rec['display']}


def decline_ban(company):
    """Remember that the user said no to banning this employer, so we stop offering."""
    if not (company or '').strip():
        return None
    d = load_flags()
    key = norm_employer(company)
    rec = d['employers'].get(key) or {'display': company.strip(), 'jobs': {}}
    rec['banPrompt'] = 'declined'
    rec.setdefault('jobs', {})
    d['employers'][key] = rec
    _save(d)
    return rec


def clear_employer(company):
    """Drop every flag for an employer — used when they get banned (the ban supersedes)."""
    if not (company or '').strip():
        return None
    d = load_flags()
    rec = d['employers'].pop(norm_employer(company), None)
    if rec is not None:
        _save(d)
    return rec


if __name__ == '__main__':                     # tiny CLI: `python src/pipeline/ghostflags.py`
    d = load_flags()
    if not d['employers']:
        print('No ghost flags recorded.')
    for k, v in sorted(d['employers'].items(), key=lambda kv: kv[1].get('display', kv[0]).lower()):
        jobs = v.get('jobs') or {}
        print('%-28s %d flagged%s' % (v.get('display', k), len(jobs),
                                      '  (ban declined)' if v.get('banPrompt') == 'declined' else ''))
        for jid, meta in jobs.items():
            print('    %-40s %s  %s' % (jid, meta.get('flagged', ''), meta.get('role', '')))
