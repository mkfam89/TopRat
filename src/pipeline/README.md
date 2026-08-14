# src/pipeline — find jobs, score them, alert you

These scripts are deterministic and use only the standard library. They need no browser, no
AI and no API key. Run them in this order. The scheduler uses the same order:

```
scrape.py  ->  jobpipe.py candidates  ->  salary_probe.py  ->  notify.py
```

| File | What it does |
|------|--------------|
| `jobpipe.py` | **The engine.** A CLI with subcommands (`candidates`, `salaries`, `rebuild`, `process-queue`). Most other scripts call it. |
| `scrape.py` | Pulls postings from hiring.cafe. If jobspy is installed, it also pulls from LinkedIn. |
| `jobdesc.py` | Gets the full description of one posting on demand. It writes the result to `cache/`. |
| `scoring.py` | Scores a posting against your skills, classifies the posting, and parses the search-result cards. |
| `locality.py` | The distance gate. It uses keywords and the geocode cache to decide if a job is near you. |
| `salary.py` / `salary_probe.py` | Salary bands, title normalization, and the pay data for each job. |
| `notify.py` | Pushes high-match jobs to your phone with ntfy.sh. It needs no account. |
| `blocklist.py` | The list of banned employers. It is deterministic, and the pipeline always obeys it. |
| `ghost_risk.py` / `ghostflags.py` / `reposts.py` | Advisory signals for probable fake postings, your own flags, and reposted listings. These scripts give a warning. They never remove a job. |
| `skip_job.py` | Marks a job SKIPPED or BOOKMARKED from the command line. |
