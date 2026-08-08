#!/usr/bin/env python3
"""stop_board.py — stop the dashboard and everything that would bring it back.

  python src/ops/stop_board.py              # pause the watchdog, then stop the board
  python src/ops/stop_board.py --list       # report what is running, kill NOTHING
  python src/ops/stop_board.py --all        # also stop running pipeline scripts
  python src/ops/stop_board.py --pause 30   # pause the watchdog for 30 minutes (default 10)
  python src/ops/stop_board.py --no-pause   # leave the watchdog alone
  python src/ops/stop_board.py --resume     # clear a pause, stop nothing

WHY THIS EXISTS
---------------
"Close the window and start it again" does not restart this dashboard, and the failure is
silent in both directions:

  1. The OLD process keeps the port. A second server started while the first still holds
     8765 exits with "address in use" — often into a console that closes instantly — so the
     board still answers, still serves the old code, and still reads whatever data root it
     resolved at import. `pipelib` resolves the data root ONCE, at import, so a stale process
     can be pointed at a folder the config abandoned days ago. That is a config change that
     looks like it did nothing.
  2. The WATCHDOG restarts it. `JobDashboardWatchdog` runs `watchdog.py` every few minutes;
     it sees a dead port and starts a server hidden, with no console. Kill the process
     without pausing the watchdog and it comes back within minutes, looking like the kill
     failed.

So this stops BOTH halves, in that order — pause first, then kill, so a tick landing
mid-kill cannot restart what we are stopping.

DELIBERATELY NOT DESTRUCTIVE: it does not uninstall or disable the scheduled task (that is
`make_watchdog.py uninstall`, and it is a persistent change the user should make knowingly).
The pause is the mechanism `watchdog.py` already documents — `watchdogPauseUntil` in
gui_settings.json — and it expires on its own, so forgetting to undo this is harmless.

Script-only and zero-token: stdlib, no Claude, no network.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import argparse
import json
import os
import socket
import subprocess
import sys
import time

HERE = _paths.ROOT
DEFAULT_PORT = 8765
DEFAULT_PAUSE_MIN = 10

# What counts as "the board". watchdog.py is here because a check in flight is exactly the
# thing that restarts the server a second after we kill it.
BOARD_SCRIPTS = ('dashboard_server.py', 'watchdog.py')
# --all only. These are the pipeline runs the board's scheduler launches; stopping the board
# does not stop a scrape already in progress.
PIPELINE_SCRIPTS = ('scrape.py', 'jobpipe.py', 'tailor_local.py', 'notify.py',
                    'salary_probe.py', 'render_resume.py', 'llm_tailor.py')


# ---------------------------------------------------------------- config helpers
def _read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def _env(suffix, default=''):
    """TOP_RAT_<suffix>, falling back to the old JOB_AGENT_<suffix>.

    A local copy of pipelib.env on purpose: this module must keep working when
    pipelib is absent (it is imported defensively everywhere else here), and the
    port lookup is the one thing that cannot afford an import error.
    """
    for name in ('TOP_RAT_' + suffix, 'JOB_AGENT_' + suffix):
        v = (os.environ.get(name) or '').strip()
        if v:
            return v
    return default


def resolve_port():
    """$TOP_RAT_PORT > config/instance.json "port" > 8765.

    Same order as dashboard_server.resolve_port() and watchdog.resolve_port(), minus the
    --port argv form. A mismatch here would have us probing a socket nobody is on and
    reporting a stopped board that is still running.
    """
    env = _env('PORT')
    if env.isdigit():
        return int(env)
    # instance.json is the bootstrap file and always sits beside the CODE — it is what
    # tells us where the data root is, so it cannot itself live in the data root.
    inst = _read_json(os.path.join(HERE, 'config', 'instance.json'))
    try:
        return int(inst.get('port'))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def is_up(port, timeout=1.0):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def _settings_paths():
    """(read_path, write_path) for gui_settings.json, via pipelib's config overlay."""
    try:
        from pipelib import cfg, cfg_write
        return cfg('gui_settings.json'), cfg_write('gui_settings.json')
    except Exception:
        p = os.path.join(HERE, 'config', 'gui_settings.json')
        return p, p


