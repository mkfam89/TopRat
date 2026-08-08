# src/web — the dashboard

A single-file HTTP server on the standard library, plus the pages it serves. No
framework, no bundler, no npm. Open it with `Start Here.bat`, or run:

```
python src\web\dashboard_server.py
```

| File | What it does |
|------|--------------|
| `dashboard_server.py` | The server: routes, JSON API, and every button the UI calls. |
| `scheduler.py` | Runs the timed jobs *inside* the server, so nothing fires when the dashboard is closed. Steps name a bare script (`scrape.py`) and `_paths.script()` resolves it. |
| `dashboard_build.py` | Rebuilds the static tracker HTML from the tracker state. |
| `cards.html` | The board itself — **both** the card view (`/`) and the table view (`/table`). |
| `setup.html` | Settings / first-run setup page. |
| `schedule.html` | Edit the timed jobs. |
| `archived.html` | Jobs moved out by the monthly archiver. |
| `help_*.html` | Walkthroughs for ntfy, Adzuna, and the Claude CLI. |
| `ui.css` | Shared styling for all of the above. |

**House rule:** the card view and the table view are one page in two presentations. A
control added to one must be adapted to the other — see `CLAUDE.md`.

The pages live beside the server and are read through `W(name)`; project files
(config, logs, resumes) are read through `P(...)`, which points at the project root.
