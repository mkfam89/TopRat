# Refactor Playbook — token reduction + accuracy hardening

Ordered, paste-ready prompts for Cowork. One step per fresh session. Each step is
independently shippable: if you stop after any step, the repo still works.

**Status:** Steps 0–6 done + render_resume wired into `tailor_local.py` (content.json →
lint-gated render, verbatim copy fallback). Plus two features beyond the refactor:
**employer blocklist** (deterministic ban, Felix Pago banned) and **ghost-risk scorer +
dashboard UI** (advisory 👻 badge, per-row Ban button, Settings card). 185 tests. Step 7: SKIP.

Fixed along the way: a circular import (tailor_local ↔ render_resume) that would have
silently no-op'd the render wiring; ghost-risk calibration (no_salary is weak, repost
needs distinct ids ≥14d apart) so it doesn't cry wolf.

---

## Scope decision — why the original prompt was cut down

The prompt you pasted is written for a 500k-line polyglot monorepo. This repo is
**~4,400 lines of first-party Python** (12,845 total, but 8,400 of that is
`Backups/snapshots/` and `showcase_job_agent/` duplicates). Several of the 14
requested components would add more code than they index, and every new module
would itself need a `DEPENDENCY_MAP.md` entry — the maintenance would exceed the
savings.

| # | Requested | Verdict |
|---|-----------|---------|
| 1 | Dependency graph | **Already have it** — `DEPENDENCY_MAP.md`, hand-maintained, better than a generated one |
| 2 | Hierarchical index | **Already have it** — the Components table + subcommand list is the hierarchy |
| 3 | Symbol index | **Right-sized → Step 7** — AST-derived `REPO_INDEX.json`, no embeddings |
| 4 | Function summaries | **Right-sized → Step 7** — docstring-derived, not LLM-generated (LLM summaries go stale silently) |
| 5 | AST metadata | **Yes → Step 7** — `ast` module, already the correct approach |
| 6 | Call graph | **Partial → Step 7** — intra-file calls via AST; cross-file is subprocess-based here, so the map covers it |
| 7 | Knowledge graph | **Skip** — 20 files. The map *is* the knowledge graph |
| 8 | Semantic/vector search | **Skip** — `grep` beats embeddings under ~50k lines, at zero index cost |
| 9 | Retrieval pipeline | **Skip as code** — adopt as *habit*: map → symbol index → source. Steps 0–6 make that habit possible |
| 10 | Context compression | **Yes** — this is what Steps 3, 4, 6 actually deliver |
| 11 | Progressive expansion | **Yes, as habit** — enabled by Step 6 (split files) + Step 5 (`doctor`) |
| 12 | Metadata | **Partial → Step 7** |
| 13 | Caching | **Skip** — mtime check in Step 7 is enough; there is no embedding cost to cache |
| 14 | Performance | **Yes** — the point of every step |

The real token sinks here are not retrieval architecture. They are: re-reading
`jobpipe.py` (63KB ≈ 16k tokens) to debug anything, re-parsing base `.docx` files
per tailor, and regenerating docx-building code for every single resume. Steps 3, 4
and 6 hit those three directly.

The real *accuracy* risk was that every rule in `CLAUDE.md` was enforced by the agent
remembering it, with zero tests over 4,367 lines. Step 0 fixed the first half; Step 2
fixes the second.

---

## Model routing

| Model | Use for |
|-------|---------|
| **Fable 5** | Steps 2, 4, 6 — large multi-file refactors, long autonomous grinds, "write your own tests and verify" work. Anthropic positions it for exactly this: codebase-wide migrations, multi-day sessions, architecture reasoning |
| **Opus 4.8** | Steps 3, 5, 7 — judgment-heavy but small-surface work, especially Step 3 where the risk is *inventing experience*, not writing code |
| **Haiku 4.5** | Step 1 — mechanical file moves and `.gitignore` edits |

Rule of thumb: **Fable 5 when the blast radius is large and the work is long;
Opus when the judgment is subtle but the diff is small.**

---

## Step 0 — `lint-resume` ✅ DONE

Built `lint_resume.py` + wired `python jobpipe.py lint-resume`. Encodes 12 rules
(R01–R12) from `CLAUDE.md` as deterministic checks.

Baseline sweep over 164 existing resumes found **real, systematic drift**:

| Rule | Files | What it caught |
|------|-------|----------------|
| R10 docx/pdf pair | 115 | missing PDF or missing DOCX — these are ATS upload failures |
| R01 font floor | 75 | 9.0–9.5pt body text, below the 10pt rule |
| R06 banned header | 56 | `CORE COMPETENCIES` / `ADDITIONAL EXPERIENCE` still present |
| R09 summary title | 18 | summary opens *"DevOps engineer with…"*, *"Senior cloud engineer with…"* — adopting the target job title, which the rule forbids |
| R12 trailing group | 15 | Sr Implementation Consultant + Implementation Analyst kept, HP intern dropped — the split you explicitly ruled out |
| R11 skills format | 11 | bold bleeding past the group label |
| R03 page-2 min | 1 | near-empty second page |

