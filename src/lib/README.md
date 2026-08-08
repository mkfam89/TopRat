# src/lib — shared plumbing

The pieces every other folder imports. Nothing here reaches the network.

| File | What it does |
|------|--------------|
| `pipelib.py` | Path constants, JSON/CSV helpers, text utilities. **Owns `resolve_data_root()`** — the single answer to "where is the user's data?". Start here. |
| `tracker.py` | The job tracker state: applied reconciliation, resume-folder scans, status transitions. |
| `runlog.py` | One-line execution logger. Every scheduled script wraps itself in `runlog.run(...)`, which is why `logs/execution.log` shows every START/OK/FAIL. |
| `backlog.py` | The pending-job cap: pauses new searches when too many jobs are already waiting on you. |
| `profile_lib.py` | Turns one plain-language profile into every derived config (search URLs, skills, summary rules). |