def pause_watchdog(minutes):
    """Set watchdogPauseUntil so watchdog.py skips its next checks. Returns (ok, message)."""
    read_p, write_p = _settings_paths()
    s = _read_json(read_p, {})
    if not isinstance(s, dict):
        s = {}
    until = time.time() + max(1, int(minutes)) * 60
    s['watchdogPauseUntil'] = int(until)
    try:
        os.makedirs(os.path.dirname(write_p), exist_ok=True)
        with open(write_p, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        return False, 'could not write %s (%s)' % (write_p, e)
    return True, 'watchdog paused until %s' % time.strftime('%H:%M', time.localtime(until))


def resume_watchdog():
    read_p, write_p = _settings_paths()
    s = _read_json(read_p, {})
    if not isinstance(s, dict):
        s = {}
    s['watchdogPauseUntil'] = 0
    try:
        os.makedirs(os.path.dirname(write_p), exist_ok=True)
        with open(write_p, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        return False, 'could not write %s (%s)' % (write_p, e)
    return True, 'watchdog pause cleared'


def end_watchdog_run():
    """Stop a watchdog task instance that is running RIGHT NOW.

    /End terminates the current run only. It does NOT disable or delete the task — the
    schedule is untouched and the next tick fires normally (into the pause set above).
    """
    if os.name != 'nt':
        return False, ''
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'schtasks.exe')
    if not os.path.exists(exe):
        return False, ''
    try:
        r = subprocess.run([exe, '/End', '/TN', 'JobDashboardWatchdog'],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stdout or r.stderr or '').strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- finding processes
def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '')
    except Exception:
        return 1, ''


def port_pids(port):
    """PIDs holding the dashboard port. This is the one that catches a server started from
    a DIFFERENT copy of the app, which no script-name match would ever find."""
    pids = set()
    if os.name == 'nt':
        rc, out = _run(['netstat', '-ano', '-p', 'TCP'])
        for line in out.splitlines():
            f = line.split()
            # proto local foreign state pid
            if len(f) >= 5 and f[0].upper().startswith('TCP') and f[3].upper() == 'LISTENING':
                if f[1].rsplit(':', 1)[-1] == str(port) and f[4].isdigit():
                    pids.add(int(f[4]))
        return pids
    rc, out = _run(['lsof', '-ti', 'tcp:%d' % port, '-sTCP:LISTEN'])
    for line in out.split():
        if line.strip().isdigit():
            pids.add(int(line.strip()))
    if not pids:
        rc, out = _run(['ss', '-ltnp'])
        for line in out.splitlines():
            if ':%d ' % port in line or line.rstrip().endswith(':%d' % port):
                for part in line.split('pid='):
                    head = part.split(',')[0].strip()
                    if head.isdigit():
                        pids.add(int(head))
    return pids


def _all_processes():
    """[(pid, ppid, commandline)] for every process we can see. Empty when unavailable."""
    procs = []
    if os.name == 'nt':
        ps = ('Get-CimInstance Win32_Process | '
              'Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress')
        rc, out = _run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], timeout=60)
        if rc == 0 and out.strip():
            try:
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                for d in data:
                    pid = d.get('ProcessId')
                    if isinstance(pid, int):
                        procs.append((pid, d.get('ParentProcessId') or 0, d.get('CommandLine') or ''))
                return procs
            except Exception:
                pass
        # Fallback for boxes where PowerShell is locked down but the old CLI survives.
        rc, out = _run(['wmic', 'process', 'get', 'CommandLine,ParentProcessId,ProcessId',
                        '/format:csv'], timeout=60)
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4 and parts[-1].isdigit() and parts[-2].isdigit():
                procs.append((int(parts[-1]), int(parts[-2]), ','.join(parts[1:-2]).strip()))
        return procs
    rc, out = _run(['ps', '-eo', 'pid=,ppid=,args='])
    for line in out.splitlines():
        f = line.strip().split(None, 2)
        if len(f) >= 3 and f[0].isdigit() and f[1].isdigit():
            procs.append((int(f[0]), int(f[1]), f[2].strip()))
    return procs


