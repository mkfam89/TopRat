# Top Rat

Top Rat finds job postings. It scores each posting against your skills. It writes a
tailored resume for the good ones. It shows the result on a local web dashboard. The app
runs on your own machine, keeps your data in your own folder, and needs no account.

**The app is plain Python 3 and the standard library.** It has no framework, no database,
and no build step. If you can read a Python script, you can read this project.

---

## Start here

Double-click **`Start Here.bat`**. On macOS and Linux, double-click **`Start Here.command`**.
The launcher finds Python, starts the dashboard, and opens the board in its own window.

If the `pywebview` package is absent, the board opens in your usual browser at
`http://localhost:8765`. It is the same app with the same features, in a tab. The Python
bundle from `tools\Make Portable Python.bat` includes `pywebview`. If you use your own
Python, the launcher offers to install it the first time.

Everything else is optional. The dashboard has a **Settings** page for your profile and
your API keys. It has a **Schedule** page for the automatic searches.

| I want to...                       | Do this                                       |
|------------------------------------|-----------------------------------------------|
| Open the board                     | `Start Here.bat` / `Start Here.command`       |
| Keep the board open after a reboot | `Install Watchdog.bat` / `.command`           |
| Stop the automatic restart         | `Uninstall Watchdog.bat` / `.command`         |
| Run one search by hand             | `tools\run_scrape_once.bat`                   |
| Run the tests                      | `tools\run_tests.bat`                         |
| Save your work to git              | `tools\Git Daily Commit.bat`                  |

---

## Where things live

```
Start Here.bat            <- the launcher; start here (Windows)
Start Here.command        <- the same launcher for macOS and Linux
Install Watchdog.bat      <- keeps the dashboard open across reboots
Install Watchdog.command    (the .command files are the mac/linux twins)
Uninstall Watchdog.bat
Uninstall Watchdog.command

src/                      <- all the code, in groups by function
  _paths.py                 the only path logic (read this file first)
  lib/                      shared code that every other script uses
  pipeline/                 finds jobs, scores them, alerts you
  resume/                   turns a job posting into a tailored resume
  web/                      the dashboard: server and its HTML pages
  ops/                      installation, backup, git and scheduler tasks

config/                   <- settings that ship with the code (and .example templates)
tests/                    <- pytest suite; it needs no network and no LibreOffice
tools/                    <- .bat helpers that you rarely need
docs/                     <- design notes and history
logs/                     <- run logs (you can delete them)
cache/                    <- caches (the app makes them again)

New/  Applied/  Skipped/  <- tailored resume files, by result
Backups/                  <- automatic snapshots, newest 5 kept
```

### Your data is NOT in this folder

Your personal data stays in its own folder. This data is the job tracker, your profile,
and your base resumes. The code folder never holds it. As a result, you can share or
publish this repository with no risk to your data. By default, the data folder is inside
the app folder:

```
auto customize resume/         <- the app (this repository)
  src/  config/  tools/
  user_data_KP/                <- all personal data (git-ignored)
    resume_template/           <- your base resumes and STAR stories
    New/ Applied/ Skipped/     <- tailored resumes
    job_tracker.json  tracking.csv  config/profile.json  ...
```

One folder holds the whole installation. Copy that folder to a new machine, and your
history moves with it. The setup wizard suggests the name `user_data_<your initials>`
after you type your name. To keep the data somewhere else, go to
**Settings → Advanced → Data folder location**.

The file `config/instance.json` holds the pointer:

```json
{ "data_root": "C:\\Users\\you\\Projects\\auto customize resume\\user_data_KP" }
```

If that file is absent, the app looks for one `user_data_*` folder beside the code. The
function `resolve_data_root` in `src/lib/pipelib.py` decides the location, and every other
script asks that function.

If your data is in an older location, run `python src/ops/migrate_data_root.py`. The
script prints the plan. Add `--apply` to move the data. The move is a verified copy, and
it does not change the source.

---

## The two rules that explain the design

**1. Every feature works with no AI.** Discovery, scoring, salary lookup, notifications,
tracking, the dashboard and backups are all deterministic scripts. Claude is an optional
layer that writes better resume text when it is available. If you remove Claude, the
pipeline still runs. It then copies a template resume instead of a tailored one.

**2. Scripts never write your own words.** The notes, the answers, and the applied and
bookmarked flags belong to the person who uses the app. Code reads these fields. Only the
dashboard writes them, and only after a human click.

Both rules are mandatory. `CLAUDE.md` gives the full text.

---

## How a job goes from the internet to a resume

```
scrape.py        pulls postings from hiring.cafe and LinkedIn   -> listings
jobpipe.py       scores each posting against your skills        -> tracker + candidates.csv
salary_probe.py  fills in missing pay data                      -> tracker
notify.py        pushes the good ones to your phone (ntfy)      -> alert
                 ... you click "Tailor" on the dashboard ...
tailor_local.py  copies the closest base resume (no AI)         -> New/<Company>/
llm_tailor.py    or rewrites the bullets with Claude, if present-> New/<Company>/
render_resume.py builds the .docx and .pdf pair                 -> New/<Company>/
lint_resume.py   rejects a file that breaks the format rules
```

`src/web/scheduler.py` runs these steps on a timer while the dashboard is open. You can
change the times on the **Schedule** page. The default times are in
`config/schedule.default.json`.

---

## Imports, and why `_paths.py` exists

The scripts are in different folders, but they import each other by plain name. Two lines
at the top of every file in `src/` make this possible:

```python
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401
```

After these two lines, `import pipelib` works from any folder. `_paths.ROOT` is the
project folder, never the folder of the script. `_paths.script('scrape.py')` finds a
script by name for a subprocess call. For this reason, a move of a file between `src/`
folders does not break the saved schedule.

---

## Before you change anything

- `DEPENDENCY_MAP.md` shows how the scripts, the configuration and the state files
  connect. Read this file instead of a search through the whole tree. **Update it in the
  same commit as the code change.**
- `CLAUDE.md` gives the mandatory rules for resume format, file names, invented content
  and protected files.
- `docs/` holds design decisions and migration notes, kept for history.
- Run `tools\run_tests.bat` before and after your change. Some tests need the data folder.
  If the data folder is absent, these tests fail because of the environment.
