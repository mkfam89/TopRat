# Cleanup Candidates — possible unused / deprecated files

Generated from `DEPENDENCY_MAP.md` + a folder scan. **Nothing here has been deleted.**
Confidence: 🟢 safe to remove · 🟡 verify first · 🔵 your call (session helpers).
Anything not listed is an active script, template, or auto-regenerated state file — leave it.

## 🟢 Stale backup / corrupt artifacts (safe to delete)
Leftover recovery copies from past truncation/corruption incidents — superseded by `Backups/snapshots/`.
- `job_tracker.json.bashtail.bak`
- `job_tracker.json.corrupt.bak`
- `job_tracker.json.nulbak`
- `jobpipe.py.trunc.bak`
- `_rf_out.txt` (diagnostic output; now git-ignored)

## 🟢 Old run outputs (safe to delete; regenerated or obsolete)
- `job_alert_digest_2026-06-29.txt`, `job_alert_digest_2026-06-30.txt`, `job_alert_digest_2026-07-07.txt` — old daily digests
- `scrape_raw_sample.json` — debug dump, rewritten on each scrape
- `listings.fixed.json` — one-off manual fix artifact (not referenced anywhere)
- `details_manual.json` — looks like a manual test copy of `details.json` (verify it isn't a kept sample)

## 🟡 Deprecated by the new in-app scheduler / dashboard (verify, then remove)
Now that jobs run inside the dashboard (`scheduler.py`) and autostart is a UI toggle:
- `setup_scrape_tasks.bat`, `setup_radar_task.bat` — register the old **Windows Task Scheduler** jobs (JobScrape24h/7d, JobRadarHourly). Redundant once you've disabled those tasks.
- `run_radar.bat` — manual hourly radar; the scheduler's `radar` job replaces it (keep only if you like a manual trigger).
- `Install Autostart.bat` (+ any `Uninstall Autostart.bat`) — replaced by the **Auto-start** toggle on `/schedule`.

## 🟡 Superseded / not wired in (verify, then remove)
- `git_daily_UPDATED.py` — **DEPENDENCY_MAP flags this as "not wired in"**; `git_daily.py` is the live one. Strong candidate.
- `_rf.bat` — one-off "refresh + check apply URLs" diagnostic; superseded by the scheduler + `run_scrape_once.bat`.
- `skip_job.py` — CLI to skip/bookmark; the dashboard now does this via `/api/save`. Confirm nothing still calls it.
- `diag_hiringcafe.py` — one-off scraper diagnostic (kept intentionally referencing both domains). Handy to keep, but removable.

## 🟡 Loose resume files in the repo root (verify vs `resume_template/` and `New/`)
Base résumés should live in `resume_template/`; tailored ones in `New/<Company>/`.
- `PHAM_KHOA_RESUME.docx` / `.pdf` — **KEEP if this is the CORE/SRE base** (per project notes it's used as the CORE base). Otherwise a stray.
- `Khoa_Pham_Resume_business_data_analyst.docx`, `Khoa_Pham_Resume_PREVIEW.docx`, `Khoa_Pham_Resume_Tangible_SupportEngineer.pdf` — likely stray copies (the ANALYST base is `resume_template/Khoa_Pham_Resume_data_analyst.docx`). Verify before removing.

## 🟡 Test scaffolding
- `_rmtest/` folder — name implies "remove test"; check it's not needed.
- `__pycache__/` — regenerated; git-ignored (safe to delete anytime).

## 🔵 Session helpers I added (keep or remove — your call)
- `run_scrape_once.bat` — manual, notify-free scrape (handy).
- `commit_now.bat` — one-click git check-in with a message (git-ignored).
- `diag_hiringcafe.py` — see above.

## Leave alone — auto-regenerated state (NOT clutter to delete blindly)
`listings.json, candidates.csv, tracking.csv, job_tracker.json/html, tofetch.json, tofetch.err,
details.json, processed.json, run.json, manual.json, raw_applied.json, scrape_meta.json,
notified.json, radar_log.txt, scrape_log.txt, schedule_log.txt, scheduler_state.json, archived.csv, archive_index.csv`
— these are the pipeline's working state; deleting mid-cycle loses data.

---
**Suggested first sweep (lowest risk):** the 🟢 sections above. I can delete those for you on request
(via a small script, so you can review the list first), or you can remove them by hand.
