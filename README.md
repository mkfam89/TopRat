# Top Rat

Finds job postings, scores them against your skills, tailors a resume for the good ones,
and shows everything on a local web dashboard. It runs on your own machine, stores your
data in your own folder, and needs no account anywhere.

**It is plain Python 3 and the standard library.** No framework, no database, no build step.
If you can read a Python script, you can read this whole project.

---

## Start here

Double-click **`Start Here.bat`** (macOS/Linux: **`Start Here.command`**). It checks
Python, starts the dashboard, and opens the board in its own window.

If the optional `pywebview` package is missing, the board opens in your normal browser
at `http://localhost:8765` instead — same app, same features, just a tab. The shipped
bundle from `tools\Make Portable Python.bat` already includes it; if you are running
your own Python the launcher offers to set it up the first time.

Everything else is optional: the dashboard has a **Settings** page for your profile and
API keys, and a **Schedule** page for the automatic searches.

| I want to...                    | Do this                                       |
|---------------------------------|-----------------------------------------------|
| Open the board                  | `Start Here.bat`                              |
| Keep it running after a reboot  | `Install Watchdog.bat`                        |
| Stop it doing that              | `Uninstall Watchdog.bat`                      |
| Run one search by hand          | `tools\run_scrape_once.bat`                   |
| Run the tests                   | `tools\run_tests.bat`                         |
| Save your work to git           | `tools\Git Daily Commit.bat`                  |

---

## Where things live

```
Start Here.bat            <- the launcher; start here
Install Watchdog.bat      <- keep the dashboard alive across reboots
Uninstall Watchdog.bat

src/                      <- all the code, grouped by what it does
  _paths.py                 the one piece of path magic (read it first)
  lib/                      shared plumbing every other script uses
  pipeline/                 find jobs, score them, alert you
  resume/                   turn a job posting into a tailored resume
  web/                      the dashboard: server + its HTML pages
  ops/                      install, backup, git, scheduler chores

config/                   <- settings that ship with the code (+ .example templates)
tests/                    <- pytest suite; no network, no LibreOffice needed
tools/                    <- one-off .bat helpers you rarely need
docs/                     <- design notes and history
logs/                     <- run transcripts (disposable)
cache/                    <- regenerable caches (disposable)

New/  Applied/  Skipped/  <- tailored resume files, by outcome
Backups/                  <- automatic snapshots, newest 5 kept
```

### Your data is NOT in this folder

Personal data — the job tracker, your profile, your base resumes — lives in its **own
folder**, never mixed into the code, so this repo can be shared or published without
leaking anything. By default that folder sits **inside the app folder**:

```
auto customize resume/         <- the app (this repo)
  src/  config/  tools/
  user_data_KP/                <- everything personal (git-ignored)
    resume_template/           <- your base resumes + STAR stories
    New/ Applied/ Skipped/     <- tailored resumes
    job_tracker.json  tracking.csv  config/profile.json  ...
```

One folder is the whole install: copy it to a new machine and your history comes with it.
The setup wizard suggests `user_data_<your initials>` once you type your name, and you can
point it anywhere else from **Settings → Advanced → Data folder location**.

`config/instance.json` holds the pointer:

```json
{ "data_root": "C:\\Users\\you\\Projects\\auto customize resume\\user_data_KP" }
```

If that file is absent the app looks for a single `user_data_*` folder beside the code.
`src/lib/pipelib.py` (`resolve_data_root`) is the authority; everything else asks it.
Already have data in an older location? `python src/ops/migrate_data_root.py` prints the
plan; add `--apply` to move it (verified copy, source left untouched).

---

## The two rules that explain the design

**1. Every feature works with no AI at all.** Discovery, scoring, salary lookup,
notifications, tracking, the dashboard, backups — all deterministic scripts. Claude is
an optional layer that writes nicer resume prose when it is available. Remove it and
the pipeline still runs, just with template-copied resumes instead of tailored ones.

**2. Scripts never write the user's own words.** Notes, answers, applied/bookmarked
flags belong to the person using the app. Code reads them; only the dashboard, driven
by a human click, writes them.

Both rules are non-negotiable and are spelled out in `CLAUDE.md`.

---

## How a job gets from the internet to a resume

```
scrape.py        pull postings from hiring.cafe / LinkedIn      -> listings
jobpipe.py       score each posting against your skills         -> tracker + candidates.csv
salary_probe.py  fill in missing pay data                       -> tracker
notify.py        push the good ones to your phone (ntfy)        -> alert
                 ... you click "Tailor" on the dashboard ...
tailor_local.py  copy the closest base resume (no AI)           -> New/<Company>/
llm_tailor.py    or rewrite the bullets with Claude, if present -> New/<Company>/
render_resume.py build the .docx + .pdf pair                    -> New/<Company>/
lint_resume.py   refuse anything that breaks the format rules
```

`src/web/scheduler.py` runs those steps on a timer while the dashboard is open. The
schedule is editable on the dashboard's **Schedule** page; the shipped defaults are in
`config/schedule.default.json`.

---

## Imports, and why `_paths.py` exists

The scripts sit in folders but still import each other by plain name. Every file under
`src/` opens with two lines that make that work:

```python
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401
```

After that, `import pipelib` works from anywhere, `_paths.ROOT` is the project folder
(never the script's folder), and `_paths.script('scrape.py')` finds a script by name for
subprocess calls. That last one is why moving a file between `src/` folders does not
break the saved schedule.

---

## Before you change anything

- `DEPENDENCY_MAP.md` — how the scripts, config, and state files connect. Read it
  instead of grepping the whole tree. **Update it in the same commit as any code change.**
- `CLAUDE.md` — the hard rules: resume formatting, filenames, what must never be invented,
  what must never be written to.
- `docs/` — design decisions and migration notes, kept for history.
- Run `tools\run_tests.bat` before and after. Some tests need the data folder present;
  a run with no data folder will show failures that are about the environment, not you.