Your six most recent resumes all **PASS** — the drift is historical. Run
`python jobpipe.py lint-resume --all --errors-only` any time; it costs zero tokens.

---

## Step 1 — Housekeeping · Haiku 4.5 · ~15 min

> Clean up scratch churn in this repo so future diffs are readable.
>
> 1. Add to `.gitignore`: `err.txt`, `tofetch.json`, `tofetch.err`, `listings.fixed.json`,
>    `scrape_raw_sample.json`, `_mount_probe.txt`, `__pycache__/`, `.~lock.*#`,
>    `*.bak`, `schedule_log.txt`, `radar_log.txt`, `scrape_log.txt`.
> 2. `schedule_log.txt` is 793KB — rotate it: keep the last 500 lines, and add rotation
>    to whatever writes it so it caps at ~1MB.
> 3. Delete these confirmed-dead files after confirming with me: `git_daily_UPDATED.py`
>    (DEPENDENCY_MAP marks it "not wired in"), `jobpipe.py.trunc.bak`,
>    `job_tracker.json.corrupt.bak`, `job_tracker.json.nulbak`, `_rmtest/`.
>    List them for approval first — do not delete anything unlisted.
> 4. Do NOT touch `Backups/snapshots/` or `showcase_job_agent/`.
>
> Then update `DEPENDENCY_MAP.md` in the same change per the standing rule.

**Why:** every `git diff` and every directory listing I read is currently ~30% noise.
**Savings:** small per-call, but it applies to every session forever.
**Trade-off:** none. **Risk:** low — deletions are approval-gated.

---

## Step 2 — Golden-fixture tests · **Fable 5** · ~2 hrs

Do this *before* the refactors so they have a safety net.

> Add a pytest suite for this repo. There are currently zero tests over ~4,400 lines.
>
> Cover these pure functions in `jobpipe.py` (verified to exist — do not go hunting):
>
> - **Scoring / matching:** `classify`, `skill_match`, `flag_skills`, `_skill_hit`, `norm`
> - **Applied / Simplify reconciliation:** `applied_match`, `applied_norm_index`, `_canon`,
>   `_canon_tokens`, `resolve_status`
> - **Salary:** `salary_lookup`, `_norm_salary_title`, `_resolve_salary` (the
>   posted → posting → jobsworth → band precedence chain)
> - **Identity / paths:** `slug`, `stem`, `camel_to_words`, `tracker_id`, `_prefer_pdf`
> - **Houston priority:** `_is_local` — the 0.20-vs-0.60 threshold rule
> - **Parsing:** `_parse_card`
>
> Skip anything that does network or disk I/O (`_probe_salaries`, `snapshot_backup`,
> `write_*`, `move_preserve_ts`) unless you can drive it with `tmp_path`. Also cover
> `lint_resume.py`'s rule functions.
>
> Method: **golden fixtures**. For each function, capture real inputs from the live
> data already in the repo (`listings.json`, `candidates.csv`, `tracking.csv`,
> `job_tracker.json`) and record current outputs as `tests/fixtures/*.json`. Where
> current behavior is clearly a bug, write the test to the *correct* expectation and
> flag it to me separately — do not silently encode a bug as correct.
>
> For `lint_resume.py`, build tiny synthetic `.docx` files in `tests/fixtures/docx/`
> that each violate exactly one rule (R01–R12), plus one clean file that must pass.
> Assert the right rule fires and no others do.
>
> Constraints: `pytest` only, no new runtime dependencies, tests must run offline with
> no network and no LibreOffice, and must not mutate any file in the repo root.
> Add a `run_tests.bat`. Update `DEPENDENCY_MAP.md` in the same change.

**Why Fable 5:** this is a long mechanical grind across many functions where the model
needs to read code, infer intent, and self-verify — the profile it's built for.
**Savings:** a code change costs one `pytest` run instead of re-reading `jobpipe.py`
(~16k tokens) and reasoning about regressions. Call it **~15k tokens saved per change**.
**Trade-off:** golden fixtures lock in current behavior, including quirks — hence the
"flag bugs separately" instruction.

---

## Step 3 — Bullet bank · **Opus 4.8** · ~1 hr

