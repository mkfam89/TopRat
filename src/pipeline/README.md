# src/pipeline — find jobs, score them, alert you

All deterministic and stdlib-only: no browser, no AI, no API key required.
Run in this order (the scheduler does exactly this):

```
scrape.py  ->  jobpipe.py candidates  ->  salary_probe.py  ->  notify.py
```

| File | What it does |
|------|--------------|
| `jobpipe.py` | **The engine.** Subcommand CLI (`candidates`, `salaries`, `rebuild`, `process-queue`, ...) that most other pieces call. |
| `scrape.py` | Pulls postings from hiring.cafe (and LinkedIn via jobspy, if installed). |
| `jobdesc.py` | Fetches a single posting's full description on demand; caches to `cache/`. |
| `scoring.py` | Scores a posting against your skills, classifies it, parses the search-result cards. |
| `locality.py` | The "is this job actually near me?" gate — keyword + geocode cache. |
| `salary.py` / `salary_probe.py` | Salary bands, title normalization, and per-job pay discovery. |
| `notify.py` | Pushes high-match jobs to your phone via ntfy.sh (no account needed). |
| `blocklist.py` | Hard employer ban list — deterministic, always obeyed. |
| `ghost_risk.py` / `ghostflags.py` / `reposts.py` | Advisory signals: likely-fake postings, your own flags, and reposted listings. They warn, they never drop a job. |
| `skip_job.py` | Marks a job SKIPPED or BOOKMARKED from the command line. |
