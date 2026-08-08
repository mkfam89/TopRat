"""_paths.py — the one piece of path magic in this project.

Why it exists
-------------
The scripts live in folders (src/lib, src/pipeline, src/resume, src/web, src/ops)
but they still need to (a) import each other freely and (b) find the PROJECT
folder — the one holding config/, logs/, New/ and the .bat launchers — not the
folder the script happens to sit in.

How a script uses it
--------------------
Every script under src/ opens with these two lines:

    import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _paths  # noqa: F401  — puts every src/ folder on sys.path

After that:
    import pipelib            # works from any folder
    _paths.ROOT              # the project folder
    _paths.script('scrape.py')  # absolute path of a script, for subprocess calls

Stdlib only, no side effects other than sys.path. Nothing here talks to the
network, reads config, or needs Claude.
"""
import os
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)

# Put src/ and every src/<group>/ folder on sys.path so plain `import pipelib`
# keeps working regardless of which group a module lives in.
for _d in [SRC] + [os.path.join(SRC, n) for n in sorted(os.listdir(SRC))]:
    if os.path.isdir(_d) and not os.path.basename(_d).startswith(('.', '__')):
        if _d not in sys.path:
            sys.path.insert(0, _d)

_SCRIPTS = None


def script(name):
    """Absolute path of a script, looked up by bare filename.

    Lets callers (and the schedule's step lists) keep saying 'scrape.py' instead
    of hard-coding 'src/pipeline/scrape.py', so moving a file between groups
    never breaks a subprocess call or a saved schedule.
    """
    global _SCRIPTS
    if _SCRIPTS is None:
        _SCRIPTS = {}
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames[:] = [d for d in dirnames if not d.startswith(('.', '__'))]
            for f in filenames:
                if f.endswith('.py'):
                    _SCRIPTS.setdefault(f, os.path.join(dirpath, f))
    if not name.endswith('.py'):
        name += '.py'
    return _SCRIPTS.get(os.path.basename(name), os.path.join(SRC, name))