> Create `resume_template/bullets.json` — a single structured bank of every résumé
> bullet I actually have. Read DEPENDENCY_MAP.md first; don't re-read the codebase.
>
> Source **only** from these files in `resume_template/`:
> - `PHAM_KHOA_RESUME.docx` (CORE/SRE base — bullets are List-style paragraphs)
> - `Khoa_Pham_Resume_data_analyst.docx` (ANALYST base — bullets live in table cells)
> - `Khoa_Pham_Resume_Technical_support.docx` (TECH_SUPPORT base — bullets in table cells)
> - `STAR Stories v3 — Full + 30-Second Versions.pdf` (factual source stories)
>
> Bullets are stored differently across these files (list paragraphs in one, table
> cells in the others), so scan both — a paragraph-only pass silently misses most of
> them. Do NOT pull from any tailored resume in `New/` or `Applied/`.
>
> Schema per entry: `id` (stable slug, e.g. `pros-sre-ansible-rundeck`), `role`
> (the position: PROS Site Reliability Engineer, PROS Cloud Operations Engineer,
> PROS Senior Implementation Consultant, PROS Implementation Analyst, HP Performance
> Engineering Intern), `track` (CORE | ANALYST | TECH_SUPPORT — which base(s) it
> appears in), `text` (canonical wording), `tags` (skills/tools it evidences),
> `metrics` (any quantified outcome, or null), `source` (which template file).
>
> Tag the three trailing-role bullets (Sr Implementation Consultant + Implementation
> Analyst + HP intern) clearly, since the CLAUDE.md rules treat them as a group.
>
> Hard rule: every bullet must trace verbatim-or-near to a source file. If you are
> tempted to merge, sharpen, or infer a bullet that isn't clearly in the source,
> **stop and ask me** — do not write it. List anything ambiguous at the end for review.
>
> Also emit `resume_template/bullets.schema.json` and a `validate_bullets.py` that
> checks every entry against the schema and that every `id` is unique. Update
> `DEPENDENCY_MAP.md` in the same change.

**Why Opus:** the failure mode here is *fabricating experience*, not writing code. The
judgment is "is this bullet actually his?" — small diff, high stakes.
**Savings:** tailoring becomes select-ids + 2–3 rewrites instead of parsing `.docx` XML
per job. Roughly **3–5k tokens per tailored resume**, and at your volume that compounds fast.
**Structural win:** "never invent experience" stops being a prose rule I have to
remember and becomes *every bullet must resolve to a bank id*.

---

## Step 4 — Content / render split · **Fable 5** · ~3 hrs

The big one. Do it after Steps 2 and 3.

> Split résumé *content* from résumé *rendering*.
>
> Build `render_resume.py` that takes a small `content.json` and emits the `.docx`
> (then the `.pdf` via soffice). All formatting lives in code, not in prose I have to follow.
>
> `content.json` schema: `{company, role, summary, skills_groups[{label, items[]}],
> roles[{company, location, title, dates, bullet_ids[], bullet_overrides{}}],
> education, volunteer}`. Bullets referenced by id from `resume_template/bullets.json`
> (Step 3, 40 entries); `bullet_overrides` maps an id → reworded text for per-job tweaks.
> An id that isn't in the bank is a hard error — that's what enforces "never invent."
>
> Reproduce the EXACT docx structure of the base files so the fidelity diff matches:
> each role is two 1-row/2-col tables (`[COMPANY, Location]` then `[Title, dates]`),
> skills lines are paragraphs with only the group label bold, bullets are List-style
> paragraphs. Reuse the `find_soffice()` pattern already in `tailor_local.py` for PDF.
>
> Lock these into the renderer so they cannot be violated: body font 10–11pt (never
> below 10), two-column title tables, section order, no banned headers, minimum two
> bullets per role, and the page-fill priority from `CLAUDE.md` (font/spacing → richer
> summary → up to 3 blank lines → only then restore roles).
>
> Match the existing visual output exactly. Verify by rendering a `content.json`
> reconstructed from `New/Alkami/Khoa_Pham_Resume_Alkami_PlatformSolutionEngineerMANTL.docx`
> (a known-clean file) and diffing the result against the original.
>
> `render_resume.py` must exit non-zero if `lint_resume.py` fails on its own output —
> wire the linter in as a post-render gate. Update `DEPENDENCY_MAP.md`.

**Why Fable 5:** new module + integration with `jobpipe`/`tailor_local.py`, with a
pixel-level fidelity target and self-verification against an existing artifact.
**Savings:** my output per job drops from a full generation script (~4k tokens) to
~1.5KB of JSON (~400 tokens). **~85% reduction on the highest-frequency operation
in the whole pipeline.**
**Trade-off:** formatting changes now require a code edit rather than an ad-hoc tweak.
That is the point — but it does mean one-off visual experiments get slower.

---

## Step 5 — `jobpipe.py doctor` · Opus 4.8 · ~30 min ✅ DONE

