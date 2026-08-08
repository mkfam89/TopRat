# Resume Tailoring — Project Rules

Standing rules for customizing Khoa's resumes. These complement the project's
numbered workflow (fetch posting → tailor from base → list changes → get approval
→ replace latest copy).

## Design principle — everything works without Claude (HARD RULE)
- **Every feature must have a deterministic, zero-token script path** that runs with no
  Claude at all: discovery, scoring, locality (keyword + geo cache), salary probe, notify,
  tracking, dashboard, archive, git checkpoint are all script-only today. Keep it that way.
- **Claude is an optional enhancement layer, never a dependency.** LLM-only work (AI resume
  prose, Chrome scrape fallback, salary WebSearch, Simplify sync) enhances the pipeline for
  users with a Claude subscription; without one, the pipeline stays fully functional and
  degrades gracefully (e.g. template-copy resumes instead of AI-tailored, cached/band
  salaries instead of web-searched).
- **When adding a feature:** build the script path first; layer the Claude enhancement on
  top second, and always leave a script fallback so removing Claude never breaks the run.

## Formatting & length
- **Body font must never be smaller than 10pt.** Recruiters skim; small fonts make
  the impactful content hard to scan. Never shrink fonts to force one page.
- **Two pages is acceptable.** One page is nice-to-have, not required. Keep the most
  relevant/impactful bullets on page 1; move Education (and other lower-priority
  sections) to page 2 if needed.
- **Don't leave an awkward, nearly-empty page 2.** If it's worth spilling to a second
  page, fill page 2 to **at minimum 5 lines** (counting section headers and the line
  spacing between sections), rather than leaving one or two stray lines.
- **No redundant/padding bullets — HARD RULE.** Never fill space by adding bullets that
  repeat the same underlying accomplishment. If two or more bullets describe the same work
  (e.g. building/monitoring a performance/data-pipeline system and presenting the findings),
  **converge them into one** strong bullet. Filler that restates existing content is worse
  than empty space.
- **How to fill space — in this priority order, before adding any bullet/role back:**
  1) increase body font (within the 10–11pt band) or section/line spacing;
  2) enrich the SUMMARY with more substantive content;
  3) add up to **3 blank lines** at the end of page 1.
  Only after these are exhausted, restore genuinely distinct bullets or the HP
  (Performance Engineering) internship from the template — never duplicative ones.
- **Minimum two bullets per role.** No role may appear with fewer than two bullets; if a
  role would have 0–1, restore additional bullets from the template so it has at least two
  (a lone single-bullet role looks sparse). Applies to every role that appears, on either page.
- Name is larger; body/tables run 10–11pt.

## Sections
- **No "ADDITIONAL EXPERIENCE" sub-header.** Keep every role under a single EXPERIENCE
  section — older/junior roles simply continue in the same list. Saves vertical space.
- **No "AREAS OF FOCUS" or "CORE COMPETENCIES" buzzword strip.** SUMMARY + TECHNICAL
  SKILLS already carry the keywords; a third keyword band is redundant. If a generated
  resume has one, remove it.
- **Keep "TECHNICAL SKILLS"** as the labeled tool breakdown.
- **Trailing roles move together (one-page case):** when trimming to ONE page, Senior
  Implementation Consultant + Implementation Analyst + HP (Performance Engineering) Intern
  are dropped as a group — never split, never merged into one section. **Exception when
  filling page 2:** if a resume is worth two pages, first fill via font/spacing/summary/blank
  lines (see Formatting), then restore trailing experience only if page 2 is still under the
  5-line minimum — and only with distinct, non-duplicative bullets (the internship may be added
  back on its own here); still honor the minimum-two-bullets-per-role rule above.
- **Volunteer Experience** (Rutherford B.H. Yates Museum) is included whenever it
  doesn't displace primary experience — it adds human-recruiter interest.
- **Summary** opens with Khoa's real professional identity; never adopt/blend the target
  job title or claim a title he never held.

## Filenames — short "Company_Role" only (HARD RULE)
- **The recruiter reads the filename.** An over-long auto-generated name (the full posting
  title with every location/timezone/team/tech-stack qualifier) reads as bot-applied. Keep
  it to just the **core company + core job title**: `Khoa_Pham_Resume_<Company>_<Role>.docx`
  (+ the `.pdf` twin).
- **Role = core title, keep seniority + level.** Drop parentheticals, location, timezone,
  remote/onsite/hybrid flags, team/specialty names, and tech-stack tails. Keep seniority
  (Senior/Sr) and level (II/III). e.g. `Site Reliability Engineer (US, Central/Eastern
  Timezone)` → `SiteReliabilityEngineer`; `Software Engineer III Power Platform` →
  `SoftwareEngineerIII`; `Technical Support Engineer Enterprise Solutions REMOTE` →
  `TechnicalSupportEngineer`.
