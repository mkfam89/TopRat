#!/usr/bin/env python3
"""git_daily.py - commit the code + tracking metadata to a per-day branch and push.

Creates/updates a branch named  work/YYYY-MM-DD  each day, commits everything (minus the
binaries excluded by .gitignore), and pushes to origin. Merge branches into master yourself
when you're happy with them.

Run:  python src/ops/git_daily.py        (or double-click "tools\Git Daily Commit.bat")
First run sets up the repo + remote automatically.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, subprocess, datetime, time, csv

HERE = _paths.ROOT
# Remote + identity load from git-ignored config/git.json (template committed as
# config/git.json.example), so a shared or handed-off copy carries no account details.
#
# There are deliberately NO hardcoded defaults any more. This file used to fall back to
# the first user's real name, email and GitHub remote, so a handed-off copy with no
# git.json committed as him and tried to push to his repository. Everything now resolves
# git.json -> profile.json `git` block -> EMPTY, and every consumer treats empty as
# "not configured" and skips that step (see main()):
#   no remote -> no origin is added and nothing is pushed (profile.example already
#                documents a blank remote as "leave blank to skip pushing")
#   no name/email -> git config is left alone, so the user's own global identity is
#                used, which is what git does for every other repo on their machine
# The commit itself still happens with no configuration at all — the zero-token script
# path keeps working, it just stays local.


def _profile_git():
    """NAME + EMAIL only, from the `git` block of config/profile.json.

    Covers the narrow case of a profile that was filled in but never regenerated into
    config/git.json. It is the same source profile_lib regenerates git.json from, so it
    is not a competing source of truth.

    **`remote` is deliberately dropped here.** profile.json is resolved through the DATA
    root, and `<data>/config/profile.json` describes the DATA repo — on this very install
    its git.remote is `...job_agent_profile_KP.git`. Honouring it would add an origin
    pointing at the profile repo and push the CODE into it, which is exactly the hazard
    git_identity()'s docstring exists to prevent. A remote comes from the code-side
    config/git.json or from nowhere."""
    try:
        import profile_lib as pl
        g = (pl.load_profile(clean=True) or {}).get('git') or {}
        return {k: v for k, v in g.items() if k in ('name', 'email', 'user_name', 'user_email')}
    except Exception:
        return {}


def git_identity():
    """(remote, user_name, user_email) from the CODE-side config/git.json.

    **Deliberately NOT read through pipelib.cfg().** cfg() overlays the data root
    on top of the code dir, and once the data split is on, `<data>/config/git.json`
    describes the DATA repo (job_agent_profile_KP) — a different repository. This
    script commits the CODE, so taking the overlay would add an origin pointing at
    the profile repo and push source into it. Read beside the code, full stop.

    Key names accept both spellings: setup.py writes `name`/`email`, while
    config/git.json.example documented `user_name`/`user_email`. Both are honoured
    so neither an old nor a new file is silently ignored.

    Never raises: a missing or malformed file just falls through to the profile block
    and then to empty strings, which main() reads as "not configured".
    """
    conf = {}
    try:
        from pipelib import CODE_CONFIG, _read_json_quiet
        conf = _read_json_quiet(os.path.join(CODE_CONFIG, 'git.json')) or {}
    except Exception:
        pass
    prof = _profile_git()

    def pick(*keys):
        for src in (conf, prof):
            for k in keys:
                v = str(src.get(k) or '').strip()
                if v:
                    return v
        return ''

    return pick('remote'), pick('name', 'user_name'), pick('email', 'user_email')

def job_counts():
    """Minimal job-count tag, applied|pending|skipped order, e.g. 'a|p|s 13|44|3'."""
    try:
        from pipelib import TRACKING_CSV
        rows = list(csv.DictReader(open(TRACKING_CSV, encoding='utf-8')))
        applied = sum(1 for r in rows if r.get('status') == 'applied')
        skipped = sum(1 for r in rows if r.get('status') == 'skipped')
        pending = len(rows) - applied - skipped
        return 'a|p|s %d|%d|%d' % (applied, pending, skipped)
    except Exception:
        return 'a|p|s ?|?|?'

def commit_message(summary=None):
    """Commit subject = <what was worked on this run> + a minimal [a|p|s applied|pending|skipped]
    job count. Falls back to a plain 'Daily checkpoint' label when no summary is supplied."""
    desc = summary.strip() if summary and summary.strip() else 'Daily checkpoint'
    return '%s  [%s]' % (desc, job_counts())

class _Fail:
    returncode = 124; stdout = ''; stderr = 'timeout'

def git(*args, check=False, timeout=120):
    # GIT_TERMINAL_PROMPT=0 so a headless/scheduled run never HANGS waiting for credentials —
    # if auth isn't cached, the push fails fast instead of blocking. A stalled push is bounded by timeout.
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:
        r = subprocess.run(['git', '-C', HERE] + list(args), capture_output=True, text=True,
                           env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        print('git ' + ' '.join(args) + ' timed out after ' + str(timeout) + 's.')
        return _Fail()
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip())
    if check and r.returncode != 0:
        sys.exit('git ' + ' '.join(args) + ' failed.')
    return r

AUTH_MARKERS = ('authentication failed', 'permission denied', 'could not read username',
                'could not read password', 'invalid username or password', 'publickey',
                'terminal prompts disabled', 'repository not found')

def push_with_retry(branch, budget_sec=600, interval=30):
    """Push, retrying transient failures for up to 10 minutes. Fail fast (no retry) on auth errors."""
    start = time.time(); attempt = 0
    while True:
        attempt += 1
        r = git('push', '-u', 'origin', branch)
        if r.returncode == 0:
            print('Pushed to origin on branch', branch); return True
        combined = ((r.stdout or '') + (r.stderr or '')).lower()
        if any(m in combined for m in AUTH_MARKERS):
            print('Push failed on authentication/permissions — not a transient issue, so not retrying.')
            print(r'Run "tools\Git Daily Commit.bat" once manually to cache your GitHub credentials.')
            return False
        elapsed = time.time() - start
        if elapsed >= budget_sec:
            print('Push still failing after ~%d min / %d attempts — giving up on the push for now.'
                  % (round(elapsed / 60), attempt))
            return False
        wait = int(min(interval, budget_sec - elapsed))
        print('Push failed (attempt %d, network/other) — retrying in %ds (up to 10 min total)...' % (attempt, wait))
        time.sleep(wait)

def main():
    if subprocess.run(['git', '--version'], capture_output=True).returncode != 0:
        sys.exit('Git is not installed / not on PATH. Install Git for Windows first: https://git-scm.com/download/win')
    if not os.path.isdir(os.path.join(HERE, '.git')):
        print('Initializing repository...')
        git('init'); git('branch', '-M', 'master')
    remote, name, email = git_identity()
    # remote — only if one is configured. An unconfigured copy keeps its commits local
    # rather than being wired to somebody else's repository.
    has_origin = git('remote', 'get-url', 'origin').returncode == 0
    if not has_origin and remote:
        git('remote', 'add', 'origin', remote)
        has_origin = True
    # identity (only if unset here AND configured; otherwise the machine's global
    # git identity applies, which is the right default for an unconfigured copy)
    if email and not git('config', 'user.email').stdout.strip():
        git('config', 'user.email', email)
    if name and not git('config', 'user.name').stdout.strip():
        git('config', 'user.name', name)
    branch = 'work/' + datetime.date.today().isoformat()
    git('checkout', '-B', branch)
    git('add', '-A')
    if not git('status', '--porcelain').stdout.strip():
        print('Nothing changed since the last commit on', branch);
    else:
        # Pass a short note describing the FEATURES/BUGS worked on this run, e.g.:
        #   python src/ops/git_daily.py "Hardened tracker writes; fixed applied-mark loss"
        # The minimal [a|p|s N|N|N] job count (applied|pending|skipped) is appended automatically.
        custom = sys.argv[1] if (len(sys.argv) > 1 and not sys.argv[1].startswith('-')) else None
        msg = commit_message(custom)
        print('Commit:', msg)
        git('commit', '-m', msg)
    if has_origin:
        print('Pushing', branch, '(will retry transient failures up to 10 min)...')
        pushed = push_with_retry(branch)
    else:
        # No remote configured. Deliberately falls through to the SAME local-snapshot
        # path as a failed push rather than returning early: with no remote there is no
        # offsite copy at all, so the local recovery point matters more here, not less.
        print('No git remote configured — set one in Settings (or config/git.json) to push.')
        pushed = False
    if pushed:
        print('Open a PR / merge into master on GitHub when ready.')
    else:
        # Push didn't happen. The commit is safe locally; make a local backup snapshot as a
        # fallback recovery point, then let the caller proceed with the rest of the run.
        print('\nNot pushed. The commit IS saved locally on branch ' + branch + '.')
        print('Making a local backup snapshot instead so there is still a recovery point...')
        bp = _paths.script('backup.py')
        if os.path.exists(bp):
            try:
                subprocess.run([sys.executable, bp], timeout=120)
            except Exception as e:
                print('(local backup snapshot failed: %s)' % e)
        print('Continuing — the pipeline should proceed. This branch will push on a later run.')

if __name__ == '__main__':
    import runlog; runlog.run('git_daily.py', main)
