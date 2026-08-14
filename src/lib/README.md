# src/lib — shared code

Every other folder imports these modules. No module here uses the network.

| File | What it does |
|------|--------------|
| `pipelib.py` | Path constants, JSON and CSV helpers, and text tools. **It contains `resolve_data_root()`**, the one function that gives the location of your data. Read this file first. |
| `tracker.py` | The job tracker state. It reconciles applied jobs, scans the resume folders, and changes the status of a job. |
| `runlog.py` | The one-line run logger. Every scheduled script calls `runlog.run(...)`. As a result, `logs/execution.log` shows each START, OK and FAIL. |
| `backlog.py` | The cap on pending jobs. It stops new searches when too many jobs wait for you. |
| `profile_lib.py` | Turns one plain-language profile into all the derived configuration: search URLs, skills, and summary rules. |