def _basename(tok):
    """Last path segment, for either separator — a Windows cmdline reaches us on Windows
    but a saved schedule step or a WSL launch can carry the other one."""
    return tok.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].strip('"\'').lower()


_PY = ('python', 'python3', 'pythonw', 'py', 'pythonw3')


def _runs_script(cmd, names):
    """True only when this command line RUNS one of `names` — not merely mentions it.

    Substring matching is what makes a stop tool dangerous. `grep dashboard_server.py`,
    an editor with the file open, a `tail` on a log named after it, or a shell whose
    recorded command line quotes the name all contain the string, and none of them is the
    server. During testing a plain `in` test matched **pid 1**. So: split the command line
    into tokens, and require a token whose BASENAME equals the script exactly, introduced
    either by a python interpreter or as the command itself (a shebang launch).
    """
    if not cmd:
        return False
    try:
        import shlex
        # posix=False on BOTH platforms: it honours quotes but leaves backslashes alone,
        # so a Windows path survives intact. (posix=True eats `\P` in C:\Python312\… and
        # the basename comes out as garbage.) Quotes are stripped in _basename.
        toks = shlex.split(cmd, posix=False)
    except Exception:
        toks = cmd.split()
    if not toks:
        return False

    # The SCRIPT is the first token that is a .py file. Anything after it is that
    # script's own arguments, and anything before it is the interpreter and its flags —
    # so a name appearing anywhere else (`grep foo.py`, `tail foo.py.log`, an editor
    # argument) can never match. Only this token is compared.
    script_i = next((i for i, t in enumerate(toks) if _basename(t).endswith('.py')), None)
    if script_i is None:
        return False                          # -c / -m launches carry no script path
    if _basename(toks[script_i]) not in {n.lower() for n in names}:
        return False
    if script_i == 0:
        return True                           # ./dashboard_server.py — shebang launch
    head = _basename(toks[0])
    if head.endswith('.exe'):
        head = head[:-4]
    return head in _PY or head.startswith('python')


def script_pids(names, procs=None):
    """PIDs actually running one of `names`. Returns {pid: commandline}."""
    found = {}
    for pid, _ppid, cmd in (procs if procs is not None else _all_processes()):
        if _runs_script(cmd, names):
            found[pid] = cmd
    return found


def protected_pids(procs):
    """Pids we must never kill: pid 0/1, ourselves, and every ANCESTOR of ourselves.

    The parent chain matters because this can be launched from a .bat, from a console, or
    from an IDE terminal — each an extra generation. Killing any of them takes down the
    thing running the stop, and `taskkill /T` would take its children with it.
    """
    safe = {0, 1, os.getpid()}
    parent = {pid: ppid for pid, ppid, _c in procs}
    seen, cur = set(), os.getpid()
    while cur in parent and cur not in seen:
        seen.add(cur)
        cur = parent[cur]
        if cur in (0, 1):
            break
        safe.add(cur)
    safe.add(os.getppid())
    return safe


# ---------------------------------------------------------------- killing
def kill(pid, tree=True):
    """True when the process is gone afterwards."""
    if os.name == 'nt':
        args = ['taskkill', '/PID', str(pid), '/F'] + (['/T'] if tree else [])
        rc, _ = _run(args, timeout=30)
        return rc == 0
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    for _ in range(12):                      # 3s grace, then insist
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except OSError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
    try:
        os.kill(pid, 0)
        return False
    except OSError:
        return True


