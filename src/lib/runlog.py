#!/usr/bin/env python3
"""runlog.py — tiny shared execution logger for the Top Rat.

Every entry-point script wraps its main() in runlog.run(...), which appends one line per
run to logs/execution.log:  <timestamp> | <script> | START|OK|ERROR|EXIT | <detail>

Purpose: see at a glance WHICH files actually run (and how often) and capture errors +
tracebacks, so failures can be investigated FROM THE LOG first instead of re-running things.

Read the latest activity:   python src/lib/runlog.py            (tail last 40 lines)
                            python src/lib/runlog.py 100        (tail last 100)
                            python src/lib/runlog.py errors     (only ERROR/EXIT lines)
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, time, traceback

HERE = _paths.ROOT
LOGDIR = os.path.join(HERE, 'logs')
LOGFILE = os.path.join(LOGDIR, 'execution.log')

def log(script, status, detail=''):
    """Append one structured line. Never raises (logging must not break the caller)."""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        line = '%s | %-20s | %-5s | %s\n' % (
            time.strftime('%Y-%m-%d %H:%M:%S'), script[:20], status, str(detail)[:600])
        with open(LOGFILE, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass

def run(script, fn):
    """Run fn(), logging START then OK / EXIT / ERROR(+traceback). Re-raises so behavior is unchanged."""
    args = ' '.join(sys.argv[1:])
    log(script, 'START', 'args=[%s]' % args)
    t0 = time.time()
    try:
        rc = fn()
        log(script, 'OK', '%.1fs' % (time.time() - t0))
        return rc
    except SystemExit as e:
        code = e.code
        if code in (0, None):
            log(script, 'OK', 'exit 0, %.1fs' % (time.time() - t0))
        else:
            log(script, 'EXIT', 'exit %r, %.1fs' % (code, time.time() - t0))
        raise
    except BaseException as e:
        tb = traceback.format_exc().replace('\n', ' ¦ ')
        log(script, 'ERROR', '%s: %s | %s' % (type(e).__name__, e, tb[-500:]))
        raise

def _tail(n=40, only_errors=False):
    try:
        lines = open(LOGFILE, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        print('No execution log yet at', LOGFILE); return
    if only_errors:
        lines = [l for l in lines if ' | ERROR ' in l or ' | EXIT ' in l]
    for l in lines[-n:]:
        print(l)

if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else '40'
    if arg == 'errors':
        _tail(200, only_errors=True)
    else:
        _tail(int(arg) if arg.isdigit() else 40)
