---
name: job-alert-resume
description: Weekday job discovery (mornings): finds every new posting into the tracker and auto-tailors only the local ones; remote jobs wait for the dashboard's Tailor queue
---

Run {{OWNER}}'s DAILY job pipeline autonomously (24-hour lookback). The deterministic logic lives in
`jobpipe.py` — RUN it, never reimplement it. Search URLs live in config/search_urls.json.

MODEL: DISCOVER every job (no skill filtering) into candidates.csv and the tracking list, then research
salary and tailor resumes ONLY for (a) local jobs at or above local_min_match and (b) jobs the user
queued from the dashboard (to_process.json). Remote jobs are NOT auto-tailored by score — they wait
for the dashboard queue.

Project folder: {{PROJECT_DIR}}
Run scripts as:  python "{{JOBPIPE}}" <cmd> ...
Git checkpoint:  python "{{GIT_DAILY}}"
Scraper:         python "{{SCRAPE}}"

## 0 — GIT CHECKPOINT (run FIRST)
Run git_daily.py. It commits and pushes best-effort. If git isn't configured or the push fails,
NOTE it and CONTINUE — git must never block the pipeline.
SAFETY: jobpipe refuses to run if job_tracker.json can't be parsed. If it exits FATAL, restore
job_tracker.json from `git show HEAD:job_tracker.json` (write the full content via bash), then continue.

## 1 — GET LISTINGS (no browser)
scrape.py handles both boards over their APIs — hiring.cafe via its `_next/data` route and LinkedIn
via jobspy. There is no browser step in this run.
Check scrape_meta.json (bash). If status=="ok" AND the timestamp is within the last 10 hours, USE the
listings.json already on disk and go to step 2.
Otherwise run scrape.py yourself, then re-check scrape_meta.json. If it still fails, record the error
in run.json, continue to step 5 so the tracker is still rebuilt, and say so in your summary — do NOT
try to scrape by hand.

## 2 — DISCOVER: score every job (jobpipe, no filtering)
`jobpipe.py candidates --listings listings.json`
Writes candidates.csv = ALL scraped jobs scored, applied-status reconciled, floors applied.

## 3 — SELECT what to tailor (jobpipe): auto LOCAL only + the user's queue
`jobpipe.py process-queue --auto --local-only > tofetch.json`
Emits {process:[...]} = jobs still needing a resume, limited to (a) local jobs >= local_min_match plus
(b) user-queued ids (any location). Local jobs sorted FIRST. Each item carries `userNotes` and a
token-saver decision: item.action == "template" (skills already cover the JD) or item.action ==
"customize" (AI-tailor). Work ONLY on this list. If it is empty, still run steps 5–6.

## 4 — PRODUCE EACH RESUME (branch on item.action)

### action == "template"  (TOKEN SAVER — no LLM tailoring)
Copy resume_template/<item.baseResume> to New/<Company>/{{RESUME_PREFIX}}<trackerId>.docx UNCHANGED and
produce the matching .pdf. No summary or bullet rewriting, no questionnaire. Still do SALARY and the
tracker update below.
EXCEPTION — if the item carries non-empty `userNotes`, the user typed instructions for this posting, so
a verbatim copy would ignore them: treat that item as "customize" instead.

### action == "customize"  (full LLM tailoring)
- CACHE BASE READS: group by item.baseType; read each baseType's skill list and base resume ONCE and reuse.

  >> ITEM.USERNOTES — THE USER'S OWN INSTRUCTIONS, HIGHEST PRIORITY (read BEFORE writing anything) <<
  A non-empty `item.userNotes` is what the user typed in the dashboard's Tailor dialog for THIS posting,
  often their answers to a questionnaire a previous pass wrote for the same job. It OVERRIDES your own
  emphasis judgement: lead with what they say to lead with, drop what they say to drop. It is the ONE
  sanctioned source of facts beyond resume_template/ — experience they assert there MAY be used, because
  they are the one asserting it. It never overrides the hard rules below and never licenses inventing
  anything they did not state; on a conflict, follow the rule and log it as a questionnaire item.
  Set `notesApplied: true` in details.json for jobs whose notes you used.