Built `_doctor_collect` + `cmd_doctor` + `tests/test_doctor.py`. Read-only, `--json`,
exit 1 on error. On first run it surfaced a stale `.git/index.lock` and reported New/
lint failures by *resume count* (3 of 25) rather than raw error count (which R01
inflates). Original prompt kept below for reference.

---

### Step 5 (original prompt)

> Add a `doctor` subcommand to `jobpipe.py` that prints one compact status block:
> job counts by state, last run timestamp, stale lock files, `job_tracker.json` parse
> check, mount-truncation cross-check (file size vs. last known good), queue depth in
> `to_process.json`, Adzuna quota state, and `lint-resume --all` error count.
>
> Output must be under ~40 lines, plain text, `--json` optional. It is the first thing
> run at the start of a session, so it must be fast and never write anything.
>
> Follow the existing subcommand pattern and note the mount-truncation hazard: verify
> `jobpipe.py` parses with `ast.parse` after editing. Update `DEPENDENCY_MAP.md`.

**Why:** session start becomes ~200 tokens instead of four exploratory bash calls
(~2–3k tokens). Runs every session.
**Trade-off:** one more subcommand to keep current.

---

## Step 6 — Split `jobpipe.py` into modules · **Fable 5** · ~3 hrs

Do this **last** among the code changes — it needs Step 2's tests.

> `jobpipe.py` is 1,166 lines / 64KB, so any debugging read is all-or-nothing.
> Split it into `scoring.py`, `tracker.py`, `salary.py`, `dashboard_build.py`, leaving
> `jobpipe.py` as a thin CLI dispatcher.
>
> Hard constraints:
> - **Every existing subcommand keeps its exact name, flags, stdout format, and exit
>   codes.** `scheduler.py`, `dashboard_server.py`, `tailor_local.py`, and the SKILL.md
>   scheduled tasks all shell out to these — nothing may break.
> - The full pytest suite from Step 2 must pass before and after, unchanged.
> - Known hazard: writes to `jobpipe.py` can truncate on this mount. After every write,
>   verify with `ast.parse`, confirm `main()` and all dispatch branches survive, and
>   check the file tail. Do not trust a silent success.
> - Update `DEPENDENCY_MAP.md` in the same change — this one substantially rewrites the
>   Components table.

**Why Fable 5:** this is precisely a codebase-wide migration with a large blast radius
and a subtle failure mode (silent truncation). Long, mechanical, verification-heavy.
**Savings:** a debug read drops from ~16k tokens to ~3k. **~80% on every debugging session.**
**Trade-off:** highest-risk step in the playbook. It is ordered last for that reason,
and it is why Step 2 comes first.

---

## Step 7 — `REPO_INDEX.json` (optional) · Opus 4.8 · ~1 hr

The one piece of the original prompt that scales without an embedding stack.

> Write `build_index.py` — an AST-based indexer emitting `REPO_INDEX.json`.
>
> Per Python file: module docstring, and for each function/class: name, signature,
> first docstring line, line range, calls made, imports, and whether it's a CLI entry
> point. Plus per-file mtime + size so the index can be rebuilt incrementally.
>
> Derive summaries from **docstrings and signatures only — never LLM-generated prose.**
> Generated summaries go stale silently and are worse than nothing.
>
> Skip `Backups/`, `showcase_job_agent/`, `__pycache__/`. Keep the whole index under
> 40KB so it can be read in full. Add a `--check` mode that reports drift against the
> current tree, and note it in `DEPENDENCY_MAP.md`.

**Why:** lets me answer "where does X live / what calls Y" from a ~10k-token index
instead of grepping and reading files.
**Savings:** ~5k tokens per navigation-type question.
**Trade-off:** a second artifact that can drift from the code. The `--check` mode and
the docstring-only rule are what keep that honest. Revisit only if the repo grows past
~10k lines — below that, `grep` plus `DEPENDENCY_MAP.md` genuinely is competitive.

---

## Suggested cadence

| Session | Step | Model |
|---------|------|-------|
| 1 | Housekeeping | Haiku 4.5 |
| 2 | Tests | **Fable 5** |
| 3 | Bullet bank | Opus 4.8 |
| 4 | Render split | **Fable 5** |
| 5 | doctor | Opus 4.8 |
| 6 | jobpipe split | **Fable 5** |
| 7 | Repo index *(optional)* | Opus 4.8 |

Run `python jobpipe.py lint-resume --all --errors-only` and the pytest suite at the
end of every session from Step 2 onward. Commit after each step —
`python git_daily.py "<what changed>"`.

**Aggregate estimate:** ~60–70% reduction in tokens per pipeline operation once Steps
2, 4 and 6 land, and — more valuable — the `CLAUDE.md` rules become machine-enforced
rather than dependent on my recall.
