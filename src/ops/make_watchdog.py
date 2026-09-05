#!/usr/bin/env python3
"""make_watchdog.py - enable/disable the "Keep it running" watchdog for the dashboard.

  python src/ops/make_watchdog.py install [--every 5]   # register the scheduled task
  python src/ops/make_watchdog.py uninstall             # remove it
  python src/ops/make_watchdog.py status                # print whether the task exists
  python src/ops/make_watchdog.py xml                   # print the task XML, register nothing

Registers a task named "TopRatWatchdog" that runs `watchdog.py` at logon and then every
N minutes for as long as you are logged on. watchdog.py starts the dashboard when it is not
running (see that file). Same shape as make_autostart.py on purpose - the dashboard imports
this module and calls supported() / is_enabled() / install() / uninstall(), and the install
and uninstall functions return (ok, message).

Why Task Scheduler instead of the old Startup-folder .vbs:
  - the .vbs fires ONLY at logon, so closing the dashboard (or a crash) left nothing running
    until the next logon. The task re-checks every N minutes.
  - Task Scheduler keeps run history and exit codes, so a failure is diagnosable.
  - it owns retry-on-failure and "run as soon as possible after a missed start" already.

Deliberately INTERACTIVE (LogonType InteractiveToken, no stored password): the watchdog opens
a browser window, which only exists inside your logged-on desktop session. That means it does
NOT run while you are logged out, and it cannot wake a sleeping machine. Covering those needs
a second, browserless task that runs the scrapers directly - a different feature.

Creating a task for your own account in the root folder needs no administrator rights.

macOS: this module is a FACADE. Everything below is the Windows implementation; on a Mac
every public function forwards to watchdog_launchd.py, which does the same job with a
launchd LaunchAgent. Callers - the dashboard's toggle, Install Watchdog.bat - see one
module with one contract and never branch on the platform themselves.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, subprocess, sys, tempfile

HERE = _paths.ROOT
WATCHDOG = _paths.script('watchdog.py')
TASK_NAME = 'TopRatWatchdog'

# Names this task has had before. The app was renamed (Job Agent -> Top Rat) AFTER
# copies were already running with the task registered, and a scheduled task is
# state that lives in Windows, not in this repo — changing the constant does not
# unregister anything. An orphan is the quiet kind of broken: the old task keeps
# starting the dashboard forever, so the user gets two watchdogs, and the app's
# own toggle (which only knows the new name) reports "off" while something still
# restarts the board. Every entry here is removed on install and on uninstall.
LEGACY_TASK_NAMES = ['JobDashboardWatchdog']
DEFAULT_EVERY_MIN = 5
MIN_EVERY, MAX_EVERY = 1, 1440

# ---- platform backend ------------------------------------------------------------------
# Everything below this block is the Windows implementation. macOS has no Task Scheduler, so
# supported() returned False there and the "Keep it running" switch had nothing to call -
# the feature was dead on a Mac while the Setup page went on offering it. watchdog_launchd.py
# provides the same five functions over launchd; this module picks a backend once and every
# public function forwards to it, so no caller has to know which OS it is on.
#
# The import is guarded because a broken or missing backend must degrade to "not supported"
# (an honest off switch) rather than take the whole dashboard down on import.
_BACKEND = None
if sys.platform == 'darwin':
    try:
        import watchdog_launchd as _BACKEND
    except Exception:
        _BACKEND = None

if _BACKEND is not None:
    # The dashboard shows this name to the user (watchdog_state()['task']), so it has to
    # name the thing that actually exists on this machine.
    TASK_NAME = _BACKEND.LABEL
    LEGACY_TASK_NAMES = _BACKEND.LEGACY_LABELS

# What to call the OS facility in a message, when a message has to name it at all.
# Empty on a machine with neither, because the UI must not name a facility that is not
# there - "this needs the Windows Task Scheduler" is a confusing thing to read on Linux.
MECHANISM = ('launchd' if _BACKEND is not None else
             'Windows Task Scheduler' if os.name == 'nt' else '')

# Whether the restart lands MINIMIZED, re-exported from the module that implements it so the
# UI has one place to ask. Imported rather than re-derived: two copies of `os.name == 'nt'`
# is exactly how the page came to promise something the code could not do.
try:
    from watchdog import MINIMIZES
except Exception:
    MINIMIZES = (os.name == 'nt')


def supported():
    """True when this machine has a facility we can register the watchdog with.

    Windows: Task Scheduler, with schtasks.exe on PATH. macOS: launchd, via the backend.
    Anywhere else: False, and the dashboard says so instead of offering a dead switch.
    """
    if _BACKEND is not None:
        return _BACKEND.supported()
    if os.name != 'nt':
        return False
    return bool(_schtasks_path())


def _schtasks_path():
    for base in (os.environ.get('SystemRoot', r'C:\Windows'),):
        p = os.path.join(base, 'System32', 'schtasks.exe')
        if os.path.exists(p):
            return p
    from shutil import which
    return which('schtasks')


def _run(args):
    """Run schtasks with no console flash. Returns (returncode, combined output)."""
    exe = _schtasks_path()
    if not exe:
        return 1, 'schtasks.exe not found'
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, str(e)


def is_enabled():
    """True when the task is registered under the CURRENT or any LEGACY name.

    Legacy names count as enabled on purpose: a copy that was set up before the
    rename really does have a working watchdog, and reporting "off" would invite
    the user to switch it on, registering a second task alongside the first.
    """
    if _BACKEND is not None:
        return _BACKEND.is_enabled()
    if os.name != 'nt':
        return False
    for name in [TASK_NAME] + LEGACY_TASK_NAMES:
        rc, _ = _run(['/query', '/tn', name])
        if rc == 0:
            return True
    return False


def remove_legacy_tasks():
    """Delete any pre-rename task. Returns the list of names actually removed.

    Safe to call when none exist: schtasks /delete on a missing task just returns
    non-zero, which is not an error worth surfacing here.
    """
    if _BACKEND is not None:
        return _BACKEND.remove_legacy_agents()
    removed = []
    if os.name != 'nt':
        return removed
    for name in LEGACY_TASK_NAMES:
        if _run(['/query', '/tn', name])[0] == 0:
            if _run(['/delete', '/tn', name, '/f'])[0] == 0:
                removed.append(name)
    return removed


def _python_for_task():
    """pythonw.exe if we can find it, so the task never flashes a console window."""
    exe = sys.executable or 'python'
    if exe.lower().endswith('python.exe'):
        pyw = exe[:-len('python.exe')] + 'pythonw.exe'
        if os.path.exists(pyw):
            return pyw
    return exe


def _user_id():
    dom = os.environ.get('USERDOMAIN') or os.environ.get('COMPUTERNAME') or ''
    user = os.environ.get('USERNAME') or ''
    return (dom + '\\' + user) if (dom and user) else user


def _xesc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def task_xml(every_min=DEFAULT_EVERY_MIN):
    """The task definition.

    Two triggers on purpose: LogonTrigger covers "I just signed in" with no delay, and its
    repetition covers "the dashboard died / I closed it" for the rest of the session. Interval
    repeats forever (Duration P forever = omit StopAtDurationEnd with an empty Duration).

    Settings that matter here:
      DisallowStartIfOnBatteries/StopIfGoingOnBatteries false - a laptop on battery still needs
        the dashboard, and the default for both is true.
      MultipleInstancesPolicy IgnoreNew - never stack watchdogs if one run is slow.
      ExecutionTimeLimit PT2M - watchdog.py waits at most ~25s; anything longer is stuck.
      Hidden true + pythonw.exe - nothing appears on screen when the check runs.
    """
    every_min = max(MIN_EVERY, min(MAX_EVERY, int(every_min)))
    return '''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{user}</Author>
    <Description>Starts the Top Rat dashboard (minimized) whenever it is not running. Created by make_watchdog.py.</Description>
    <URI>\\{task}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Repetition>
        <Interval>PT{every}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{py}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{here}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''.format(user=_xesc(_user_id()), task=_xesc(TASK_NAME), every=every_min,
           py=_xesc(_python_for_task()), script=_xesc(WATCHDOG), here=_xesc(HERE))


def install(every_min=DEFAULT_EVERY_MIN, retire_vbs=True):
    """Register (or replace) the task. Returns (ok, message).

    retire_vbs: the Startup-folder launcher from make_autostart.py becomes redundant once the
    task exists - it only duplicated the logon case - so it is removed by default. Two
    mechanisms starting the same server means two servers racing for one port.
    """
    if _BACKEND is not None:
        return _BACKEND.install(every_min, retire_vbs)
    if os.name != 'nt':
        return False, 'The watchdog needs the Windows Task Scheduler. This is not Windows.'
    if not supported():
        return False, 'schtasks.exe was not found, so the watchdog cannot be registered.'
    if not os.path.exists(WATCHDOG):
        return False, 'watchdog.py is missing from ' + HERE
    path, retired = None, []
    try:
        # schtasks /xml insists on UTF-16 with a BOM, matching the XML declaration above.
        fd, path = tempfile.mkstemp(suffix='.xml', prefix='toprat_watchdog_')
        with os.fdopen(fd, 'wb') as fh:
            fh.write(task_xml(every_min).encode('utf-16'))
        rc, out = _run(['/create', '/tn', TASK_NAME, '/xml', path, '/f'])
        if rc != 0:
            return False, 'Could not create the scheduled task: ' + (out or 'schtasks failed')
        # Only AFTER the new task exists: if creation failed we would otherwise have
        # deleted a working watchdog and left the user with none.
        retired = remove_legacy_tasks()
    except Exception as e:
        return False, 'Could not create the scheduled task: ' + str(e)
    finally:
        if path:
            try: os.remove(path)
            except OSError: pass

    msg = 'Watchdog enabled. "%s" checks every %d minute%s.' % (
        TASK_NAME, every_min, '' if every_min == 1 else 's')
    if retired:
        msg += ' Removed the old task%s (%s), so only one watchdog runs.' % (
            '' if len(retired) == 1 else 's', ', '.join(retired))
    if retire_vbs:
        ok, vmsg = _retire_vbs()
        if ok and 'removed' in vmsg:
            msg += ' The old login launcher was removed (the task covers login now).'
    return True, msg


def _retire_vbs():
    """Delete the make_autostart .vbs, since the task covers logon. Never fatal."""
    try:
        import make_autostart as mau
        if not mau.is_enabled():
            return True, 'no launcher'
        ok, msg = mau.uninstall()
        return ok, 'removed' if ok else msg
    except Exception as e:
        return False, str(e)


def uninstall():
    """Remove the task. Returns (ok, message). Missing task is treated as success."""
    if _BACKEND is not None:
        return _BACKEND.uninstall()
    if os.name != 'nt':
        return True, 'Nothing to remove (not Windows).'
    if not is_enabled():
        return True, 'The watchdog was not enabled (nothing to remove).'
    # Legacy names first, and unconditionally: "off" has to mean nothing is left
    # restarting the board. Removing only the current name on a pre-rename copy
    # would report success while the old task carried on doing its job.
    retired = remove_legacy_tasks()
    if _run(['/query', '/tn', TASK_NAME])[0] == 0:
        rc, out = _run(['/delete', '/tn', TASK_NAME, '/f'])
        if rc != 0:
            return False, 'Could not remove the scheduled task: ' + (out or 'schtasks failed')
    note = (' Also removed the old task%s (%s).' % ('' if len(retired) == 1 else 's',
                                                    ', '.join(retired))) if retired else ''
    return True, ('Watchdog disabled (scheduled task removed). The dashboard is not '
                  'restarted for you any more.' + note)


def run_now():
    """Ask the Task Scheduler to run the task immediately. Returns (ok, message)."""
    if _BACKEND is not None:
        return _BACKEND.run_now()
    if not is_enabled():
        return False, 'The watchdog is not enabled.'
    rc, out = _run(['/run', '/tn', TASK_NAME])
    return (rc == 0), (out or ('Started ' + TASK_NAME if rc == 0 else 'schtasks failed'))


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
        print(TASK_NAME + ' is ' + ('REGISTERED' if is_enabled() else 'NOT registered')
              + (' (supported)' if supported() else ' (NOT supported on this OS)'))
    elif cmd in ('xml', 'plist'):
        # One verb, whichever definition this machine actually uses.
        print(_BACKEND.plist_xml(_every_from_argv(sys.argv)) if _BACKEND is not None
              else task_xml(_every_from_argv(sys.argv)))
    elif cmd == 'run':
        print(run_now()[1])
    elif cmd == 'uninstall':
        print(uninstall()[1])
    else:
        print(install(_every_from_argv(sys.argv))[1])
