#!/usr/bin/env python3
"""Low-token phone push for new high-match jobs via ntfy.sh (no account, no API key).

Runs at the SCRIPT level (pure stdlib HTTP) so it costs zero Claude tokens and works
even when Claude is closed. Intended to be called right after `jobpipe.py candidates`.

Modes:
  python src/pipeline/notify.py            # send ONE push listing new matches not seen before
  python src/pipeline/notify.py --seed     # mark ALL current candidates as already-notified (no push) — run once at setup
  python src/pipeline/notify.py --test     # send a test push to confirm the phone is subscribed

Config: config/notify.json  -> {"service":"ntfy","topic":"...","enabled":true}
State:  notified.json        -> the job ids already sent (you get no second alert for them)
Match rule: skillMatch >= strategy.min_match, or >= strategy.local_min_match for Houston-metro
(location matches strategy.local_keywords). Applied jobs are skipped.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, csv, json, urllib.request

HERE = _paths.ROOT
# All of these are per-user (ntfy topic, GUI prefs, candidate pool) -> pipelib DATA root,
# It falls back to HERE when the data repo is not separate.
from pipelib import (cfg, INSTANCE_JSON, CANDIDATES_CSV as CANDS, NOTIFIED,
                     env_truthy as _env_truthy)
CONFIG = cfg('notify.json')
STRATEGY = cfg('strategy.json')
GUI = cfg('gui_settings.json')
INSTANCE = INSTANCE_JSON          # git-ignored per-instance overrides (stays with the CODE)


def load(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return d


def notifications_disabled():
    """Instance-local kill switch for ALL ntfy pushes — used to neutralize a staging copy
    without touching the profile's own notify.enabled (which regenerates from profile.json).
    Sources (either wins): env TOP_RAT_DISABLE_NOTIFY, or config/instance.json
    {"disable_notifications": true}. instance.json is git-ignored so it never leaks to the
    live copy on a merge."""
    if _env_truthy('DISABLE_NOTIFY'):
        return True
    return bool((load(INSTANCE, {}) or {}).get('disable_notifications'))


def save_notified(ids):
    tmp = NOTIFIED + '.tmp'
    data = json.dumps(sorted(ids), indent=0)
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, NOTIFIED)


def push(topic, title, body, click=None, priority='high', tags='briefcase'):
    url = 'https://ntfy.sh/' + topic
    # ntfy headers must be latin-1 safe; strip anything exotic from title/click
    headers = {'Title': title.encode('ascii', 'ignore').decode('ascii'),
               'Priority': priority, 'Tags': tags}
    if click:
        headers['Click'] = click
    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def main():
    if notifications_disabled():
        print('notify: STOPPED by the instance kill switch '
              '(config/instance.json disable_notifications or TOP_RAT_DISABLE_NOTIFY). No alert was sent.')
        return
    cfg = load(CONFIG, {})
    topic = cfg.get('topic')
    if not cfg.get('enabled', True) or not topic:
        print('notify: disabled, or no topic is configured')
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ''

    if mode == '--test':
        push(topic, 'Job radar test',
             'You can see this, so the live job alerts work. Apply early. Aim to be in the first 50.',
             priority='high')
        print('notify: test alert sent to topic', topic)
        return

    strat = load(STRATEGY, {})
    gui = load(GUI, {}) or {}
    # Alert floors mirror the dashboard sliders (gui_settings.json), falling back to
    # strategy.json: remote jobs use remoteFloor, Houston-metro use localFloor.
    remote_min = float(gui.get('remoteFloor', strat.get('remote_min_match', strat.get('min_match', 0.3))))
    local_min = float(gui.get('localFloor', strat.get('local_min_match', 0.2)))
    local_kw = [k.lower() for k in strat.get('local_keywords', [])]
    # Geo-aware locality (locality.py: keyword pass + geo_cache radius check).
    # Falls back to the inline keyword match if the module is unavailable.
    try:
        import locality
        _is_local = locality.is_local
    except Exception:
        _is_local = lambda loc: any(k in (loc or '').lower() for k in local_kw)
    notified = set(load(NOTIFIED, []))

    try:
        with open(CANDS, encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        print('notify: the app cannot read candidates.csv:', e)
        return

    if mode == '--seed':
        for r in rows:
            if r.get('id'):
                notified.add(r['id'])
        save_notified(notified)
        print('notify: seeded', len(notified), 'ids as already-notified (no push sent)')
        return

    matches = []
    for r in rows:
        jid = r.get('id') or ''
        if not jid or jid in notified:
            continue
        if (r.get('status') or '').lower() in ('applied', 'skipped'):
            continue
        try:
            score = float(r.get('skillMatch') or 0)
        except ValueError:
            score = 0.0
        is_local = _is_local(r.get('location'))
        thr = local_min if is_local else remote_min
        if score >= thr:
            matches.append((is_local, score, r))

    if not matches:
        print('notify: no new matches to send')
        return

    matches.sort(key=lambda m: (not m[0], -m[1]))  # local first, then score desc
    CAP = 25  # keep the push under ntfy's message-size limit; note any overflow
    lines = []
    for is_local, score, r in matches[:CAP]:
        tag = 'LOCAL ' if is_local else ''
        # Flag a re-listing so you can reuse an already tailored resume (advisory only).
        rp = ' [repost]' if (r.get('repost') or '').strip().lower() == 'yes' else ''
        lines.append('%s%s - %s (%.0f%%)%s\n%s' % (
            tag, r.get('role', '?'), r.get('company', '?'), score * 100, rp, r.get('applyUrl', '')))
    if len(matches) > CAP:
        lines.append('(+%d more. Open the ntfy app or the dashboard to see them all.)' % (len(matches) - CAP))
    body = ('%d new match%s. Apply early, and aim to be in the first 50:\n\n' % (
        len(matches), '' if len(matches) == 1 else 'es')) + '\n\n'.join(lines)
    # No single-job Click: tapping the notification opens the full list (every job's
    # apply link is tappable in the ntfy app) instead of jumping to only the first job.
    push(topic, 'New job match (%d)' % len(matches), body)
    for _, _, r in matches:
        notified.add(r['id'])
    save_notified(notified)
    print('notify: pushed', len(matches), 'matches; recorded as notified')


if __name__ == '__main__':
    import runlog; runlog.run('notify.py', main)