- **Company = strip legal/location suffixes, cap ~3 words.** Remove Inc/LLC/Corp, "North
  America", "ND Division", parentheticals. e.g. `ENGIE North America Inc` → `ENGIE`; `EPAM
  Systems Inc` → `EPAM`; `Farmers Educational Cooperative Union Of America ND Division` →
  `FarmersEducationalCooperative`.
- **The script path is authoritative.** `pipelib.tracker_id()` runs company/role through
  `clean_company()`/`clean_role()`; the tailored id == the filename stem, so the deterministic
  pipeline already emits the short name. When Claude hand-tailors a resume, name the file the
  **same** way (short company + core title) so it matches `tracker_id`. The company **folder**
  under `New/` uses the same short company slug.
- **Existing files keep their names.** Discovery recognizes an already-tailored/skipped
  resume by normalized company+role even after the id shortens, so shortening never re-tailors
  a duplicate. Only rename old files if Khoa explicitly asks.

## Git checkpoint / commit messages
- The daily/weekly runs pass a **short one-line summary of the features/bugs worked on
  this run** to the commit script: `python git_daily.py "Hardened tracker writes; fixed applied-mark loss"`.
- `git_daily.py` appends a **minimal job count in `[a|p|s applied|pending|skipped]` order**
  automatically (from tracking.csv) — e.g. `... [a|p|s 148|7|0]`. Don't spell counts out verbosely.
- If no summary is passed the message is a plain `Daily checkpoint [a|p|s N|N|N]` label.
  (An earlier version of this file described a `--note` flag writing to `worklog.txt`; that was
  never implemented, and passing a dash-arg just triggers a normal commit. Removed 2026-08-06.)
- Git identity + remote URL are NOT hardcoded — they load from git-ignored `config/git.json`
  (template committed as `config/git.json.example`), so the repo can be shared without leaking them.

## Content sourcing
- Build only from files in `resume_template/` (base resumes + the STAR Stories PDF as a
  factual source). The pipeline scans any files there — no hardcoded filename.
- **Never invent** skills, tools, titles, or experience Khoa hasn't confirmed. If a
  posting names tools with no evidence, flag it and ask rather than guessing.
- Keep both `.docx` and `.pdf` for each tailored resume (ATS uploads).

## Never write to user-owned data (HARD RULE)
- **Claude does not author content in fields Khoa owns.** `notes`, `answers`, `applied`,
  `bookmarked`, `skipped_jobs` in `job_tracker.json` (and their `tracking.csv` columns) are
  the user's record of what *he* did and decided. Scripts and Claude read them; only the
  dashboard UI, driven by Khoa, writes them.
- **Never seed fake data to verify a UI state.** Testing the note bar's "has note" state, the
  📝 chip, an applied badge, or any populated-vs-empty rendering does NOT justify writing a
  sample value into the real tracker. Invented recruiter names, referrals, and follow-up dates
  are indistinguishable from real ones a week later, and Khoa acts on this file.
- **Verify against a scratch copy instead.** Point the dashboard at a throwaway data root
  (`config/instance.json` → `data_root`), stub the value client-side in the browser console
  without saving, or read a `Backups/snapshots/` copy. If none of that works, ask Khoa to type
  a note on one card himself.
- **If a test value does get written, remove it in the same session** and say so — never leave
  it to be discovered later.
- Applies to `to_process.json` `notes` (Tailor-dialog text) and `details_manual.json` too:
  both carry Khoa's own words about his experience.

## Dashboard GUI — card ↔ table parity (HARD RULE)
- **Whatever GUI feature changes are made to the card view, adapt them to the table view — and
  vice versa.** The board is ONE page with two presentations (`cards.html`, served at `/` and
  `/table`); both render from the same `currentList()` and call the same handlers. A control that
  exists in only one view is a behavior difference the user walks into by switching views.
- **Adapt in the other view's idiom, don't paste markup.** A card chip becomes a table cell or an
  icon in the row's flag strip; a toolbar control (search, filters, sort) is shared and needs no
  duplicate; anything in the detail drawer is automatically shared, since both views open the same
  drawer — so put a feature there when it fits.
- **Pause and ask when it isn't obvious** how a feature should read in the other view, or whether
  it belongs there at all. Don't guess, and don't quietly skip it.

## Architecture & code maintenance
- **Where the code lives (2026-08-06):** modules are grouped under `src/lib`, `src/pipeline`,
  `src/resume`, `src/web`, `src/ops`; `README.md` (root + one per group) is the orientation.
  `src/_paths.py` is the only path magic: `_paths.ROOT` = project folder, `_paths.script('x.py')`
  resolves a script by bare name. New modules go in a group and start with the two-line shim.
- **Wiring/dependency reference:** see `DEPENDENCY_MAP.md` for how the scripts, config, and
  state files connect, plus the agent-vs-script split (what Claude does vs. `jobpipe.py`).
  Consult it instead of re-reading the whole codebase each session.
- **Keep the map current — RULE:** whenever you change code (add/remove/rename a script, change
  what a script imports or invokes, or change which config/state files it reads or writes),
  update `DEPENDENCY_MAP.md` **in the same change** so it never drifts from the code.
- **Graphical version (`DEPENDENCY_MAP.html`)** is a manual visualization aid — do **NOT**
  regenerate it on every code change. Only rebuild it when Khoa explicitly asks.