def _short(cmd, width=96):
    cmd = ' '.join((cmd or '').split())
    return cmd if len(cmd) <= width else cmd[:width - 1] + '…'


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Stop the dashboard and anything that would restart it.')
    ap.add_argument('--list', action='store_true', help='report only; kill nothing')
    ap.add_argument('--all', action='store_true',
                    help='also stop running pipeline scripts (scrape, jobpipe, tailor…)')
    ap.add_argument('--pause', type=int, default=DEFAULT_PAUSE_MIN, metavar='MIN',
                    help='minutes to pause the watchdog (default %d)' % DEFAULT_PAUSE_MIN)
    ap.add_argument('--no-pause', action='store_true', help='do not touch the watchdog')
    ap.add_argument('--resume', action='store_true', help='clear a watchdog pause and exit')
    ap.add_argument('--port', type=int, default=0, help='override the dashboard port')
    a = ap.parse_args(argv)

    if a.resume:
        ok, msg = resume_watchdog()
        print(('  ' if ok else '  FAILED: ') + msg)
        return 0 if ok else 1

    port = a.port or resolve_port()
    print('\n  Stopping the job board')
    print('  project : %s' % HERE)
    print('  port    : %d (%s)' % (port, 'answering' if is_up(port) else 'not answering'))
    print('  ' + '-' * 68)

    # 1. PAUSE FIRST. A watchdog tick between our kill and our verify would restart the
    #    server and make a successful stop look like a failed one.
    if not a.no_pause and not a.list:
        ok, msg = pause_watchdog(a.pause)
        print('  %s %s' % ('pause  :' if ok else 'pause  : FAILED —', msg))
        ended, _ = end_watchdog_run()
        if ended:
            print('  pause  : ended the watchdog run that was in progress')

    # 2. Collect. Port owners first — that is the set that catches a server launched from
    #    another copy of this project, which a script-name match cannot see.
    procs = _all_processes()
    cmd_by_pid = {pid: cmd for pid, _ppid, cmd in procs}
    targets = {}
    for pid in port_pids(port):
        targets[pid] = ('holds port %d' % port, cmd_by_pid.get(pid, ''))
    for pid, cmd in script_pids(BOARD_SCRIPTS, procs).items():
        targets.setdefault(pid, ('board process', cmd))
    if a.all:
        for pid, cmd in script_pids(PIPELINE_SCRIPTS, procs).items():
            targets.setdefault(pid, ('pipeline run', cmd))

    # Never take down ourselves, our console, or anything we are running inside of.
    for own in protected_pids(procs):
        targets.pop(own, None)

    if not targets:
        print('  nothing to stop — no process holds the port and none is running the board.')
        if not a.no_pause and not a.list:
            print('  (the watchdog stays paused; --resume clears it early)')
        print('')
        return 0

    print('  found %d process(es):' % len(targets))
    for pid, (why, cmd) in sorted(targets.items()):
        print('    %-7d %-16s %s' % (pid, why, _short(cmd)))

    if a.list:
        print('\n  --list: nothing was stopped.\n')
        return 0

    print('  ' + '-' * 68)
    failed = []
    for pid, (why, _cmd) in sorted(targets.items()):
        ok = kill(pid)
        print('    %-7d %s' % (pid, 'stopped' if ok else 'COULD NOT STOP'))
        if not ok:
            failed.append(pid)

    # 3. Verify against the PORT, not against the kill's exit code — the question is
    #    whether the board still answers, and a taskkill can report success while a
    #    replacement is already listening.
    still = False
    for _ in range(12):
        time.sleep(0.25)
        still = is_up(port)
        if not still:
            break
    print('  ' + '-' * 68)
    if still:
        print('  the port STILL answers. Something restarted the server, or a process')
        print('  outside your account holds it. Re-run with --list to see what is there.')
    else:
        print('  the board is stopped (port %d is free).' % port)
    if failed:
        print('  could not stop: %s — try again from an elevated prompt.'
              % ', '.join(str(p) for p in failed))
    print('  restart it with:  python src\\web\\dashboard_server.py'
          if os.name == 'nt' else
          '  restart it with:  python src/web/dashboard_server.py')
    print('')
    return 1 if (still or failed) else 0


if __name__ == '__main__':
    sys.exit(main())
