#!/usr/bin/env python3
"""watchdog_launchd.py - the macOS half of "Keep it running".

  python src/ops/watchdog_launchd.py install [--every 5]   # write and load the LaunchAgent
  python src/ops/watchdog_launchd.py uninstall             # unload and delete it
  python src/ops/watchdog_launchd.py status                # print whether it is loaded
  python src/ops/watchdog_launchd.py plist                 # print the plist, write nothing

macOS has no Task Scheduler, so the switch on the Setup page had nothing to call and the
whole feature was dead on a Mac while the page still offered it. This module is the
launchd twin of make_watchdog.py: same five functions, same (ok, message) returns, so
make_watchdog.py can hand off to it and no caller changes.

  Windows                          macOS
  -------                          -----
  schtasks /create /xml            launchctl bootstrap gui/<uid> <plist>
  schtasks /delete /f              launchctl bootout gui/<uid>/<label>
  schtasks /query                  launchctl print   gui/<uid>/<label>
  schtasks /run                    launchctl kickstart gui/<uid>/<label>
  LogonTrigger                     RunAtLoad
  Repetition Interval PT5M         StartInterval 300

What it registers: a LaunchAgent named com.toprat.watchdog that runs watchdog.py at login
and then every N minutes. watchdog.py is already portable - it probes the port with a
plain socket, launches the server with start_new_session on POSIX, and falls back to
webbrowser.open() where `start /min` does not exist - so nothing under it had to change.

A LaunchAgent, not a LaunchDaemon, and deliberately so. Agents live in the user's own
~/Library/LaunchAgents, need no administrator rights, install nothing system-wide, and run
INSIDE the logged-in GUI session - which is the only place a browser window can exist.
The same trade as the Windows InteractiveToken choice: it does not run while you are
logged out, and it cannot wake a sleeping Mac.

KeepAlive is deliberately absent. watchdog.py is a one-shot check that exits in a second,
so KeepAlive would read every clean exit as a crash and respawn it in a tight loop.
StartInterval is the correct primitive: run it, let it exit, run it again in N minutes.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, plistlib, subprocess, sys

HERE = _paths.ROOT
WATCHDOG = _paths.script('watchdog.py')
LABEL = 'com.toprat.watchdog'
PLIST = os.path.expanduser('~/Library/LaunchAgents/' + LABEL + '.plist')

# Labels this agent has had before, removed on install and on uninstall. Empty today:
# the Job Agent -> Top Rat rename happened before any Mac copy shipped a watchdog, so
# there is nothing in the wild under an old name. The list stays because the hazard is
# real - a loaded agent lives in launchd, not in this repo, so renaming LABEL alone would
# leave the old one restarting the board forever while the switch reported "off".
LEGACY_LABELS = []
DEFAULT_EVERY_MIN = 5
MIN_EVERY, MAX_EVERY = 1, 1440


def supported():
    """macOS with launchctl on PATH."""
    return sys.platform == 'darwin' and bool(_launchctl())


def _launchctl():
    for p in ('/bin/launchctl', '/usr/bin/launchctl'):
        if os.path.exists(p):
            return p
    from shutil import which
    return which('launchctl')


def _run(args):
    """Run launchctl. Returns (returncode, combined output)."""
    exe = _launchctl()
    if not exe:
        return 1, 'launchctl was not found'
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, str(e)


def _domain():
    """The GUI domain of the current user, e.g. gui/501.

    os.getuid() and not $UID: a process started by launchd itself does not always carry
    $UID in its environment, and this module is imported by the dashboard, which the
    agent may well have started.
    """
    return 'gui/%d' % os.getuid()


def _loaded(label=LABEL):
    """True when launchd currently holds the agent.

    Two probes because the verb changed. `launchctl print` is the modern one and gives a
    real answer per domain; `launchctl list` is the 10.10-era fallback that still works
    everywhere. Either one succeeding is enough.
    """
    if _run(['print', _domain() + '/' + label])[0] == 0:
        return True
    return _run(['list', label])[0] == 0


def is_enabled():
    """True when the plist exists AND launchd has it loaded.

    Both halves matter. A plist on disk that launchd never loaded restarts nothing, and
    reporting that as "on" would leave the user believing in a watchdog that does not
    run. install() is idempotent, so the dashboard's reconcile step simply repairs the
    half-installed case on the next start.
    """
    if sys.platform != 'darwin':
        return False
    if os.path.exists(PLIST) and _loaded():
        return True
    return any(os.path.exists(_legacy_plist(n)) and _loaded(n) for n in LEGACY_LABELS)


def _legacy_plist(label):
    return os.path.expanduser('~/Library/LaunchAgents/' + label + '.plist')


def remove_legacy_agents():
    """Unload and delete any pre-rename agent. Returns the labels actually removed."""
    removed = []
    if sys.platform != 'darwin':
        return removed
    for name in LEGACY_LABELS:
        path = _legacy_plist(name)
        if not (os.path.exists(path) or _loaded(name)):
            continue
        _unload(name, path)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        removed.append(name)
    return removed


def _unload(label, path):
    """Take the agent out of launchd. Best effort, both verbs, never raises.

    Not loaded is not a failure here - every caller wants "make sure it is gone", and on
    that path launchctl returns non-zero for the entirely normal reason that there was
    nothing to remove.
    """
    rc, _ = _run(['bootout', _domain() + '/' + label])
    if rc != 0 and os.path.exists(path):
        _run(['unload', '-w', path])


def plist_dict(every_min=DEFAULT_EVERY_MIN):
    """The agent definition.

    RunAtLoad covers "I just logged in" with no delay. StartInterval covers "I closed the
    board / it crashed" for the rest of the session - the same two cases the Windows
    LogonTrigger and its Repetition cover.

    Choices worth keeping:
      ProgramArguments uses ABSOLUTE paths. launchd starts the job with a minimal
        environment and no useful working directory, so a bare "python3" would resolve
        against a PATH this process cannot see.
      WorkingDirectory = the project folder, because watchdog.py reads config/ relative
        to it.
      StandardErrorPath into logs/, so a failing agent leaves evidence. launchd keeps no
        run history of its own, which is the one thing Task Scheduler gives us for free.
      ProcessType Background - tells macOS this job may be throttled under load. It is a
        port check every few minutes; it must never compete with what the user is doing.
      LimitLoadToSessionType Aqua - load only in a GUI login session. The job opens a
        browser window, so there is no point running it in an ssh or pre-login context.
    """
    every_min = max(MIN_EVERY, min(MAX_EVERY, int(every_min)))
    return {
        'Label': LABEL,
        'ProgramArguments': [_python_for_agent(), WATCHDOG],
        'WorkingDirectory': HERE,
        'RunAtLoad': True,
        'StartInterval': every_min * 60,
        'ProcessType': 'Background',
        'LimitLoadToSessionType': 'Aqua',
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': os.path.join(HERE, 'logs', 'watchdog_launchd.err'),
    }


def _python_for_agent():
    """The interpreter to bake into the plist, as an absolute path.

    sys.executable is the right answer and the bundled python/ copy is the reason: a
    shipped folder must keep using ITS own interpreter, not whatever the Mac happens to
    have. The fallback only matters for an embedded interpreter that reports no
    executable, which this app never runs under.
    """
    exe = sys.executable
    if exe and os.path.isabs(exe):
        return exe
    from shutil import which
    return which('python3') or which('python') or '/usr/bin/python3'


def plist_xml(every_min=DEFAULT_EVERY_MIN):
    """The plist as text, for `plist` on the command line and for tests."""
    return plistlib.dumps(plist_dict(every_min)).decode('utf-8')


def install(every_min=DEFAULT_EVERY_MIN, retire_vbs=True):
    """Write the plist and load it. Returns (ok, message).

    Idempotent by construction: unload first, rewrite, load. Bootstrapping a label that
    launchd already holds is an error, and quietly leaving the OLD definition in place
    after the user changed the interval is worse than an error, because nothing tells
    them the new number did not take.

    retire_vbs is accepted and ignored. It exists so make_watchdog.py can call both
    backends with one signature; the .vbs it names is a Windows Startup-folder file that
    cannot exist on a Mac.
    """
    if sys.platform != 'darwin':
        return False, 'The launchd watchdog needs macOS. This is not a Mac.'
    if not supported():
        return False, 'launchctl was not found, so the watchdog cannot be registered.'
    if not os.path.exists(WATCHDOG):
        return False, 'watchdog.py is missing from ' + HERE

    every_min = max(MIN_EVERY, min(MAX_EVERY, int(every_min)))
    try:
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
        _unload(LABEL, PLIST)
        with open(PLIST, 'wb') as fh:
            plistlib.dump(plist_dict(every_min), fh)
    except Exception as e:
        return False, 'Could not write the LaunchAgent: ' + str(e)

    rc, out = _run(['bootstrap', _domain(), PLIST])
    if rc != 0:
        # bootstrap arrived in OS X 10.11. Older systems, and a few odd states on newer
        # ones, still answer to load -w. Only when BOTH refuse is this a real failure.
        rc2, out2 = _run(['load', '-w', PLIST])
        if rc2 != 0 and not _loaded():
            try: os.remove(PLIST)      # do not leave a plist that nothing loads
            except OSError: pass
            return False, 'Could not load the LaunchAgent: ' + (out2 or out or 'launchctl failed')

    retired = remove_legacy_agents()
    msg = 'Watchdog enabled. "%s" checks every %d minute%s.' % (
        LABEL, every_min, '' if every_min == 1 else 's')
    if retired:
        msg += ' Removed the old agent%s (%s), so only one watchdog runs.' % (
            '' if len(retired) == 1 else 's', ', '.join(retired))
    return True, msg


def uninstall():
    """Unload and delete the agent. Returns (ok, message). Missing agent = success."""
    if sys.platform != 'darwin':
        return True, 'Nothing to remove (not a Mac).'
    retired = remove_legacy_agents()
    if not (os.path.exists(PLIST) or _loaded()):
        if not retired:
            return True, 'The watchdog was not enabled (nothing to remove).'
    else:
        _unload(LABEL, PLIST)
        try:
            if os.path.exists(PLIST):
                os.remove(PLIST)
        except OSError as e:
            return False, 'Could not remove the LaunchAgent file: ' + str(e)
        if _loaded():
            return False, 'launchd still holds ' + LABEL + '. Log out and back in to clear it.'
    note = (' Also removed the old agent%s (%s).' % ('' if len(retired) == 1 else 's',
                                                     ', '.join(retired))) if retired else ''
    return True, ('Watchdog disabled (LaunchAgent removed). The dashboard is not '
                  'restarted for you any more.' + note)


def run_now():
    """Run the check immediately, without waiting for the interval. Returns (ok, message)."""
    if not is_enabled():
        return False, 'The watchdog is not enabled.'
    rc, out = _run(['kickstart', _domain() + '/' + LABEL])
    if rc != 0:
        rc, out = _run(['start', LABEL])      # pre-10.11 verb
    return (rc == 0), (out or ('Started ' + LABEL if rc == 0 else 'launchctl failed'))


def _every_from_argv(argv):
    for i, a in enumerate(argv):
        if a == '--every' and i + 1 < len(argv):
            try: return int(argv[i + 1])
            except ValueError: pass
        if a.startswith('--every='):
            try: return int(a.split('=', 1)[1])
            except ValueError: pass
    return DEFAULT_EVERY_MIN


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'install'
    if cmd == 'status':
        print(LABEL + ' is ' + ('LOADED' if is_enabled() else 'NOT loaded')
              + (' (supported)' if supported() else ' (NOT supported on this OS)'))
    elif cmd in ('plist', 'xml'):
        # 'xml' is an alias because make_watchdog.py accepts BOTH verbs for its dry run.
        # Before this, 'xml' missed every branch and fell into the else, so the verb that
        # PRINTS on one module INSTALLED AND LOADED the LaunchAgent on the other
        # (QA 1.2.0). An unknown verb must never be a write.
        print(plist_xml(_every_from_argv(sys.argv)))
    elif cmd == 'run':
        print(run_now()[1])
    elif cmd == 'uninstall':
        print(uninstall()[1])
    elif cmd == 'install':
        print(install(_every_from_argv(sys.argv))[1])
    else:
        sys.exit('unknown command %r. Use: install | uninstall | status | plist | run'
                 % cmd)
