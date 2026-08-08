# Ship Plan — Top Rat as a cross-platform desktop app

**Goal:** Windows + macOS desktop app. Own window, own icon, no browser tab, no terminal.
**Constraint chosen:** wrap only — `src/` is not refactored.
**Status:** proposed, awaiting approval. Nothing below is built yet.

---

## 1. What already works (audited 2026-08-06, no work needed)

| Concern | State |
|---|---|
| Scheduling | `src/web/scheduler.py` runs jobs **in-process**, uses `sys.executable`, no `.bat`, no `schtasks`. Already cross-platform. |
| Code/data split | `pipelib.resolve_data_root()` — 3-rung resolution (`$JOB_AGENT_DATA` → `config/instance.json` → fallback). App folder can be read-only. |
| Platform guards | `os.name != 'nt'` branches already present in `watchdog.py`, `make_watchdog.py`, `pipelib.py`. Nothing crashes off Windows; it degrades. |
| Server | `ThreadingHTTPServer` bound to `127.0.0.1`, `resolve_port()` already handles a busy port. |
| Icons | `assets/icon/` has `.svg`, `.ico`, and PNG 16→512. |
| Tests | 20 test modules under `tests/`. |

Roughly 70% of "shippable" is already done. The remaining work is packaging, not rewriting.

---

## 2. The one real blocker: `sys.executable`

28 call sites across 12 modules spawn children as `[sys.executable, <script>.py]`
(`dashboard_server.py` ×13, `tailor_local.py` ×3, `jobpipe.py` ×2, `scheduler.py` ×2, plus 8 more).

Under **PyInstaller**, `sys.executable` is the frozen app, not a Python interpreter.
Handing it a `.py` path does nothing. Every tailor, salary probe, scrape and scheduled
step silently breaks.

### Two ways out

**Option A — embedded interpreter (recommended, matches "wrap only")**
Ship a real CPython inside the app bundle. A small native launcher starts
`<bundle>/python/bin/python app.py`. `sys.executable` stays a genuine interpreter,
so all 28 spawns keep working untouched.
- Windows: `python-embed` zip (the existing `tools\Make Portable Python.bat` already
  does this — the pattern is proven here).
- macOS: `python-build-standalone` runtime inside `JobAgent.app/Contents/Resources/`.
- Cost: ~40 MB per platform. Zero code changes to `src/`.

**Option B — PyInstaller + a `_child_cmd()` shim**
Add one helper and change 28 call sites, plus an argv dispatcher so the frozen binary
can re-enter itself as any script. Smaller download, but it *is* a refactor and every
future `sys.executable` call becomes a trap.

**Recommendation: A.** B contradicts the constraint and adds a permanent footgun.

---

## 3. Work items

### A. Desktop shell — `app.py` (new, project root)
- Starts `dashboard_server` on a free port in a background thread.
- Opens a native window via **pywebview** (WebKit on macOS, WebView2 on Windows).
- **Falls back to `webbrowser.open()` if pywebview is unavailable** — the app must
  still work with no window toolkit, mirroring the project's "degrades gracefully" rule.
- Window title, icon, sane min size; closing the window shuts the server down cleanly.

### B. Launchers
- macOS: `JobAgent.app` bundle — `Info.plist`, `.icns`, launcher shim.
- Windows: `JobAgent.exe` shim (or keep `Start Here.bat` as the fallback path).
- Linux: `start.sh` + `.desktop` file. Low cost once A and B exist.

### C. First run
Bundle contents must be treated as read-only (macOS Gatekeeper, Program Files).
- On first launch with no `config/instance.json`, write `data_root` to
  `~/Documents/Top Rat/` (per-OS default) instead of inside the bundle.
- Then hand off to the existing `/setup` page. No new wizard.

### D. Gaps found in the audit
1. `find_soffice()` (`src/resume/tailor_local.py:110`) has Windows and Linux paths but
   **no macOS path** — add `/Applications/LibreOffice.app/Contents/MacOS/soffice`.
   Without this, macOS gets `.docx` but no `.pdf`.
2. **No dependency manifest.** Add `requirements.txt`: `python-docx`, `jsonschema`,
   `python-jobspy` (optional). All three are already import-guarded.
3. `scrape.py:165` **pip-installs `python-jobspy` at runtime.** That must not happen
   inside a shipped bundle — pre-bundle it, or make the failure a clean "LinkedIn
   search unavailable" message.
4. **No version string anywhere.** Add `src/_version.py`, surface it in an About row on
   `/setup`. Needed for any support conversation or update check.
5. **Autostart is Windows-only** (`make_autostart.py` writes to the Startup folder).
   For v1, hide the toggle on non-Windows. macOS LaunchAgent is a follow-up, not a blocker —
   the in-process scheduler already covers scheduling while the app is open.

### E. Ship hygiene (before any zip leaves the machine)
- Exclude `user_data_KP/`, real `config/*.json`, `Applied/`, `New/`, `Skipped/`, `logs/`,
  `cache/`, `Backups/`. `.gitignore` already covers most of this — the packaging script
  needs its own allowlist, not a blocklist.
- Add a `LICENSE`.
- Verify a clean-machine run: unpack to an empty folder, launch, reach `/setup`.

---

## 4. Suggested order

1. `app.py` + pywebview shell, run from source on Windows. *(smallest useful step — a real window today)*
2. First-run `data_root` selection.
3. `requirements.txt`, `_version.py`, macOS `soffice` path, jobspy guard.
4. `build.py` packaging script → embedded interpreter + platform launcher.
5. Clean-machine verification on Windows, then macOS.

Steps 1–3 are ordinary edits. Step 4 is the bulk of it. Step 5 is where the real
surprises live, and macOS cannot be verified from this machine.

---

## 5. Open questions

- **macOS testing** — is there a Mac to test on? If not, macOS ships untested, which
  I would not call shippable. Windows-first is the honest sequencing.
- **Code signing** — unsigned apps get a Gatekeeper warning on macOS and SmartScreen on
  Windows. Signing costs money per year. Acceptable for v1?
- **Distribution** — GitHub Releases zip, or something more polished?
