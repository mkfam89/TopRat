# src/web — the dashboard

One HTTP server file on the standard library, and the pages that it serves. It has no
framework, no bundler and no npm. To open it, double-click `Start Here.bat`. You can also
run this command:

```
python src\web\dashboard_server.py
```

| File | What it does |
|------|--------------|
| `dashboard_server.py` | The server. It holds the routes, the JSON API, and the handler for every button on the board. |
| `scheduler.py` | Runs the timed jobs *inside* the server. As a result, nothing runs while the dashboard is closed. Each step names a bare script (`scrape.py`), and `_paths.script()` finds it. |
| `dashboard_build.py` | Builds the static tracker HTML again from the tracker state. |
| `cards.html` | The board itself — **both** the card view (`/`) and the table view (`/table`). |
| `setup.html` | The Settings page and the first-run setup page. |
| `schedule.html` | The page where you change the timed jobs. |
| `archived.html` | The jobs that the monthly archiver moved off the board. |
| `help_*.html` | The guides for ntfy, Adzuna and the Claude CLI. |
| `ui.css` | The shared style for all these pages. |

**House rule:** the card view and the table view are one page with two presentations. If
you add a control to one view, adapt it to the other view. `CLAUDE.md` gives the rule.

The pages are beside the server, and `W(name)` reads them. `P(...)` reads the project files
(configuration, logs, resumes). `P(...)` points at the project root.