- Re-weight and rephrase EXISTING bullets, front-load relevant experience, NEVER invent skills.

  >> SUMMARY OPENING — HARD RULE (config/summary_rules.json) <<
  The first phrase is the user's REAL identity, taken from summary_rules.json. NEVER adopt, restate or
  blend the target job's title into it, and never promote them into a title they never held. When unsure,
  copy the base resume's own opener verbatim up to " with N years".

  >> STRUCTURE — HARD RULE (titles live in TABLES) <<
  Base resumes keep each job's Company/Title in a 2-column TABLE with bullets beneath. NEVER leave an
  orphaned title. Every role that appears keeps AT LEAST TWO bullets. Verify every remaining title has
  bullets before you save.

  >> CLEAN OUTPUT — HARD RULE <<
  No notes, comments, flagged-skills lines, TODOs or meta-text in the resume — in BOTH the .pdf and the
  .docx. Missing skills go ONLY into details.json flaggedSkills and the questionnaire.

- QUESTIONNAIRE (customize items only): 2–5 specific questions about the gaps (flaggedSkills) plus
  domain/clearance/travel/certs. Ask ONLY about the user's own experience in those gap areas — things
  whose answer would change how the resume is written. NEVER ask about the posting itself, logistics, or
  whether they want to apply. Skip anything item.userNotes already answers. They answer on the dashboard
  card and the answers come back to you as userNotes on the next pass, so a question is only worth asking
  if its answer would change the next version.

### Both actions
- Save New/<Company>/{{RESUME_PREFIX}}<trackerId>.pdf AND the matching .docx (same stem); jobpipe dedupes
  the pair. DELETE only .tmp/.~lock/PREVIEW leftovers (call allow_cowork_file_delete once).
- SALARY (config/salary_bands.json — search at most once per role family): if the posting lists pay, use
  it. Else use item.salaryEst. If that is empty, WebSearch ONE market range for the family, then PERSIST it:
  `jobpipe.py salary-set --family <salaryFamily or new-name> --range "$X-$Y (est.)" [--senior-range "..."] [--match kw1,kw2]`
  and reuse it for every job in that family. Never store "Undisclosed".

## 5 — UPDATE THE TRACKER (jobpipe)
details.json = { trackerId: {foundDate, applyUrl, salary, location, source, flaggedSkills, skillMatch,
questionnaire, customized(true/false), notesApplied(optional)} } per job.
processed.json = [ every scrape id seen ].
run.json = { timestamp, scrape_source, jobs_new, resumes_created, customized, templated, discovered,
queued_processed, git_pushed }.
`jobpipe.py update --details details.json --processed processed.json --run run.json`
If you processed a user queue this run, delete to_process.json.

## 6 — REBUILD THE DASHBOARD (jobpipe)
Write `{}` to manual.json, then `jobpipe.py rebuild --manual manual.json`.
Do NOT open job_tracker.html in a browser. Never hand-edit tracking.csv, candidates.csv or
job_tracker.json — jobpipe owns them.

## RULES
- GIT FIRST; never let a git failure block the run.
- DISCOVER ALL, TAILOR SELECTIVELY. Auto-tailor ONLY local jobs; remote waits for the dashboard queue.
  Max 20 tailored per run.
- Respect item.action: template => copy the base resume (no tokens); customize => full tailor. Salary and
  the tracker update happen for BOTH. The one override is item.userNotes on a template item, which
  promotes it to customize.
- item.userNotes outranks your own emphasis judgement; it never outranks the hard rules.
- CLEAN RESUME + SUMMARY TITLE + no orphaned titles + SALARY(est.) are non-negotiable for customize items.
- NEVER write to the user's own fields — notes, answers, applied, bookmarked, skipped_jobs in
  job_tracker.json are their record of what they did. Read them; never author them.
- Never invent skills, tools, titles or experience. Output a .pdf + .docx pair per job.
