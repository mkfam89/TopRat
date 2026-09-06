# Dependency Map — Resume Tailoring Pipeline

Single source for "what relies on what," so the code doesn't have to be re-read each
session. Reference this from CLAUDE.md instead of re-deriving the wiring.
Paths are repo-root relative. `config/` = settings; machine-written state lives in the data root.
**Since the 2026-08-06 reorg the modules sit in `src/<group>/` — see "Repo layout" below.
This map still calls them by bare filename (`scrape.py`), which is also how the code refers to
them: `_paths.script('scrape.py')` resolves a name to its folder.**

## Two run paths

1. **Scripts only (zero-token, no Claude)** — discovery → scoring → notify → git → archive.
   Driven by `scheduler.py` (runs *inside* the dashboard, only while it's open) or Windows Tasks.
2. **Agent path (Claude)** — the daily/weekly `SKILL.md` scheduled task. Claude orchestrates
   `jobpipe.py` and does the parts scripts can't: **resume customization (LLM)**, Chrome scrape
   fallback, Simplify sync, salary web search.

`jobpipe.py` is the deterministic hub both paths call. Since the 2026-07-21 module split it is a
**thin CLI dispatcher** over five local modules (`pipelib` → `scoring` → `tracker` → `salary` /
`dashboard_build`; imports flow only in that direction, no cycles). The **subprocess boundary is
still the contract**: external callers (scheduler.py, dashboard_server.py, tailor_local.py, the
SKILL.md tasks) shell out to `python jobpipe.py <cmd>` and must NOT import the modules directly.
jobpipe.py re-exports every moved name (public + underscore) so tests keep resolving
`jobpipe.<name>` and monkeypatching `jobpipe.BASE` / `JSON_PATH` / `SALARY_CACHE` still works
(`_resolve_salary` is a thin wrapper passing jobpipe's `SALARY_CACHE` through for that reason).

## Repo layout (2026-08-06 reorg)

Every module moved out of the repo root into a `src/` group. **No module was renamed, no
import statement changed** — `src/_paths.py` puts every group folder on `sys.path`, so
`import pipelib` still works from anywhere.

| Folder | Modules |
|--------|---------|
| `src/lib/` | pipelib.py, tracker.py, runlog.py, backlog.py, profile_lib.py |
| `src/pipeline/` | jobpipe.py, scrape.py, jobdesc.py, scoring.py, locality.py, salary.py, salary_probe.py, notify.py, blocklist.py, ghost_risk.py, ghostflags.py, reposts.py, skip_job.py |
| `src/resume/` | render_resume.py, llm_tailor.py, tailor_local.py, lint_resume.py, validate_bullets.py |
| `src/web/` | dashboard_server.py, scheduler.py, dashboard_build.py + cards/setup/schedule/archived/dev/help_*.html + ui.css |
| `src/ops/` | setup.py, watchdog.py, make_watchdog.py, watchdog_launchd.py, make_autostart.py, git_daily.py, backup.py, archive.py, cleanup_user_data.py, migrate_data_root.py, diag_hiringcafe.py |

Three rules keep this from drifting:

1. **`_paths.ROOT` is the project folder, never the script's folder.** Every module that
   used `HERE = os.path.dirname(os.path.abspath(__file__))` now reads `HERE = _paths.ROOT`,
   so `P('config', ...)`, `Backups/`, `New/` and the data-root resolution are unchanged.
2. **Subprocess targets go through `_paths.script('x.py')`**, never a hand-built join.
   This is what lets a saved `schedule.json` keep listing bare `scrape.py` steps, including
   the live user copy in the data root that the code must never rewrite.
3. **Web assets go through `W(name)` in dashboard_server.py** (`src/web/`, beside the
   server), while `P(...)` stays pointed at the project root. Don't mix them.

Also in that reorg: `docs/` now holds the working notes (CARD_UI_DECISIONS, REFACTOR_PLAYBOOK,
SPLIT_DATA_STEPS, STAGING, MERGE_STAGING_STEPS, CLEANUP_CANDIDATES, DEPENDENCY_MAP.html);
`cache/` holds regenerable caches (jobdesc_cache.json moved here); `logs/` holds every run
transcript (radar_log.txt / scrape_log.txt / _rf_out.txt were repointed there);
`showcase_job_agent/` and the pre-split state copies in the repo root were deleted, along with
worklog.txt (the `--note` flag it documented was never implemented) and adzuna_keys.png.
`README.md` at the root and one per `src/` group are the new-owner entry points.

**Test folders — two of them, and they are not interchangeable (`qa/` added 2026-08-15):**

| Folder | What it tests | Imports `src/`? | Ships? |
|--------|---------------|-----------------|--------|
| `tests/` | the CODE, in-process, offline | yes — it is the suite under `pytest.ini` | to the git export **only**; `package.py` strips it from the zip |
| `qa/` | the built ARTIFACT, from outside, on a clean OS | **no** — it unzips `dist/TopRat-*.zip` and drives it | never, anywhere |

`qa/` exists because a whole class of defect is invisible in-process: the `.bat` failing to
find Python on a machine that has none, the app writing a scheduled task nobody asked for, a
zip whose exec bits or layout are wrong, the window never appearing. All four are properties
of the *packaged* copy on a *clean* machine, which is what Windows Sandbox provides free.
See "Distribution" under Config vs state files for the wiring.

`qa/` now has **two targets, three entry points** (`qa/vm/` added 2026-08-16):

| Entry point | Target | Agent | Tokens |
|---|---|---|---|
| `qa/sandbox/Run Windows Sandbox Test.bat` | throwaway Windows Sandbox | none | zero |
| `qa/sandbox/Claude Sandbox.bat` | throwaway Windows Sandbox | Claude **Code** (`sandbox_ui.ps1`) | yes |
| `qa/vm/Run VM Test.bat` | persistent Hyper-V VM `TopRat-QA` | Claude **Desktop** (computer-use) | yes |

The zero-token path remains the authority; deleting `qa/vm/` entirely changes nothing about
it, per the design rule in `CLAUDE.md`.

## Agent prompts (`agent_prompts/`, 2026-08-08)

The two Claude scheduled tasks that drive the pipeline are prompts, and they used to live ONLY
in Claude's own `Scheduled/<taskId>/SKILL.md` — outside this repo. A new user cloning the code
had no way to get them and no way to know they existed, and the copies that did exist drifted:
they still called `jobpipe.py` at the repo root, which the `src/` refactor moved, and pointed at
`to_process.json` beside the code rather than in the DATA root.

`agent_prompts/daily-discovery.md` + `tailor-queue.md` are now the versioned source. They are
de-personalized and carry `{{TOKEN}}` placeholders; `dashboard_server.render_agent_prompt()`
substitutes them (`{{JOBPIPE}}` / `{{GIT_DAILY}}` / `{{SCRAPE}}` built with `os.path.join`, NOT
literal backslashes, because this app ships a macOS launcher too; `{{TO_PROCESS}}` from
`pipelib.TO_PROCESS`, so the DATA root is honoured; plus `{{PROJECT_DIR}}` / `{{OWNER}}` /
`{{RESUME_PREFIX}}` / `{{LOCAL_AREA}}`). `GET /api/agent-prompts` serves the rendered text.

**2026-08-18 — the `/setup` card that consumed it is GONE.** *Run it automatically* (`#sec_agents`
on step 4) was removed: automation is configured on the **Schedule** page, by the in-process
`scheduler`, and offering a second, unrelated way to automate the same pipeline — copy a prompt,
paste it into Claude — made the two read as alternatives when only one of them is the app's own.
`agent_prompts/`, `render_agent_prompt()` and `GET /api/agent-prompts` are **left in place and
still work**; they simply have **no caller in the UI** now. Anyone reviving them should add the
card back to `/schedule`, not to `/setup`.

**Deliberately NOT written into Claude's Scheduled folder.** That is another app's storage and
its layout is not ours to depend on; pasting into the Claude window is also a step a
non-technical user can watch succeed. Only two tasks ship — the evening and weekly variants are
the same work at other hours, and a menu of four is a menu a new user closes.

**Keep the two prompts consistent:** they share the resume rules, the `item.action` branch and
the tracker-update contract almost verbatim. A change to how a resume is produced belongs in
both files or in neither.

## Components

| File | Role | Imports (local) | Invokes | Reads | Writes |
|------|------|-----------------|---------|-------|--------|
| **jobpipe.py** | Thin CLI dispatcher / hub (subcommands below). Owns only `filter`, `process-queue`, `doctor`, and the `lint-resume` passthrough; every other subcommand dispatches into the modules. Re-exports all moved names for the tests. `_is_local` delegates to `locality.is_local` (keyword + cached-geo pass) | `pipelib`, `scoring`, `tracker`, `salary`, `dashboard_build`, `locality` (lazy, in `_is_local`) | `salary_probe.py` (process-queue auto-probe), `lint_resume.py` (doctor + `lint-resume`) | config: strategy, gui_settings, search_urls · state: candidates.csv, to_process.json, salary_cache.json, tracking.csv, scheduler_state.json, run.json, salary_quota.json (doctor) | — (stdout; state writes live in the modules) |
| **pipelib.py** | **Owns the CODE/DATA split** (see "Data root" below): `resolve_data_root()` → `DATA`/`SPLIT`, the `cfg()`/`cfg_write()` config overlay, `CODE_CONFIG`/`DATA_CONFIG`/`USER_CONFIG`, and `INSTANCE_JSON` (bootstrap, always beside the code). Shared path constants (BASE/CONFIG/JSON_PATH/TRACKING_CSV/CANDIDATES_CSV/TO_PROCESS/**TAILOR_ERRORS** (2026-08-05, `tailor_errors.json` — why the last queue run built no resume, per job id; written by tailor_local, read by dashboard_server)/SALARY_BANDS/SALARY_CACHE/SALARY_QUOTA/GEO_CACHE/SCRAPE_META/NOTIFIED/RAW_APPLIED/BULLETS…) + io/text helpers (`load_json`, **`data_path`** / **`load_listings`** (2026-08-06 — CLI file args resolve DATA → HERE → cwd, never bare-relative off the cwd; see "Command-line file args" under Data root), `_safe_write_text`, `stem`/`slug`/`camel_to_words`/`tracker_id`, `TEMPLATES`). **Filename shorteners** `clean_company`/`clean_role` keep the resume name to a recruiter-friendly `Company_Role` core (drop parentheticals, location/timezone/remote flags, team + tech-stack tails; keep seniority+level; strip company legal suffixes, cap ~3 words). `tracker_id` = `slug(clean_company)_slug(clean_role)` capped 80 — so id == filename stem stays short. `slug` itself is left PURE (its camel round-trip + the applied reconciler depend on it — only the *inputs* are trimmed). **`resume_prefix()` / `is_resume_file()` / `reset_resume_prefix()` (2026-08-08 — the profile phase-2 refactor)**: the tailored-resume filename prefix was a hardcoded literal carrying the FIRST user's name, in six places across `pipelib`, `tracker`, `dashboard_build`, `render_resume` and `tailor_local`, so a second user's resumes carried someone else's name. It now reads `profile.json` `identity.resume_prefix` (the field `setup.py` and `/setup` have always written and nothing ever read), cached per process, falling back to a name derived from `identity.full_name` and then to a **generic anonymous** `_DEFAULT_RESUME_PREFIX` — so the zero-token script path still runs with no profile at all. That last fallback is generic on purpose: keeping the original owner's literal read like harmless backward compatibility, but it is exactly what `release.py`'s PII gate exists to keep out of a shipped copy, and it was dead weight since any configured install sets step 1. `tests/test_release.py::test_export_is_clean` is the check. **The scan/parse side is deliberately prefix-AGNOSTIC**: `stem()` strips the configured prefix, else any `<name>_Resume_` via `_ANY_PREFIX_RE`, and `is_resume_file()` (the single predicate the folder scans share) matches the same regex — an equality test on the current prefix would orphan every resume already on disk the moment the user edits the field in Settings. `is_resume_file` also rejects `~$`/`PREVIEW` itself, because `dashboard_build._id_in_dir` has no separate guard and a stale Word lock file matching the regex would read as "already filed" and delete the real copy out of `New/`. `TEMPLATES` is now **just** `set(template_files())`: the four hardcoded legacy basenames it used to be unioned with were the original owner's personal filenames living in shipped code, and they were the wrong mechanism anyway — any base the user actually has is already covered by `template_files()`, and a legacy name no longer in the template folder names a file this install has no other reason to know about. No business logic **`detect_employment_type(title, location, description, salary)`** = deterministic badge classifier: returns `Contract`/`Part-time`/`Temporary`/`Internship`/`Contract-to-hire`/`Freelance`/… when a role is clearly NOT full-time W2, else `''` (title+location trusted first, then the description body; a "contract management"-style phrase with no standalone arrangement wording is suppressed). Used by `dashboard_server` for the card's employment badge.  | — | — | config: **profile.json** (`_profile_template_dir` → TEMPLATE_DIR; **`resume_prefix()` → `identity.resume_prefix`**) · resume_template/ (template scan) | — |
| **scoring.py** | `classify` / `skill_match` / `flag_skills` / `strategy` + `_shrinkage` (thin-extraction confidence correction) + `title_fit` / `score_job` (title-relevance gate) — both described below + the hiringcafe card parser (`parse-cards` impl). *(2026-08-06: **`_pick_base` no longer invents a filename.** It keyword-matches whatever files `pipelib.template_files()` finds in `resume_template/` and returns **`('', '')`** when there are none — it used to fall back to a hardcoded owner-specific filename, so an EMPTY or unresolvable template folder (e.g. a `data_root` that does not exist, which reads identically) made every consumer report a specific missing FILE instead of an empty folder. `classify` therefore emits `baseResume: ''` / `baseType: ''` in that case; consumers must branch on it — `tailor_local` → `template_none`, `dashboard_server._tailor_err` → predicted `template_none`. **(2026-08-08, profile phase 2: the CORE route no longer uses the first user's surname.** It was a keyword pass carrying the first user's SURNAME, so another user whose base was named after themselves matched neither keyword and fell through to the "any .docx" catch-all — which could hand back their ANALYST or SUPPORT variant as the general base. CORE now goes through **`_pick_core_base()`**: explicit generic names first (`_CORE_KEYWORDS` = core/master/general; `base` is deliberately absent — it is a substring of "database"), then **the first resume carrying none of `_SPECIALIZED_KEYWORDS`** (analyst/data/support/tangible/techsupport), then `_pick_base()`'s catch-all so an empty folder still yields `('', '')`. **No personal alias is needed** — a base named after its owner carries no specialization keyword, so the no-keyword rule resolves it; that is why the original alias could be deleted outright rather than kept for compatibility. Deriving the keyword from `identity.full_name` does NOT work and was tried first: `resume_prefix` puts the user's name in EVERY variant's filename, so a name keyword matches the `<name>_Resume_data_analyst.docx` variant before the real base. `_pick_core_base` also must not call `_pick_base(*_CORE_KEYWORDS)` for its first step — `_pick_base` falls through to "any .docx", so it always returns something and the no-keyword rule becomes unreachable. Both traps are pinned by `tests/test_scoring.py::test_core_base_*`.)* **The keyword pass also prefers the `.docx` over its `.pdf` twin**: a base exists as both, the pass used to take the first `os.listdir` hit, and a folder that listed the .pdf first handed `tailor_local` a PDF that it copied to a `.docx` filename. Preference, not a filter — a keyword with only a .pdf still resolves.)* | `pipelib` | — | config: skills, skill_aliases, strategy, **profile** (in-lane titles), gui_settings · resume_template/ (base pick) | listings.json (`parse-cards`) |
| **tracker.py** | Tracker/html/CSV state: read/write_tracker, applied-status normalization + reconcile, `scan_active_jobs` (its "is this a tailored resume" test is now `pipelib.is_resume_file`, not a literal prefix — see pipelib), `resolve_status`, tracking.csv / archive_index.csv writers; impls for `candidates`, `simplify`, `archive`, `export-csv`, `import-csv`. `candidates` now also annotates each row via `reposts.annotate` → the `repost`/`repostNote` columns (advisory, never excludes). **`candidates` matches the tailored + skipped sets by NORMALIZED company+role too** (same `applied_norm_index`/`applied_match` used for applied), so a resume tailored/skipped under the older LONG-id scheme is still recognized once `tracker_id` shortens the id — prevents re-tailoring a short-named duplicate. **`candidates` enforces the PAY floor as well as the discovery floor (2026-08-13):** after the repost pass it runs `payfilter.filter_rows` over the non-queued rows and deletes anything a real figure prices below `profile.search.salary_min`, printing `pay-drop <id> <reason>` per row plus a `N under $X` clause in its summary line. It runs here with whatever `salary_cache.json` already holds — a job discovered THIS run has no figure yet, which is exactly why `salary_probe.py` re-sweeps the same file at the end of its own step | `pipelib`, `scoring`, `blocklist`, `reposts`, `payfilter` | — | job_tracker.json/html, listings.json, to_process.json, salary_cache.json + config/profile.json (via payfilter) · scans `New/ Applied/ Skipped/` | job_tracker.json/html, tracking.csv, archive_index.csv, candidates.csv, Backups/job_backup_*.zip (`archive`) |
| **salary.py** | `salary_lookup` / `_norm_salary_title` / `_resolve_salary` precedence chain / `_probe_salaries`; impls for `salary`, `salary-apply`, `salary-set`, **`salaries`** (`_bucket_source` → `{id:{value,source,kind}}` resolved map for ALL candidate/tracking/job_details jobs; `kind` ∈ confirmed\|adzuna\|rough, drives the tracker's italic/(est.)/tooltip; **source `manual` buckets as `confirmed`** so a user-entered override reads as real pay) | `pipelib`, `tracker` | `salary_probe.py` | config/salary_bands.json, salary_cache.json, candidates.csv+tracking.csv (`salaries`) | salary_bands.json (`salary-set`), job_tracker.json (`salary-apply`, incl. the manual override from `/api/salary-edit`) |
| **dashboard_build.py** | Impls for `update` (fold details/processed/run into the tracker, backup.py snapshot first) and `rebuild` (reconcile folders, regenerate JOBS/ARCHIVED arrays in the html, persist skip/bookmark, re-emit CSVs). Both folder scans and `_id_in_dir` test files with `pipelib.is_resume_file` rather than a literal prefix (2026-08-08) | `pipelib`, `tracker` | `backup.py` (on `update`) | details/processed/run.json, manual.json · scans `New/ Applied/ Skipped/` | job_tracker.json/html, tracking.csv, archive_index.csv |
| **scrape.py** | *(2026-08-06: **`_ensure_jobspy()` no longer pip-installs unconditionally.** It skips the install and returns a plain "LinkedIn search is unavailable, `pip install python-jobspy`" note when `sys.frozen` is set or `JOB_AGENT_NO_AUTOINSTALL` is in the environment. A packaged copy has no pip to call, and on a shipped install a surprise 600-second network install is worse behaviour than a sentence telling the user what is off. Unchanged on a normal source checkout.)* *(2026-08-08, profile phase 2: **`_profile_location()`** replaces the literal `'Houston, Texas, United States'` jobspy fallback — the first user's city baked into the code, so a second user's LinkedIn pass silently searched Houston. Reads `profile.json` `search.location` (formatted_address → query → city+state); empty string if unresolvable, which makes jobspy run unlocated rather than in the wrong city. Only reached when the configured `linkedin_24h` URL carries no `location` param.)* hiringcafe.com scraper (Next.js `_next/data` route) + jobspy for LinkedIn. No browser, no LLM. Each hiringcafe listing carries `reqId` (employer requisition_id) as a stable posting identity for repost detection. After a successful scrape, best-effort `locality.refresh_from_files()` geocodes new job cities into geo_cache.json (never fails the scrape) | `jobspy` (ext, lazy), `locality` (post-scrape, lazy) | — | config: search_urls, strategy, skills, skill_aliases, gui_settings | listings.json, scrape_meta.json, geo_cache.json (via locality) |
| **salary_probe.py** | Per-job salary discovery, zero Claude tokens. **Stage 1 `probe_posting`, cleanest source first:** (1) `jobdesc.fetch(url)` → run `parse_pay` on the clean text — this is why a posting that *states* pay but renders it via React (e.g. `job-boards.greenhouse.io`, whose pay range lives in split `<span>`s) now resolves: jobdesc pulls the server-side `content` via the Greenhouse/Lever JSON API (or JSON-LD / generic strip), not the JS shell; (2) `parse_jsonld_salary` off the raw HTML — structured schema.org `JobPosting.baseSalary` with an explicit unit (HOUR/WEEK/MONTH/YEAR → annualized), the most reliable source and the clean path for hourly **contract** rates; (3) the original raw-HTML fetch + `parse_pay` regex, kept as fallback (LinkedIn/Indeed/Glassdoor et al are in `GATED_HOSTS` and skipped without a fetch; still-JS-only sites like hibob miss → user can set pay via `/api/salary-edit`). Stage 2: Adzuna **Jobsworth** salary predictor — skipped if `config/adzuna.json` is absent OR the free-tier quota is spent. Band fallback stays in jobpipe. `--stats` per-ATS hit rate · `--quota` usage · `--json` for the dashboard · `--ids` targets any job in candidates.csv **or** tracking.csv | `jobdesc` (clean-text fetch for stage 1), `payfilter` (**pay sweep at the end of a run, 2026-08-13** — see payfilter.py; skipped for `--ids`/`--json`/`--dry`/`--stats`/`--quota`, so a dashboard one-job probe never deletes rows) | — | candidates.csv, tracking.csv, config/adzuna.json, config/profile.json (via payfilter) | **salary_cache.json**, **salary_quota.json**, candidates.csv (pay sweep only) |
| **payfilter.py** | *(new 2026-08-13)* **Pay floor — `search.salary_min` applied to the RESULT, not just the search URL.** The /setup "Min salary" number previously only shaped the LinkedIn bucket + hiring.cafe `minCompensationLowEnd` (profile_lib), which the boards honour loosely and not at all for a posting that hides pay — so the board still filled with jobs under it. `min_expected()` reads the number (0 = filter off), `top_figure()` parses the CEILING of a range, `resolve()` returns (value, source), `verdict()`/`filter_rows()` decide, `sweep()` rewrites candidates.csv, `note()` formats the log line. **Drops only on evidence:** `_ACT_SOURCES = posted|posting|jobsworth`. A `salary_bands.json` family guess is this pipeline's OWN estimate, so a band-priced job is kept (dropping on it would be the tool arguing with itself), and a job with no figure is kept (most postings hide pay; silence is not a low offer). **Source is read from `salary_cache.json`, never sniffed off the string** — Adzuna values carry the same `" (est.)"` suffix a band price does (307 of 429 cached entries), so string-sniffing would misclassify every one. **Edge = top of range:** `$95K-$130K` clears a $105K minimum; only a job whose CEILING is under the number goes. For Adzuna (a point estimate +/-12%) the top is the deliberately generous read of an uncertain prediction. **Hourly/weekly/monthly rates are unparseable on purpose** (`_PERIODIC`) — $60/hr is ~$125K, and the filter refuses to assume an hours-per-week the posting never stated. **Never drops** applied/tailored/skipped/blocked/queued/bookmarked (a decision made or work spent) — same exemptions as the discovery floor. Dropped rows leave candidates.csv entirely (user's choice over a "Below pay" status), so **both callers print every dropped id + reason**: the row is gone, the log IS the audit trail. Stdlib + deterministic, zero tokens. | `pipelib` | — | candidates.csv, salary_cache.json, config/profile.json | candidates.csv (via `_safe_write_text`; untouched when 0 dropped, so a no-op never moves the mtime) |
| **locality.py** | Geo-aware "is this job local?" gate, zero tokens. Pass 1: `local_keywords` substring (legacy). Pass 2: job's "City, ST" looked up in **geo_cache.json** and haversine-checked against the profile's `search.location` lat/lon within `radius_miles` — so in-radius suburbs ("Spring, TX") get local priority for ANY user's city without listing them. Hot path (`is_local`) is offline/cache-only; the network step (`refresh_cache`, Nominatim via `profile_lib.geocode_city`, capped + throttled, failures cached w/ 30-day retry) runs from scrape.py post-scrape or `python locality.py --refresh`. `--check "City, ST"` explains a verdict. Switch: `strategy.local_geo_enabled` (default on). *(NOTE superseded 2026-08-13: `tests/fixtures/local.json` no longer tracks the LIVE cache. `test_golden_locations` runs under `frozen_config`, reading the committed `geo_cache.frozen.json`, so populating or re-geocoding your real cache can no longer flip a golden and needs no regen. Regen only when the frozen cache or the locality CODE changes.)* Guarded by `tests/test_locality.py` | `pipelib`, `profile_lib` (geocode, lazy) | — | config: strategy, gui_settings, profile · geo_cache.json, listings.json, candidates.csv (refresh scan) | **geo_cache.json** |
| **backlog.py** | **Pending-job cap** (2026-08-05) — the board fills up, the searches pause. Zero tokens, no Claude: it reads three files and does arithmetic. `pending_ids()`/`count()` = every tracked or candidate job that is **not applied, not skipped, not from a banned employer**, deduped by id — deliberately the SAME set `/api/jobs` calls `status == 'pending'` and the same banned-employer drop, because the banner offers to act on "all N" and a count that disagreed with the list under it would act on jobs the user never saw. `state()` = `{count, cap, enabled, over, paused, bookmarked, groups}`; `bookmarked` is how many pending jobs are starred, so the delete confirmation can name them. `cap()` reads `gui_settings.backlogCap` (default **50**, `0` = off; a junk value falls back rather than raising). `pauses(job)` = is this job in a paused group — pure predicate over `job['group']`, so **adding a search task to schedule.default.json holds it automatically** with no edit here. **`PAUSED_GROUPS = ('search',)` only**: the git checkpoint still runs, and the archive job still runs *because archiving drains the board*. **The pause is COMPUTED, never stored** — nothing writes `enabled:false` into schedule.json, so the user's own switches are untouched and the searches resume by themselves the moment the count drops. Both consumers (`scheduler._backlog_state`, `dashboard_server.backlog_state`) fail OPEN: a missing or throwing module reports "not paused", because over-fetching for a day beats a scheduler that silently stops. CLI `python backlog.py [--json|--ids]`. Guarded by `tests/test_backlog.py` | `pipelib` (optional — HERE-relative fallback for a legacy single-folder install), `blocklist` (optional, in `_blocked_check`) | — | tracking.csv, candidates.csv, job_tracker.json, config/gui_settings.json, config/employer_blocklist.json | — (pure read; the cap is a computed verdict) |
| **notify.py** | Push new matches to phone via ntfy; tags a re-listing `[repost]` in the push line (reads the candidates.csv `repost` column). `notifications_disabled()` kill switch (`config/instance.json disable_notifications` or `JOB_AGENT_DISABLE_NOTIFY`) suppresses ALL pushes for a staging copy | `locality` (is_local; inline keyword fallback if import fails) | — | candidates.csv · config: notify, strategy, gui_settings, **instance** | notified.json |
| **backup.py** | Snapshot rotation of tracker/tracking | — | — | job_tracker.json/html, tracking.csv, archive_index.csv | Backups/snapshots/* |
| **migrate_data_root.py** | *(2026-08-06)* One-shot move of the data root INTO the app folder (`<app>/user_data_<INITIALS>`) for the self-contained layout. Dry-run by default, `--apply` to commit; `--dest`, `--from`, `--force`, `--keep-resume-folder`. Copies (skipping `__pycache__`/`cache`/`logs`/lock junk), then **re-reads both sides and compares SHA-256** — `copy2` returning cleanly is not proof, a truncated write on a synced folder reports success. Folds an outside `resume_template/` in **and rewrites `resume.template_dir` to the relative `"resume_template"` in the COPIED profile.json** — without that, `_profile_template_dir()` honours the old absolute path (it still exists) and the app keeps reading the folder it was just moved away from, leaving the copy unused. Repoints `config/instance.json` **last**, so aborting anywhere earlier leaves a working install. **Never deletes from the source**; refuses when the data root IS the app folder, when source == dest, or when dest is inside source; re-running after a successful move exits "Already migrated" | `pipelib`, `profile_lib` (initials) | — | current data root, `config/profile.json` | `<app>/user_data_<INITIALS>/**`, `config/instance.json` |
| **archive.py** | Move old applied jobs out of the active board. **Age is user-settable (2026-08-03):** `archive_age` + `archive_age_unit` (days/weeks/months) on the `monthly_archive` job in schedule.json, read directly by `_sched_age()` (shipped default first, user copy wins — the same overlay `scheduler.load_schedule` does, but WITHOUT importing scheduler: archive.py runs from cron/scheduler/CLI and must not pull in the scheduler's threads for one number). CLI `--older-than N --unit days\|weeks\|months` overrides for a one-off run; falls back to 1 month when neither is set, so the zero-token script path never needs config. Cutoff = later of foundDate/statusDate; bookmarked never archived | `pipelib.cfg` (soft, for the schedule read) | `backup.py` | tracking.csv, job_tracker.json, archive_index.csv, **config/schedule.default.json + config/schedule.json (age only)** | archived.csv, archive_index.csv |
| **tailor_local.py** | *(2026-08-06: **`find_soffice()` gained the two macOS paths** — `/Applications/LibreOffice.app/Contents/MacOS/soffice` and the `~/Applications` twin. LibreOffice.app never puts `soffice` on PATH, so `shutil.which` always missed on a Mac and every Mac silently got the `.docx` with no PDF twin — which is the copy ATS uploads ask for.)* Instant deterministic TEMPLATE tailoring for the on-demand queue (runNow): copy base + soffice PDF → jobpipe update/rebuild; AI-customize jobs left for the queue processor. Cheap-exits when idle (runs every 2 min via scheduler; picks up ids the per-row Tailor button wrote to to_process.json via /api/queue). **Render wiring:** any queued job that ships a `content.json` (inline `content` field, or `New/<Co>/<cid>.content.json`) is built via `render_resume.render()` (lint-gated, `customized=True`); a render/lint failure skips the job and leaves it queued (never writes a broken resume). Jobs without content.json keep the verbatim template copy — identical to before. **AI path (2026-08-03):** a `content.json`-less job whose `action=="customize"` is handed to `llm_tailor.build_content(item)` **only when the Claude Code CLI is present** (`llm_tailor.llm_available()`; 2026-08-04 — this used to read "when an Anthropic API key is configured", before the metered path was removed in favour of `claude -p` on the subscription); the returned dict flows through the SAME `render_resume.render()` lint gate. No CLI → `build_content` isn't reached; CLI present but logged out or over the plan limit → `build_content` returns None. **Both land on the same fallback**: the customize job gets the deterministic template copy / stays for the Cowork agent, exactly as before (optional enhancement, never a dependency). This call site did NOT change when the transport was swapped — `llm_available()`/`build_content()` kept their signatures on purpose. `render_resume` imported **after** `find_soffice` is defined (render_resume imports find_soffice from here — reversed import order = circular). The output `New/<Co>/` folder + `<prefix><cid>.docx` (prefix from `pipelib.resume_prefix()`) use the **short** company slug (`safe_co` = the id's `cid.split('_')[0]`, matching `tracker_id`'s shortened company), not the raw company name | `render_resume` (deferred, post-find_soffice) | `jobpipe.py` (process-queue/update/rebuild), `render_resume.render`, `soffice` | to_process.json, candidates.csv, resume_template/, `New/<Co>/<cid>.content.json` | New/<Company>/*.docx+pdf (short company folder), details/processed/run.json, to_process.json |
| **render_resume.py** | Deterministic content→docx renderer: takes a small `content.json` (company/role/summary/skills_groups/roles with `bullet_ids`+`bullet_overrides`/education/volunteer) and emits `<prefix><Company>_<Role>.docx` + `.pdf` (prefix from `pipelib.resume_prefix()` since 2026-08-08 — no longer a hardcoded owner-specific literal) with the exact base-resume structure. `output_name`/`outdir` run company+role through `pipelib.clean_company`/`clean_role` (+`slug`) so the filename + `New/<Co>/` folder use the SAME short scheme as `tracker_id` — the tailor_local rename target then matches with no rename. *(2026-08-08, profile phase 2: **the header is no longer hardcoded.** `DEFAULT_NAME`/`DEFAULT_CONTACT` were the first user's real name, home address, phone and personal email, used whenever a content.json omitted them — so any other user's rendered resume carried his contact details, as did the `dc:creator` document metadata. Replaced by **`template_header()`**, which reads the name + contact line off the first two paragraphs of the CORE base in `resume_template/` (via `scoring._pick_core_base`, cached; `reset_template_header()` clears it). This is the deliberate design: the user owns their header and puts it in their template — the app customizes an existing resume, it does not author one from scratch. python-docx is optional, so an unreadable/missing template falls back to `profile.json` `identity.full_name` for the name and an **EMPTY contact, which emits NO contact paragraph at all** rather than someone else's details. An explicit `content.json` name/contact still wins. `_CORE_XML` became `_core_xml()` so `dc:creator` follows the same resolution.)* ALL formatting lives in code: font floor 10–11pt clamp, fixed section set (banned headers rejected), min-2-bullets, trailing-group all-or-none, summary-title guard, spacer_lines ≤3. **Unknown bullet_id = hard error** — every bullet must trace to `resume_template/bullets.json` (overrides reword, never add). Renders to a temp dir, **lint-gates itself** (shells out to `lint_resume.py`), and only moves output into place on PASS — exit 1 otherwise. `--extract` reverses a rendered .docx back into content.json (bullets matched to bank ids; untraceable bullet = hard error). Additive: nothing invokes it yet; wiring into tailor_local can come later | `tailor_local` (find_soffice), `lint_resume` (rule constants: BANNED_HEADERS, TRAILING_ROLES, DATE_RANGE, …) | `lint_resume.py` (subprocess gate), `soffice` | content.json (arg), resume_template/bullets.json | New/<Company>/*.docx+pdf (or `--outdir`). **Now wired:** `tailor_local` calls `render()` for jobs with a content.json (verbatim copy is the fallback) |
| **llm_tailor.py** | **OPTIONAL** Claude enhancement for the CUSTOMIZE path (2026-08-03; transport replaced 2026-08-04). `build_content(item)` asks Claude to SELECT `bullet_ids` from `resume_template/bullets.json` (never invent) and write a JD-tailored summary, returning a `content.json` dict that `render_resume.render()` then validates + lint-gates — so a bad AI result never ships. **FLAT RATE, NEVER METERED — the whole point of the 2026-08-04 rewrite.** The transport is the **Claude Code CLI in non-interactive mode** (`claude -p`), which draws from the user's Claude **subscription** usage limits. The former `sk-ant-` Messages-API path was **deleted**, not disabled: it billed per use at API rates, which this project explicitly does not want. There is no API key anywhere in the app any more; authentication is the user's own `claude login`. **`_child_env()` strips `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` from every child process** — Claude Code prefers an API key over the logged-in subscription, so a stray env var would silently move every tailoring run onto pay-as-you-go. **That strip IS the cost guarantee; do not remove it.** `find_claude()` locates the binary (explicit `config/claude_cli.json` `binary` → PATH → per-installer paths), mirroring how `tailor_local` finds soffice. `llm_available()` = binary found; it deliberately does NOT verify the login (that would cost a round trip per queue tick) — a logged-out CLI fails inside `build_content`, which returns None and drops the job to the template copy. **Prompt goes in on STDIN, not argv** (bank + 8k of JD blows the Windows command-line limit) and the system rules are prepended rather than passed via a flag, so it works on any CLI version; **cwd is a throwaway temp dir** so Claude Code doesn't load this repo's CLAUDE.md and start behaving like a coding session. **Zero new hard deps:** stdlib `subprocess` against a CLI the user installed. Config (all optional, all defaulted): `config/claude_cli.json` `{binary, model, timeout, extra_args}`, template `config/claude_cli.json.example`. `python llm_tailor.py --check` locates + pings the CLI, distinguishing "not found" (install it) from "found but call failed" (log in / usage limit). Never raises — every failure logs + returns None. *(2026-08-08, profile phase 2: the system prompt's "You tailor <owner>'s resume…" and "the summary's opening phrase is <owner>'s REAL identity" now interpolate **`_owner_name()`** — `profile.json` `identity.full_name`, cached, falling back to the neutral "the candidate". A wrong name here is worse than none: rule 4 is specifically about not inventing an identity, so telling the model the previous user's name works directly against it.)* Called lazily from `tailor_local._content_for` (customize items only). Surfaced by **`/api/claude-cli`** (binary present?) and **`/api/claude-cli-test`** (shells `--check`, so the Settings verdict and the terminal verdict come from the same code); `setup.py` `_report_claude_cli()` prints the same status in the wizard and **asks for nothing** — there is no secret to store | `pipelib` (cfg, BULLETS, _read_json_quiet), `jobdesc` (JD text, optional), stdlib `subprocess`/`shutil`/`tempfile` | `render_resume.render` (the lint gate, via the caller), **`claude -p`** (the Claude Code CLI, run in a temp cwd with the metered env vars stripped) | resume_template/bullets.json, config/claude_cli.json, config/summary_rules.json, job apply URL (network) | — (returns a content dict to `tailor_local`) |
| **git_daily.py** | Commit + push code & tracking metadata to a per-day branch. *(2026-08-08, profile phase 2: **no hardcoded identity defaults.** `DEFAULT_REMOTE`/`DEFAULT_NAME`/`DEFAULT_EMAIL` were the first user's real GitHub remote, name and email, so a handed-off copy committed as him and tried to push to his repository. Resolution is now code-side `config/git.json` → the profile's `git` block → **empty**, and `main()` treats empty as "not configured": no remote ⇒ no origin added and nothing pushed; no name/email ⇒ `git config` is left alone so the machine's global identity applies. **`_profile_git()` returns name/email ONLY and drops `remote` on purpose** — profile.json resolves through the DATA root and `<data>/config/profile.json` names the DATA repo (`…job_agent_profile_KP.git` on this install), so honouring it would add an origin pointing at the profile repo and push the code into it, the exact hazard `git_identity()`'s docstring exists to prevent. The no-remote path falls through to the SAME local-snapshot branch as a failed push, not an early return: with no remote there is no offsite copy, so the local recovery point matters more, not less. **No behaviour change on the original install** — its repo already has `origin` and a local `user.name`/`user.email`, and both are only set when unset.)* | — | `git` CLI | tracking.csv, config/git.json (identity+remote), profile.json (name/email fallback only) | git branch/remote |
| **dashboard_server.py** | *(2026-08-08: **every scheduled-task warning consolidated into the nav chip + its bubble.** The app had FIVE places to learn something was wrong with the scheduler: this chip, plus four banners stacked down `/schedule` (`#missBanner`, `#whyBanner`, `#blBanner`, `#wdWarn`). Three of those were only visible from `/schedule` — the page you visit *because* something is wrong — so they arrived after the user had already diagnosed it. **`SCHED_PILL_JS` rewritten:** the chip now states ONE number, **missed runs** (`N missed`, or `Nothing missed · next <time>` when there are none), plus a single `⚠` mark (`.swarn`) when something non-missed also wants attention; `.is-error`/`.is-missed`/`.is-idle` still colour it worst-first. It no longer prints `stuck?` / `failed` / `running` / `searches paused` as chip text — five different sentences in one chip made it a thing you re-read instead of recognised. **The bubble carries all four banners' content:** `missedList()` → one `missRow()` per missed task (last-ran · next-run + **▶ Run now**) under the non-obvious cause note; then `runningList()` (plain rows, **filtered to exclude `stuck`**, which gets a full block below — same fact twice otherwise); then `problems()` under an "Also worth a look" divider — the former `WHY.stuck`/`WHY.failed` texts, the paused-searches message, and the watchdog states — each a `.sprob` block tinted by `sev` (`bad`/`warn`/`info`; **`info` never colours the chip**, it is context) with its own action buttons. **Button delegation** in the bubble handles `data-act="board"` (→ `/`), `"wd-go"` (→ `/schedule#watchdog`), `"wd-on"` (POST `/api/watchdog {enable:true, everyMin}` — the SAME endpoint the /schedule switch calls), alongside the existing `.srun` → POST `/api/run`. **`/api/sched-status` now also returns `watchdog`**, via the new **`watchdog_state_cached()`** (45 s TTL over `watchdog_state()`, because `make_watchdog.is_enabled()` shells out to `schtasks.exe` and every open tab polls this endpoint once a minute); **POST `/api/watchdog` calls `watchdog_cache_clear()`** so the bubble agrees with the switch on the next poll rather than up to 45 s later. `shortLabel()` is **removed** (the chip no longer prints a task name). The chip's href drops `&why=` and keeps `?job=` — `/schedule` only spotlights the card now. **Supersedes the 2026-08-03/04/05 pill notes below** wherever they describe `state`-driven chip text, `shortLabel`, or a `?job=&why=` banner on arrival. New `ui.css`: `.ui-nav-sched .swarn`, `.ui-sched-pop .ssec/.sprob/.spname/.spbody/.spacts/.sact`, and the bubble is 360px with `max-height:min(76vh,620px)` + scroll.)* *(2026-08-08: **app mark + name.** New GET route `/favicon.ico` + `/assets/icon/*` serves the icon files out of the CODE dir's `assets/icon/` (basename-only — this is the one route that reads a caller-named file out of `assets/`, so it must not accept a path; 404 on anything missing). `ICON_LINK` is injected into `<head>` next to `UI_CSS_LINK` in `inject_nav()`, and is skipped when the page already declares its own `rel="icon"`. `nav_html()`'s brand is now the 64px rat PNG (`.ui-nav-mark`) + **Top Rat**, replacing the 🗂 emoji + "Job Agent"; every `<title>` in `src/web/` now reads `Top Rat — …`.)* *(2026-08-06: `backlog_state()` now merges **`hourlyOn`** into every backlog payload (`/api/backlog`, `/api/jobs`) via **`_hourly_search_on()`** — is the `radar` task enabled in `sched.load_schedule()`. Defaults to **True** (= on, no bubble) when the scheduler module or the task is absent, so a scheduler-less build never accuses the user of switching off something that does not exist. It rides with the cap because the board's ONE bubble speaks for both ways the searches go quiet, and must read them out of a single response. The nav pill's popover Run now also calls **`window.backlogRemind()`** when the task's `group === 'search'` (see scheduler.py `group`), re-showing a bubble the user dismissed earlier in the visit.)* *(2026-08-05: **the pending-job cap** — `/api/backlog` **GET** returns `backlog.state()` (the SAME call `scheduler._loop` gates on, so the banner can never claim a pause that is not in force), and **POST** takes `{action: "skip-all"|"delete-all", expect: N}`. The ids are re-resolved server-side from `backlog.pending_ids()`, never taken from the request — a board left open for an hour would otherwise act on a stale list — and `expect` (the count the banner displayed) is compared against the live count, answering **409 `{stale:true, count}`** on a mismatch without writing anything. `/api/jobs` also carries **`backlog`** so the banner's number and the board's Pending list arrive in ONE response. `skip-all` → `apply_marks(status='skipped')` and **must pass `bookmarked` through** — apply_marks reads that field off every row it is given and treats an absent one as "not bookmarked", so a bare `{id,status}` row silently un-bookmarks all 50 jobs. `delete-all` → **`delete_jobs(ids)`**: PURE delete, no memory (nothing records the ids, so a later scrape may re-find them), removing the rows from tracking.csv + candidates.csv via `_filter_csv` (header kept) and every id-keyed leftover from job_tracker.json (`job_details`, `notes`, `answers`, `applied`, `bookmarked`, `skipped_jobs`) plus its `to_process.json` queue entry + note — otherwise a rebuild re-materialises a half-job. Aborts on an unreadable tracker rather than clobbering it, and snapshots the four state files to `<data>/Backups/snapshots/<ts>_delete` first via **`_snapshot_before()`** (rooted in DATA — `backup.py` still snapshots beside the CODE, which is not where job_tracker.json lives since the split). `backlogCap` joins the `/api/threshold` key list (default 50). The nav pill gained a **`paused`** branch and the bubble a paused head/body ranked ahead of missed/failed, with no Run-now button — running a search would add to the very backlog the cap is holding. **Also fixed here: `atomic_write` now reads back with `newline=''`.** Without it the read-back got universal-newline translation, so a `csv.DictWriter` payload (CRLF line terminators) never compared equal, the verify loop spun 5× and raised on EVERY CSV — `_rewrite_tracking_csv` had been failing silently since job_tracker.json is written first and the board reads status from there, so tracking.csv had quietly stopped tracking status.)* *(2026-08-05: `/api/jobs` gained **`tailorError`** — ONE field for every reason the tailoring queue produced no résumé, so the board needs no branch per reason. `_tailor_err(jid, has_resume, tname, texists, sm)` resolves it: (1) a RECORDED failure from `tailor_errors.json` (tailor_local), suppressed once the job has a résumé — the file on disk is the newer fact, since the Cowork agent may have built what the script path could not — with `pdf_failed` exempt because that record IS about a job that built; (2) else a PREDICTED `template_missing` — or **`template_none` when `classify` named no base file at all (2026-08-06)** — when the job has no résumé, sits at/above `resumeCustomizeThreshold`. **`_template_for` resolves the base file against `pipelib.TEMPLATE_DIR` (2026-08-06 fix)**, the same folder `tailor_local` copies from. It used to test `P(*base.split('/'))` = `resume_template/` **beside the CODE**, which stopped existing at the code/data split — so every job on the board read `templateExists:false` ("NO TEMPLATE") and predicted a `template_missing` for a file sitting in the data root the whole time; on Windows the `split('/')` also yielded one unusable segment, since `classify` returns a backslash path (the SAME number the UI gets as `CUSTOMIZE_THR`, so tag and Templated/None split can't disagree) and its base file is absent — knowable before the run, and worth saying early since the fix takes longer than the run. Shape `{code, message, detail, at, stage, predicted}`; emitted on the tracking rows AND the candidate rows (which have no `resumePath`, so `hasResume` is read off the yes/no column there).)* *(2026-07-30: **`/api/ghost-flag`** POST — the user's own ghost-job flag. `{id, company, role, flagged}` → `ghostflags.set_flag` (returns `{flagged, count, key, declined, display}`); `{company, decline:true}` → `ghostflags.decline_ban`, recording a turned-down ban offer. `/api/jobs` gained **`ghostFlagged`** / **`ghostFlagCount`** (per employer, keyed via `norm_employer`) / **`ghostBanDeclined`** per job, alongside the heuristic `ghost`/`ghostReasons` they must not be confused with.)* Local web UI (localhost only), multi-page with a shared nav + the shared stylesheet injected on serve (`inject_nav` puts `<link href=/ui.css>` first in `<head>`, skipping it when the page already links it, then the nav right after `<body>`): Jobs `/` (board — also `/cards`, `/table`, `/job_tracker.html`; **one page, two views**, see cards.html) · Settings `/setup` · Schedule `/schedule` · Archived `/archived` · Adzuna help `/help/adzuna` (static `help_adzuna.html`) · ntfy help `/help/ntfy` (static `help_ntfy.html`) · Claude Code help `/help/claude-code` (static `help_claude_code.html`; `/help/claude-key` kept as an alias for old bookmarks) · **`/ui.css`** (the shared design system, served from the CODE dir). **Nav has NO separate Table tab** (`NAV_ITEMS` = Jobs/Settings/Schedule/Archived) — the table is a view of the Jobs board, not a second page, so a tab would be a second route to the same board. **The generated `job_tracker.html` (HTML_PATH, data root) is no longer SERVED** — it baked the rows into the file as a JS array and only refreshed on `jobpipe rebuild`, so it could disagree with the card board between rebuilds; it is still WRITTEN by tracker.py/dashboard_build.py (the Simplify sync reads `SIMPLIFY_APPLIED` back out of it), so nothing downstream changed. Review/skip/bookmark, per-row **Tailor** button (queues one id via `/api/queue`), thresholds/caps, **watchdog toggle** (autostart toggle retired from the UI 2026-08-05); **hosts `scheduler` + autostart/watchdog-reconcile in-process** (all SKIPPED when `config/instance.json disable_scheduler` / `JOB_AGENT_DISABLE_SCHEDULER` set — for staging). Bind port = `--port N` > `JOB_AGENT_PORT` > `instance.json port` > 8765; nav shows an amber `label` badge when set. APIs: `/api/{data,jobs,jobdesc,save,threshold,settings,queue,schedule,sched-status,run,autostart,watchdog,archived,salary-probe,salary-quota,salary-edit,adzuna,anthropic,anthropic-test,data-root,notify-test,profile,geocode,profile-status,blocklist,block-employer,unblock-employer,pick-folder,**backlog**}` (**2026-08-05: `/api/pick-folder`** POST = native OS folder dialog for the Settings Browse… buttons, run in a child process via `pick_folder()`; returns `{ok,path,canceled}` and saves nothing — see the note below) (**2026-08-03: `/api/sched-status`** = status-only twin of `/api/schedule` — returns `sched.status_all()` plus `available`, and **200 with `available:false`** rather than 500 when the scheduler module is absent, so a scheduler-less/staging build silently hides the chip instead of erroring every few seconds. It exists because the **nav scheduler pill** (`SCHED_PILL_JS`, injected by `inject_nav` into *every* page including `/schedule`) polls on a timer and must not re-serialize the whole job config each time. **2026-08-05 — the pill is now the ONLY run-status readout** (the /schedule sections dropped their status pills), so it carries a `<span class="slbl">Scheduled tasks status:</span>` prefix naming what it reports — unlabelled it read as being about the job postings the rest of the nav counts. `.ui-nav-sched .slbl` in `ui.css` dims it and hides it under 760px. **Terminology:** every string a user can read calls a scheduler unit a *scheduled task*, never a *job* (a job here is a job POSTING) — nav tab "Scheduled tasks", `/schedule` `<h1>`, bubble head/footer, `scheduler.run_job` error strings ("unknown scheduled task …", "another scheduled task is already running"; `cards.html runSearchNow()` matches `/unknown (scheduled )?(job|task)/i` so an older scheduler still falls through), and the `description` fields of `config/schedule.default.json`. **Internal names are unchanged** — the config key is still `jobs`, `/api/run` still takes `{job:…}`, `status_all()` still returns `jobs`/`runningJob`/`nextJob`/`stateJob`. The pill renders whatever `state` says and **holds no logic of its own** — `stuck > running > failed > missed > idle > off` is decided once in `status_all()` so the nav and the /schedule page can never disagree about whether something is wrong. **No countdown:** an earlier build ticked "in 5h 47m" down every second on every page — motion without information — so the chip shows the next run *time* (`when()`: today = time only, tomorrow/this week = day + time), and the only number that moves is how long a run has actually been going. **Clicking it opens a BUBBLE, not a page (2026-08-04):** `nav_html` wraps the chip in `<span class="ui-sched-wrap">` (positioned) with a sibling `#uiNavSchedPop`, and the pill JS renders the popover client-side from the `jobs` map `/api/sched-status` already returns — **no new endpoint, no extra request**. The chip has room for one word ("2 missed") and the question it always provokes is WHICH job; that answer used to cost a page load and a scan of six cards. The bubble grows out of the chip (`transform-origin:top right`) and lists every enabled job that is `missed`/`failed`/`running`, each with the fact behind its tag (last-ran + next-run for a missed one, `lastResult` for a failed one, elapsed for a running one) and a **▶ Run now** button → POST `/api/run` → `scheduler.run_job(name, manual=True)` — the SAME manual path the Schedule page uses, then `poll()` repaints chip and bubble together. With nothing wrong it states the next run instead, so a click is never a no-op. Missed rows carry the one non-obvious cause: jobs only run while the dashboard is open. Closes on outside click / Escape (never on a click inside — the Run buttons are in there); a **modifier-click still navigates**, and the footer link **`/schedule?job=<name>&why=<state>`** stays for what a bubble should not do (edit a time, disable a job). Styles are `.ui-sched-wrap` / `.ui-sched-pop` in `ui.css`. That footer link goes to, where `WHY[kind]` prints a plain-language explanation + a **Run it now** button and `.spotlight` rings the card in question; the banner clears itself once the state no longer holds. Poll is 5s while running/stuck, else 60s. Labels are **trimmed at the dash** by `shortLabel()` (`"Hourly scrape - find new jobs and send an alert"` → `"Hourly scrape"`, 26-char cap): schedule labels are written for the /schedule card, where the full sentence reads well, but in the nav they push the tabs off screen — the full text stays in the chip's `title` tooltip, so the config is unchanged and the cards keep their explanations — a dashboard opened a few times a week always has missed runs, and a chip stuck on "5 missed" would never answer when the next run fires. Polls every 15s idle / 3s while running, re-polls on `visibilitychange`, and ticks the visible countdown locally each second off the payload's server `now` — so the browser clock can be skewed without the chip lying) (`/api/jobdesc?id=` = on-demand job-description fetch for the card drawer, delegates to `jobdesc.fetch`; a stored `job_details[id].description` wins **only if it passes `jobdesc.assess`** (2026-08-05 — a scrape can have captured a sign-in wall; junk falls through to a live fetch instead of being served as the job), else it fetches + caches; the response carries `reason`+`message` and the drawer prints the message instead of the page's metadata. `/api/jobs` + the queue list ship stored descriptions to the client through `_readable_desc(det)`, the same gate, so the drawer never renders one directly without it; graceful `ok:false` when a site is JS-only) (**2026-07-29:** `/api/jobs` also returns per job **`resumePdf`/`resumeDocx`** — the résumé's PDF twin + docx, each only when the file exists, checked under the CODE dir *and* the data root, driving the card's preview-vs-download branch — and **`tailorNotes`**, the job's customization notes read back out of `to_process.json → notes` so the drawer can show/edit/clear them. **`/api/queue`** accepts **`remove:[ids]`** (unqueue; applied after the merge) and now **keeps notes for unqueued ids** — inert to every consumer, since they all key off `ids`, and it lets the user come back, edit and re-queue without retyping; capped at 400 entries. Résumé files are served with `Content-Disposition: inline` so the card's iframe renders the PDF instead of downloading it, and fall back to the data root when not found beside the code.) (`/api/jobs` = additive read-only merged job list for the card view `cards.html` at `/cards`: same live stores as `/api/data` but also surfaces `location` from job_details + a derived `Remote/Hybrid/Onsite` tag (falls back to a muted `Location` placeholder when none stored) + **`isLocal`** (`locality.is_local`, offline/cache-only — drives the card board's local filter) + **`queued`** (id present in to_process.json — drives the "queued to tailor" chip) + resolved salary/`salarySource`/`salaryEstimated`/skillMatch/missingSkills/status/bookmarked/note/repost + advisory ghost-risk `level`/`reasons` per job, plus a top-level **`claudeEnabled`** (gui_settings) telling the card UI whether to prompt for tailoring notes, and merges the `candidates.csv` pool (deduped, `candidate:true`); the card UI reuses `/api/{save,queue,block-employer,salary-probe}` for its skip/apply/bookmark/note/tailor/ban/retry-salary actions — no CSV schema change, table board untouched) (`/api/profile-status` = deterministic "is profile.json filled in?" check driving the first-run onboarding: `inject_nav` appends `ONBOARD_JS` to every non-settings page — unconfigured first visit redirects to `/setup?welcome=1`; after "Skip for now" it instead shows a dismissible reminder banner at most once per 24h, timed via localStorage `setupSkippedAt`/`setupRemindAt`, both cleared on save) (`/api/salary-quota` also returns `configured`, driving the salary hover hint; `/api/adzuna` GET returns `app_id`/`keySet`/`configured` — never the raw key — and POST writes `config/adzuna.json`, the same file `salary_probe.py` reads, from the Settings **Adzuna salary API** card) (**2026-08-04: `/api/claude-cli`** GET reports `available` / `binary` / `apiKeyInEnv` via `llm_tailor.find_claude()` — "can this machine tailor unattended?". Cheap: it looks for the executable, it does not start Claude. `apiKeyInEnv` exists to *reassure*, not alarm — `llm_tailor._child_env()` strips the variable, so the plan still pays. **`/api/claude-cli-test`** POST shells `llm_tailor.py --check` (300s cap — the CLI is far slower to start than an HTTP call — `OK  `/`FAIL  ` prefix stripped) and always answers 200 with `{ok,message}`, so a logged-out CLI reports a failure instead of 500-ing the Settings page. **There is no save endpoint and no key**: authentication is the user's own `claude login`. Locals here are `lt`/`binp`, never `cfg` — see the shadowing rule below. **Removed the same day: `/api/anthropic` + `/api/anthropic-test`**, which stored an `sk-ant-` key and billed per use; they existed for a few hours only, and `config/anthropic.json` stays in `.gitignore` so an old checkout can never commit one) (**`/api/agent-prompts`** GET returns the two scheduled-task prompts from `agent_prompts/`, rendered with this user's paths/name (see "Agent prompts" above) — always 200, a missing file yields an empty `prompt` and the Settings card disables itself rather than the section erroring. `/api/data-root` GET reports the resolved personal-data root + WHICH rule picked it (`env`/`instance`/`default`/`sibling`/`code`) + a `pending` path when a saved override hasn't taken effect yet; POST validates a candidate folder (`{check:true}` = validate only), creates it when the parent exists, write-probes it, and records it as `data_root` in `config/instance.json` — **never moves files**, and only takes effect on restart because `pipelib` resolves the root at import; `{clear:true}` reverts to automatic. Helpers `data_root_info()` / `check_data_root()` live in `dashboard_server.py`) (`/api/notify-test` POST shells `notify.py --test` for the Settings **Send test alert** button; a "disabled or no topic" result is reported as a failure, not a success) (`/api/salary-edit` POST = **manual salary override** from the card's Salary row; persists via `jobpipe salary-apply --source manual` (reads as confirmed, always wins the resolve chain); an empty value clears the override back to the auto figure — deterministic, zero-token) (`/api/jobs` also returns **`employmentType`** per job = stored label (persisted on a jobdesc fetch) else `pipelib.detect_employment_type(...)` at display time — retroactive, no schema change; `/api/jobdesc` **detects + persists a small `job_details[id].employmentType` label on a successful fetch**, so the card badge sticks on the board without re-opening) + `/help/adzuna` + `/help/ntfy` (setup pages) + `/archived-resume` (unzips a résumé from its archive zip). `/api/data` also returns a **ghostRisk** map + caveat, a **repost** map + caveat (advisory), a **salaries** map (resolved `{id:{value,source,kind}}` via `jobpipe salaries` — so every row shows a figure and a probed salary survives refresh), and the current **blocklist**. Tracker shows a 👻 on high-risk rows and a 🔁 on recognized reposts (tooltip = last-seen date + prior applied/skipped status + prior note; click for detail), both with "advisory" caveats, plus a per-row 🚫 Ban button; Settings has a Blocked-employers card **Routes added 2026-08-04:** GET `/queue` (serves **`cards.html` in queue mode**, nav key `queue` — the nav gained a **Tailoring queue** tab between Jobs and Settings), GET `/api/queue` (read side of `to_process.json`: ids in order, notes for still-queued ids, `requestedAt`), POST `/api/queue-run` (**no UI caller since 2026-08-04** — the /queue run bar was removed; endpoint kept and working. Marks the queue `runNow` atomically, then `scheduler.run_job("tailor_local", manual=True)` — the manual press reuses the scheduled job so runs stay serialized, logged, and visible on the nav pill; 400 on an empty queue, 409 when another job holds the lock). | `profile_lib`, `scheduler`, `make_autostart` (legacy), `make_watchdog`, `blocklist`, `ghost_risk`, `reposts`, `scoring` (classify → suggested template family for `/api/jobs`), `jobdesc` (on-demand description fetch for `/api/jobdesc`), `locality` (is_local → `/api/jobs` `isLocal`; offline/cache-only), **`backlog`** (pending-job cap → `/api/backlog`, `/api/jobs backlog`) | `jobpipe salaries` (resolved salary map for `/api/data`), `jobpipe salary-apply` (persist a probed salary), `salary_probe.py` (per-job $ Find button) | job_tracker.json/html, tracking.csv, gui_settings, schedule.json, archive_index.csv, archived.csv | **to_process.json** (Tailor queue, +runNow flag, + optional `notes {id:text}` from the Tailor dialog), gui_settings.json, schedule.json, manual marks |
| **tailor_local.py** | **Notes survive the partial rewrite (2026-08-04):** when a run leaves AI-customize ids queued, the `to_process.json` rewrite now carries `notes` across — it used to drop the user's own tailoring instructions for every job still waiting. Zero-token TEMPLATE tailoring (base-resume copy + LibreOffice PDF + tracker update). **Ships DISABLED on the schedule since 2026-08-03**: it only acts on a `to_process.json` queue with `runNow` set, and *nothing sets `runNow`* — `/api/queue` writes `ids` without it and the dashboard Tailor button is owned by the hourly Cowork `job-tailor-queue-processor`. The 2-minute tick was a permanent no-op (`tailor_local: nothing to do (no runNow queue)`). Manual runs only: the schedule page's **Run now**, or `python tailor_local.py --force` **Failures are now RECORDED, not just printed (2026-08-05):** every path that ends without a resume — the named base resume not in `resume_template/` (`template_missing`), **`resume_template/` holding no base resume at all (`template_none`, added 2026-08-06 — `classify` returns an empty `baseResume`, so there is no filename to print and the message names the FOLDER)**, `render_resume` lint rejection (`render_rejected`), copy failure (`copy_failed`), unreadable content.json (`content_unreadable`), an `llm_tailor` exception (`ai_failed`), a soffice PDF failure on a built .docx (`pdf_failed`) — stages a record via `_rec_err` and `_flush_errors` merges them into **`tailor_errors.json`** (`pipelib.TAILOR_ERRORS`) at the end of the run, keyed by job id: `{code, message, detail, at, stage}`. **Every id that BUILT clears its own key** (except `pdf_failed`, which is about a job that did build), so the file holds live problems only and never needs pruning. Before this, a failure printed one console line and the job sat in the queue with the board still showing its old résumé state — the failure existed only in a log. `code` is the stable key the UI switches on; adding a new failure reason needs no dashboard change | `pipelib` (TO_PROCESS, **TAILOR_ERRORS**), `render_resume` (deferred — `render_resume` imports `find_soffice` back from here, so a top-level import is circular) | LibreOffice `soffice` | to_process.json, **tailor_errors.json** | New/&lt;Co&gt;/ .docx + .pdf, tracker, **tailor_errors.json** |
| **scheduler.py** | *(2026-08-13: **`after_steps` — the background tail.** A job may now declare `after_steps` beside `steps`. They run only if `steps` returned `ok`, and they start from `_start_after(name, job)` **after** the `finally` block has written the state row, logged `DONE`, popped `_RUNNING` and released `_JOB_LOCK` — so the task reads as FINISHED while they are still going. Deliberately **invisible**: not in `_RUNNING`, never `runningJob`, no row on the /schedule page, no new key in `status_all()`. The tail records `lastAfterResult` / `lastAfterDurationSec` / `lastAfterAt` **into the job's existing state row** (a row of its own would put it back on the schedule page, which is the thing being avoided) and logs `after-steps START/DONE` to schedule_log.txt. It holds **no `_JOB_LOCK`**, so a slow tail can never delay the next search; the new non-blocking **`_AFTER_LOCK`** (+ `_AFTER` = `{name: t0}`, for the log and the guard only) stops two tails overlapping on the file they write. A tail that loses that race is **skipped and logged**, never queued — salary_cache.json is durable, so the jobs it missed are simply probed next run. Step execution was factored out into **`_run_steps(name, steps, tag='')`**, shared by both paths so they cannot drift on step resolution, logging or the 1800s timeout. **Why:** `salary_probe.py` sat in `steps` and ran ~236s for a 2-of-88 hit rate, holding `runningJob` — and therefore the board's "Searching…" panel — for four minutes after `jobpipe.py candidates` had already written the rows the board renders. Moved to `after_steps` on `radar`, `daily_scrape` and `weekly_sweep` in the same change; `weekly_sweep` now reports DONE at ~69s instead of 305s. Existing user copies of schedule.json need no migration: `USER_FIELDS` excludes step lists, so `load_schedule()` always takes them from `config/schedule.default.json`.)* *(2026-08-06: `status_all()` jobs gained **`group`** (`search`/`upkeep`/`resumes`, straight off the schedule config) so a caller can tell a SEARCH run from any other run without hardcoding task names — the nav bubble's Run now uses it to re-raise the board's paused bubble.)* *(2026-08-05: **the pending-job cap gate.** `_loop` computes `backlog.state()` ONCE per tick and `continue`s past any enabled job with `backlog.pauses(job)` true — i.e. `group == "search"`. Only the SCHEDULED path is gated: `run_job()` itself is untouched, so /schedule "Run now" and the board's empty-state "Run a search" still work, since a press is the user asking for it and the cap exists to stop *unattended* growth. Nothing is written, so the next tick under the cap runs normally. `status_all()` gained per-job **`pausedByBacklog`** — kept separate from `enabled`, which must keep reading ON or clearing the backlog would look like the app switched the searches back on behind the user's back — force-clears `missed` on a held job (a run the app chose to skip is not a catch-up candidate, and it would otherwise light the nav pill amber for the whole backlog), adds top-level **`backlog`** (= `backlog.state()`) so every pill can explain the pause with no second request, and inserts a new headline state **`paused`** ranked `stuck > running > failed > **paused** > missed > idle > off` — above `missed`/`idle` because a chip reading "next run 9:00" while the cap holds that run back would be a lie. Helpers `_backlog_state()`/`_paused_by_backlog()` swallow every failure and report "not paused" — fail OPEN.)* In-process cron (runs only while dashboard open; interval/daily/weekly/**monthly** jobs; misses flagged, not auto-run). `_rotate_log()` caps schedule_log.txt at 1MB / last 500 lines — job steps pipe subprocess output into it, so it grew to 793KB before rotation existed. **Data-root aware since 2026-08-03**: it used to hardcode `HERE/config/schedule.json`, which `cleanup_user_data.py` deletes after the split → `load_schedule()` returned `{}` and the /schedule page rendered **no jobs** (scrapers silently stopped). Now reads via `pipelib.cfg()`, writes via `cfg_write()`, and falls back to shipped `config/schedule.default.json` so the job list is never empty. **`load_schedule()` overlays, it does not choose**: label/description/type/steps always come from the defaults (code-owned — `description` is the plain-language summary the card renders under the job name), and only `USER_FIELDS` (enabled, at, days, weekday, day, interval_minutes, window_start/end, **archive_age, archive_age_unit**) are read back from the user copy — `archive_age*` is a job *parameter* rather than a time, but the user owns it (archive card of /schedule), so it must survive the defaults merge identically; `archive.py` reads it straight out of schedule.json — a user copy is written once and never re-seeded, so without this a reworded label or a changed step list would never reach an existing install. **Run-progress 2026-08-03:** `_RUNNING` became a **dict `{name: start_time}`** instead of a set (truthiness and `name in _RUNNING` are unchanged, so no caller moved) purely so the UI can show live progress — `status_all()` now emits per job `startedAt`/`elapsedSec`/`etaSec`/**`stuck`**/**`failed`** plus a single pre-computed headline **`state`** (`stuck > running > failed > missed > idle > off`) with `stateJob`/`stateLabel`/`stateDetail`/`stateElapsedSec`, so every caller shows the same answer instead of re-deriving it from the jobs map. **`failed`** closed a real hole — the status payload carried `lastResult` but nothing surfaced it, so a nightly scrape that died still showed a calm "next run" everywhere. **`stuck`** = elapsed > `max(prev × STUCK_FACTOR(3), STUCK_FLOOR(600s))`; advisory only, the run is **never killed** (a slow network and a hung scrape are indistinguishable from here) and the step's own 1800s subprocess timeout is still the real backstop; the floor exists because `tailor_local` finishes in 0.2s on an empty queue and would otherwise be "stuck" two seconds in. **`_recover_interrupted()`** (called from `start()`) closes the other hole: `run_job` writes its result in a `finally`, which does not cover the app being closed or killed mid-run — the previous run's `ok` then stood forever and a job dying half way every night looked healthy. It re-reads the tail of schedule_log.txt for a `<name>: START` with no later `DONE` and records `lastResult = INTERRUPTED`, only when the orphaned START is newer than the recorded `lastRunAt` (so a later good run is never clobbered); idempotent, no new state file, zero-token | `pipelib` (optional — falls back to `HERE` paths if the import fails, keeping legacy single-folder installs working) | `scrape.py`, `jobpipe.py candidates`, `notify.py`, `salary_probe.py`, `git_daily.py`, `archive.py`, `tailor_local.py` | `<data_root>/config/schedule.json` → **fallback** `config/schedule.default.json`; scheduler_state.json (legacy `HERE` copy read once for lastRunAt history) | `<data_root>/`: schedule.json, schedule_log.txt, scheduler_state.json |
| **profile_lib.py** | Profile schema, geocode, config generators, **named-profile storage** | — | — | profile.json, config/profiles/*, config/active_profile.json | search_urls, skills, skill_aliases, summary_rules (generated only if missing), named-profile copies |
| **setup.py** | One-time setup wizard (CLI) | `profile_lib` | — | — | profile.json (via profile_lib) |
| **ui.css** | *(2026-08-08 — later the same day: **the scheduler bubble absorbed `/schedule`'s four banners**, so it grew the styles to hold them: `.ui-sched-pop` is 360px wide with `max-height:min(76vh,620px)` + `overflow-y:auto` (four banners' worth of message must be able to run out of room without falling off the window), `.ssec` is the small-caps divider above the folded-in warnings, `.sprob` + `.sprob.bad`/`.warn` are the warning blocks tinted by severity, `.spname`/`.spbody`/`.spacts` their parts, and `.sact`/`.sact.primary` the buttons each one carries. `.ui-nav-sched .smiss::after` (the `·` separator) is **gone** and replaced by `.swarn`: the chip's text is now only a missed-run count, and anything else that needs attention is one `⚠`. See `dashboard_server.SCHED_PILL_JS`.)* *(2026-08-08: `.ui-nav-brand` is an inline-flex row and gains `.ui-nav-mark` — the 20px app icon — so the mark sits on the brand text's baseline; the narrow-screen rule that hides `.brand-text` now leaves the mark behind rather than an emoji.)* **Shared design system for every page** (added 2026-07-29). One set of CSS custom properties — surfaces, ink, lines, accent, semantic colors, radii, shadows, type scale, `--nav-h` — plus base element styles, the `.ui-nav*` bar, `.ui-btn` / `.ui-input` / `.ui-card` / `.ui-chip` / `.ui-table` primitives, and the shared responsive breakpoints (760px / 420px). Served by `dashboard_server.py` at `/ui.css` and injected into `<head>` by `inject_nav`; each page also links it explicitly. **Why:** the pages had drifted into three unrelated themes (light board+settings, dark-navy schedule, beige-parchment archived) under a near-black nav, and only cards.html had a single breakpoint. Each page now keeps its historical variable names as ALIASES onto these tokens (e.g. schedule's `--fg` → `--ink`), so colors moved without touching any page's rules or JS. Palette is deliberately low-glare: off-white surfaces rather than pure `#fff`, soft-slate body ink rather than near-black, desaturated accent — all still ≥4.5:1 WCAG AA. **Edit colors here, not in a page.** **2026-08-03:** added **`.ui-nav-sched`** — the live scheduler chip at the right-hand end of the nav (`.sdot` status dot, `.seta` countdown, `.smiss` missed-count prefix; `is-idle`/`is-running`/`is-missed` states, and a `uiSchedPulse` pulsing ring rather than a rotating spinner, which is unreadable at 8px). Markup is written by `dashboard_server.SCHED_PILL_JS`; styling lives here like the rest of `.ui-nav*` | — (served by `dashboard_server.py`) | — | — | — |
| **cards.html** | *(2026-08-13: **`watchSearch()` waits for the JOBS, not for the task.** It used to stop only when `/api/sched-status` reported `runningJob` clear, which is a different (and much later) moment than the one the user is waiting for — a search task ends with work that ENRICHES postings already on the board, so "Searching…" stayed up minutes after the board could have been drawn. It now reads **`backlog.count` off the SAME `/api/sched-status` payload** (no new request, no new server work — `backlog._pending()` counts tracking.csv + candidates.csv, the exact two files `/api/jobs` merges), takes the **first reading as the baseline** and ends the wait on **any rise**. The old `seen && !running` test stays as the fallback for a run that adds nothing, as does the `tries > 100` (~5 min) ceiling. The baseline is set on the first reading that actually arrives, so a failed first poll costs the arrival test but never mis-seeds the baseline to a post-arrival count. When the board is drawn while the task is still running it toasts **"Jobs are in. Pay is still loading."** — the one case where a blank pay chip means "not yet" rather than "not found". Paired with `scheduler.py`'s `after_steps` tail in the same change; the section comment above `RUN_JOBS` no longer claims `/api/run` chains into salary_probe.)* *(2026-08-08: `MOTTO` is now "Stay ahead of the pack, be a top rat."; `navHeight()` identifies the injected bar by its `.ui-nav` CLASS instead of matching "Job Agent" in its text — the rename would have failed that test silently and dropped the drawer to the 44px fallback.)* *(2026-08-06: **app mark + motto in the empty state.** `EMPTY_ART` is no longer the generic stacked-cards line drawing — it is the Job Agent mark (cyborg rat, lightning-bolt tail) inlined as SVG: body/ear `currentColor` (`--muted`, via `.emptyart`), visor + bolt `var(--amber)` so the accent stays inside the ui.css token set, circuit trace + nose knocked out in `var(--surface)`. `.emptyart` opacity moved `.32 → .5` because the two-tone drawing needs the extra weight. A new `MOTTO` const ("A rat race is still a race — be a winner.") renders as `.emptymotto` at the bottom of `.emptybox`, below the buttons and the `.emptynote`. **No new state, no API, no script touched** — pure presentation, and `#empty` lives outside both view containers so card view and table view get it from the same markup (card/table parity holds). The standalone icon files are `assets/icon/job-agent-icon.{svg,ico}` + `-{16,32,48,64,128,256,512}.png`; nothing in the codebase links them yet.)* *(2026-08-06: **the paused bubble is dismissable, and speaks for a second cause.** The paused state's primary button is now **Dismiss** (`dismissBacklog()` → `BL_DISMISSED`) in place of *Review them*, which only ever scrolled to the pending list the bubble was already sitting on top of; **Skip all / Delete all are unchanged**. The dismiss is in-memory ONLY — it dies on the next page load, because what it hides is still true and a stored dismiss would turn "not now" into permanent silence. `backlogRemind()` (exported as `window.backlogRemind` for the nav popover, which is not in this file) clears it and scrolls the bubble back whenever the user asks the searches to DO something: `runSearchNow()` on this page, and a **search-group** Run now in the nav bubble. New third trigger — **`searchIdle()`**: `hourlyOn === false && !count`, i.e. an empty board with the hourly search switched off. Same amber bubble and same title (the silence is identical from the user's side), body naming the real cause, actions **Scheduled tasks** + Dismiss — there is no board full of postings to skip or delete. The near-cap soft nudge keeps *Review them* and is NOT dismissable: it has nothing to decide yet.)* *(2026-08-05: **the backlog banner** — `#blbanner`, rendered by `renderBacklog()` from the `backlog` block on `/api/jobs`. It sits ABOVE the toolbar, so BOTH views get it from one piece of markup with nothing to keep in sync (the card↔table parity rule). Three states: hidden under 80% of the cap; a **soft nudge** ("40 of 50", `.blbanner.soft`, Review-them only — **no destructive buttons on a board that is still working**); and the **paused** banner, which names the count and the cap, promises the searches resume on their own, says the scheduled tasks were NOT switched off, and offers Review them / Skip all N / Delete all N. `backlogNote()` holds both confirm strings so the dialog uses the banner's own words; the delete one names how many are **bookmarked** (`state().bookmarked`), since "all" is exactly what would take them. `backlogAct()` sends `expect` = the count on screen, so a board left open cannot act on a stale list, and `BL_BUSY` swaps the buttons for "Skipping…"/"Deleting…" so neither can be double-fired. `showPending()` clears the search box AND the filters before switching to the Pending list — a leftover filter would show fewer jobs than the banner just claimed. Hidden entirely in `QUEUE_MODE`: the cap is about the search board, and its buttons act on jobs that page does not list.)* The job board — **ONE page rendering TWO views** (2026-07-29): the card column and the classic table. Served at `/`, `/cards`, `/table` and `/job_tracker.html`. Both views render from the SAME `currentList()` (one filtered+sorted array off `/api/jobs`) and call the SAME action handlers and detail drawer, so they can never disagree about the data and switching is a client-side toggle with no reload and no rebuild. `VIEW` ('cards'|'table') starts from the URL path (so `/table` stays a working bookmark), else the last choice in `localStorage.board_view`, else cards; `setView()` rewrites the URL via `history.replaceState` and a `popstate` listener keeps back/forward honest. `render()` dispatches to `renderCards()` or `renderTable()`. **Table view:** 9 columns (Company/Role/Found/Location/Salary/Match/Status/Resume/Actions), `body.view-table` widens `.wrap` to full width; clicking a column header drives the SAME sort state as the dropdown (`initHeaderSort`/`syncHeaderSort`), row click opens the shared drawer, and `markOpenCard` highlights either a card or a row. **Detail pane in table view = an inline expander (2026-07-30):** instead of overlaying the table from the right, the SAME `#drawer` element is re-parented into a `tr.rowexp > td[colspan] > .expwrap` inserted directly under the clicked row and slid open with a max-height transition (`dockDrawer`/`undockDrawer`/`revealRow`; `placeDrawer` picks fixed-pane vs docked by `VIEW`, `closeDetail` animates it shut and returns the drawer to `<body>`, `renderTable` parks the drawer on `<body>` before wiping `#tbody` and re-docks silently after — closing the pane if its job fell out of the filter). Re-parenting rather than cloning is what keeps every id, handler and the résumé/JD toggle identical across views; a row click on the already-open row collapses it, and the right-hand scrim is gone. Card-specific behavior below is unchanged. **Layout (2026-07-29): ONE left-hand column of mini cards** (`--listw`/`--gutter` CSS vars size the column; `.wrap` is exactly that wide and the detail pane's `left` is derived from the same vars, so the pane can never cover a card) with the whole rest of the window reserved for the JD/detail preview; a `.preview-hint` fills that area until a card is picked, and the open card gets an `.is-open` outline (toggled directly via `markOpenCard`, not a re-render, so the list keeps its scroll position). Under 900px the column goes full-width and the pane becomes a full-screen overlay. Cards are bigger/mobile-readable (16.5px company, 15px role, 12.5px chips) and carry company · role · **found date** · Remote/Hybrid/Onsite · 📍local · **employment-type badge when not full-time W2** (Contract/Part-time/Temp/Intern…) · pay · skill-match % · **tailoring state** (`⏳ queued to tailor` from `/api/jobs` `queued`, else `✓ resume ready` when a resume exists — this replaced the old `✎ untailored` chip, which only restated the filter). **Job-detail chips are white-filled with a colored border + text (2026-07-31, was soft-tint fills)** — `.chip.remote/.hybrid/.onsite/.local/.pay/.queued/.done` and the skill-match chip's inline style (`matchChip()`: still green/amber/red-banded by score, now `background:#fff` instead of a tinted fill) all moved to the same white base. A row of 6+ solid pastel chips read as "badge soup"; white quiets it while the border/text color keeps each type scannable. **Deliberate exception:** `.chip.emp` (employment-type: Contract/Temp/etc.) and `.chip.repost` stay solid-filled — those two are meant to interrupt ("not a normal W2 role", "seen this listing before"), not blend in as one more attribute chip. The **toast sits at the TOP** (under the nav), not the bottom — at the bottom it landed on the drawer's action row and hid the buttons the user had just pressed, exactly when they wanted to see the new state. Corner actions: 📝 note · ☆ bookmark · **Status ▾** (see the status control below); clicking a card opens the right-hand detail pane (in-window preview, LinkedIn/Indeed-style, positioned below the sticky nav) with exact locations, found date, repost note, salary + hover explaining the estimate source + a **↻ Retry salary lookup** button (`/api/salary-probe`) + a **✎ edit** control that manually overrides the salary (`/api/salary-edit`; blank clears it), an employment-type badge when the role isn't full-time W2, skill-match bar, missing skills, the **questionnaire** (see below), the job description (stored one if present, else an on-demand **⬇ Load full description** button that calls `/api/jobdesc` → `jobdesc.fetch`, with a "view original posting" fallback; when the fetch comes back unreadable `loadDesc` prints `d.message` — the plain-English reason from `jobdesc.assess`, in a `.desc-empty` box with the posting link + ↻ Try again — rather than the sign-in wall / redirect blob / nav chrome the site actually returned, and when `d.message` arrives WITH text it renders as a `.desc-notice` bar above it, e.g. a posting that has been taken down), a private note field, and Apply / **Status ▾** / Tailor resume / 👻 ghost-flag buttons. **Apply** opens the posting and remembers the click (browser localStorage) rather than auto-marking — next open shows an in-card "did you apply?" Yes/No banner. **Ban/Unban** uses a styled confirm modal (are-you-sure + optional reason on ban); banned jobs (`blocked` via `blocklist.norm_employer`) move to a **Banned** filter and drop out of the other lists — **except an employer banned in this session** (`JUST_BANNED`), whose jobs stay exactly where they are, struck through with a red diagonal (`.card.is-banned::after`; the table's idiom is a red line-through on company + role) . A ban is a snap judgement people reverse seconds later, so the card must not vanish out from under the cursor; it drops off on the next reload. **Status ▾ — ONE control for every status move (2026-07-31):** the board used to carry a separate button per transition (👎 skip · ✓ mark applied · Move to pending · Un-skip · Ban · Unban), several of which were the same move under two names — the applied card's "Move to pending" and the skipped card's "Un-skip" both just set the status to pending — and WHICH buttons you saw depended on the status the job was already in, so the control moved under the cursor as you worked down a list. `statusBtn(j[,'big'])` now renders one button in **all three places a job is drawn** (mini-card corner, the table's **Status column** — the read-only chip there BECAME the button, which is the table idiom for the card's corner control, so the status actions left the table's action cell entirely — and the detail card's action row). It **wears the job's current list as its label + tint** (so it is the status readout as well as the control) and `openStatusMenu()` drops a menu of the four lists from `STATUSES` (banned · pending · applied · skipped, in the tint-rank order), current one greyed and marked "· current" rather than hidden. `statusOf(j)` is the single reader — a `blocked` job reads as `'banned'` whatever its underlying status (Banned is the list it actually appears in), and **any unrecognized/missing status falls back to `'pending'`** rather than rendering a status nobody can name. `setStatus(id,to)` is the single writer, so queue-cleanup, the ban/unban side effects and `afterStatusChange` can't drift apart per-button; `requestStatus(id,to)` is what the menu calls and adds the one confirmation: **moving a BANNED job to any other list necessarily unbans its employer** (a job can't sit in Pending while its company is blocklisted) — a company-wide effect from a single-job click, so it asks first, naming the other postings that will return too, then `setStatus` calls `unbanEmployer(j, /*quiet*/true)` and clears `blocked` on every job of that employer before setting the status. Moving TO banned routes into the existing `doBan` reason prompt. **Résumé status bubble — ONE control replacing a per-view button cluster (2026-07-31):** the mini card's résumé indicator (`tailorChip`, blank unless a résumé already existed) and the table's Resume column (a cluster of 📄 View / ✎ re-tailor / ✕ unqueue / a bare Tailor button, whose Tailor button — Claude assistant OFF — queued the job the instant it was clicked, a status readout doubling as a silent write) are now the SAME `resumeBubble(j)` control in both listings: a plain text pill (no icon) reading **Tailored / Templated / None / Queued** — the exact same label + lookup (`F_LABEL[resumeState(j)]`) the Resume filter chips use, so the bubble and the filter can never disagree, and "None" replaced "Undetermined" in both places together. `resumeCell(j)` (table) is now a one-line wrapper around it; card ↔ table parity by construction rather than by two implementations staying in sync. Click routes through `resumeBubbleClick(ev,id)` and never queues by itself: **queued** → the bubble wears a **▾** and opens the SAME `openTailorMenu(ev,id)` the drawer's split button uses — ✎ Edit instructions · ⏳ See the tailoring queue · ✕ Remove from queue (2026-08-04). Queued is the only résumé state with anything to decide, and the move people make most while scanning is taking a job back OUT, which used to cost a click into the big card + a scroll to the instructions box + a click there; it briefly opened the filtered queue view directly instead, which answered "what else is in there?" but still made unqueueing a trip through the drawer. Unqueueing stays a MENU ROW, never the bubble's own press — a status readout must not write by itself. The other states have nothing to choose, so they stay plain (no caret); **tailoring ERROR** (2026-08-05) → the bubble turns red and reads **⚠ Error**, and a click opens `showTailorError(id)`: a one-button dialog with cause → what exactly failed (`detail`) → the FIX (`ERR_FIX`, since every one of these is fixed outside the board) → where the job stands now. **Error outranks Queued**, because most failures leave the job queued and a "Queued" bubble hides the reason it is still sitting there; `resumeState`/`F_LABEL` are deliberately untouched, so this is a display rank on top of the filter lookup and the Resume filter chips keep meaning what they meant. The same tag (`errTag`, class `.errtag`) appears wherever a résumé FILE NAME would print — the big card's résumé row and the /queue table's Resume cell — plus beside the path when the résumé exists but its PDF twin failed. It REPLACED a paragraph of prose in the big card that covered only ONE reason (a missing template) and that the mini card and table never showed at all, so those views said "Templated" for a job whose template does not exist. `openConfirm` gained **`infoOnly`** (hides Cancel — nothing to decide) and renders the message `pre-line`; **has a résumé** → **opens the file in a NEW TAB** (`openResumeTab(id)` → `resumeHref(j)` = `/`+`resumePdf || resumeDocx || resumePath`, PDF twin preferred; 2026-08-05). It used to toast the file name, which repeated what the tag already said and left the file two clicks away inside the drawer — reading the tag and reading the résumé are one intent, so they are one click. Still read-only (a new tab writes nothing); preview-in-pane and the `.docx` download still live in the big card's résumé row. The drawer header's **`tailorChip` ✓ tailored** is now a `<button class="chip done">` calling the SAME `openResumeTab`, so the tailored tag behaves identically in mini card, table and big card. `resumeHref` is the single file-URL lookup the bubble, the chip and the preview pane share, so they can't point at different files; **neither** → `openConfirm` asks "Tailor a resume for this job?" and only on yes calls `promptTailor(id)` — the SAME landing spot the card's own Tailor button uses (opens the drawer, scrolls/focuses the tailoring-instructions box) — so queuing stays a deliberate press inside the drawer, never a side effect of reading a status bubble. The Resume filter chips (`✓ Tailored`/`📄 Templated`/`❔ Undetermined`→`None`/hourglass **Queued**) also **dropped their icons** in the same pass, text-only now, matching the bubble. **Toolbar (2026-07-29, filters reworked 2026-07-31):** search · status filter · sort · **a `Filters ▾` button + grouped popover panel** (`.fwrap`/`.fbtn`/`.fpanel`, `toggleFilterPanel`/`setFilterPanel`). This replaced the always-on `.filterbar` strip — 8 loose chips, 2 floating group labels and 3 `.fsep` hairlines camping on the page whether or not anything was filtered, with a chip's group inferable only from which side of a hairline it sat on. **Nothing is hidden:** the button carries a count badge (`#fcount`) and goes `.active`, and every ON filter renders as a **removable pill** in `#factive` under the toolbar (`syncFilterChips` builds it in declared-group order, click = `toggleFilter` off). Panel closes on outside click / Escape, **not** on a chip press (filters are picked in combinations). THREE groups now: **Distance** — a single **📍 Near me** toggle off `/api/jobs` `isLocal` (renamed from "Local"/"Local only": sitting beside Remote/Hybrid/Onsite it read as a fourth *work arrangement*, i.e. an onsite job, when it actually filters on where the EMPLOYER is — a remote job based nearby still passes) · **Work arrangement** (Remote / Hybrid / Onsite, OR'd, off `locationType`) · **Resume** — **✓ Tailored** / **📄 Templated** / **❔ Undetermined** / **⏳ Queued**. The first three come off `resumeState(j)`, which partitions the board: `hasResume`/`resumePath` → tailored, else `skillMatch >= CUSTOMIZE_THR` → templated (the deterministic `resume_template/` path, same test the detail card's TEMPLATE badge uses), else undetermined. **`queued` moved INTO that group (was its own AND'd group, `TAILOR_F`, now deleted)** — it is OR'd with the three states, so "Queued + Tailored" now means *queued **or** already tailored*; the old AND reading described jobs both queued and tailored, a combination that barely exists since a job is queued precisely because it has no usable resume yet. `queued` is orthogonal to the other three (a job can be queued in any state), which is why selecting all four is not "no filter" the way all three alone is. Saved sets are migrated on load (`ready`→`tailored`, `nonlocal` dropped) and the localStorage key is unchanged (`board_filters`), so an existing `queued` selection survives the regrouping — chips inside a group are OR'd, groups AND'd, so any combination is reachable; chip rows are `.frow`, **not `.row`** (the drawer owns a global `.row`); a **back-to-top** button (`.totop` / `backToTop()`, shown past 400px of window scroll, one control for both views since the window is the scroller in each) · state in `FILTERS` (localStorage `board_filters`), applied in `currentList()` so **both views** filter identically · **filters from the URL (2026-08-04):** `applyUrlFilters()` runs at init (BEFORE `syncFilterChips`) and reads `?filter=<comma-separated keys>&status=<list>&q=<text>`, making any filtered board a copyable/bookmarkable link and giving `openQueueView()` somewhere to point. When `filter` is present the URL **REPLACES** the saved set rather than adding to it — a link to the queue must never arrive pre-narrowed by whatever the last visit left in localStorage ("queued + Remote" would silently show an incomplete queue) — and then persists like a chip press. `status` matters as much as `filter` (a queued job can be applied or skipped, and the default Pending list would hide it), which is why `queueUrl()` sends `status=all`. Unknown keys/values are dropped, so a stale link can only ever show LESS filtering, never an error; `setView()`'s `replaceState` drops the query string on a view switch, by design (the filters are already in memory) · sort (**Found date default**, Skill match, **Job title**, Pay, Company) · a **↑/↓ direction toggle** that applies to whichever sort is active (one ASC comparator per field × `SORTDIR`; the button spells out the meaning per sort — "newest first" vs "A → Z"). **Tailor resume** posts to `/api/queue`; when `/api/jobs` reports `claudeEnabled` it no longer opens a modal — `promptTailor()` opens the job and drops the cursor straight into the **tailoring-instructions box inside the JD pane** (`{notes:{id:text}}` → to_process.json → `process-queue` `userNotes`); with the assistant off it queues silently. **Résumé preview overlay (2026-07-29):** the drawer's Resume row shows the résumé's **path** as the click target (`.respath`) plus 👁 Preview / ↗ New tab / ⬇ .docx; `openResume()` lays a `.respane` (PDF in an iframe) **over** the job description inside the same `.dstage`, and a `📄 Job description | 📑 Résumé` toggle in the drawer header (`showJD()`/`syncDToggle()`, `RES_OPEN`) flips between them — the state survives a drawer re-render. Preview needs the **PDF twin** (`resumePdf`); a docx-only job gets a download panel instead of a broken viewer. **Two boxes, deliberately unalike (2026-07-30).** The private job note is a **yellow note bar** (`.notebar`, **in flow** inside `.dfoot`, directly ABOVE the `.actions` row — it was a floating bottom-right sticky square for a day; a bar reads the note without covering the job description) with three states in `NOTE_STATE` (`setNoteState`, persisted in localStorage `board_note_state`): **mini** = a one-line strip whose `.snpeek` shows the note text itself, ellipsized (the square's tooltip-only peek is gone), **open** = the same bar grown to hold the textarea, with **−** collapse + **×** hide, **hidden** = gone except an inline `.snpin` "📝 Show note" that brings it back (hiding must never lose the note). It goes deeper yellow (`.has`) when a note exists. `syncNoteBar(val)` updates the strip + `.has` on save, because a note save deliberately does NOT re-render the drawer (that would steal focus mid-typing). **Tailoring instructions** (`to_process.json → notes`, surfaced as `/api/jobs` `tailorNotes`) are `tailorInstructions()` → a `.tnbox` **inside the JD pane, directly under Missing / flagged skills** and above the questionnaire it pairs with — that box IS the input (the old confirm-modal notes prompt is gone), carrying ⚡ Queue for tailoring / 💾 Save for later, or 💾 Save instructions / ✕ Remove from queue once queued, plus 🗑 Clear — the box is where queueing lives, which is why the button beside it has no menu. Rendered **only when `claudeEnabled`** (nothing reads them otherwise) **and** the job is queued, has saved instructions, or the user just pressed Tailor (`TAILOR_PROMPT`). Naming is load-bearing: they were called "notes" and got confused with the job note. **Not-available notice (2026-08-04):** when `/api/claude-cli` reports `available:false` (the Claude Code CLI isn't on this machine), an amber `.tnkey` strip sits directly above the `.tnbox` saying the queue will copy the template rather than act on these instructions — and that the notes are still saved — with a link to `/help/claude-code`. **Informational only, no inline fix:** the repair is `claude login` in a terminal, and there is no key to paste (tailoring runs on the user's plan). It lives in the shared drawer, so the card and table views get it identically — no per-view markup. `AI_KEY` is fetched by `loadAiKey()` alongside `load()` but on its own request, so a slow or failed check only hides the notice; it never delays the board. **Tailoring** (`tailorSplit`/`tailorState`): any job that isn't queued — with or without an existing résumé — gets ONE plain **✎ Tailor resume** button that runs `promptTailor` (opens the job, switches off the résumé preview, scrolls the instructions box into view, focuses it and flashes it); **queueing itself happens in that box**, so there is no menu to choose from. A **queued** job becomes *⏳ Queued* + ▾, and its primary press **toggles the menu** (press again to close it) holding three options: ✎ Edit instructions · **⏳ Go to the tailoring queue** (2026-08-04 → `openQueueView()` → **`/queue` in the SAME tab** — the queue became its own page, so `queueUrl()` no longer builds a `?filter=queued` board URL and nothing spawns a tab the user has to close) · **✕ Remove from queue** → `/api/queue {remove:[id]}`. **This one menu is what EVERY "Queued" tag on the board opens** (2026-08-04): the split button here, the mini-card/table `resumeBubble` in its Queued state, and the drawer header's `queuedChip` — which became a `<button class="chip queued">▾</button>` for it (CSS `button.chip` only undoes the browser's button defaults so it still draws like the span chips beside it). One tag, one menu, wherever the word is written, so quick-unqueue is one press from the scanning views instead of a trip through the drawer. Every element that opens it is in the outside-click guard's exclusion list (`#btnmenu`, `.btnsplit`, `.statusbtn`, `.resbtn`, `.chip.queued`) so its own press can't be read as the click that closes it. The menu is `claudeEnabled`-aware — with the assistant off the Edit item is replaced by a disabled "Instructions are off" line, so no item can silently do nothing. Wording rule: it's "Tailor" and "Queue", never "re-tailor"/"re-queue". **Never interpolate note/instruction text into a menu `onclick`** (it broke the handler the moment a note contained a `"`); handlers take the id only and `promptTailor` reads the saved text itself. **Skip / mark-applied / ban auto-dequeue (2026-07-30):** all three mean "I'm done deciding about this job," so a job sitting in the tailoring queue comes back OUT of it automatically — `setStatus` calls `dequeueSilently([id])` whenever the target status is anything but pending (never on the way back TO pending), and `banEmployer` calls it for every OTHER queued job from the same employer too (a ban is company-wide). `dequeueSilently` is the same `/api/queue {remove:[…]}` call as a manual unqueue, so the **tailoring instructions survive** — if the status gets reversed and the job is queued again, nothing needs retyping. The toast says so inline ("Skipped · removed from tailoring queue") only when there was something to remove. **Employer-name tags + the user's ghost flag (2026-07-30):** `nameTags(j)` renders the **banned** tag and the **👻 ghost ×N** tag beside the EMPLOYER NAME in all three places the name appears (mini card `.co`, drawer `.dhead .co`, table `.t-co`) — the banned chip left the card's chip row and the table's flag strip, where it was competing with pay/match and got skimmed past. `ghostBtn(j)` (mini-card corner, `.qbtn.gflag`), a `👻 Flag ghost risk` button in the drawer action row, and a `.t-acts .gf` button in the table's action cell all call **`toggleGhostFlag(id)`** → `/api/ghost-flag` → `ghostflags.set_flag`. A flagged card/row goes **ghost gray** (`.card.is-ghost` / `tr.is-ghost`) and NOTHING else changes — status, queue and filters are the user's call (his instruction). The button is `.gflag`/`.gf`, **not `.ghost`** — that class is the heuristic scorer's icon and carries `cursor:help`. On the **2nd** flag for one employer `offerBanAfterFlags()` opens the confirm modal (→ `banEmployer(j, reason, 'ghost')`, which now takes a **tactic**); its **Cancel button** is the only path that fires the new `openConfirm` **`onCancel`** hook → `declineGhostBan()` → `/api/ghost-flag {decline:true}`, so it never asks about that employer again. **Escape / scrim-click deliberately do NOT count as a no** (`closeConfirm(answered)`) — a stray Escape must not silently mean "never ask me again". **Auto-advance on a status change (2026-07-30):** skip / mark-applied / un-bookmark move a job into a different status list, so under any filter but "All" the open job drops out from under the pane. `listPos(id)` captures the job's index in `currentList()` **before** the status is mutated and `afterStatusChange(id, pos)` (the tail of `setStatus`/`toggleBookmark`/`answerApplied`) re-renders, then — only when the pane was open on that very job — opens the job that took its place (same index → the next one down; the last one when you were at the end; `closeDetail()` when the list empties) and `revealCard()` scrolls it into the column (table view is already handled by `dockDrawer`→`revealRow`). Gated on the pane being open so a 👎 pressed on a mini card or a table row never pops open a pane the user didn't ask for. Fully data-driven off `/api/jobs` (never needs a rebuild); actions post to `/api/{save,queue,block-employer,salary-probe,salary-edit}`. **Card/table parity is a CLAUDE.md rule** — the table's Resume column and the mini card's résumé indicator are now the SAME `resumeBubble(j)` control (2026-07-31, see the résumé-status-bubble entry below), and the toolbar/drawer are shared by construction. **Empty state (2026-08-03):** `renderEmpty(list)` replaced the one-line `#empty` div ("No jobs match this filter.") with a centered panel (`.emptybox` — faint `EMPTY_ART` svg + title + explanation + action buttons) and a `body.is-empty` class. Shared by BOTH views (it runs in `render()` before the view dispatch), so parity is by construction. `is-empty` hides `.grid` / `#tableWrap` / `.preview-hint` / `.totop` and widens `.wrap` to the window in card view — the awkward part was the layout, not the sentence: the card column reserves the whole right-hand side for the preview pane, so an empty board read as one grey line in a 460px column beside a screen-wide blank area with "Select a job to preview it here" hovering in it. The toolbar is pinned back to `--listw` while `is-empty` is on, so it does not jump wider the instant a search narrows the list to zero. Copy names the CAUSE and each button undoes exactly that cause. **No jobs at all → the panel RUNS a search instead of linking to the two pages that only configure one** (`runSearchNow()` → POST **`/api/run`** → `scheduler.run_job(name, manual=True)` → scrape.py → jobpipe candidates → salary_probe; zero-token, no Claude in the path). `RUN_JOBS = ['weekly_sweep','daily_scrape','radar']` is tried in order, widest net first, and only an `unknown job` error falls through to the next name — "already running" and a missing scheduler apply to every job equally. `run_job` returns the moment its worker thread starts, so `watchSearch()` polls `/api/sched-status` (the pill's endpoint, no new server work) until `runningJob` clears, then `load()`s the board; run state lives in module-level `SEARCH_RUNNING`/`SEARCH_NOTE`, **not on the button**, because renderEmpty rebuilds its innerHTML on every render and a keystroke in the search box would otherwise wipe "Searching…". Second button is `/schedule` (make it recurring); **`/setup` is deliberately NOT a button** — it and `/schedule` both read as "go configure something" from an empty board, and `ONBOARD_JS` has already redirected a genuinely unconfigured first-run visitor to `/setup` long before this panel renders; it appears only as a link inside `.emptynote` when a run fails or finds nothing. Otherwise the active search (`clearSearch()`), the filter set (`clearFilters()`), and/or the status list (`setStatusFilter('all')`) — but **each undo is offered only if it would actually reveal a job**: `currentList(over)` gained an optional override arg (`{q, fs, filters}`, default = read the controls, so every other caller is unchanged) and renderEmpty re-runs the filter with that one condition lifted before pushing its button. Three conditions can be on at once and dropping one does not necessarily un-empty the board — with a search that matches nothing anywhere, a Clear-filters button leaves the board exactly as empty, and a button that visibly does nothing is worse than no button. When no single lift is enough but all of them together are, the panel offers one `resetBoard()` instead; when not even that works (every job belongs to a banned employer) it offers the Banned list. First surviving action becomes the primary. Per-list wording lives in `STATUS_EMPTY` (an empty Pending list is "You are all caught up", an empty Bookmarked list is an unused feature) and the all-jobs-banned case pointing at the Banned list. **Run a search from "all caught up" too (2026-08-13):** the run button used to hang off the `!JOBS.length` branch ALONE, so it never appeared on a worked board — with 245 jobs all applied/skipped the panel said "You are all caught up" and offered only `Show all jobs`, an undo that re-shows decisions already made. An empty *Pending* list is a SUPPLY problem, not a display problem, so `renderEmpty` now pushes the same action there (`fs==='active'` with no search and no filters), ahead of that undo; the button object itself moved into one hoisted `runAct()` used by both branches, and the in-progress copy into a shared `SEARCHING_MSG`. Deliberately NOT offered on the other status lists (applied/skipped/bookmarked/banned are empty because nothing has been PUT in them, and a scrape does not fill them) nor on a search/filter emptiness (the same condition would hide whatever the run returns). `watchSearch()`'s found-nothing note now tests **`currentList()`**, not `JOBS` — on a caught-up board `JOBS` is never empty, so the old test reported success while the panel still read "all caught up" and the button looked inert. No new endpoint, state file or script: same `/api/run` path. **QUEUE MODE (`/queue`, 2026-08-04):** the Tailoring queue is THIS page, not a second one — `QUEUE_MODE = location.pathname==='/queue'` locks `VIEW` to table, makes `currentList()` return `j.queued` only (search still applies; status list + filter chips are ignored AND hidden via `body.queue-mode`, since a filter that could hide a queued job would make the count lie), retitles the page ⏳ Tailoring queue, counts "N jobs waiting", drops the `is-queued` row tint (every row has it) and swaps the row's Apply ↗ link for **✕ Un-queue** (`unqueueTailor` — the posting link lives in the detail card the row opens). The row expander, drawer, notes, status/resume controls and empty-state panel are the board's own, so there is nothing to drift. **No run bar (removed 2026-08-04, same day it shipped):** the `#qbar` — ▶ Run tailoring now → POST `/api/queue-run`, the `/api/sched-status` poll and the `/api/claude-cli` AI-vs-template line — is gone, along with `runQueueNow`/`pollQueueRun`/`syncQueueBar`/`loadQueueMode`/`qmsg`. Building resumes is the scheduled hourly `tailor_local` pass; a manual trigger at the top of the page was a second, racing way to start the same job and pushed the worklist below the fold. **`/api/queue-run` still exists in `dashboard_server.py` and still works — it has no caller in the UI.** **Resume column on /queue (2026-08-04):** `resumeCell(j)` branches on `QUEUE_MODE` — a column of identical "Queued ▾" bubbles restated the filter instead of describing the row, so the queue shows the résumé FILE NAME (`.t-resname`, shared `<name>_Resume_` prefix trimmed for readability, full name in the tooltip, click → `openResume`) or a faint **None**. The queue options the bubble carried moved into the action cell: **✕ Un-queue is a split button** (`.unqsplit`), wide half = `unqueueTailor`, `▾` half = the same `openTailorMenu` (caret suppressed when `!CLAUDE_ENABLED`, since the menu would then hold nothing but the un-queue it duplicates); `.unqsplit` joined the outside-click exclusion list. **Board default state (2026-08-04):** the saved filter set drops `queued` on load (`s.delete('queued')` in the `FILTERS` initializer) — every other chip is a standing preference, the queue is a worklist, and restoring it opened the board on a narrow slice instead of the full Pending list; an explicit `?filter=queued` link still wins via `applyUrlFilters`. The **Untailored (candidates)** option left the status `<select>` (and `STATUS_LABEL`/`STATUS_EMPTY`/the `fs==='untailored'` branch) — it asked a résumé question from a list about application status, which the Resume chips already answer. Empty-state title is now "No jobs matching filter(s) set" / "No jobs matching search". `queueUrl()` returns `/queue` and `openQueueView()` navigates in the SAME tab (it used to `window.open` the board with `?filter=queued`); the "Go to the tailoring queue" menu row is omitted while already there. One vocabulary: the action is **Un-queue** in the row, the ▾ menu and the drawer. | — (served by `dashboard_server.py`) | — | via `/api/jobs` | via `/api/{save,queue,block-employer,salary-probe,salary-edit}` |
| **docs/build_runbook.py** | *(new 2026-08-13)* **Source for `RELEASE_RUNBOOK.pdf`.** The 1.0.0 runbook was a binary with no source, so bumping a version meant hand-editing a PDF; this reproduces it with reportlab from content held as Python literals. `VERSION`/`UPDATED` at the top drive the header, the Step 6/7/8 examples, the zip name and the page furniture, so one edit + re-run is the whole bump. **Lives in `docs/`, not `tools/`, deliberately:** `tools/` is in the export allowlist, and the runbook quotes the personal résumé-filename handover caveat (the `<First>_<Last>_Resume_` prefix), so shipping it failed the PII gate (verified — 5 hits on one line). **Write that prefix ONLY in the placeholder form, even here:** DEPENDENCY_MAP.md is itself in the export allowlist, so spelling the real filename out while explaining why it cannot ship put 5 fresh hits on THIS line and reddened `test_export_is_clean` / all of `test_package.py` (fixed 2026-08-13). `docs/` is not exported, which is also correct on the merits: the runbook is a maintainer document and the PDF does not ship either. Needs `reportlab` (dev-only, not a runtime dep). Run: `python docs\build_runbook.py RELEASE_RUNBOOK.pdf`. Like `DEPENDENCY_MAP.html`, it is a manual artifact — regenerate only when the pipeline or the version changes, not on every code change. | `reportlab` (3rd-party) | — | — | RELEASE_RUNBOOK.pdf |
| **setup.html** | *(2026-08-18: **an unsaved-changes dialog on every exit, and "Run it automatically" removed.** This page writes nothing until a save, so every way out of an edit was a way to lose it, and the only warning was an amber `•` on a tab — a mark that stops being seen. `askSave({title,body,saveLabel,skipLabel})` is one reusable modal (`#ask_modal`, `.modal-box.ask`) resolving to `save` / `skip` / `cancel`; its resolver is nulled as it fires, so a backdrop click arriving during the awaited save cannot answer twice. **Three guards use it.** (1) **Steps:** `show()` stays the raw renderer that `load()` and the first paint call; every user-driven move now goes through **`navStep(i)`**, which awaits `guardStep()` and can be refused — the step tabs' `onclick` and `go(d)` were repointed at it, and `gotoClaudeSetup()` became async and bails when the move is cancelled. Nothing is destroyed by moving (the values stay in the form), so *Continue without saving* keeps both the edits and the dot. (2) **Sections:** a `<details>` cannot veto its own `toggle`, so the guard sits on the `.sec > summary` click in the **capture** phase and re-closes the row itself once answered. Only rows with saveable state of their own are listed in **`SEC_SAVERS`** (`filters`/`ntfy`/`git` → `saveStep()`, `claudecli` → `savePrio`, `adzuna` → `saveAdzuna`); the data-root, Ban and watchdog rows are excluded on purpose — each acts through its own button with a restart, a list write or a scheduled task behind it, so there is never a pending edit to flush. "Changed" is a value snapshot (`secSnap`) taken when the row **opens**, re-baselined by **`resnapSecs()`** from `clearDirty()`, `loadAdzuna()` and `loadPrio()` — without that last part a probe filling its own fields after auto-expand would read as a user edit. (3) **Leaving the page:** a capture-phase `click` handler on `a[href]` (skipping `#`, `javascript:` and anything with a `target` — every `/help` link opens a new tab and takes nothing with it), plus `skipSetup()`, which is a button and had to call `guardLeave()` by hand. This is the exit that really discards, so the wording says so. `leaveTo()` sets `LEAVING` to suppress the browser prompt on the navigation the dialog just authorised; `beforeunload` stays as the backstop for the one exit no page can style (closing the tab), and now also fires for the two **self-saving** settings — `anyUnsaved()` = `DIRTY.size || adzunaDirty() || prioDirty()` — which the old check missed entirely. **Removed the same day:** `#sec_agents` and its markup, the `.agent` / `.steps3` CSS, `loadAgents`/`renderAgents`/`copyAgent` and the `loadAgents()` init call. See "Agent prompts" above — `/api/agent-prompts` still serves, nothing calls it.)* *(2026-08-13: **"Min salary" gained a hint, because the field gained a second job.** `search.salary_min` used to be a request sent to the boards; `payfilter.py` now also removes below-minimum jobs from the board, and a number that silently deletes results has to say so where it is typed. The hint states the three limits of the rule — states-or-Adzuna only, top-of-range, no-pay-always-stays — and that 0 turns removal off. **Same day: an unset min/max says "none", never "$0".** 0 in these fields means "no limit", but `setV` wrote a literal 0 into the box and the Review row rendered `$105,000 – $0` for a one-sided range — both read as a $0 the user chose. New `setMoney(id,v)` blanks the field (placeholder `none`) and `money(n)` formats the Review row, so it reads `$105,000 – none`. Deliberately NOT folded into `setV`: 0 is a legitimate radius and years-of-experience. Storage is untouched — save is still `+V(id)||0`, and `payfilter`/`profile_lib` keep treating 0 as off. `ops/setup.py` gained the matching `ask_money()` (prompts `[none]` instead of `[0]`, accepts the word back).)* Guided 5-step Settings wizard served at `/setup` (About you → Preferences → Skills → Integrations → Review); `/api/{profile,geocode,adzuna,claude-cli,claude-cli-test,blocklist,data-root,notify-test}`; `?welcome=1` shows the first-run welcome + Skip-for-now (sets localStorage `setupSkippedAt`). Palette + responsive breakpoints now come from **`ui.css`** (its own `--acc`/`--fg`/`--mut`/`--field` are aliases onto the shared tokens, so no rule or JS here changed); a 760px block regrids the 5 step tabs 2-up, unstacks the two-column field rows and wraps the fixed bottom action bar. **Step 4 (Integrations)** carries four explained cards. **`AI resume tailoring` (`#sec_claudecli`) — ONE Claude row as of 2026-08-05.** It used to be two: a *Tailoring notes* checkbox writing `gui_settings.claudeEnabled`, and *Hands-off resume tailoring*. They described one thing twice, and the preference could disagree with reality in both directions (on with no CLI = a notes box nothing reads; off with the CLI installed = a working feature hidden), so **the preference is deleted** — `loadClaude`/`saveClaude`/`#claude_enabled` are gone, `/api/threshold` no longer writes the key, and availability is derived from the CLI: `dashboard_server._claude_cli_present()` (60s-cached `llm_tailor.find_claude()`) is what `/api/jobs` now returns as `claudeEnabled`, so the board's notes box simply appears when the thing that reads it exists. The row keeps **Check now** on `/api/claude-cli` (load) + `/api/claude-cli-test` (button) and the **How to set this up →** link to `/help/claude-code`; nothing is entered or stored (tailoring runs on the user's Claude plan — no API key, nothing metered — and without it the queue still writes a template copy). It also now owns the two AI-spend settings, saved together by one **Save tailoring settings** button (`loadPrio`/`prioDirty`/`savePrio` → GET `/api/settings` + POST `/api/threshold`, also flushed by *Save this step*): (1) the **customize trigger** `#cust_level` → `resumeCustomizeThreshold` — a 5-level `<select>` (`CUST_LEVELS`, 1.01/0.80/0.60/0.40/0.20, `custIndex()` snaps a stored value to the nearest, `syncCustHint()` prints the consequence). **This setting had NO editor between the retirement of `job_tracker.html` and 2026-08-05** — `cards.html` only ever read it as `customizeThreshold`, so the AI-vs-template line could only be changed by hand-editing JSON; `1.01` is deliberately unreachable, meaning "never take the template path". (2) the **auto-tailor caps** moved off `/schedule` (`prio_daily`/`prio_queue` → `dailyAutoTailorCap`/`hourlyAutoTailorCap`). They sit together because they are one idea: *when* to spend AI on a resume, and *how many* per run. The **LinkedIn Top-Applicant gate** on step 2 follows the same single condition (`claudeReady() === CLAUDECLI_OK`). The other three cards: *Phone alerts* (what ntfy is + why the radar needs it, prominent **How to configure ntfy →** button to `/help/ntfy`, **Send test alert** → `/api/notify-test`), *Backup & version history* (explains that the Git remote URL is the OFF-MACHINE copy of the daily checkpoint commits — blank = local-only history — plus a private-repo warning and what name/email are for), *Adzuna* (**How to get these keys →** button to `/help/adzuna`), and *Where your settings & data are stored* (shows the resolved data root + which rule chose it, Test folder / Use this folder / Reset to default via `/api/data-root`, with a restart-pending banner; nothing is copied for the user). **Explicit save on every step (2026-08-04):** the wizard no longer commits anything as you type. Steps 1–4 each carry a **✓ Save this step** button in the action bar (the Review step keeps **✓ Save & generate**); one press POSTs the whole profile to `/api/profile` (`quiet` mode — no completion dialog) and clears every pending step. Edited-but-unsaved steps show a `•` on their tab plus an "unsaved changes on step N" note in the bar, `beforeunload` warns, and **Reload** confirms before discarding. Step 4's save flushes the Adzuna keys and the auto-tailor caps if they changed (Claude Code itself has nothing to save — it is detected, not configured). Controls with their own confirm/restart semantics stay self-saving and are excluded via `data-selfsave` (data-root path, the Ban row). **After a successful save** a completion dialog confirms what was generated and offers the next step — **Schedule automatic job searches** (→ `/schedule`, recommended, since nothing runs on its own until a schedule exists) or **Go to the dashboard** (→ `/`); a green "Profile saved & generated" bar then persists on the page, and also shows on load whenever `/api/profile-status` reports `configured` **“Keep it running” add-on section (2026-08-05, FIRST row of step 4):** the same watchdog switch as `/schedule`, off the same `/api/watchdog`, so a first-run user meets it during onboarding instead of having to find the Schedule page. Leads `AUTO_KEYS`, so on a fresh install it is the section that auto-opens. **Acts immediately** (it registers a Windows task) — deliberately NOT wired to the Save button and never marks the step dirty, and the hint says so. When the watchdog is unsupported or `/api/watchdog` fails, `noAutoOpen('uptime')` sets `SEC_STATE.uptime` true WITHOUT the green pill: a row the user cannot act on must not claim the one auto-open slot nor read as an unfinished gap. | — (served by `dashboard_server.py`) | — | via APIs | via APIs |
| **help_adzuna.html** | Static "how to get Adzuna keys" guide at `/help/adzuna` (embeds a blurred screenshot of the access-details page). Palette + 760px breakpoint from `ui.css` | — (served by `dashboard_server.py`) | — | — | — |
| **help_claude_code.html** | Static "set up unattended resume tailoring" guide at `/help/claude-code` (2026-08-04): what changes with vs. without Claude Code (template copy vs. AI-rewritten resume), install → `claude login` **with the plan, not a Console API key** → `claude -p "say OK"` → Settings **Check now**, plus a failure table (not found / found-but-failed / timed out) and the `python llm_tailor.py --check` equivalent. Opens by stating **flat rate, nothing metered** — it runs on the subscription — and closes by restating that none of it is required. Carries the amber **ANTHROPIC_API_KEY warning**: this app strips the variable, but if the user set it for another tool their own terminal `claude` sessions bill at API rates (`echo %ANTHROPIC_API_KEY%` to check, `setx ANTHROPIC_API_KEY ""` to clear). Linked from the Settings **Unattended tailoring** card and the tracker's not-available notice. Palette + 760px breakpoint from `ui.css`. Replaced the one-day-old `help_claude_key.html` when the metered path was removed | — (served by `dashboard_server.py`) | — | — | — |
| **help_ntfy.html** | Static "what ntfy is / how to set it up" guide at `/help/ntfy`: what ntfy is (no-account topic-based push), why the pipeline needs it (script-level hourly radar has no other way to reach you), what a push looks like, an in-page secure-topic generator + copy button, phone-app install/subscribe steps, and troubleshooting. Linked from the Settings step-4 **Phone alerts** card. Palette + 760px breakpoint from `ui.css` | — (served by `dashboard_server.py`) | — | — | — |
| **schedule.html** | *(2026-08-08 — later the same day: **all four banners REMOVED from this page.** `#whyBanner`, `#missBanner`, `#blBanner` and `#wdWarn` are gone, along with `showMissed()`, `showWhy()`, the `WHY` map, `WHY_KIND`, `enableWatchdog()`, the `.banner`/`.banner.bad`/`.banner .bact` CSS, and `runNow()`'s scroll-to-`#blBanner`. Every one of those messages now lives in the nav chip's bubble (see `dashboard_server.SCHED_PILL_JS`), which is on every page instead of only this one. **This page is the SETTINGS now — times, switches, limits, and nothing that warns.** What stayed: the per-card badges (`badgesHtml`, incl. `paused — board full`), `metaHtml`'s `Next: held — board full`, `#blMsg` (which absorbed one clause: "the searching tasks are holding off until this drops below N"), `#wdMsg`, force-opening a group that holds a missed/failed/stuck task, and `.card.spotlight`. `showWhy()` was replaced by **`spotlightWhy(st)`** — reads `?job=` only and rings that card, no text; a modifier-click on the chip is the only way here now. **`showWatchdogCard()` is reached from the bubble via the `#watchdog` hash** (`render()` calls it when `location.hash === '#watchdog'`), replacing the removed in-page "Show me the setting" button. **Supersedes the watchdog-bubble, `#blBanner` and `#whyBanner` notes below.** RULE going forward: do not add a warning banner back to this page.)* *(2026-08-08: **the watchdog warning bubble now carries its own actions.** `renderWatchdog` builds an `act` string beside `warn` and renders it into `#wdWarn` as a `.banner .bact` row: **Turn it on** (OFF state) or **Try again** (install-failed state, where "turn it on" would be a lie — the switch already reads on), plus **Show me the setting**. "Turn it on" calls the new `enableWatchdog()`, which goes through the SAME `postWatchdog({enable:true, everyMin})` the card's switch uses — one code path, so bubble and toggle cannot drift. "Show me the setting" calls `showWatchdogCard()`: scrolls `#wdCard` into view, adds the existing `.spotlight` ring for 3 s and focuses `#wdToggle`. Handlers are re-bound on every render because the bubble's `innerHTML` is rewritten by each 15 s poll. Nothing is offered on an unsupported OS — there is no switch to point at.)* *(2026-08-06: `runNow(name)` scrolls `#blBanner` into view when the task's `group === 'search'` and the banner is showing — the same "you are starting a search the cap is holding" reminder the board and the nav bubble give. This page's banner is never dismissable, so nothing else is needed here, and the board needs no flag from here either: its dismiss lasts one page load.)* *(2026-08-05: **the pending-job cap card + banner** — `#blCard` holds the one number (`backlogCap`), read via `/api/backlog` and written via `/api/threshold`, i.e. the same gui_settings writer every other board number uses; no new file, no new endpoint. It lives on THIS page because the cap governs these tasks. `#blBanner` appears only while the pause is in force and says WHICH group stopped and where to clear it — a banner that merely announced a pause would send the user hunting the cards below for a task that looks switched off, and none of them is, because the pause is computed. `badgesHtml` gained a **"paused — board full"** badge and `metaHtml` prints **"Next: held — board full"** instead of a time: the card is NOT drawn disabled (the user's switch really is on), so without those two the card would read "on, next run 09:00" while the scheduler skips it. `loadBacklog()` polls every 15s — slower than the 4s run-status poll, since it reads three files — and never overwrites the cap field while it has focus.)* Scheduler UI at `/schedule` (interval/daily/weekly/monthly job cards, **watchdog toggle** — also offered in `setup.html` → *Keep it running*, both driving the one `/api/watchdog`) off `/api/{schedule,run,watchdog}`. **Watchdog card replaced the autostart card (2026-08-05):** label → "Restart the dashboard by itself if it stops", plus a *Check every* select (1/5/15/30 min) and a *Let me close it for* pause select (0/30/120/480 min, which posts `pauseMin` and resets itself to 0) — both disabled while the toggle is off. Badges: `on` / `paused N min` / `not enabled` (install failed, with the schtasks error) / `off` / `unsupported OS`; `#wdMsg` names the actual Windows task so it can be found in Task Scheduler. The page intro no longer claims the jobs "do not need the Windows Task Scheduler" — with the watchdog on, they do. `loadWatchdog()` polls `/api/watchdog` every 15 s. The old `autostartToggle`/`renderAutostart` block is **gone** (its element ids no longer exist on the page); `/api/autostart` is untouched for old installs. **Collapsible groups (2026-08-05):** job cards render inside `<details class="sec">` sections — *Finding jobs · Resumes · Upkeep · Other* — using the same `.sec` markup/idiom as `setup.html`. Membership comes from the **`group` field of each job in `config/schedule.default.json`** (`search`/`resumes`/`upkeep`; unknown or absent → `other`), so the page is never keyed to job names and an added/renamed job files itself. All groups start **closed**; the summary carries **`N of N on` only**. **Run status left the sections (2026-08-05):** the amber `N missed` / red `N needs attention` pills are **gone** (and so are the `.sec .pill.warn/.err` rules) — run status is reported in exactly ONE place, the nav chip at top right, because three readouts of the same fact on one page can disagree for a poll interval. A folded section still cannot hide trouble: it force-opens on missed/failed/stuck, and the card badge + `#whyBanner` explain it. A group force-opens when it holds a missed/failed/stuck job or the `?job=` target; the in-memory `OPEN` set preserves what the user opened across a Save re-render. Enabled-checkbox changes repaint the group pill at once; the 4s status poll repaints pills and force-opens a section that turns bad. **Autostart copy (2026-08-05):** label → "Start the dashboard by itself when I log in" + a `.desc` paragraph stating the consequence (no dashboard process → no schedule; runs missed while closed); the duplicate autostart sentence was dropped from the page intro. **Auto-tailor caps moved OUT to `setup.html` (2026-08-05)** — they bound AI resumes only, so they belong with the AI integration, not with the run times; `loadPrio`/`savePrio` and the `#prioCard` markup are gone from this page. **Hourly card layout (2026-08-05):** `interval_window` renders *Every (minutes) · Days* on the first row and pushes *From/To* to a second via a `.brk` flex spacer, so its day strip sits in the same slot as the daily card's. **2026-08-03 — arrival-from-the-nav-chip:** reads `?job=&why=` and shows a `#whyBanner` written by the `WHY` map (`stuck`/`failed`/`missed`/`running`) — what the state means in plain words plus a **Run it now** button — and `.spotlight`-rings + scrolls to that job's card, because a red chip that drops the user on six near-identical cards has said only that something is wrong. The banner re-evaluates on the 4s status refresh and hides itself once the state clears. Card badges gained **`may be stuck`** / **`last run failed`**, and `missed` moved from red to **amber**: a skipped run is a nudge, a failed one is a problem, and sharing the red badge made them read as equally alarming. **Retheme 2026-07-29:** the dark-navy palette it carried on its own — the only dark page in the app, sitting under a light nav — was retired for `ui.css`; class names and JS unchanged, plus a 760px block that stacks the header action, the control fields and the card footer buttons. **Archive age 2026-08-03:** a card renders the extra "Archive applied jobs older than [N] [days/weeks/months]" pair whenever its job definition carries `archive_age` — keyed to the field, NOT to the job name, so the control follows the archive job if it is ever renamed or re-typed. `AGE_UNITS` here must match `archive.py`'s `UNITS` | — (served by `dashboard_server.py`) | — | via APIs | via APIs |
| **archived.html** | Archived-applications table at `/archived` off `/api/archived`; resume links hit `/archived-resume` (unzips from the archive zip). **Retheme 2026-07-29:** the beige "aged paper" palette (which read as a different product rather than as older data) was retired for `ui.css` — archive-ness is now carried by the 🗄 header and amber match pills; the table gained a `.tscroll` horizontal scroller and a 760px block | — (served by `dashboard_server.py`) | — | via `/api/archived` | — |
| **dev.html** | *(2026-08-06)* **Hidden developer page at `/dev`** — not in `NAV_ITEMS`, nothing links to it, you type the URL. **Restart button** → `POST /api/dev/restart` → `dashboard_server.dev_restart()`: reply first, `SERVER_OBJ.server_close()` to free the port, relaunch detached carrying `--port=<PORT>` forward (a staging copy must not come back on 8765), `os._exit(0)`. The page then polls `GET /api/dev/ping` until the **pid/boot differ** from the process it asked to leave — waiting for a live port alone would reload against the old server in the moment before it exits — then reloads. Also renders read-only `GET /api/dev/info` (port/label, pid + uptime, python + argv, app folder, data root **and which rule chose it**, scheduler enabled/disabled, watchdog, last 20 lines of `logs/execution.log` with ERROR/EXIT lines reddened), refreshed every 15s. **Exists because .py edits need a new process** — .html/.css are re-read per request and sent `no-store`, so those only need a browser refresh; the page says so. Read-only apart from the restart: writes nothing to the tracker or any user-owned field. Replaces the deleted `Start Dashboard.bat`. `inject_nav(..., 'dev')` suppresses the first-run `/setup?welcome=1` redirect without lighting up a tab | — (served by `dashboard_server.py`) | — | `/api/dev/info`, `/api/dev/ping` | — (restart only) |
| **skip_job.py** | CLI to skip / bookmark jobs | — | — | job_tracker.json/html | skipped_jobs.json, job_tracker.json |
| **make_autostart.py** | **LEGACY (superseded 2026-08-05 by `make_watchdog.py`).** Install/remove Windows Startup launcher for the dashboard. Fires at logon **only** — closing the dashboard or a crash left nothing running until the next logon, which is why the watchdog replaced it. Still imported by `dashboard_server.py` (so old installs keep working) and by `make_watchdog.install()`, which **deletes the `.vbs`** when the watchdog is registered — two mechanisms starting one server would race for the port. `/api/autostart` still answers; the Schedule page no longer offers the switch | — | — | — | Startup `.vbs` |
| **make_watchdog.py** | *(2026-08-19: **now a PLATFORM FACADE.** macOS has no Task Scheduler, so `supported()` returned False there and the "Keep it running" switch had nothing to call — the feature was dead on a Mac while `/setup` went on offering it. On `sys.platform == 'darwin'` this module imports **`watchdog_launchd.py`** as `_BACKEND` — **guarded**, so a broken or missing backend degrades to an honest "not supported" instead of taking the dashboard down on import — and every public function forwards to it. `TASK_NAME`/`LEGACY_TASK_NAMES` are rebound to the backend's label (the dashboard shows `TASK_NAME` to the user, so it must name the thing that actually exists on this machine), and a new **`MECHANISM`** (`'launchd'` / `'Windows Task Scheduler'` / `''` on neither) rides in `watchdog_state()` so the pages can name the facility without branching on the platform themselves. **`MINIMIZES` (2026-09-05)** rides beside it and is **imported from `watchdog.py`, not re-derived** — it is the same constant `open_minimized()` branches on, so the promise and the behaviour cannot drift. `watchdog_state()['minimizes']` is what `setup.html` and `schedule.html` read: both pages used to say the dashboard comes back **"minimized, so it never covers your screen"** on every platform, while on macOS `open_minimized()` falls back to `webbrowser.open()` and raises a window to the **front** every 300 s — a setting that promised the exact opposite of what it did (1.2.0 macOS QA, F-4). The pages now fill that sentence from `minimizes` (`restartHow(s)` in each), `INSTALL.md` says the same, and the static HTML fallback is platform-neutral so a page whose script has not run yet still says nothing false. `make_watchdog.py xml` prints whichever definition this machine uses — `plist` is an alias. **No caller changed.** Everything below this note is the Windows half.)* Register/remove the **watchdog** (Windows task `TopRatWatchdog`). `supported()`/`is_enabled()`/`install(every_min)`/`uninstall()`/`run_now()` — same shape as `make_autostart.py`, so `dashboard_server.py` drives both identically; install/uninstall return `(ok, message)`. Builds the task from `task_xml()` and registers it via `schtasks /create /xml` (UTF-16 + BOM, as schtasks requires). **One `LogonTrigger` with a `Repetition` interval**, so logon is covered with no delay and the rest of the session every N min (default 5, 1–1440). Settings that matter: `InteractiveToken` (no stored password, no elevation needed — and the watchdog opens a browser window, which only exists in the logged-on session), `DisallowStartIfOnBatteries`/`StopIfGoingOnBatteries` **false** (both default true, which would kill it on a laptop), `IgnoreNew` (never stack runs), `Hidden` + `pythonw.exe` (no console flash), `ExecutionTimeLimit PT2M`. **Deliberately does NOT run logged-out and does NOT wake the machine** (`WakeToRun false`) — that needs a separate browserless scraper task | `watchdog_launchd` (macOS backend, guarded import), `make_autostart` (only to retire the `.vbs`) | `schtasks.exe` | — | Windows task `TopRatWatchdog` (legacy `JobDashboardWatchdog` removed on install **and** uninstall) |
| **watchdog_launchd.py** | *(new 2026-08-19)* **The macOS half of `make_watchdog.py`** — same five functions (`supported`/`is_enabled`/`install(every_min, retire_vbs)`/`uninstall`/`run_now`), same `(ok, message)` returns, so the facade forwards with no branching at the call sites (`retire_vbs` is accepted and ignored — it names a Windows Startup-folder file that cannot exist on a Mac). Writes `~/Library/LaunchAgents/com.toprat.watchdog.plist` with **`plistlib`**, not a string template — the Windows side hand-escapes its XML and that is the one thing worth not copying — and loads it with `launchctl bootstrap gui/<uid>`, falling back to `load -w` for pre-10.11. **A LaunchAgent, not a LaunchDaemon:** the user's own folder, no admin rights, nothing system-wide, and it runs INSIDE the GUI session — the only place the browser window `watchdog.py` opens can exist. Same trade as `InteractiveToken` on Windows: **does not run logged out, cannot wake the Mac**. **`RunAtLoad` + `StartInterval` = the LogonTrigger + Repetition pair**; **`KeepAlive` is deliberately ABSENT** — `watchdog.py` is a one-shot check that exits in a second, so KeepAlive would read every clean exit as a crash and respawn it in a tight loop. `ProcessType Background` (throttleable under load), `LimitLoadToSessionType Aqua` (GUI sessions only), absolute interpreter + script paths (launchd gives the job no useful PATH or cwd), stderr → `logs/watchdog_launchd.err` because **launchd keeps no run history** — the one thing Task Scheduler gives us for free. `is_enabled()` requires the plist on disk **AND** the label loaded, since a plist nothing loaded restarts nothing; `install()` is unload→rewrite→load so a changed interval always takes. Interval clamped 1–1440 min, same as the Windows side. `LEGACY_LABELS` is empty (no Mac copy ever shipped under the old name) but kept, because a loaded agent lives in launchd, not in this repo. **`__main__` verbs (fixed 2026-09-05):** `make_watchdog.py` accepts **both** `xml` and `plist` for its dry run, but this module knew only `plist` — and its `else` branch was `install()`, so **`watchdog_launchd.py xml` installed and loaded the LaunchAgent instead of printing it**. `xml` is now an alias for `plist`, `install` is an explicit verb, and an **unrecognised verb exits with usage** rather than writing to the machine | `_paths` | `launchctl` (`bootstrap`/`bootout`/`print`/`list`/`kickstart`; `load -w`/`unload -w`/`start` as pre-10.11 fallbacks) | — | `~/Library/LaunchAgents/com.toprat.watchdog.plist`, `logs/watchdog_launchd.err` |
| **stop_board.py** | *(added 2026-08-06)* **The other half of the watchdog — stops the board and keeps it stopped.** Zero-token, stdlib. Order matters and is the whole point: **pause first, then kill**, because a watchdog tick landing between the kill and the verify restarts the server and makes a successful stop read as a failure. (1) writes `gui_settings.watchdogPauseUntil` = now + N min (default 10 — the pause `watchdog.py` already documents, and it EXPIRES, so forgetting to undo it is harmless) and `schtasks /End`s a run in flight (**/End only — never `/Change /DISABLE` or uninstall**; disabling automation is a persistent change the user makes knowingly via `make_watchdog.py uninstall`); (2) collects targets from **`netstat -ano` LISTENING on the port** (`lsof`/`ss` off Windows) — this is the set that catches a server started from **another copy of the project**, which no script-name match can see — plus command-line matches on `dashboard_server.py`/`watchdog.py` (`--all` adds `scrape/jobpipe/tailor_local/notify/salary_probe/render_resume/llm_tailor`) via `Get-CimInstance Win32_Process` (`wmic` fallback, `ps -eo pid=,args=` off Windows); (3) drops **own pid + parent pid** so it cannot kill the shell running it; (4) `taskkill /PID /T /F` (SIGTERM→SIGKILL off Windows); (5) **verifies against the PORT, not the kill's exit code** — the question is whether the board still answers, and a kill can report success while a replacement is already listening. `resolve_port()` mirrors `watchdog.resolve_port()` (**keep all three in step** with `dashboard_server.resolve_port()`). Flags: `--list` (report only), `--all`, `--pause MIN`, `--no-pause`, `--resume` (clear a pause, stop nothing), `--port`. Exit 1 when the port still answers or a kill failed. **Why it exists:** closing the window does not stop this dashboard — the old process keeps the port (a second server exits "address in use", often into a console that closes instantly) and, because `pipelib` resolves the data root ONCE at import, a stale process serves old code against a data root the config has abandoned. Both failures are silent | `_paths`, `pipelib.cfg`/`cfg_write` (optional — falls back to `config/` beside the code) | `netstat`/`taskkill`/`schtasks`/`powershell` (`lsof`/`ss`/`ps` off Windows) | config/instance.json (`port`), config/gui_settings.json | config/gui_settings.json (`watchdogPauseUntil` only) |
| **watchdog.py** | **The check itself — deterministic, zero-token, stdlib.** One run = probe `127.0.0.1:<port>`; if it answers, exit 0 **silently** (it runs every few minutes; logging each pass would bury `logs/execution.log`), otherwise launch `dashboard_server.py --no-browser` detached+hidden via `pythonw.exe`, poll up to 25 s for the port, then open the board **minimized** (`cmd /c start "" /min <url>` — the shell has to apply the window style for a URL; if a browser window is already open the URL becomes a tab there and nothing is minimized). Logs only when it acts or fails. `resolve_port()` mirrors `dashboard_server.resolve_port()` minus the `--port` argv form (**keep the two in step** — a mismatch means probing the wrong socket and starting a second server every run). **Pause:** `gui_settings.watchdogPauseUntil` (unix ts) makes it do nothing, so the user can close the app and have it stay closed. Flags: `--check` (report only, starts nothing), `--no-open`, `--force`. Run by the scheduled job (Task Scheduler on Windows, launchd on macOS), and by `Install Watchdog.bat` / `Install Watchdog.command` once at install | `runlog`, `pipelib.cfg` (both optional — falls back if absent) | `pythonw.exe`, `cmd /c start` | config/gui_settings.json (`watchdogPauseUntil`), config/instance.json (`port`) | logs/execution.log, spawns `dashboard_server.py` |
| **lint_resume.py** | Deterministic checker for the CLAUDE.md resume rules (R01–R12: font floor, page count, page-2 min lines, bullets-per-role, banned headers, notes leakage, summary-title rule, docx/pdf pair, skills-line format, trailing-role group). Zero tokens. `--all` sweeps New/ + Applied/ · `--json` for machines · exit 1 on any ERROR. Called by `jobpipe lint-resume` (shelled out, **not** imported, so python-docx stays optional for jobpipe) and by `render_resume.py` (subprocess post-render gate; render_resume also imports its rule constants so the two can't drift) | `python-docx` (ext) | `pdftotext` (page checks) | `New/**.docx` + sibling `.pdf`, `Applied/**.docx` | — (stdout only) |
| **ghost_risk.py** | Heuristic ghost-job risk scorer — **ADVISORY ONLY, nothing downstream reads it**. `score_job` (pure) + `score_all` + `build_repost_index` + `parse_salary_range`. Signals: STRONG = salary_width / remote_hybrid / repost (distinct ids ≥`repost_gap_days` apart); WEAK = no_salary / stale_active. level high (≥2 strong) / medium (1) / low (weak only) / none. Thresholds in config/ghost_risk.json (optional). Imported by `dashboard_server.py` for the /api/data badge only. Guarded by `tests/test_ghost_risk.py` | `pipelib`, `blocklist` (norm only) | — | config/ghost_risk.json | — |
| **jobdesc.py** | On-demand job-description fetcher — **script-only, zero-token, stdlib**. `fetch(url)` pulls a posting's description text from its apply URL when the user opens a card (so full descriptions are never stored in bulk): tries per-host JSON APIs (Greenhouse `boards-api`, Lever `api.lever.co`, **Eightfold** `/api/pcsx/position_details`) → **session/encoded ATS handlers** → schema.org `JobPosting` JSON-LD → generic HTML→text strip, in that order. **Eightfold** (`*.eightfold.ai/careers/job/<id>`): the SPA loads a clean public JSON API (`position_details?position_id=<id>` — the `domain` param the page sends isn't required); `parse_eightfold` pulls `data.name` + `data.jobDescription` (which carries the location-based pay bands). **Taleo** (`*.taleo.net/careersection/.../jobdetail.ftl`): server-rendered but **session-gated + URL-encoded** — a cold request returns a JS shell, so `_session_get` seeds a cookie jar via the section root (`jobsearch.ftl`/`moresearch.ftl`) then retries the job URL until the real body (hundreds of `%3C`) appears; `parse_taleo` unquotes (`%3C`→`<`, `%24`→`$`) before stripping and returns '' on a shell so chrome is never cached. **Paylocity** (`recruiting.paylocity.com/Recruiting/Jobs/Details/<id>`): server-rendered, but pay sits in a `job-listing-header`/`Salary Description` div AFTER the description container, so `parse_paylocity` strips the whole `job-preview…`→`preview-bottom-apply` region so the salary isn't dropped. **Workday** (`*.myworkdayjobs.com` / `*.myworkdaysite.com`, added 2026-08-05): the posting URL answers a cold request with a redirect stub (`{"widget":"redirect","url":…,"externalSpa":true}`) — page data, not the job — so `_workday_api` maps the URL (optional `/<lang>/` prefix tolerated) to the public CXS record `…/wday/cxs/<tenant>/<site>/job/<path>`, and when that 404s, `redirect_target()` absolutises the stub's path and the CXS mapping is retried against it; `parse_workday` reads `jobPostingInfo.title/timeType/jobDescription`. **Unreadable-page gate — `assess(text, url) → (reason, message)` (2026-08-05):** a page can fetch fine and still carry no job, and everything below cleared the old bare 120-char check and got cached + rendered AS the description. `assess` names what came back instead — `metadata` (redirect/config blob), `js` (unrendered `{{…}}` template or "enable JavaScript"), `login` (LinkedIn-style sign-in wall), `expired` (taken-down posting), `chrome` (nothing but site menus/legal text/empty field labels), `empty` (<120 chars) — and `MESSAGES[reason]` is the plain-English line the drawer shows. **Contract:** an empty `reason` means the text is renderable and `message` is either '' or an ADVISORY to show above it (`NOTICE_EXPIRED`, for a taken-down posting whose body is still on the page); a non-empty `reason` means show `message` INSTEAD of the text, and `fetch` then returns `text:''` and caches nothing. Deliberately conservative — the strong signatures reject alone, while the fuzzy "it's just chrome" call also requires the posting vocabulary (`_SIGNALS`) to be absent, and a sign-in wall whose page still prints the posting is KEPT. On-disk TTL cache (`jobdesc_cache.json`, 14d) keeps re-opens instant and external calls low; **cached text is re-`assess`ed on read** and dropped when it fails, so entries stored before/under an older gate re-fetch instead of rendering stale metadata. Degrades gracefully (`ok:false` + reason + message) when a site is JavaScript-only or blocks the fetch, so the drawer states why and falls back to a "view original posting" link. Imported by `dashboard_server.py` for `/api/jobdesc` **and by `salary_probe.py`** (stage-1 clean-text fetch, so the JS-rendered-ATS fix is shared, not duplicated). Guarded by `tests/test_jobdesc.py` (pure extractors; live fetch not exercised). Also runnable as a CLI (`python jobdesc.py --url <url>`) | `urllib` (stdlib) | — | — (network) | jobdesc_cache.json |
| **reposts.py** | Deterministic repost detector — **ADVISORY ONLY, never excludes a job**. `annotate(rows, job_details, applied, skipped, notes)` recognizes a fresh listing/candidate we've seen before (identity: normalized `applyUrl` → `reqId` → employer+role-token match, a prior sighting must be a DISTINCT id ≥`gap_days` older) and returns `{id: {note, lastSeen, priorStatus, priorId}}` — a short note carrying the last-seen date, prior applied/skipped status, and prior user note. Zero tokens, pure/no-network. `norm_url` reused for the apply key. Thresholds in config/reposts.json (optional; default gap 7d). Imported by `tracker.py` (candidates → `repost`/`repostNote` cols) and `dashboard_server.py` (/api/data badge). Guarded by `tests/test_reposts.py` | `pipelib`, `blocklist` (norm only) | — | config/reposts.json | — |
| **ghostflags.py** | The USER's own ghost-job flags — deterministic, zero tokens, and deliberately NOT the same thing as `ghost_risk.py` (which *guesses* from heuristics and is advisory): this module *records* a judgement the user made on a specific posting. `set_flag(job_id, company, role, flagged)` / `flagged_ids()` / `employer_counts()` / `flag_count(company)` / `decline_ban(company)` / `declined_employers()` / `clear_employer(company)`. Employer keys come from `blocklist.norm_employer`, so a flag counts against the SAME identity a ban would use. Per the owner (2026-07-30) a flag changes **nothing else** — no skip, no unqueue, no filtering; the only behaviour it drives is the dashboard's ban offer on the **2nd** flagged posting from one employer, and a `banPrompt:"declined"` mark that stops it ever asking about that employer again. Storage `config/ghost_flags.json` (git-ignored), atomic verified write via pipelib. **Read path is resolved per call (`flags_path()`), never at import** — the file doesn't exist on first run, so a module-level `cfg()` would pin the code-dir path for the life of the dashboard process and never see the copy the first write puts in the data root. Also a tiny CLI (`python ghostflags.py` lists what's flagged) | `pipelib`, `blocklist` (norm only) | — | config/ghost_flags.json | config/ghost_flags.json |
| **blocklist.py** | Deterministic employer ban list (the reliable half of fake-job handling; the heuristic ghost-risk scorer is separate/planned). `is_blocked(company)`, `block_employer`, `unblock_employer`, `blocked_employers`, `norm_employer` (lowercase + strip corp suffixes so "Felix Pago"/"FelixPago" collapse). Imported by `tracker.py` (candidates + resolve_status) and `jobpipe.py` (process-queue + CLI). Atomic verified write via pipelib | `pipelib` | — | config/employer_blocklist.json | config/employer_blocklist.json |
| **validate_bullets.py** | Validates the bullet bank against its schema — every entry conforms to `resume_template/bullets.schema.json`, all ids unique, `trailing_group` consistent with `role`, and each declared `track` has its file in `source`. Zero tokens; exit 1 on any problem. Uses `jsonschema` if installed, else a built-in structural check covering the same rules (so it runs dep-free). Standalone — nothing imports it | `jsonschema` (ext, optional) | — | `resume_template/bullets.json`, `resume_template/bullets.schema.json` | — (stdout only) |
| **runlog.py** | Shared execution logger — entry scripts wrap main() in `runlog.run()`; also a CLI to tail the log | — | — | — | logs/execution.log |
| **diag_hiringcafe.py** | Scraper diagnostic (standalone, aux) | — | — | — | — |
| **tests/** (+ `tools\run_tests.bat`) | Offline pytest suite over jobpipe's pure functions + lint_resume rules R00–R12. Golden fixtures in `tests/fixtures/*.json` snapshot live INPUTS against **FROZEN config** (regen: `tests/make_fixtures.py`). *(2026-08-13: **`tests/frozen_root.py` + `tests/fixtures/config_frozen/` — the config dials left the live data root.** The config-derived goldens used to be computed against the OWNER's `skills.json`/`skill_aliases.json`/`strategy.json`/`profile.json`/`geo_cache.json`, which made them snapshots of an OPINION rather than of behaviour: removing "Change Management" from skills.json moved `li_4445471875` 0.92 → 0.85 and reddened `test_golden_skill_match` with a failure that pointed at `scoring.py` and had nothing to do with it. Those five files are now committed under `tests/fixtures/config_frozen/` and materialized into a tmp data root by `frozen_root.materialize()`; the **`frozen_config`** fixture wraps `frozen_root.activate()`, which patches `pipelib.DATA` (enough on its own — `_overlay()` reads it at CALL time, so no module reload, unlike `test_backlog`'s scratch root) plus the three latched things that do NOT follow DATA: `pipelib.GEO_CACHE`, `locality.GEO_CACHE` (imported by value) and `profile_lib.PROFILE`, and clears locality's `_strat`/`_cache`/`_center` memos both ways. **8 tests moved off `needs_userdata`** (3 `test_scoring`, 3 `test_parse_card`, `test_local::test_golden_locations`, `test_blocklist::test_candidates_excludes_blocked_from_ready`) and now run in CI: 411 selected, 23 deselected, was 403/31. **The marker now means real CONTENT only** — the bullet bank (all 22 `test_render_resume`) and `resume_template/` (`test_golden_classify`), neither of which can be committed (`*.docx` is gitignored) or synthesized. **`make_fixtures.py` imports the SAME module** so a regeneration cannot write values the suite disagrees with; it computes `skill_match`/`local`/`cards` inside `activate()` but `classify` OUTSIDE it, because classify resolves the base resume from the live `resume_template/` and the frozen root has none — computing it inside would silently blank `baseResume` in every row. **The `.frozen.json` filenames are load-bearing:** `.gitignore` excludes `geo_cache.json` by bare name and `release.py`'s `FORBIDDEN_PATHS` blocks `geo_cache.json` + `config/{profile,skills,skill_aliases,gui_settings}.json` ANYWHERE in an export, so the real names could not be committed at all; the rename is deliberately preferred over carving an exception into either guard. The frozen copies' `note`/`_note` prose was also rewritten — the originals named the owner and tripped the PII gate. **Same change fixed `make_fixtures.py`'s stale paths**: it did a bare `sys.path.insert(ROOT)` + `import jobpipe` and read `ROOT/listings.json`, neither of which survived the `src/` refactor and the data-root split — it now goes through `src/_paths` and `pipelib.DATA`, so it produces real goldens instead of empty ones.)* synthetic one-rule-per-file docx fixtures in `tests/fixtures/docx/` (regen: `tests/build_docx_fixtures.py`). **Those docx fixtures are BUILT, never committed (fixed 2026-08-12):** `.gitignore` excludes `*.docx` repo-wide (the repo commits code, not resume binaries), which silently excluded the fixture folder too — so a fresh clone, CI's included, checked out an empty `fixtures/docx/` and every `test_lint_resume` case failed with R00 `could not read document: PackageNotFoundError`. `conftest.py::pytest_sessionstart` now runs `tests/build_docx_fixtures.py` in a subprocess when the folder holds no `*.docx`, and no-ops when it does; if `python-docx` is absent it prints one line and lets the lint tests report it, so the rest of the suite still runs. Nothing was added to CI — the hook covers both CI and a fresh local clone. No network/LibreOffice/pdftotext (pdf_pages monkeypatched); read-only over repo root — writes go to pytest tmp_path. `run_tests.bat` = `python -m pytest tests -q`, preceded by an `import pytest` probe that pip-installs `requirements-dev.txt` into the chosen interpreter when it is missing (the bundled `python\` ships without pytest; a failed install exits 1 with instructions instead of running nothing). **`test_backlog.py`** (2026-08-05) builds a whole scratch board under `tmp_path`, points `JOB_AGENT_DATA` at it and **reloads `pipelib` + `blocklist` + `backlog`** — all three resolve their paths at import, and forgetting `blocklist` means it keeps reading the real ban list and no ban ever matches. **It must reload them BACK (fixed 2026-08-11):** the `board` fixture is a yield fixture whose teardown calls `monkeypatch.undo()` and then reloads those three plus `scheduler`. Undo first — finalizers run in reverse dependency order, so `monkeypatch` has not restored `TOP_RAT_DATA` yet, and restoring the variable moves nothing anyway once a module has resolved. Missing teardown here WAS the long-standing "config goldens are order-dependent" defect: `test_backlog` is second in collection order, so the scratch root (an already-deleted `tmp_path`) stayed live and the 8 config-derived goldens in `test_blocklist`/`test_parse_card`/`test_scoring` failed in a full run while passing alone. `conftest.py::_data_root_restored` (autouse) now fails at **setup** if `pipelib.DATA` has moved, so a future leak blames its own cause; `pytest_sessionfinish` covers a leak in the last test. It pins the two load-bearing properties: the count equals the board's Pending list (applied / skipped / banned / cross-CSV duplicates all excluded), and the pause writes **no** `schedule.json` — the auto-resume promise rests on it staying computed | `pytest`, `python-docx` (ext) | imports `jobpipe`, `lint_resume`, `render_resume` (as modules under test, not pipeline wiring; `tests/fixtures/content_alkami.json` is the render-fidelity golden fixture) | listings.json, candidates.csv, tracking.csv, job_tracker.json, config/* (fixture regen only), tests/fixtures/* | tests/fixtures/* (regen scripts only) |

## jobpipe.py subcommands (the API the agent + scheduler call)

`urls · classify · flag-skills · match · filter · candidates · process-queue · salary · salaries · salary-set · update · rebuild · archive · export-csv · import-csv · simplify · parse-cards · lint-resume · doctor · block-employer · unblock-employer · list-blocked`

- **candidates** ← listings.json → candidates.csv (scores all, reconciles applied status, applies floors).
  The `--listings` arg goes through `pipelib.load_listings` → `data_path` (see "Command-line file
  args" under Data root): a bare `listings.json` resolves in the DATA root, missing/corrupt input
  is FATAL, and an empty scrape leaves candidates.csv untouched instead of blanking the board.
- **skillMatch is SHRUNK, not a raw fraction (2026-07-28):** `scoring.skill_match` returns
  `(matched + k·prior) / (n + k)` where n = skills the source extracted, k/prior come from
  `strategy.json` (`skill_match_shrinkage_k` = 1.0, `skill_match_prior` = 0.0) via `scoring._shrinkage()`,
  read per call so a settings edit needs no restart. **Why:** the raw `matched/n` is meaningless at small
  n — hiring.cafe tagged a "Senior Product Manager, Cloud Video" posting with a single skill (`REST APIs`),
  so 1÷1 scored a perfect **1.00** and tied the genuine 16-skill Site Reliability Engineer hit; 4 of 11
  perfect scores came from ≤2 extracted skills. There is **no title-relevance gate anywhere** (`classify`
  only picks a base résumé), so a thin extraction was the only thing standing between an off-lane role
  and the top of the queue. Defaults behave as *one phantom unmatched skill*: 1-of-1 → 0.50, 2-of-2 → 0.67,
  5-of-5 → 0.83, 16-of-16 → 0.94 — thick well-matched postings barely move. **Zero matched skills always
  returns 0.00**, so a nonzero prior can never lift a no-overlap job over `discovery_min_match`. `k=0`
  restores the old raw fraction exactly. Because every score shifts down slightly, the floors
  (`min_match` / `remote_min_match` / `resume_customize_threshold`) are now effectively a touch stricter —
  most visibly, a 1-skill perfect match no longer clears `resume_customize_threshold` (0.80) and so gets
  a real AI-tailored résumé instead of a verbatim template copy. Guarded by `tests/test_scoring.py`
  (`test_skill_match_shrinks_thin_extractions`, `..._zero_matched_is_zero_even_with_prior`,
  `test_shrinkage_defaults_and_bad_config`); `tests/fixtures/scoring.json` re-snapshotted for the new curve.
- **Title-relevance gate — `scoring.title_fit` / `score_job` (2026-07-28):** there was previously **no
  check anywhere that a posting's ROLE was in the owner's lane** — `classify()` only picks a base résumé, and
  `TITLE_EXCLUDE` (principal|staff|manager|director) lives in `cmd_filter`, a legacy path `candidates`
  never calls. So an off-lane job that shared tool keywords ranked like a real one. `title_fit(title)` →
  `(factor, reason)`; `score_job(title, required)` → `(score, flagged, factor, reason)` = shrunk
  skillMatch × factor. **`cmd_candidates` now calls `score_job`**, not `skill_match`.
  - **In-lane terms come from `config/profile.json` `search.titles`** — the same list the searches use, so
    the gate is per-user with no second list to maintain (`strategy.title_lane_terms` overrides).
    Off-lane phrases default to `scoring._TITLE_OFFLANE`; `strategy.title_offlane_terms` overrides,
    `title_offlane_factor` (0.35) tunes the penalty, **1.0 disables the gate entirely**.
  - **Penalize, don't drop.** An off-lane hit keeps its row in candidates.csv with the reason in the new
    **`titleFit`** column (appended LAST in `CANDIDATE_COLS`; every reader is a `csv.DictReader`, so the
    extra column is backward-compatible). It just falls under `remote_min_match` and stops showing/alerting.
  - **OFF-LANE WINS over in-lane, deliberately.** The in-lane list holds bare modifiers (`cloud`,
    `platform`, `support`) that appear *inside* off-lane titles — "Senior Product Manager, Cloud Video"
    contains `cloud`, so an in-lane-first rule would wave through the exact job the gate exists to catch.
  - **Unknown titles are never penalized** (factor 1.0): the list can only demote what it explicitly
    recognizes. Matching is whole-phrase on a punctuation-normalized, space-padded title (`_title_norm`),
    so `nurse` can't fire on "Nursery" and `ops` can't fire on "devops".
  - **Validated against all 210 historical titles** in candidates.csv + tracking.csv: 151 in-lane,
    57 neutral, **2 penalized — both genuine Senior Product Manager postings, 0 false positives.**
    Live effect: Flock Safety PM 0.50 → **0.17** (under remoteFloor, off the board); UnitedHealth PM
    0.17 → 0.06 (under the 0.10 discovery floor, dropped). Guarded by `tests/test_scoring.py`
    (`test_title_fit_*`, `test_score_job_*`, `test_title_terms_read_live_config`).
  - `jobpipe match` gained **`--title`**, returning `skillMatch` (post-gate), `skillMatchBeforeTitle`,
    `titleFit` and `titleReason` so a single job's score can be explained without a full run.
- **Salary triggers:** (a) scheduled full — `salary_probe.py` (stage 1 + Adzuna) runs after the daily + weekly
  scrapes; (b) scheduled free — the **hourly radar** now runs `salary_probe.py --no-adzuna` (stage-1 posting
  scrape only, zero Adzuna quota) right after `candidates`, so **newly found jobs get a salary within the hour**
  without touching the 250/day ceiling; (c) automatic — `process-queue` probes any queued job with no listed
  salary before emitting, so a resume is never tailored on a blank figure (`--no-probe` opts out; the dashboard
  passes it so a UI click never blocks on network); (d) manual — the tracker's **$ Find** button per row hits
  `/api/salary-probe`, which probes that one id and persists it via `jobpipe salary-apply`; on a probe miss the
  endpoint now falls back for real — it shells `jobpipe salary --title` for the band range and persists that as
  source `band`, so the cell never stays blank when a family matches.
- **Stage-2-pending misses re-probe automatically:** a cached miss with no real Adzuna verdict —
  probed stage-1-only by the hourly radar (no `jobsworthStatus`), before creds existed (`no-adzuna-key`), or while
  quota was exhausted — counts as un-probed once `config/adzuna.json` has an `app_id`
  (`salary_probe.stage2_pending`/`retryable_miss` + the mirrored `_probed` check in `process-queue`), so the next
  full run upgrades them without `--refresh`. A definitive Adzuna miss (`adzuna-no-estimate`/`-implausible`) is
  NOT pending — no churn. A `--no-adzuna` run never re-probes an already-cached id (it only takes fresh ids).
- **Resolved salary map + persistence (fixes vanishing probes):** the tracker no longer trusts the embedded
  `JOBS[].salary` string for display. On load it reads the `salaries` map from `/api/data` (`jobpipe salaries`)
  into a `SALARIES` global and resolves each row through `salaryInfo()`; a **$ Find** click updates that map and
  re-renders, and because the probe wrote `job_details`, a reload re-resolves the same value — so the figure
  survives both refresh and detail-expand (previously it lived only in the DOM/`JOBS`, and candidate rows are
  rebuilt from `CANDIDATES` every render, so it disappeared). Every row now shows a figure (band fallback), so
  the `$ Find` button appears only when nothing resolves at all (no URL + no band family).
- **Salary source tooltip:** hovering any `.salary-val` opens `#salary-tip` explaining the source from its
  `data-kind` — **confirmed** (from the posting, upright text) vs **adzuna** / **rough** (italic + `(est.)`).
  Estimates also offer "🔄 Retry salary lookup" (→ `probeSalary`); a rough band guess additionally shows the
  "Set up Adzuna" nudge + "Don't show" when `!ADZUNA_CONFIGURED` (this replaced the old est-only adzuna-hint
  popover, folding the nudge into the one bubble). Dismissal still persists in `localStorage`.
- **Adzuna-configured signal:** `salary_probe.py --quota --json` now also returns `configured`
  (`adzuna_creds()[0] is not None`). `/api/salary-quota` passes it through; the tracker uses it to drive the
  salary-source tooltip's Adzuna nudge (above). The **Show me how** button → `/help/adzuna` (served from
  `help_adzuna.html`, which embeds a blurred screenshot of the Adzuna access-details page and links there);
  the nudge is server-mode only (never nags in offline file mode).
- **Adzuna creds are GUI-managed:** the Settings (`/setup`) **Adzuna salary API** card reads/writes creds via
  `/api/adzuna` → `config/adzuna.json` (git-ignored). The file remains the single source `salary_probe.py`
  reads, so the zero-Claude script path is unchanged; hand-editing `config/adzuna.json` (from `.example`) still
  works for file-first users. The POST only overwrites `app_key` when a new value is supplied (editing `app_id`
  alone never wipes the key); `{clear:true}` empties both. GET never returns the raw key (only `keySet` +
  last-4 `keyHint`).
- **CODE/DATA split leak (fixed 2026-07-29):** `/api/jobs` and `/api/jobdesc` were written before
  the personal-data split (`3230e6d`) and still read state off the CODE dir via `P()` —
  `P('job_tracker.json')`, `P('tracking.csv')`, `P('candidates.csv')`. With `data_root` set, those
  files don't exist beside the code, so **the card board rendered 0 jobs** (or, where a pre-split
  `candidates.csv` was left behind, a stale pool with NO applied state — every card looked
  un-applied even though `job_tracker.json` had the marks). `/api/jobdesc` also *wrote* the
  employment-type label back to `P('job_tracker.json')`, which would have created a phantom
  tracker in the code folder. All six now use `JSON_PATH` / `TRACKING_CSV` / `CANDIDATES_CSV`.
  **Rule: never build a personal-state path off `HERE`/`BASE`/`P()` — use the `pipelib` constants**
  (`JSON_PATH`, `HTML_PATH`, `TRACKING_CSV`, `CANDIDATES_CSV`, `ARCHIVE_CSV`, `TO_PROCESS`,
  `SALARY_CACHE`, `SALARY_QUOTA`, `GEO_CACHE`, `NOTIFIED`, `RAW_APPLIED`, `SCRAPE_META`), or `_d()`
  for a new one. Same miss found and fixed in `archive.py` (tracking/archived/archive_index/tracker
  — `Backups/` deliberately stays with the code), `skip_job.py` (job_tracker.json/html),
  `tailor_local.py` (to_process.json) and `jobpipe.py` (salary_quota.json).
- **`cfg` shadowing bug (fixed):** `/api/adzuna` GET assigned a local named `cfg`, which made pipelib's module-level `cfg()` local to the WHOLE of `do_GET` — so `/api/profile-status` raised `UnboundLocalError` into its bare `except` and reported `configured:false` **forever, even after a successful save**. `ONBOARD_JS` reads that flag, so every non-Settings page bounced straight back to `/setup?welcome=1` and the Schedule tab was unreachable. The Adzuna locals are now `az` (GET + POST), the Anthropic locals are `an` (GET + POST), and the schedule local is `sc`. **Rule: never bind a name in `do_GET`/`do_POST` that matches an imported module-level helper** (`cfg`, `cfg_write`, `load_json`, `P`, `D`).
- **Data root is GUI-selectable:** the Settings **Where your settings & data are stored** card (`/api/data-root`) writes `data_root` into `config/instance.json` — the same key `pipelib.resolve_data_root()` already honored, so the deterministic script path is unchanged and a hand-edited `instance.json` still works. `instance.json` deliberately stays beside the CODE (the data root is resolved FROM it) and is git-ignored. The server VALIDATES only — creates the folder if its parent exists, write-probes it, warns when the target has no `job_tracker.json` — and never moves data; `$JOB_AGENT_DATA` still wins and the card says so.
- **Folder fields have a real Browse button (2026-08-05):** `/setup`'s **Resume folder** (`template_dir`)
  and **Data folder** (`data_root`) each get a 📂 Browse… button → POST **`/api/pick-folder`** →
  `dashboard_server.pick_folder()`, which runs the tkinter folder dialog in a **child process**
  (tkinter needs the main thread; the handler is on a request thread) and returns the chosen absolute
  path, normpath'd. A web `<input type=file webkitdirectory>` cannot report a path, and the dashboard is
  localhost-only, so the server and the browser are the same machine — this is the only way to get one.
  The endpoint **picks and nothing else**: no validation, no save. The step's Save button / "Use this
  folder" still own that, so a cancelled or failed dialog changes no settings. Zero new deps (tkinter
  ships with CPython); a missing tkinter returns `ok:false` + "type the path instead", exactly the
  behavior the fields had before. Client side: `browseInto(inputId, title)` in `setup.html`, which
  re-fires an `input` event so the step is marked unsaved.
  - **macOS uses `osascript`, not tkinter (2026-08-18):** `pick_folder()` tries
    `_pick_folder_macos()` first on `darwin`. Tk attaches `askdirectory` to its `parent` as a
    **document-modal sheet**, and the parent here is a *withdrawn* root - so on macOS the panel
    was drawn on an unmapped window: **off the visible screen, and a sheet has no title bar to
    drag it back by** (found on macOS). `osascript` runs the same Cocoa panel Finder uses,
    which the window server positions itself, and `tell me to activate` raises it above the
    browser **without System Events**, so it triggers no accessibility-permission prompt.
    Escaping is done in Python (backslash and `"`), and `default location` is parenthesised.
    `_pick_folder_macos()` returns **`None` to mean "fall through to tkinter"** and never an
    error, EXCEPT that **a cancel (`-128`) returns `('', '')` and stops there** - falling through
    on a cancel would pop a second dialog at someone who just dismissed the first. The tkinter
    child now also **drops `parent=` on darwin** (free-floating panel instead of a sheet) so the
    fallback is not broken the same way; `parent=` still keeps it above the browser elsewhere.
  - **Picker hardening (2026-09-05, from the 1.2.0 macOS QA pass):** three things that all ended
    in the same place - back in the off-screen tkinter panel, or holding a path the app then
    failed to recognise.
    1. **A failed `default location` no longer regresses to tkinter.** A stale or unreadable
       `init` makes `osascript` exit non-zero with something that is **not** `-128`, which used
       to return `None` and fall straight through to the picker this whole path exists to avoid.
       `_pick_folder_macos()` now **retries once without `default location`** (the init path is a
       hint, never a requirement) before giving up. `build(loc)` and `run(script)` are inner
       helpers so the second attempt is the same script minus one clause.
    2. **`text=True` is gone.** It decodes with the **locale** encoding, and a server started by
       **launchd** frequently has no `LANG` at all, so the locale resolves to ASCII and any
       non-ASCII folder name came back mangled. Output is captured as bytes and decoded
       **UTF-8 explicitly**, which is what `osascript` emits.
    3. **The returned path is NOT normalised** - it came from the OS and names a folder that
       exists. macOS hands back **NFD** (decomposed) filenames, so the fix belongs on the
       COMPARISON side instead: see `_pathkey()` below.
- **`_pathkey(p)` — the only way paths are compared in `dashboard_server.py` (2026-09-05):**
  `unicodedata.normalize('NFC', os.path.normcase(p))`. macOS stores filenames decomposed, so a
  folder saved as `Tëst` comes back from the OS panel as `Te` + U+0308, `normcase` leaves both
  alone, and a plain `==` says "different folder" - the app then believes it is looking somewhere
  it is not. **All five** path equality tests now route through it (`is_portable()`,
  `nested_user`, `DEFAULT_DATA`, the pending-move check, and `check_data_root()`'s
  app-folder-itself warning). Normalisation is for comparing **only**; nothing hands a
  normalised string to the filesystem.
- **`profile.resume.template_dir` is now READ (2026-08-05):** the field had been written by the wizard
  and the Settings page since day one but nothing consumed it — `pipelib.TEMPLATE_DIR` was hardcoded to
  `<data root|code>/resume_template`, so a browsed folder would have been stored and silently ignored.
  `pipelib._profile_template_dir()` now resolves it first: absolute path as-is, relative against `DATA`
  then `HERE` (so the shipped default `"resume_template"` lands exactly where it always did), and `''`
  for unset/missing/unreadable → the old built-in fallback. Takes effect at import, i.e. on restart, like
  the data root. Everything downstream (`BULLETS`, `BULLETS_SCHEMA`, `template_files()`, `TEMPLATES`,
  scoring's base-resume pick, `tailor_local`/`render_resume`) follows automatically because they all
  derive from `TEMPLATE_DIR`. The zero-token script path still runs with no profile at all.
- **ntfy is explained, not just configured:** step 4's Phone-alerts card states what ntfy is and why the zero-token hourly radar depends on it, links `/help/ntfy` as a button (not buried hint text), and offers **Send test alert** → `/api/notify-test` → `notify.py --test`. The Adzuna card's how-to link got the same button treatment.
- **Adzuna quota + fallback warning:** `salary_quota.json` counts day/week/month calls against the free tier
  (25/min · 250/day · 1000/week · 2500/month) and auto-resets per period. HTTP 429/403 flips an `exhausted` flag.
  While exhausted, stage 2 is skipped and estimates come from `salary_bands.json` — the tracker shows a yellow
  banner saying so (on load via `/api/salary-quota`, and after any probe), and `process-queue` prints the same
  note to stderr for the tailoring run.
- **`salary-apply --id --value --source`** sets `job_details[id].salary` directly. It exists because `update`
  never overwrites an existing job_details entry, so a re-probe could not otherwise correct a stale salary.
- **Salary resolution (one value, no LLM judgement):** `process-queue` emits **`salaryFinal`** + `salarySource`
  per item, resolved in order — `posted` (hiringcafe.com structured field) → `posting` (salary_probe regexed it
  from the job page) → `jobsworth` (Adzuna estimate) → `band` (config/salary_bands.json). Stages 2-3 come from
  `salary_cache.json`. The tailoring run copies `salaryFinal` verbatim into details.json — it must never pick.
  Family matching normalizes titles first (`_norm_salary_title`: dev ops→devops, infra→infrastructure, sr→senior,
  eng→engineer, strips `Engineer II`), which cut the no-family-matched rate on tracked roles from 21% to 4%.
- **process-queue** ← candidates.csv + to_process.json → `tofetch.json`. Emits **`userNotes`** per item
  (the per-job tailoring notes the user typed in the card board's Tailor dialog, stored as
  `to_process.json → notes {id: text}` by `/api/queue`; empty string when none). Instructions for the
  LLM pass only — the deterministic template path ignores it, and readers that only consume `ids`
  (tailor_local.py) are unaffected. **Always prioritizes** (Houston-local first → best skillMatch), then auto-tailors the **top N**: cap = `dailyAutoTailorCap` (`--auto`) / `hourlyAutoTailorCap` (`--hourly`), default 20/10; `--cap` overrides; **hand-queued ids are never capped**. Tags each item `action=template` (skills cover JD → FREE copy) or `action=customize` (LLM-tailor)
- **classify(title, skills)** → base-resume pick: ANALYST / TECH_SUPPORT / CORE, with a skill-aware override (≥3 SRE/infra markers → CORE regardless of title)
- **update** ← details.json / processed.json / run.json → job_tracker.json (calls `backup.py` first)
- **rebuild** ← tracker + scan of `New/ Applied/ Skipped/` → job_tracker.html + job_tracker.json + tracking.csv
  - Tracker table columns (markup lives in `job_tracker.html`, preserved across rebuilds — rebuild only
    re.subs the JOBS/ARCHIVED_JOBS/PIPELINE_SKIPPED/BOOKMARKED/SIMPLIFY_APPLIED arrays), current order
    (2026-07-22 rework): **Mark Applied** (labeled checkbox, was a bare "✓") | Company | Role | **Salary**
    (moved next to Role — `salaryCellHTML()`, sourced from the `SALARIES` map, see below) | **Status**
    (redesigned — badge+star on one line, then a `.status-actions` row of LABELED chips: "⊘ Skip"/"↩ Restore",
    "🔖 Save"/"🔖 Saved", "🚫 Ban employer"/"🚫 Banned" — no more bare icons) | Found | **Apply** | Resume |
    **Applied Date** (moved to right of Resume; was between Status and Found) | Notes. The **Apply** column
    renders a `.btn-apply` link straight from `job.applyUrl` on every row (no need to expand the detail row);
    rows with no captured URL show a disabled dash. Found/Applied Date both run through `dateOnly()` (strips
    any time-of-day component, defensive against non-date-only source data) so they only ever show the
    calendar date. The expanded detail row no longer repeats salary (dropped from `detail-meta`, since the
    Salary column already shows it) — `hasDetail` was adjusted to stop counting salary. Detail-row colspan
    is 10 — bump it if a column is added. The settings panel's local-floor toggle label is city-dynamic: on
    load the page fetches `/api/profile` and swaps
    `#loc-city` to the profile's `search.location.city` (falls back to "Houston" in file mode /
    offline), so other users see their own target city.
- **simplify** ← raw_applied.json → reconciles the Applied set
- **salary-set** → persists a band into salary_bands.json · **parse-cards** ← cards.json → listings.json
- **lint-resume** → passes every argument straight through to `lint_resume.py` and returns its exit code
  (0 clean, 1 rule violation). Run it on each tailored `.docx` before marking a job applied — it enforces the
  CLAUDE.md formatting rules deterministically instead of relying on the agent to remember them.
- **doctor** (`_doctor_collect` + `cmd_doctor`) → compact **read-only** session-start health block: job counts
  by status (tracking.csv), job_tracker.json parse + truncation cross-check (size vs. newest
  `Backups/snapshots/*/job_tracker.json`), queue depth (to_process.json), last scheduled runs
  (scheduler_state.json), last tailoring run (run.json), Adzuna quota (salary_quota.json, often absent),
  stale locks (`.~lock.*#`, `.git/index.lock`), and count of New/ resumes failing `lint-resume`. `--json` for
  machines; exit 1 if any ERROR. Run it first each session (~200 tokens) instead of four exploratory bash calls.
  Guarded by `tests/test_doctor.py`.
- **block-employer / unblock-employer / list-blocked** → manage `config/employer_blocklist.json`
  via `blocklist.py`. `block-employer --name --reason --tactic {ghost|salary|bait-switch|other} --example`.
  Enforced in two places: `cmd_candidates` marks a blocked employer's jobs `status=blocked` and excludes
  them from the tailoring-ready set; `resolve_status` returns `skipped`/source `blocked` on the board
  (applied always wins, so banning never rewrites a job already applied to); `process-queue` skips blocked
  ids as defense-in-depth. Match is suffix-normalized, so ban the name as it appears in listings
  ("Felix Pago", not "Felix"). Guarded by `tests/test_blocklist.py`.

## The questionnaire loop (Claude asks → the owner answers → next pass reads it)

The one place the agent and the user exchange free text about a specific job. All three stores
already existed; before 2026-07-29 only the deprecated table page rendered any of it.

| Leg | Where it lives | Written by | Read by |
|---|---|---|---|
| Questions | `job_details[id].questionnaire` (list of strings) | the LLM tailoring pass (customize items only — template copies always write `[]`) | `/api/jobs` → the card drawer's 📋 section |
| Answers | `job_tracker.json → answers{}` + the `answers` column in tracking.csv | the card drawer textarea → `/api/save` → `apply_marks` (same row shape as notes) | `/api/jobs` → drawer; CSV export/import |
| Instructions back to Claude | `to_process.json → notes{id:text}` | the drawer's **Tailoring instructions** box under the missing skills — the single input (Tailor jumps to it; the questionnaire's ✎ button seeds it from the saved answers) | `jobpipe process-queue` → item **`userNotes`** → the SKILL.md tailoring step; echoed back to the card via `/api/jobs` `tailorNotes` |

The card board closes the loop: answering a question and pressing **✎ Use as tailoring instructions ↑**
seeds the instructions box just above with those answers, so the next pass gets them as `userNotes`. Both
tailoring SKILL.mds treat `userNotes` as outranking their own emphasis judgement (and as the only
sanctioned source of facts outside `resume_template/`, since the owner is the one asserting them) but
never as outranking the hard formatting rules; a non-empty `userNotes` also promotes a
`template` item to `customize`, since a verbatim copy would ignore what he typed.

**Where the agent prompts live:** `C:\Users\<you>\Claude\Scheduled\<taskId>\SKILL.md` — outside this
repo, one folder per scheduled task (`job-alert-resume`, `job-alert-resume-evening`,
`job-alert-resume-weekly`, `job-tailor-queue-processor`). They're read-only to the file tools; edit
them through the scheduled-tasks MCP (`update_scheduled_task`), not by writing the file.

## The Claude / agent layer (`SKILL.md`, not a script)

Claude runs the daily/weekly task, orchestrating `jobpipe` and doing the non-deterministic work:

- **Resume customization (LLM)** — for `action=customize` jobs (skillMatch < `resume_customize_threshold`
  from strategy.json / gui_settings) Claude writes the tailored prose: summary opener, re-weighted &
  rephrased bullets, per-job questionnaire (and it must honor `item.userNotes` — see the questionnaire
  loop above). For `action=template` it's a **deterministic copy** of the
  classified base (no LLM) + LibreOffice (`soffice`) PDF. Both write `New/<Company>/<prefix><id>.{docx,pdf}` (prefix from `pipelib.resume_prefix()`).
- **Chrome scrape fallback** — only when scrape_meta.json is stale/missing → cards.json → `jobpipe parse-cards`.
- **Salary WebSearch** — only when the role's family band is empty → `jobpipe salary-set`.
- **Simplify tracker sync (Chrome)** — reads the Applied column → raw_applied.json → `jobpipe simplify`.
- Base résumé **content** lives in `resume_template/`, one file per lane — an ANALYST base, a
  CORE/SRE base and a TECH_SUPPORT base. Filenames are the user's own and are NOT fixed:
  `scoring._pick_base` keyword-matches whatever `pipelib.template_files()` finds there, plus a
  `STAR Stories …pdf` as the factual source. Never invent skills.
- **`resume_template/bullets.json`** — structured bank of every résumé bullet, each traced verbatim(-or-near)
  to one of the three base résumés (STAR PDF is factual cross-reference only, contributes no bullets). Per-entry
  fields: `id` (unique slug) · `role` (one of the 6 canonical roles, incl. the Rutherford B.H. Yates Museum
  volunteer role) · `track` (CORE/ANALYST/TECH_SUPPORT it
  appears in) · `text` · `tags` · `metrics` · `source` · `trailing_group` (true for the Sr Impl Consultant +
  Impl Analyst + HP Intern group that moves together). Schema: `resume_template/bullets.schema.json`; validated
  by `validate_bullets.py`. Intended as the re-weighting source for tailoring so bullets are picked, never invented.
- **Content vs rendering split:** for customize jobs the agent can now author only a small
  `content.json` (summary, skills groups, role list with `bullet_ids` from the bank + optional
  per-job `bullet_overrides`) and run `python render_resume.py content.json` — the renderer owns
  every formatting rule, hard-errors on any bullet id not in the bank, and refuses to ship output
  that fails `lint_resume.py`. `--extract` converts an existing tailored .docx back to content.json.

## Data root — CODE / DATA split

Personal data lives in its **own folder**, never mixed into the code, so this repo carries no
identity, no application history, and no résumé content. Since **2026-08-06** the default is
**self-contained**: `<app>/user_data_<INITIALS>/`, holding `config/`, the tracker + CSVs, the
tailored resumes, and `resume_template/`. One folder is then the entire install — copy it,
back it up, hand it to another machine. `.gitignore` carries `user_data_*/`, which is what
keeps `git_daily.py`'s `git add -A` from staging the data now that it lives inside the repo.

`pipelib.resolve_data_root()` picks the root, first hit wins:

| # | Source | Notes |
|---|---|---|
| 1 | `$JOB_AGENT_DATA` | per-shell override |
| 2 | `config/instance.json` → `"data_root"` | **how the user relocates the data** — relative paths resolve off the code dir. This is what the setup wizard writes. **Guard (2026-08-06):** a Windows-style value (`C:\...`) read on a non-Windows box is ignored and resolution falls through to 3–6. Without it, `\` and `:` are ordinary characters there and the first `makedirs` creates one folder literally named `C:\Users\...\job_agent_profile_KP` **inside the repo**, silently writing state to junk. |
| 3 | nested `./user_data_*` | **the DEFAULT** (`pipelib.nested_user_data()`) — self-contained layout, zero config |
| 4 | `~/Documents/my_job_agent` | legacy default (`pipelib.DEFAULT_DATA`) |
| 5 | sibling `../job_agent_profile_KP` | legacy repo-beside-repo layout |
| 6 | nested `./job_agent_profile_KP` | transitional, while migrating |
| 7 | the code dir itself | legacy single-folder install |

**Rung 3 is a glob, deliberately.** The folder is named from the user's initials and the
initials live in `profile.json` — which is *inside* the root being resolved, so reading it
here would be circular. `nested_user_data()` therefore globs, and returns `''` unless
**exactly one** `user_data_*` directory exists: two of them (a second person on the same copy,
a half-finished migration) is ambiguous, and picking the alphabetically-first one would point
the pipeline at somebody else's tracker. Ambiguity falls through to the later rungs.

3–6 only win if the directory exists. Step 7 is load-bearing: with no data folder and no env
var the pipeline behaves exactly as it did before the split, so the **deterministic zero-token
script path never depends on the data folder existing** (CLAUDE.md — degrade gracefully).
`SPLIT` is the boolean "data is elsewhere" — still true for the nested layout, since the data
folder is inside the app folder but is not the app folder.

**Moving to the self-contained layout:** `src/ops/migrate_data_root.py` (dry-run by default,
`--apply` to commit) copies the current data root to `<app>/user_data_<INITIALS>`, re-reads
and SHA-256-verifies every file, folds in an outside `resume_template/`, then repoints
`config/instance.json` **last** — so an abort at any earlier point leaves a working install.
It never deletes from the source. One-shot `cleanup_user_data.py` (dry-run by default,
`--apply` to delete) removes leftover personal copies from the code folder — each file only
after verifying the data root holds a matching copy; standalone, nothing imports it.

**Config resolution.** Per-file, not per-directory, because `config/` is split down the middle:

- `cfg(name)` — READ. Data repo's copy wins; the shipped copy is the fallback, so a file that
  hasn't been migrated yet still resolves. **All config reads go through this** — never
  `os.path.join(CONFIG, ...)`.
- `cfg_write(name)` — WRITE. Always lands in `USER_CONFIG` (= `DATA_CONFIG` when split).
- `CODE_CONFIG` — shipped defaults/examples. `DATA_CONFIG` — the user's private config.

**Command-line file args resolve through `pipelib.data_path()` (2026-08-06) — RULE.** A path
that arrives as a CLI argument (`--listings`, `--raw`, `--details`, `--processed`, `--run`,
`--manual`) must never be opened relative to the process cwd. `scheduler.py` runs every step
with `cwd=HERE` (the CODE root) while `scrape.py` writes `listings.json` into the DATA root, so
the saved `schedule.json` step `jobpipe.py candidates --listings listings.json` read a
nonexistent file, got `[]`, and rewrote `candidates.csv` with only a header — the board went
empty while the same run logged 100+ scraped listings and exit 0. `data_path(p)` returns an
absolute path unchanged, otherwise the first hit of DATA → HERE → cwd (falling back to the DATA
path so errors name the right folder). `load_listings(p, cmd)` wraps it for the two commands
that consume a scrape: **missing / unparseable / non-list → `sys.exit` with a FATAL message**
(a path mistake must fail the step, not look like a quiet day), while an **empty list is not
fatal** — a real 24h window can legitimately find nothing, so `candidates` prints
`candidates.csv: UNCHANGED` and skips the write rather than blanking a pool earlier runs built.

| Lives in DATA | Lives with CODE |
|---|---|
| profile.json (+baks, profiles/, active_profile) | strategy.json (scoring behaviour + tuning notes) |
| search_urls / skills / skill_aliases / summary_rules (all generated FROM profile) | salary_bands.json (reference data) |
| gui_settings, schedule, employer_blocklist | `*.example.json` (templates) · **schedule.default.json** — shipped job definitions scheduler.py falls back to (label, **group**, type, steps, description; `group` drives the collapsible sections of `schedule.html` and is NOT in `USER_FIELDS`, so it always comes from here) |
| git.json, adzuna.json, notify.json (secrets — gitignored even there), claude_cli.json (no secret — per-machine binary path) | **instance.json** — bootstrap: the data root is resolved FROM it, so it must sit beside `pipelib.py` · **runtime.json** — live process state (the port actually bound), written by the running server; belongs to the CODE copy because two worktrees sharing one data root are two servers |
| tracker state: tracking.csv, job_tracker.json/.html, candidates.csv, archive_index.csv, raw_applied.json, geo_cache, scrape_meta, salary_cache/quota, notified, scheduler_state | |
| resume_template/ (base résumés, STAR stories, bullets.json) | |

**Modules that resolve their own paths** — `notify`, `salary_probe`, `scrape`, `profile_lib`,
`locality`, `git_daily`, `dashboard_server`, `scheduler` — all import from `pipelib` now rather than
building off their own `HERE`. `dashboard_server` keeps `P()` for **code assets** (setup.html,
schedule.html, archived.html, help_adzuna.html, help_ntfy.html, help_claude_key.html, jobpipe.py, salary_probe.py) and uses `D()` /
the pipelib constants for **data**. Adding a new state file means adding it to `pipelib`, not
re-deriving a path locally.

> **Not yet moved:** `New/`, `Applied/`, `Skipped/` tailored résumé binaries still resolve off
> `BASE` (`jobpipe.py`, `dashboard_build.py`), so they stay with the code while `tracking.csv`
> — which indexes them — lives in the data repo. Worth reuniting; deliberately out of scope so
> far.

## Data flow

```
# candidates.csv cols: id,company,role,skillMatch,flaggedSkills,hasResume,status,source,
#   foundDate,location,salary,applyUrl,repost,repostNote,titleFit,requiredSkills
#   (repost/repostNote filled by reposts.annotate — advisory, never drops the row)
#   (titleFit = "<factor> off-lane: <phrase>", blank when the title is in-lane/unrecognized;
#    skillMatch is ALREADY multiplied by that factor — the column is the audit trail)
scrape.py ─→ listings.json ─→ jobpipe candidates ─→ candidates.csv ─┬→ notify.py ─→ phone (ntfy)
                                                                    └→ jobpipe process-queue ─┐
dashboard "Tailor" ─→ to_process.json ───────────────────────────────────────────────────────┤
                                                                                              ▼
                                                                                          tofetch.json
                                          [agent] template copy  OR  customize (LLM)  ◄───────┘
                                                     │
                                                     ▼
                              New/<Company>/*.docx + *.pdf   +   details/processed/run.json
                                                     │
                                                     ▼
                                jobpipe update ─→ job_tracker.json  ─(backup.py snapshot)
                                                     │
                    job_tracker.json + New/ scan ─→ jobpipe rebuild ─→ job_tracker.html + tracking.csv
                                                     │
                                                     ▼
                                          dashboard_server.py (UI)  ◄── scheduler.py runs steps here
Simplify (Chrome) ─→ raw_applied.json ─→ jobpipe simplify ─→ tracker
git_daily.py ─→ git remote        archive.py ─→ archived.csv / archive_index.csv
```

## Config vs state files

- **Config** (`config/`, settings — hand- or wizard-edited): strategy, skills, skill_aliases,
  salary_bands, search_urls, summary_rules, gui_settings, notify, git, schedule, profile (+ `.example`).
  `gui_settings.json` keys the pipeline reads: threshold/globalFloor/remoteFloor/localFloor,
  `resumeCustomizeThreshold` (the AI-vs-template line: `scoring.strategy()` folds it over
  `strategy.resume_customize_threshold`, `jobpipe` reads it as `cust_thr`, and it is edited in
  **Setup → Add-ons → AI resume tailoring** — it had no GUI editor at all from the retirement of
  `job_tracker.html` until 2026-08-05), `excludeManyApplicants`/`maxApplicants`, `autostart`
  (**legacy** — the Startup `.vbs`; forced to `false` whenever `watchdog` is turned on),
  `watchdog` (default **false** — registering a Windows task is not done unless the user asks),
  `watchdogEveryMin` (default 5), `watchdogPauseUntil` (unix ts; `watchdog.py` no-ops until then,
  so the user can close the dashboard and have it stay closed — all three edited on **Schedule**),
  `dailyAutoTailorCap`/`hourlyAutoTailorCap` (edited in **Setup → Add-ons → AI resume tailoring**;
  they cap **AI-customized resumes only** — `jobpipe.cmd_process_queue` never counts a `template`
  item or a hand-queued id against them, since a template copy costs no tokens),
  **`backlogCap`** (default **50**, `0` = off; edited on **Schedule** → *Pause the searches when the
  board fills up*) — how many PENDING postings pause the `search`-group scheduled tasks. Read by
  `backlog.cap()`, so the zero-token script path gets the number without the dashboard ever having
  been opened. It caps a QUEUE, not a spend: unrelated to the two auto-tailor caps above.
  **`claudeEnabled` is retired** — the card board now learns whether Claude is available from
  `dashboard_server._claude_cli_present()` (is the Claude Code CLI on this machine, 60s-cached),
  surfaced as `claudeEnabled` in `/api/jobs`. A leftover key in an old gui_settings.json is ignored,
  and `/api/threshold` no longer writes one.
- **Per-instance overrides (`config/instance.json`, git-ignored):** neutralizes a staging/test
  worktree without committing anything, so nothing leaks to live on a merge. Keys: `label`
  (nav badge + console tag — this is also the **BETA/DEV chip**: `nav_html()` renders `label` as
  the amber chip beside the Top Rat logo and tints the nav bar, so a dev copy is identifiable at
  a glance. It is *config, not code*, which is why `release.py` needs no "remove the badge" step:
  `instance.json` is outside `CONFIG_ALLOW_EXACT` **and** named in `FORBIDDEN_PATHS`, so an export
  cannot carry a badge — `tests/test_release.py::test_the_beta_badge_cannot_ship` asserts both
  halves), `disable_notifications` (notify.py kill switch — `notifications_disabled()`
  short-circuits ALL ntfy pushes incl. `--test`), `disable_scheduler` (dashboard_server.py skips
  `sched.start()` AND the autostart + watchdog reconcile), `port` (dashboard bind port —
  **`watchdog.py` reads this key too**, so a staging worktree's watchdog probes its own port). Env vars win over the file:
  `JOB_AGENT_DISABLE_NOTIFY`, `JOB_AGENT_DISABLE_SCHEDULER`, `JOB_AGENT_PORT`; port CLI is
  `--port N`. Live has no instance.json → defaults (notify on, scheduler on, 8765). Template
  committed as `config/instance.example.json`. Staging workflow = a `git worktree` on a `staging`
  branch with its own `config/instance.json`.
  - **Busy-port fall-forward + `config/runtime.json` (2026-08-08, git-ignored):** the wanted port
    and the bound port can differ, so the process that owns the socket writes down where it is.
    `dashboard_server.bind_port(preferred, wait=2.5)`: (1) `port_is_ours()` GETs `/api/dev/ping`
    — which now returns `codeDir` — and raises `AlreadyRunning` if **this same copy** answers, so
    a second Start Here reopens the first window instead of starting a rival on the same data
    root; (2) otherwise it re-tries the wanted port for ~2.5s (the `/dev` restart closes its
    socket and relaunches immediately, and falling forward on that half-second would strand the
    restarted server on the next port while the browser polls the old one); (3) only then scans
    `preferred+1 … +PORT_SCAN` (10) and prints a NOTE naming the port it took. On success
    `runtime_write()` records `{port, pid, boot, codeDir}`; `runtime_clear()` removes it on a
    clean stop, and only when the `pid` is still ours.
    **`watchdog.py` and `stop_board.py` each split their resolver in two:** `wanted_port()` is
    the old order (env > instance.json > 8765) and `resolve_port()` = wanted-if-listening, else
    the recorded port **if that port is listening**, else wanted. Configured-first keeps an
    explicit `$TOP_RAT_PORT` authoritative; the liveness check keeps a stale record (crash) from
    hiding a genuinely stopped board — which without this file was the real hazard: the watchdog
    probing an empty 8765 and launching a *second* server every tick. `app.py` no longer
    inherits it via `watchdog.resolve_port()`; see the next bullet. Covered by
    `tests/test_port_fallback.py`.
  - **Who owns the port? (2026-09-06, QA 1.2.1 gate 3): `watchdog.our_port()`.**
    `resolve_port()` answers "which port should I probe", which is NOT the question a
    launcher needs. `app.py` asked the old one, found 8765 answering, and opened the window
    onto whatever was there. In the macOS pass that was an 18-day-old server from a
    `TopRat-main` folder the user had since DELETED, still answering 200 with an empty body
    to every request. That is the normal upgrade path (unzip the new version beside the old
    one), and it is silent, because no version is shown anywhere the user would look.
    So `watchdog.py` gained, mirroring `dashboard_server.port_is_ours()` from the other
    side: `port_holder(port)` (GET `/api/dev/ping`, returns `codeDir`, or `''` when nothing
    identifiable answers), `port_is_ours(port)` (`_same_dir()` against `HERE`; `None` means
    not us / no answer), `_candidate_ports()` (wanted, then `config/runtime.json`, then
    `wanted+1 ... +PORT_SCAN`, the range the server itself falls forward into when
    `runtime_write()` could not write) and `our_port(seconds=0)`, which polls those and
    accepts a **codeDir match only**. `app.ensure_server()` is now `our_port()`, else
    `start_server()`, then `our_port(STARTUP_WAIT)`, and it prints which port it landed on
    when a stranger held the wanted one. `watchdog.py`'s own tick still uses
    `resolve_port()`: restarting the board because a FOREIGN program holds the port is a
    different decision, deliberately left alone. Pinned by `tests/test_first_run_macos.py`.
  - **Two macOS-only fixes in `dashboard_server.py` (2026-09-06, QA 1.2.1):**
    (a) the `/ui.css` route 404s when `read_bytes()` comes back empty. It used to send 200
    with a 0-byte body, so a missing stylesheet rendered as unstyled default HTML with
    nothing in the console and nothing in the network panel: every scripted check passed
    and only a human could see it. The `/assets/icon/` route below it always did this.
    (b) `_pick_folder_macos()` returns `('', 'The folder window did not open. Type the path
    instead.')` once the no-default-location retry is spent; `None` (fall through to the
    tkinter picker) is now reserved for "osascript is not on this machine at all".
    Also: the Setup page's watchdog quick-action says `did not turn on`, never `Windows
    refused`, since launchd is the mechanism on macOS; and `app.py --stop-info` branches on
    `sys.platform` / `os.name` and adds the `lsof -nP -iTCP:<port> -sTCP:LISTEN` line that
    names the exact pid.
  - **Launchers are port-agnostic:** `Start Here.bat` / `Install Watchdog.bat` resolve the
    port via `python dashboard_server.py --print-port` (now prints `live_port() or resolve_port()`
    — a RUNNING instance wins, so the launcher opens the window that exists rather than the port
    the config asked for — and exits without binding) instead of hardcoding one. So both files
    are identical across worktrees and merge-safe, and live + staging dashboards run side by side.
    The static `Open Dashboard.url` can't read config, so it's git-ignored (per-copy).
    **`Start Dashboard.bat` was DELETED (2026-08-06)** — it was a restart (taskkill the PID on
    this instance's port, relaunch via the `.vbs`) that nothing invoked and no doc told a user to
    click, and a second "Start…" file at the root competed with the one entry point. Restart now
    lives on **`/dev`** (below); cold start is `Start Here.bat`. `start_dashboard_hidden.vbs`
    STAYS: it is independently double-clickable as the silent launcher and no longer has a
    `.bat` caller.
  - **Handoff layout (2026-08-05) — ONE file a non-technical user clicks: `Start Here.bat`.**
    It resolves an interpreter (**bundled `python\` → `py` → PATH**), *tests* it (`where python`
    finds the Windows **Store stub**, which is not Python), delegates "is it up?" to `watchdog.py
    --no-open` rather than re-implementing that in batch, then opens the board. **Every failure
    path ends in a `pause`** — a launcher that flashes and vanishes is unusable for the audience
    it exists for. `tools\Make Portable Python.bat` builds the `python\` bundle from python.org's
    **embeddable zip** (curl+tar, both in-box on Win10 1803+): unzip → rewrite `python312._pth`
    (the embeddable build disables `import site`, so pip and packages are invisible until it is)
    → get-pip → `python-docx` (the app's ONE non-stdlib import). Result: no installer, no PATH
    edit, no UAC, nothing for AV to inspect. `start_dashboard_hidden.vbs` and the Watchdog `.bat`
    pair use the same bundled-first resolution; `make_watchdog`/`watchdog` inherit it for free via
    `sys.executable`. **`Install Autostart.bat` + `Uninstall Autostart.bat` were DELETED** — they
    installed the superseded Startup `.vbs`, so a user who clicked them got the double-start the
    watchdog design removes. Maintainer-only scripts moved to **`tools\`** (`Git Daily Commit.bat`,
    `commit_now.bat`, `_rf.bat`, `Make Portable Python.bat`), each now `cd /d "%~dp0.."`.
    **The root now holds only what a non-technical user may click:** `Start Here.bat`,
    `Start Here.command`, `Install/Uninstall Watchdog.bat`, **`Install/Uninstall Watchdog.command`** (2026-08-19 — the mac/linux twins, same bundled-python-first
    resolution, calling the same `make_watchdog.py`), `start_dashboard_hidden.vbs`.
  - **Desktop shell (2026-08-06) — `app.py` at the root, plus `Start Here.command`.**
    `app.py` is a THIN shell and holds no application logic: it imports `watchdog.py` and
    calls `our_port()` / `port_holder()` / `wanted_port()` / `start_server()` rather than
    repeating process launching, then shows the board in a native window via the optional
    **`pywebview`** package. **pywebview missing, or no usable web view → it opens the normal
    browser instead**, which is byte-for-byte the old behaviour; the window is an enhancement,
    never a dependency (same rule as the Claude layer). `Start Here.bat` step 2 was collapsed
    into `"%PY%" app.py` — that one call replaces the old watchdog-then-`--print-port`-then-
    `start` sequence, and since app.py falls back to the browser it is a superset, not a
    regression. **`Start Here.command`** is the mac/linux twin (bash; `cd "$(dirname "$0")"`
    because Finder starts a double-click in `$HOME`; bundled `./python/bin/python3` → `python3`
    → `python`, each *tested* for >= 3.8; every failure path ends in a read-key so it cannot
    flash and vanish). Closing the window does NOT stop the server — it is detached and the
    watchdog would restart it anyway; `python app.py --stop-info` prints how to stop it.
    `src/_version.py` (`VERSION`, `APP_NAME`, `version_string()`) is the one place the version
    lives and supplies the window title. `requirements.txt` documents the four OPTIONAL
    packages (`python-docx`, `jsonschema`, `python-jobspy`, `pywebview`) — the app runs with
    none of them installed. **`requirements-dev.txt` is a separate, developer-only list
    (`pytest`)**: nothing in it is imported by the app, it is never installed for a user, and
    `package.py` strips it back out of the bundle it ships (see Distribution below).
  - **App window by default (2026-08-08) — pywebview is SHIPPED, and installed once otherwise.**
    `app.py` already preferred a window, but nothing ever installed `pywebview`, so in practice
    every new user landed in the browser and the README said so. Two paths now close that gap,
    and they are deliberately different in strictness:
    - *Shipped copies:* `tools\Make Portable Python.bat` gained a **step [5/5]** that pip-installs
      `pywebview` into the `python\` bundle and then **imports `webview` to prove it works** —
      installing is not the same as working, since Windows pulls in **`pythonnet` (.NET)** and the
      backend wants the **Edge WebView2 runtime**, either of which can be absent. A failure here
      **warns and the build still succeeds** (unlike `python-docx`, which stays fatal): app.py
      degrades to the browser and loses no feature, so a bundle that cannot be built would be the
      worse outcome. The check runs on the maintainer's machine so the warning never surfaces on
      the recipient's first launch. **`setuptools` + `wheel` are installed first, and that order
      is load-bearing:** `get-pip.py` installs pip ALONE now and the embeddable zip carries no
      setuptools, so any dependency shipping as an **sdist** rather than a wheel kills pip with
      `BackendUnavailable: Cannot import 'setuptools.build_meta'`. pywebview reaches that through
      **`proxy_tools`** (sdist-only). `python-docx` never exposed it because lxml and
      typing_extensions are both wheels — which is why the gap survived until pywebview was added
      (hit for real 2026-08-08).
    - *Own-Python users:* `Start Here.bat` (`:setup_window`) and `Start Here.command`
      **install it automatically, once**, and only when all three hold: no bundled `python\`, no
      `config/app_window.answered` marker, and `import webview` fails. **Installed, not asked
      (2026-08-18)** — the first build put a yes/no question in front of a first launch, which
      asks the user to decide something they have no way to evaluate; `app.py`'s browser fallback
      makes a failed install cost nothing, so the question bought nothing and a "no" permanently
      denied a window that would have worked. Every failure is swallowed and pip's output is
      discarded (a wall of red text about an OPTIONAL package reads as a broken app): the routine
      prints one line either way and **must never be able to stop the app starting**.
      **Success is judged by `import webview`, never by pip's exit code** — installed is not
      importable, and on Windows the backend can still be missing. The `.command` walks a **flag
      ladder** — `""` → `--user` → `--break-system-packages` → both — because a Homebrew or
      distro Python is *externally managed* (**PEP 668**) and refuses a plain install outright
      (hit on macOS, 2026-08-18); the `.bat` needs only the `--user` retry, for a
      Python whose site-packages this user cannot write. The marker is written **before** the
      attempt and whatever the outcome, so a machine that cannot install it is not re-hammered on
      every launch; deleting it retries.
    Writes `config/app_window.answered` (git-ignored marker, no content read — existence is the
    whole signal). README "Start here" and INSTALL Step 2 track this text; INSTALL Step 2 was
    rewritten again on 2026-08-18 when the question became an automatic install.
  - **MSHTML counts as "no web view" (2026-08-16) — `app.py::has_modern_webview()`.**
    Found by the sandbox QA run (`qa/FINDINGS-2026-08-16.md`): on a clean Windows machine with no
    **Edge WebView2** runtime, pywebview does not fail — it silently selects **MSHTML (IE11)** and
    reports success. IE11 has no flexbox, grid or CSS custom properties, so the board renders as
    unstyled default HTML (blank job canvas, native `<select>` boxes, overlapping Filters control).
    `open_in_window()` returned `True` for that, so the browser fallback the module promises never
    fired and every check in `qa/sandbox/inside/smoke.py` still passed — HTTP 200 on `/`, `/table`,
    `/setup` says nothing about rendering.
    - **`has_modern_webview()`** probes the registry for WebView2's `pv` version value under
      `…\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`, trying **HKLM and HKCU** ×
      **`SOFTWARE\WOW6432Node\…` and `SOFTWARE\…`** (machine-wide installs register under
      WOW6432Node even on x64 because the updater is 32-bit; per-user ones do not). `pv` of
      `0.0.0.0` is the *registered-but-not-installed* placeholder and counts as absent, as does
      any lookup error. **Non-Windows returns `True` unconditionally** — GTK/Cocoa have no
      equivalent trap, pywebview either finds a WebKit or raises, and the raise is already handled.
    - **Absent → `open_in_window()` returns `False` before creating a window**, printing why, so
      `main()` takes the existing `open_in_browser()` path. A browser tab is strictly better than
      an MSHTML window: same board, same features, correct rendering.
    - **`webview.start()` is now called with `gui='edgechromium'` on Windows** — belt to the
      probe's braces. If the registry check is ever wrong, naming the backend makes pywebview
      *raise* rather than fall back to MSHTML, and the existing `except Exception` converts that
      into the same browser fallback. The `TypeError` retry for older pywebview (which rejects
      `icon=`) is preserved and now carries `**kw` too.
    - **…and a clean Windows may have NO BROWSER EITHER — `app.py::has_browser()` /
      `show_url()` (2026-08-16).** Found one screen later in the same sandbox run: with the
      WebView2 fallback working correctly, `open_in_browser()` produced Windows' *"We can't
      open this 'http' link"* dialog while the console printed **"Opened … in your browser."**
      and `Start Here.bat` printed **"Done."** — the app asserting success over the top of an OS
      error. `webbrowser.open()`'s return was never checked, **and checking it is not enough**:
      on Windows it goes through ShellExecute, which returns success and then raises that dialog
      itself. So `has_browser()` reads the association directly —
      `HKCU\…\UrlAssociations\http\UserChoice` `ProgId` — and any lookup failure counts as
      "there is one" (a redundant banner is cheaper than a false claim). With no handler,
      `show_url()` prints the address in a box and says the dashboard is running but cannot be
      displayed. **Deliberately not an error:** the server is up and reachable, so the exit code
      stays 0 — calling it a failure would fail the smoke test on a machine that works. The
      bogus `pip install pywebview` hint went with it; the bundled zip already ships pywebview.
    - **Paired harness change, LANDED 2026-08-16:** `smoke.py`'s `it is a native app window, not a
      browser tab` was **unconditional**, and its premise — a bundled zip can never land in a
      browser — is wrong: bundling pywebview cannot bundle the OS web view. Left alone it would
      have failed the *correct* behaviour while passing the broken one. It now branches on
      `smoke.has_webview2()`: **present** → a browser tab means pywebview failed, FAIL (as before);
      **absent** → the browser fallback is the designed outcome, PASS, and an *app-owned* window is
      by definition the MSHTML backend, so that is the FAIL. `the window belongs to the extracted
      release` drops to INFO when WebView2 is absent — with the board in Edge it is a true and
      meaningless statement about `msedge.exe`. The registry probe is **duplicated** from `app.py`
      rather than imported: `smoke.py` imports nothing from the release it is asserting about, or a
      bug in the app would be inherited by the test meant to catch it.
    - **The GUI poll waited on the wrong thing (corrected same day).** `check_gui` polled
      `for _ in range(12): if matched: break` — and `matched` is a **title** match, which the
      launcher console satisfies before the app exists. So the loop broke out on attempt one,
      every run, and the 60-second budget that exists precisely because *the window lags the
      port* was never spent. With WebView2 installed the window is slower still (the runtime
      initialises on first use), so the 21:41 run reported `no non-browser window owns it` about
      a machine that had opened a perfectly good window seconds later. Now polls on `appWindow`
      (the probe's considered, console-excluded pick) for up to **120 s** — and skips polling
      altogether when there is neither a web view nor a browser, since nothing can appear.
    - **THREE machines, not two — `smoke.has_browser_handler()` (corrected same day).** The branch
      above shipped with a two-case assumption: WebView2 present, or "expect a browser tab". A
      stock Windows Sandbox has **neither**, so the 17:27 run scored the correct behaviour as
      `FAIL` and printed a message that was false as well — *"an app-owned window exists … so it is
      the MSHTML/IE11 backend"* beside a window list containing only two `cmd` consoles. There was
      no app window; `native` and `browser` were both empty, and `bool(browser) and not native`
      cannot tell "nothing displayed it" from "the wrong thing displayed it". Third branch added:
      with no web view and no handler, the only failure available is opening a window **at all**
      (on that machine it could only be MSHTML), the reachability checks above already prove the
      server is up, and an INFO row records that the board is serving but undisplayable. The
      per-window assertions are skipped rather than run against nothing — which is also what
      stopped `a window other than the launcher console is on screen` from failing a machine that
      is behaving correctly.
  - **`python/` — ignored by git, SHIPPED by `release.py` (2026-08-08).** Found while rebuilding
    the bundle: the interpreter was **tracked** (825 files, ~51 MB) and not in `.gitignore`, so one
    rebuild produced an 800-file diff that the next daily checkpoint would have swept into history.
    It is a downloaded artifact, not source. Now `python/` is git-ignored — and because `.gitignore`
    is itself in `INCLUDE_FILES`, the same rule lands in the export, so **neither** repo carries it.
    - **It still ships**, via `release.py --out`: `add_python_bundle()` copies `python/` into the
      export **after** `verify()` passes, and is skipped entirely under `--check`. Both exclusions
      are deliberate, which is why this is not simply another `INCLUDE_DIRS` entry: `verify()`
      **also skips `python/` by path** at the top level of its walk — copy ORDER alone was not
      enough, because the export folder is REUSED: on the second build the previous bundle is
      already sitting in it when `verify()` runs, and CPython's own licences produced 40 failures
      (upstream author emails, DFARS clause numbers matching the phone pattern, "Santa Clara, CA
      95051", `python312.zip` tripping the binary rule). Exemption is **top level only** — a
      nested `src/python/` is still scanned, or the rule would double as a hiding place. The file
      count excludes the bundle too, so "N files total" stays a usable did-this-change signal.
      Covered by `test_verify_ignores_the_bundled_interpreter` +
      `test_bundle_exemption_is_top_level_only`. Note `verify()`
      **rejects binaries** and the bundle is 800+ `.exe`/`.dll`/`.pyd` files that would fail a gate
      designed for *our* content, and `--check` discards its output so copying 51 MB into a temp
      folder buys nothing. Destination is `rmtree`'d first — a merged copy would keep an old package
      set (a stale pywebview, a dropped dependency) alive invisibly in the ship folder.
    - **Procedure lives in `RELEASE_RUNBOOK.pdf`** (project root, maintainer-only — not in
      `INCLUDE_FILES`, so it does not ship): steps 0–8 from `run_tests` through `git_daily`,
      plus a troubleshooting table. `PUSH_TopRat.md` was trimmed to the one-time GitHub repo
      creation and now defers to it, so the two cannot drift into contradicting each other.
    - **Two delivery channels, on purpose:** git carries the code (small, cloneable, diffable);
      the **zip of the export folder** carries the interpreter, for the user who does not know how
      to install Python. `--no-python` opts out. Missing `python/` is a warning, not a failure —
      the export is still valid, it just expects Python to be present.
  - **RENAME (2026-08-07): the app is "Top Rat"; the ship repo is `TopRat`.** Three kinds of
    name were involved and they carry different risk, which is why they were handled separately:
    - *Strings* (`_version.APP_NAME`, window title, launcher banners, README/INSTALL, docstrings)
      — no state, renamed outright.
    - *Environment variables* — `TOP_RAT_*` now, with **`JOB_AGENT_*` still honoured as a
      fallback** via `pipelib.env(suffix)` / `env_truthy(suffix)`; the new name wins when both
      are set. The old names may already be set in a Windows task or a shell profile, and a
      rename there fails SILENTLY (the var is simply unset, the code takes its default, and the
      user gets a working app pointed at the wrong data root). `watchdog.py` and `stop_board.py`
      each keep a LOCAL `_env` copy rather than importing pipelib — both are written to work
      when pipelib is absent, and the port lookup cannot afford an import error.
      **Precedence hazard, learned the hard way:** `tests/test_backlog.py` isolated itself by
      setting only `JOB_AGENT_DATA`, so a `TOP_RAT_DATA` inherited from the caller's shell
      outranked it and 14 tests ran against the wrong root. The fixture now sets the new name
      and `delenv`s the old one. Anything else that pins a data root must do the same.
    - *Registered objects* — `TASK_NAME` is now `TopRatWatchdog` and the autostart launcher is
      `Top Rat Dashboard.vbs`. These live in **Windows**, not in this repo, so changing the
      constant unregisters nothing. Both modules therefore carry `LEGACY_TASK_NAMES` /
      `LEGACY_VBS`: `is_enabled()` returns True for a legacy entry (a pre-rename copy really
      does have a working watchdog, and reporting "off" would invite the user to switch it on
      and end up with two), `install()` removes the legacy entry **only after** the new one is
      created (a failed create must never leave zero watchdogs), and `uninstall()` removes
      legacy entries unconditionally, because "off" has to mean nothing is left restarting the
      board. Delete an entry from those lists only when no copy of that vintage can still exist.
  - **Release pipeline (2026-08-07) — `src/ops/release.py` is the ONLY way the ship copy is
    built.** TopRat is not a branch of this repo: this history carries tracking data, job
    descriptions and real resumes, so the ship copy is a fresh TREE with its own history.
    **Never subtree/filter-branch this repo into it** — that grafts the history you are trying
    not to publish. `build()` copies from an **allowlist** (`INCLUDE_FILES` / `INCLUDE_DIRS`,
    plus a config rule that admits only `*.example*` and the three shipped defaults). Allowlist,
    not blocklist, on purpose: a blocklist ships every new file by default and omits only what
    someone remembered to forbid, which is the wrong failure direction when the thing being
    forbidden is personal data. **Add a new top-level file to `INCLUDE_FILES` or it silently
    will not ship.** That is not a hypothetical: 1.2.0 shipped **no** `Install Watchdog.command`
    or `Uninstall Watchdog.command` — both were written, committed, and named in `INSTALL.md` as
    the macOS way to turn on "Keep it running", and nobody added them to the list, so every zip
    went out without them and the install document pointed Mac users at files their download did
    not contain (1.2.0 macOS QA, F-2). Both are in `INCLUDE_FILES` now, and
    **`test_every_double_clickable_launcher_ships`** (`tests/test_release.py`) closes the class:
    it walks the project root for `.bat` / `.command` / `.vbs` and asserts each one reached the
    export. Matched by **suffix**, deliberately — a second hand-maintained list is the thing that
    failed. `verify()` is the other half and the one that matters: it re-derives the
    verdict from the files ON DISK (not from what `build()` believes it copied) against
    `FORBIDDEN_PATHS` / `FORBIDDEN_SUFFIXES` / `SECRET_PATTERNS`, so a bug in the allowlist
    surfaces as a failed check rather than as a resume on the internet. CLI: `--check` (build to
    a temp dir, verify, discard), `--out DIR`, `--push` (refuses unless the tree verifies clean).
    Stdlib only, zero tokens, no network without `--push`. **`tests/test_release.py`** runs a
    real build into `tmp_path` and additionally plants a fake key / data folder / resume / real
    config file to prove the guard can still FAIL — a guard that cannot fail is not a guard.
    (Its fake API key is assembled at runtime; written literally it would trip its own scanner
    and GitHub push protection.)
  - **Stale artifacts INSIDE the export folder self-heal (2026-08-19).** `build()` already swept
    `_STALE_TOP_LEVEL`, but that list matches by NAME and could not know about the two things a
    misdirected run leaves behind: a **nested export** (`--out TopRat` with the cwd already inside
    `TopRat` builds `TopRat/TopRat`) and the **release zip** dropped beside it. Neither is tracked
    by git, so `git status` and `git ls-files` both look clean and nothing reaches GitHub - but
    `verify()` walks the DISK, so the nested tree's own `python/` bundle (exempt only at the export
    ROOT), its `.docx` fixtures and its `LICENSE` copyright line were all scanned as export
    content. A 1.1.0 leftover failed the 1.2.0 gate on ~60 problems, none of them real, and buried
    the fact that the export itself was clean - `--check` passed at the same moment, because it
    builds into an empty temp dir and never sees the folder that is actually shipped. `build()`
    now deletes a directory whose name equals the export folder's, plus any `*.zip` at the export
    root, immediately after the `_STALE_TOP_LEVEL` sweep. Root ONLY, deliberately: a `.zip` deeper
    in the tree is somebody's fixture, and the interpreter's own `python/python312.zip` must
    survive.
  - **PII gate inside `verify()` (2026-08-08).** The pre-existing guard checked forbidden PATHS,
    binary documents and API-KEY-shaped secrets — it had no concept of a person, so the owner's
    name, phone, home ZIP, personal email, LinkedIn handle, real employer history and 643-row
    applied-jobs fixture all passed it as "clean". Two layers now run over every text file:
    **`PII_PATTERNS`** (shape-based — phone, email, LinkedIn handle, `C:\Users\<name>`, `City,
    ST NNNNN`, `First_Last_Resume_`) which works everywhere **including CI**, and
    **`_local_identity()`** which matches the full name, the underscored filename form AND
    **each name token on its own** (≥4 chars, word-bounded via `_needle_re`) — prose says "runs
    on <First>'s machine", never the full name, and 19 such references survived a full-name-only
    first pass. It reads real values (`identity.full_name` / `resume_prefix` /
    `past_companies` / `real_titles`, `git.name` / `git.email`, `notify.topic`) out of the
    git-ignored `config/profile.json` via `pipelib.cfg` — present on the release machine, absent
    in CI. **The split exists because release.py itself ships:** a literal denylist of the real
    name and phone would publish the exact strings it exists to suppress. Same reason the tests
    use a fake persona assembled at runtime (`_s()`). `PII_ALLOW` whitelists deliberate
    placeholders by MATCHED TEXT (Jane Doe, `example.com`, `(555)`, `C:\Users\<you>`) so generic
    defaults stay silent — a bare "Houston" is a default location and is NOT flagged; a ZIP,
    phone or handle is. `PII_EXEMPT_LINES` carves out **only** the `Copyright (c)` line of
    `LICENSE` (attribution is the point of that file) — one line, not one file, and scoped to
    that filename. Problems are reported `label: path:line (match)`.
    **The relative PATH is scanned as well as the contents** — for a BINARY file the name is
    the only leak there is, and `tests/fixtures/docx/` is binary-exempt, so a linter fixture
    called `<First>_<Last>_Resume_Acme.docx` was invisible to every other check in `verify()`.
    **Three defects this surfaced, all fixed in the same change:**
    (1) `TEXT_NAMES` — the scanner keyed off extensions only, so `LICENSE`, `.gitignore` and
    every extension-less file was **never opened**, by the secret check either.
    (2) `_STALE_TOP_LEVEL` + config pruning in `build()` — `INCLUDE_DIRS` are `rmtree`'d and
    self-clean, but `config/` is filtered rather than replaced and top-level files are only
    copied, so **dropping a name from the allowlist did not remove what a previous release had
    already put in the export folder**. A hand-built `TopRat/` was holding nine live config
    files (real `profile.json`, `git.json`, `notify.json` with the private ntfy topic) plus
    `scheduler_state.json` — git-ignored there, so invisible to `git status`, and caught only
    because `verify()` walks the disk. Un-shipping now actually un-ships.
    (3) `PRUNE_RELPATHS` — a nested-path exclusion for files that must exist in the dev tree
    but are private and useless outside it. One entry today:
    **`tests/fixtures/content_alkami.json`**, a real tailored resume (name, contact line, five
    real roles). It is the input to `test_fidelity_against_alkami`, which diffs the render
    against the actual `New/Alkami/*.docx` — a file that never ships — so in a clone it can
    only skip. It loads lazily inside that one test, unlike the other goldens which load at
    module import, so dropping it costs CI nothing. **Do not add the import-time goldens
    (`applied.json`, `salary.json`, `cards.json`, `local.json`, `scoring.json`) here** —
    CI collects tests from those modules and would fail at import.
  - **Goldens must not encode the owner's resume FILENAMES (2026-08-08).** `scoring.json` stored
    `classify.baseResume` verbatim, i.e. `resume_template/<owner's file>.docx`, 74 times.
    `make_fixtures.depersonalize_classify()` now rewrites it to a slot token (`<CORE base>`,
    `<ANALYST base>`, …) at generation time and `test_scoring._slot()` applies the SAME mapping
    to live output before comparing — **change one and change the other.** The golden still pins
    that a title lands on the right base, which is what it was ever testing; which real file
    fills that slot is covered by `test_pick_base_prefers_docx_over_its_pdf_twin`.
    Test-side resume filenames are either the `Jane_Doe_` persona (synthetic inputs) or built
    from `pipelib.resume_prefix()` (`test_render_resume`), never spelled out.
  - **`CLAUDE.md` and `docs/` no longer ship (2026-08-08).** CLAUDE.md is the personal
    resume-tailoring rulebook (real name, real role history) and `docs/` is the internal working
    record — refactor playbooks, staging steps, `C:\Users\<name>` paths. Deleted from
    `INCLUDE_FILES` / `INCLUDE_DIRS` rather than scrubbed: they are notes-to-self, and
    `README.md` + `INSTALL.md` are what a user of the shipped copy needs. `DEPENDENCY_MAP.md`
    still ships and is gate-checked, so keep it free of real identifiers.
  - **Distribution (2026-08-08): a Release ZIP, not a repo clone.** `release.py` builds the
    sanitized SOURCE tree for the git repo; **`src/ops/package.py`** builds what a person
    downloads. Two reasons the repo is the wrong handoff: TopRat is **private**, so "download
    the ZIP from GitHub" needs an account and a collaborator invite, and a clone carries no
    interpreter. `package.py` stages `release.build()`, copies in a `python/` bundle,
    **re-runs `release.verify()` on the staged tree** (the bundle landed after the first
    check, and a published zip cannot be un-published), then writes
    `dist/TopRat-<version>-<platform>.zip`. It refuses to write anything at all if the tree
    is dirty. The zip prefixes every path with its own stem, so an unzip yields ONE folder
    rather than 150 loose files, and it preserves the POSIX mode bits — without that,
    `Start Here.command` and `python/bin/python3` extract non-executable and die with
    "permission denied" for no visible reason. **The mode is decided by PATH, not by the
    build host (2026-08-11):** `_zip_mode()` forces `0o755` for `EXEC_SUFFIXES`
    (`.command`, `.sh`) and `EXEC_DIRS` (`python/bin/`). Copying `os.stat().st_mode`
    straight through was correct only on POSIX — NTFS has no exec bit and reports `0o666`
    for every file, so a zip built on Windows shipped the launcher non-executable, which
    is the exact failure the surrounding comment warns about. The old
    `test_zip_keeps_the_executable_bit` could not catch it (it asserted on a zip built by
    the host, so it only failed *on* Windows); `test_zip_mode_decides_exec_from_the_path_not_the_host`
    feeds a synthetic `0o100666` and now catches it everywhere. **The bundle copy is filtered (2026-08-11):**
    `_copy_python()` passes `shutil.copytree(ignore=_prune_dev)` to drop `package.DEV_ONLY`
    (`pytest`, `_pytest`, `pluggy`, `iniconfig`, their `*.dist-info` and console scripts).
    The working `python\` doubles as the developer's test interpreter — `tools\run_tests.bat`
    pip-installs `requirements-dev.txt` into it on first run — so without the filter every
    release zip would carry a test runner for a tree that ships no fixtures.
    `tests/test_package.py::test_dev_only_packages_are_stripped_from_the_bundle` pins it.
  - **The repo export and the download differ by design (2026-08-15): `package.ZIP_EXCLUDE`.**
    `release.py` exports `tests/` + `pytest.ini` to the TopRat git repo — a maintainer who
    clones it wants the suite, and the exported `.github/workflows/ci.yml` exists to run it —
    but `package._strip_zip_only()` removes both from the staged tree on the way into the zip,
    right after `release.build()` and before the interpreter is copied in. The download already
    ships no pytest (`DEV_ONLY`, above), so a suite in it is ~2 MB the user cannot execute:
    a test suite with no runner is a puzzle, not a safety net. **The split is by ARTIFACT, not
    by allowlist**, which is why it lives in `package.py` and not in `INCLUDE_DIRS`. Each side
    asserts its own half so deleting one does not silently break the other:
    `test_package.py::test_zip_carries_no_test_suite` (absent from the zip) and
    `test_release.py::test_the_test_suite_does_ship_to_the_repo` (present in the export).
  - **Launchers the target OS cannot run (2026-09-06, QA 1.2.1): `package._strip_foreign_launchers()`.**
    `ZIP_EXCLUDE` is a flat list that knows nothing about the target, so the macOS 1.2.1
    archive carried 14 Windows files (`Start Here.bat`, `start_dashboard_hidden.vbs`,
    `tools/Repair Windows Tasks.bat`, `tools/Make Portable Python.bat` and nine more) into
    the same folder `INSTALL.md` walks a non-technical Mac user through. The new step runs
    right after `_strip_zip_only()` and drops `*.bat` / `*.vbs` **when the TAG starts with
    `macos`**, keyed off the artifact's tag and never `platform.system()`, so
    `--tag macos-arm64` builds a macOS zip anywhere and the suite can assert it without a
    Mac. One-directional on purpose: a Windows zip keeps the three `.command` files, since
    that user is double-clicking the `.bat` and has nothing to be confused by. The repo
    export keeps everything, being the cross-platform source. Pinned by
    `test_package.py::test_macos_zip_ships_no_windows_launchers` and
    `test_windows_zip_keeps_its_launchers`; `test_zip_contains_what_a_user_needs` now
    varies its required set by tag.
    `test_strip_is_scoped_to_the_staged_copy` pins that the rmtree cannot escape the stage —
    the same function pointed at `HERE` would delete the real `tests/`.
  - **Who RUNS `package.py` (2026-08-16): `.github/workflows/release.yml`.** The bundle must be
    built on the target OS — `bundle_python.install_packages()` pip-installs `python-docx` +
    `pywebview` and that step selects native wheels (pyobjc on macOS) — so one machine cannot
    produce the three downloads INSTALL.md's table promises. Cross-building with
    `--triple ... --no-pip` is the trap it closes: it runs fine from Windows and yields a mac zip
    with a bare interpreter, i.e. no `.docx` at all. The workflow is **tag-only** (`v*`) plus a
    manual dry run; `push: branches` must never be added, for the reason ci.yml's header gives
    (macOS bills 10x on a private repo), and every job carries `timeout-minutes` so a hung asset
    download cannot bill at that rate for hours. Order is `guard` (ubuntu, 1x — the ci.yml test
    deselection + `release.py --check` + **tag vs. `src/_version.py`**, so no macOS minute is
    spent on a tree that cannot ship) → `build` (matrix: windows-latest, macos-latest = arm64,
    **macos-15-intel** for Intel — `macos-13` was retired 2025-12-04 and a job requesting a dead
    label QUEUES instead of failing, which hangs `publish` even with `fail-fast: false`;
    `macos-15-intel` is the last x86_64 image Actions will offer and expires August 2027) → `publish` (needs `contents: write`;
    `gh release create --draft --verify-tag`, so the last gate is a person downloading one and
    double-clicking it). The build leg **asserts** the platform tag `package.platform_tag()`
    derived rather than passing `--tag`: forcing the label would mislabel the zip if a runner
    image changed arch (macos-latest has done exactly that), and a mislabeled download is worse
    than a failed build. Its zipfile check re-proves the four properties the surrounding code
    cares about — bundled interpreter at the launcher's path, `docx` + `webview` in
    site-packages, no `tests/`/`pytest.ini` (see ZIP_EXCLUDE above), and the `.command` /
    `python/bin/` exec bits, which is why that check runs on the **Windows** leg too. It does
    NOT push to the ship repo: `release.py --push` stays a hand-run command. `.github` is in
    `release.INCLUDE_DIRS`, so this file also lands in TopRat and works there unchanged.
  - **`qa/` — the release harness, and it ships NOWHERE.** Not in `INCLUDE_DIRS`, so
    `release.py`'s allowlist excludes it by saying nothing about it; `'qa'` (and `'dist'`) are
    in `_STALE_TOP_LEVEL` so a hand-dropped copy is removed from an existing export on the next
    build; `test_release.py::test_the_sandbox_harness_does_not_ship` pins both. It is a
    different KIND of test from `tests/`: it imports nothing from `src/`, and instead drives the
    built `dist/TopRat-*-windows-x64.zip` from outside on a machine that has never seen the
    project. `qa/sandbox/Run Windows Sandbox Test.bat` (host) resolves the repo root, checks
    `WindowsSandbox.exe` exists and a zip is present, then generates `TopRat.generated.wsb`
    from `TopRat.wsb.template` (mapped folders must be ABSOLUTE host paths, so a committed
    `.wsb` would hardcode one machine) and launches it. Inside, `inside/logon.cmd` unzips to the
    Desktop, then runs `inside/smoke.py` **with the bundled interpreter** — `--phase before`
    snapshots scheduled tasks / HKCU Run / profile folders, then `Start Here.bat` is launched,
    then `--phase check` polls `config/runtime.json` for the bound port, GETs `/`, `/table`,
    `/setup`, `/api/jobs`, scans `logs/execution.log`, re-snapshots for containment, and
    verifies the download carries no `tests/`, no `pytest.ini` and no dev-only packages.
    Verdict lands in `qa/results/` (the one read-write mount; git-ignored). `smoke.py` is
    stdlib-only on purpose: it runs on a machine with no pip and no guarantee of network.
    **First green run 2026-08-16: PASS, 14/14** on a clean machine with no Python, after the
    host's own Sandbox app was repaired (see the hostfxr note below). **That 14/14 was not
    trustworthy** — four of its window checks measured `cmd.exe` (FINDINGS Fix 2) and its
    browser-tab check had the wrong premise. The first verdict that means what it says is
    **PASS 21/21, 21:50**, on the assisted run with WebView2 installed: `judging window: python
    "Top Rat 1.1.0"`, ownership asserted at FAIL severity, and the resize proven to have reached
    the window it claims to have resized.
    **GUI checks — `inside/gui_probe.ps1` (2026-08-16).** The scripted phase used to stop at
    "the server answered", leaving the most user-visible property of the release — *does a
    window open, and is it a real window or a browser tab?* — to a human reading a checklist,
    i.e. untestable unattended and unreviewable afterwards. `gui_probe.ps1` enumerates
    top-level windows (`MainWindowHandle`/`MainWindowTitle` + `GetWindowRect`/`IsWindowVisible`
    P/Invoke) and captures the desktop with `System.Drawing.CopyFromScreen`; `smoke.check_gui`
    polls for the window (it lags the port — the server binds, THEN pywebview builds a
    webview), then asserts it exists, is **not** owned by `chrome`/`msedge`/`firefox`/
    `applicationframehost` (a bundled zip ships pywebview so the browser fallback must be
    impossible), has a plausible size, is visible, belongs to the extracted copy, and survives
    a `SetWindowPos` resize. PNGs go to `qa/results/shots/` and outlive the sandbox, so window
    checks are evidence rather than trust.
    **Which window is THE window — FINDINGS Fix 2, landed 2026-08-16.** The probe picked the first
    title match, and the launcher console is titled `Administrator:  Top Rat` while the app window
    is `Top Rat 1.1.0` — both match, and the console sorts first. Four consecutive checks (size,
    visibility, ownership, resize-survival) were therefore measured against `cmd.exe`, and the
    `900x700` resize landed on the console while the app window sat untouched at 1280x860 — all
    reported `[ok]`. `gui_probe.ps1` now takes **`-AppDir`** and ranks with `Select-AppWindow`:
    consoles are never the app window, and a process running out of the extracted release beats one
    that is not. The decision is emitted as **`appWindow`**, so `smoke.py` reads the probe's recorded
    pick instead of indexing a list (it re-applies the console filter on the legacy fallback path).
    `the window belongs to the extracted release` was promoted **WARN → FAIL** — its old comment
    called it vacuous in the sandbox, and it was not: it was the one check that noticed the window
    being measured was `C:\Windows\system32\cmd.exe`. New assertion `the resize actually reached the
    window` compares before/after dimensions, because a reflow check that cannot tell whether it
    resized anything is not a check. **PowerShell rather than more Python** because
    `smoke.py` must stay stdlib-only on the bundled interpreter: screen capture from stdlib
    means ctypes against gdi32 plus a hand-rolled PNG encoder over zlib, to reimplement what
    `System.Drawing` already provides on every Windows install. `inside/CHECKLIST.md` is now
    only what needs judgement (does the board *read* as broken) or deliberately changes the
    machine (watchdog opt-in, uninstall).
    **ASSISTED RUN — `sandbox/Claude Sandbox.bat` → `TopRatClaude.wsb.template` →
    `inside/claude_logon.cmd` (2026-08-16). A SECOND entry point; the unattended one above is
    untouched.** Installing Claude in the sandbox had been rejected on three grounds, all of
    which the implementation answers rather than argues with: *reinstall every run* — no,
    `Stage Claude Bundle.bat` → `stage_claude.ps1` copies a `claude.exe` into git-ignored
    `qa/sandbox/claude/` ONCE (Claude Code's npm `bin` is a self-contained executable, so there
    is no Node and no network install; it reuses the local install found the same way
    `llm_tailor.find_claude()` finds it, and falls back to pulling
    `@anthropic-ai/claude-code-win32-x64` straight off the registry); *interactive sign-in* —
    no, `%USERPROFILE%\.claude` is mounted **read-only** at `C:\hostclaude` and
    `claude_setup.ps1` copies `.credentials.json` in, so **no credential is ever written into
    the repo folder** and the VM cannot corrupt the host's Claude state; *tests would depend on
    Claude* — no, separate `.bat`, `.wsb` and logon script, so deleting `qa/sandbox/claude/`
    leaves the zero-token path exactly as it was, per the CLAUDE.md design rule. What changed
    the answer: the 2026-08-16 run returned a scripted `PASS` on a build whose board rendered
    in IE11 with no styling — every reachable assertion passed and the defect was in what the
    screen *looked like*. Driving that from the HOST is not practical: the sandbox window is a
    remote-session client, so a click lands on a video of a desktop, coordinates are scaled,
    there is no shell, and nothing reads back but pixels. Inside the VM there is a prompt and a
    real UI Automation tree. `claude_logon.cmd` sets Claude up, `start`s the **unchanged**
    `inside/logon.cmd` in its own window (so unzip/launch/assert logic lives in exactly one
    place), polls up to 300 s for `verdict.txt`, then opens `claude
    --dangerously-skip-permissions` in `C:\work` — correct in a throwaway VM whose only
    writable host folder is `C:\results`, and nowhere else. `inside/SANDBOX_CLAUDE.md` is
    copied to `C:\work\CLAUDE.md` as the briefing.
    **NEVER MAP A SUBFOLDER OF A MAPPED FOLDER.** The first version added a `C:\claudebin`
    mount for `qa/sandbox/claude/`, which is a CHILD of the `qa/sandbox` folder already mapped
    to `C:\qa`. Windows Sandbox answers a nested pair by applying **no mappings at all**, and
    silently: the VM boots to an ordinary desktop, `C:\qa` does not exist, the LogonCommand
    path resolves to nothing, and nothing runs — no error anywhere, on the host or in the VM.
    It presents as "the sandbox launched and just sits there", with an empty results folder as
    the only clue. The mount is gone; `claude_setup.ps1` reads `C:\qa\claude\claude.exe`, which
    the existing mount already exposes (`C:\claudebin` kept as a second candidate so an older
    generated `.wsb` still works). The generator now also **aborts if any `{{TOKEN}}` survives
    substitution** — an unreplaced token in a `HostFolder` is the other way to produce a
    silently inert `.wsb`.
    **Two modes; WebView2 INSTALLED by default, `-Bare` opts out (2026-08-16).** A stock
    sandbox has no WebView2 *and* no browser, so the board cannot be displayed there at all.
    That is a true property of a bare Windows — and one essentially no real user is in, since a
    normal machine has Edge. Judging "does the board read as finished" through a condition
    nobody ships into is the wrong lens, and looking at the product is what this mode is for.
    **The bare case is not lost:** `Run Windows Sandbox Test.bat` boots a stock machine on every
    unattended run, so the mean case stays covered by the run that is meant to be mean.
    `Stage Claude Bundle.bat` fetches `MicrosoftEdgeWebview2Setup.exe` (~2 MB, non-fatal if it
    fails), and a missing bootstrapper **degrades to bare with a warning** rather than refusing
    to launch — the run is still worth having, and being told which machine you got beats being
    blocked. The mode travels
    as a **marker file** (`.install-webview2`) written into the results folder, because a `.wsb`
    `LogonCommand` is fixed at generation time and takes no arguments, and results is the one
    mount the VM can both read and write. The install must run **before** `Start Here.bat`:
    `app.py` probes for the runtime once at startup and commits to window-or-browser there.
    **`IS_SANDBOX=1`, and Claude gets its OWN window (2026-08-16).**
    `--dangerously-skip-permissions` **refuses to run under elevated/root privileges and exits
    1**, by design. Windows Sandbox logs in as `WDAGUtilityAccount`, an administrator, so the
    session died three seconds after every launch — `logon.log` caught it (`starting claude` →
    `claude exited with 1`) after it had been mistaken twice for "Claude never started".
    `IS_SANDBOX=1` is Claude Code's own documented escape hatch for that check and is a
    statement of fact here, not a workaround. The launcher now also **probes headlessly first**
    (`claude … -p` into `claude_probe.log`), which records the refusal reason in a file instead
    of a TUI that clears on exit, doubles as an end-to-end auth check that `--version` cannot
    give, and **falls back to a prompting session** rather than none if the flag is still
    refused. The session `start`s in its own titled window because, run inline, it was invisible
    whenever the logon console sat behind the app window. Desktop shortcuts (`Start Claude`,
    `Read Checklist`, `Read Verdict`) exist because **the sandbox image has no text editor at
    all** — no notepad, no wordpad — so reading a file there means `more`.
    **QuickEdit is disabled at logon (2026-08-16).** `reg add HKCU\Console /v QuickEdit 0` runs
    before any console is spawned, because a console reads that key at creation. QuickEdit is on
    by default and one stray click puts a window into selection mode, where **every write to
    stdout blocks** — the run freezes mid-line with no error, no output and no files, and the
    only visible symptom is a `Select ` prefix on the title bar. It presented as "the cmd shows
    but nothing happens" and was diagnosed from a screenshot's title bar; `Esc` in the window
    releases it. Noted in `SANDBOX_CLAUDE.md` as the first thing to rule out on an apparent hang.
    **Elevation is a FALLBACK on the assisted launcher, and the reason is UIPI (2026-08-16).**
    Windows blocks input from a lower-integrity process to a higher-integrity one, so an
    **elevated** sandbox window cannot be clicked or typed into by Claude Desktop or any other
    normal-integrity automation — screenshots still work, input is silently refused. Since the
    whole point of the assisted run is driving the GUI, `Claude Sandbox.bat` now tries
    **unelevated first** (21 s budget), and only re-launches itself with `-Elevated` via RunAs
    if nothing appears (90 s budget, `-Elevated` also guarding against a loop). Adding the
    account to the local **Hyper-V Administrators** group makes the unelevated path work
    permanently — which is the configuration to be in if the sandbox is to be automated at all.
    `Run Windows Sandbox Test.bat` still self-elevates unconditionally: it is unattended, so
    nothing needs to click into it.
    **Elevation (2026-08-16):** both launchers self-elevate via `Start-Process -Verb RunAs`.
    Creating the container is privileged, and an unelevated attempt fails invisibly —
    `WindowsSandbox.exe` is a shim that hands off and returns 0 regardless, so the launcher
    printed `Sandbox launched.` and nothing appeared. Adding the account to the local **Hyper-V
    Administrators** group removes the need for elevation, but the `net session` probe tests for
    *admin* and would still prompt.
    **`inside/sandbox_ui.ps1` — the pointer.** `gui_probe.ps1` looks; this one touches:
    `shot` (full screen or one window), `windows`, `tree`/`find` (UI Automation, hand-walked to
    a depth bound because a webview document is thousands of nodes), `click` (InvokePattern →
    SelectionItem → Toggle → physical `mouse_event` at the element centre, so web content with
    no pattern is still clickable), `type`/`key` (ValuePattern, else escaped/raw SendKeys),
    `resize` (reports `changed`, closing the vacuous-reflow hole from FINDINGS Fix 2), `close`
    (WM_CLOSE, not `Stop-Process` — the check is what the app does when a *user* closes it).
    Every verb prints JSON. It carries the FINDINGS Fix 2 lesson in code: consoles are excluded
    from the ranked target pick, because the launcher console is titled "Top Rat" too. Also
    unlike the plain launcher it does **not** clear results — `qa/results/claude/<timestamp>/`
    per run, after two silent no-op launches deleted a real PASS — and it detects an
    already-running sandbox instead of exiting 0 having done nothing.
    **Diagnostics — `sandbox/Diagnose Sandbox.bat` + `Diagnose Sandbox 2.bat` →
    `diagnose_sandbox.ps1`.** Read-only, no admin: binaries, CmService/vmcompute/HvHost,
    edition, MSIX package status, FULL event-log text, base images, disk, policy. Written when
    Sandbox refused to start on the dev machine: `WindowsSandboxRemoteSession.exe` died every
    launch with `0x80070005` loading its own `hostfxr.dll` out of `Program Files\WindowsApps`.
    **`sfc`/`DISM` do NOT repair per-package WindowsApps ACLs** — don't re-run them expecting a
    fix; repairing the Sandbox app itself is what cleared it. Batch lesson baked into the first
    script: `call :label "text with (parens)"` silently swallows the line, which ate the whole
    services section on its first run — hence the second round being PowerShell.
  - **`qa/vm/` — the PERSISTENT QA VM, a THIRD entry point (2026-08-16). Nothing in
    `qa/sandbox` is touched by it, and deleting the folder leaves both existing paths
    exactly as they were.** It exists because Claude *Desktop* cannot live in the Sandbox
    the way Claude *Code* can. Claude Code's credential is a plaintext `.credentials.json`
    that `claude_setup.ps1` copies in from a read-only mount; Claude Desktop's session is
    Electron/DPAPI state bound to the host user and machine, so the same copy does not
    decrypt in a fresh `WDAGUtilityAccount` profile — and the Sandbox image has no browser
    to complete an OAuth sign-in with. So the target inverts: install and sign in **once**,
    then `Checkpoint-VM`. **The clean-machine property comes from the checkpoint, not from
    destroying the VM** — `Restore-VMSnapshot` is a five-second rollback to a guest that has
    never seen this project *and* is already signed in. The motivating defect is the same one
    that produced the assisted Sandbox run: driving a Sandbox window from the HOST is not
    practical (remote-session client, clicks land on a video, scaled coordinates, nothing
    reads back but pixels), and `Claude Sandbox.bat` documents the sharper version — an
    **elevated** Sandbox window cannot be clicked by normal-integrity automation at all,
    because of UIPI. Inside the guest, Claude Desktop is on the desktop it is testing.
    **NESTED VIRTUALIZATION IS THE CONSTRAINT, and it is three settings, not one.** Cowork
    needs the Virtual Machine Platform inside the guest (HCS: HNS, vmcompute, vfpext), and
    Anthropic's Windows deployment doc states that VMs *without* nested virtualization are
    unsupported. `provision_vm.ps1` therefore sets `-ExposeVirtualizationExtensions $true`,
    **`-DynamicMemoryEnabled $false`** (dynamic memory silently blocks nested virt) and
    `-MacAddressSpoofing On` (the nested guest's frames carry a MAC the vSwitch did not
    assign). Miss any one and the failure is unreadable backwards: Claude Desktop installs,
    launches, and Cowork simply refuses to start. `baseline_vm.ps1` reads `HypervisorPresent`
    **inside** the guest and **refuses to freeze** when the chain is broken — a bad baseline
    is worse than none, because every later run restores it and the cause is invisible by then.
    It warns just as loudly on `python.exe` being on the guest's PATH: a guest with Python no
    longer proves the bundled-interpreter claim, which is the most valuable property the
    target has. **MSIX, not the friendly installer** — `stage_desktop.ps1` pulls the MSIX
    because a per-user `Add-AppxPackage` install *can complete without registering the Cowork
    virtualization service*, so `inside/vm_setup.ps1` uses `Add-AppxProvisionedPackage
    -SkipLicense` and only falls back per-user with a warning. `vm_setup.ps1` is phased
    (`Features` → restart → `Apps`) because those HCS services only exist after a real boot,
    and `setup_guest.ps1` issues `shutdown /r` rather than stop/start — Fast Startup can leave
    the virtualization stack uninitialized, which is the documented cause of "Missing HCS
    services". **Transport is PowerShell Direct + `Copy-VMFile` over the VMBus** — no SMB, no
    shared folder, no open port, no firewall rule, and therefore nothing to misconfigure the
    way a nested `.wsb` mapping can (see NEVER MAP A SUBFOLDER, above). `run_vm_test.ps1`
    pushes `smoke.py` / `gui_probe.ps1` / `sandbox_ui.ps1` / `CHECKLIST.md` **from
    `qa/sandbox/inside`, not copies** — duplicating them would guarantee drift — plus
    `inside/VM_CLAUDE.md` as `C:\work\CLAUDE.md`, the Desktop-side analogue of
    `SANDBOX_CLAUDE.md`. **Checkpoints are Standard, not Production**, set at provision time: a
    production checkpoint is a VSS snapshot of a disk and restores to a *logon screen*,
    discarding the signed-in session the whole design exists for; a standard checkpoint saves
    RAM and returns to the signed-in desktop. `Remove-VMSnapshot` merges asynchronously, so
    `-Replace` polls `Status -match 'Merging'` before taking the new one. Results land in
    `qa/results/vm/<timestamp>/` and nothing is deleted. Git-ignored (root **and** `qa/`):
    `qa/vm/payload/` (multi-GB evaluation ISO + the MSIX) and `qa/vm/guest.cred.xml` (the
    throwaway guest password as a DPAPI SecureString scoped to one user on one machine).
    The **90-day evaluation ISO is a real expiry** — when it lapses, `-Force` rebuild and redo
    the one-time list in `qa/vm/README.md`. Ships nowhere, same as the rest of `qa/`.
  - **`src/ops/bundle_python.py` — the private interpreter.** Both launchers ALREADY search
    bundled-first (`python\python.exe` on Windows, `python/bin/python3` elsewhere); this only
    supplies it. Source is **python-build-standalone (astral-sh)**, whose `install_only`
    tarballs unpack to exactly those two layouts, which is why ONE script covers Windows,
    macOS (arm64 + Intel) and Linux. The asset is resolved through the GitHub API, not a
    hardcoded URL — the release tag is a date that rolls every few weeks and a pinned link
    404s; the *Python* version stays pinned by `PY_SERIES`. Extraction is path-checked
    (`_safe_extract`) because a tarball is arbitrary data off the network and `filter='data'`
    needs 3.12 while the app supports 3.8. **Run it on the platform you are building for** —
    the pip step selects native wheels (pywebview drags in pyobjc on macOS). The older
    `tools\Make Portable Python.bat` remains but is superseded: it uses python.org's
    *embeddable* zip, which disables `import site` and omits pip, so it needs a `._pth`
    rewrite plus a get-pip bootstrap. **`python/` is NOT in `INCLUDE_DIRS` and must never be
    committed** — ~40 MB of per-OS binaries, in git history on every clone forever, and wrong
    for two of the three platforms. `tests/test_package.py` asserts that.
  - **What goes IN the bundle, and the one thing that does not (2026-09-05).**
    `BUNDLED_PACKAGES = ['python-docx', 'pywebview', 'jsonschema']` — the rule is that a
    download never fetches anything the first time it is opened, so everything the app can
    use is already inside it. **`python-jobspy` is the exception and ships as its own zip**
    (`ADDON_PACKAGES = {'linkedin': ['python-jobspy']}`). Measured in a clean venv: docx +
    pywebview = **47 MB**, + jsonschema = **51 MB**, + python-jobspy = **321 MB**. That last
    270 MB is tls_client (89), pandas (79), numpy (79) and lxml (12) — five times the app,
    on all three platforms, for one job source among several.
    `bundle_python.py --addon linkedin --out dist` builds it, and **it must run AFTER
    `package.py`**: it installs the add-on's packages INTO the existing bundle and zips the
    difference, leaving the bundle changed. Deliberately not `pip install --target` into an
    empty folder — `--target` resolves against nothing, so it re-downloads its own copy of
    whatever the bundle already has and can pin a *different* version, which then overwrites
    the working one when the user unpacks. Installing into the real bundle makes pip resolve
    against what is actually there. Entries are rooted at the app folder (`python/…`) so the
    user unzips it over their install; `release.yml` asserts nothing in the add-on falls
    outside `python/`, because one stray top-level entry would overwrite an app file. The
    add-on step is `continue-on-error` — a PyPI hiccup on one leg must not cost the release
    its three main downloads.
  - **`_ensure_jobspy()` in `scrape.py` used to pip-install behind a shipped copy's back
    (fixed 2026-09-05).** Its own comment said "do NOT reach for the network+pip in a
    packaged install", and then asked `sys.frozen` — which **PyInstaller** sets and this
    project never uses. A release zip carries a real interpreter and is not frozen, so the
    guard never fired where it mattered: the first LinkedIn scrape on a fresh install kicked
    off a silent 600 s pip download of that same ~270 MB tree using the bundled pip. The new
    `_is_packaged()` asks the question the launchers ask — is `sys.executable` inside the
    app's own `python/` — and the message now points at the LinkedIn add-on rather than at a
    pip command. **If you add another convenience auto-install anywhere, gate it on
    `_is_packaged()`, not on `sys.frozen`.**
  - **CI = `.github/workflows/ci.yml`, and it does NOT publish.** Releasing stays the manual
    `python src/ops/release.py --out ../TopRat --push`, so nothing reaches the ship repo
    because a workflow fired. Every push runs Linux tests + `release.py --check`; **Windows runs
    on tags only** and there is no macOS leg, because on a PRIVATE repo the included minutes
    drain at Linux 1x / **Windows 2x / macOS 10x** — a macOS matrix leg spends ten minutes of
    allowance per wall-clock minute. CI runs `pytest -m "not needs_userdata"`.
  - **`pytest.ini` + the `needs_userdata` marker (2026-08-07).** 28 tests read the owner's live
    data root — `resume_template/bullets.json` (all of `test_render_resume.py`), `geo_cache.json`
    (`test_local.py::test_golden_locations`), and the goldens snapshotted against the live
    `skills.json`/`skill_aliases.json`/`strategy.json`/blocklist (4 in `test_scoring.py`, 3 in
    `test_parse_card.py`, 1 in `test_blocklist.py`). With an empty data root all 28 fail for want
    of DATA, not for want of correct code, so CI and fresh clones deselect them and get
    **258 passed** instead of a red board nobody reads. `norecursedirs` also excludes
    `TopRat/` — it is a built copy of this tree, and collecting it imports a second copy of
    every test module under the same name, which aborts the entire run with a file-mismatch error.
  - **OPEN DEFECT (2026-08-07, not fixed):** the 8 config-golden tests above **pass when their
    file is run alone and fail when the suite runs together**, with `DATA`, the resolved config
    path and cwd all identical in both cases. Marking them `needs_userdata` removes them from CI
    but does NOT explain the ordering dependency, which still applies on a machine that has the
    data. Cause unknown; do not treat the marker as the fix.
  - **`git_daily.py` now reads `config/git.json`** (remote + identity), as this file has claimed
    since the split — it previously hardcoded both and read nothing. **It reads `CODE_CONFIG`
    directly, deliberately NOT through `pipelib.cfg()`:** cfg() overlays the data root, and
    `<data>/config/git.json` describes the **DATA** repo (`job_agent_profile_KP`), so the overlay
    would point the CODE repo's origin at the profile repo and push source into it. Accepts both
    key spellings — `setup.py` writes `name`/`email`, the old example documented
    `user_name`/`user_email` — and falls back to the previous hardcoded constants, so a copy with
    no `git.json` behaves exactly as before.
  - **`INSTALL.md`** is the end-user install guide (Python check, download, the one file to
    click, the unsigned-app click-through on both OSes, optional extras, uninstall). The app
    is **not code-signed** — an Apple Developer ID is $99/yr and Windows OV/EV certs run
    $200-500/yr while, since Microsoft's March 2024 change, EV no longer skips SmartScreen —
    so the guide teaches the one-time "Open anyway" instead. `tests/README.md` records which
    ~20 tests need the user's own data (`resume_template/bullets.json`, `geo_cache.json`) and
    therefore fail on a fresh clone without being bugs.
    Everything else moved to `tools\` — `run_radar.bat`, `run_scrape_once.bat`, `run_tests.bat`,
    `setup_radar_task.bat`, `setup_scrape_tasks.bat`, plus the maintainer scripts above. Each got
    `cd /d "%~dp0.."` and the same bundled-first interpreter block, and `setup_scrape_tasks.bat`
    **no longer hardcodes `C:\Users\<you>\...`** — it derives `PROJ` from its own location, so a
    handed-off copy registers the new owner's path.
  - **HAZARD — Windows tasks bake in absolute paths.** `JobRadarHourly` / `JobScrape24h` /
    `JobScrape7d` were registered with the pre-move root path in their action. Windows does not
    warn when that file disappears: the task runs, fails, and records a non-zero result nobody
    reads. **`tools\Repair Windows Tasks.bat` re-registers only the tasks that already exist**
    (it repairs, it never adds automation) and is the one-time fix after the move. Re-running
    `setup_radar_task.bat` / `setup_scrape_tasks.bat` also repairs, since both use `/F`.
    Note these three overlap with the in-app scheduler's own scrape/radar jobs — `CLEANUP_CANDIDATES.md`
    already flags them as redundant, so the honest options are "keep the app scheduler" or "keep the
    Windows tasks", not both.
  - **Tracker data is per-worktree (git-ignored):** `tracking.csv`, `job_tracker.json`,
    `job_tracker.html`, `candidates.csv`, `archive_index.csv`, `raw_applied.json` — each copy keeps
    its own on disk; staging test entries never merge into live's real tracker. Resume binaries
    (`New/`/`Applied/`/`Skipped/`, `*.docx`, `*.pdf`) were already git-ignored the same way.
- **Named profiles:** `config/profile.json` remains the ACTIVE single source the whole pipeline
  reads. Every wizard/API save ALSO writes a per-user named copy to
  `config/profiles/<first>_<last>_profile.json` (`profile_lib.profile_filename`) and points
  `config/active_profile.json` at it. Name collision → POST `/api/profile` returns
  `{needName, base, suggestion=<first>_<last>_profile####.json}`; the wizard prompts for a name
  (blank = the random suggestion). Re-saving a known profile sends `profileName` (from
  `activeProfile`) so it never re-prompts. All naming is deterministic/script-only in
  `profile_lib` — no Claude in the path.
- **State** (root, machine-written): geo_cache.json, listings.json, scrape_meta.json, candidates.csv, tracking.csv,
  job_tracker.json, job_tracker.html, to_process.json, tofetch.json, details.json, processed.json,
  run.json, manual.json, raw_applied.json, notified.json, archive_index.csv, archived.csv,
  scheduler_state.json, schedule_log.txt. Run transcripts live in `logs/` (execution.log,
  radar_log.txt, scrape_log.txt); regenerable caches live in `cache/` (jobdesc_cache.json).
