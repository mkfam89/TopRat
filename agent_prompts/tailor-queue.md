---
name: job-tailor-queue-processor
description: Hourly resume tailoring: no-op if the dashboard queue is empty, else tailors the jobs the user pressed Tailor on; rebuilds the tracker
---

On-demand resume-tailoring processor for {{OWNER}}. Tailor ONLY the jobs they queued from the dashboard
(to_process.json) — never auto-select by score. The deterministic logic lives in `jobpipe.py` — RUN it,
never reimplement it.

Project folder: {{PROJECT_DIR}}
Run scripts as:  python "{{JOBPIPE}}" <cmd> ...
Git checkpoint:  python "{{GIT_DAILY}}"

## STEP 0 — CHEAP EXIT IF NOTHING QUEUED (do this FIRST, before anything else)
Via bash, read to_process.json in the project folder. If the file is missing, or its "ids" array is
empty, STOP IMMEDIATELY: print "queue empty - nothing to tailor" and END the run. Do NOT git-commit,
scrape, open a browser, or call any other tool. This keeps idle runs essentially free.

  python - <<'PY'
  import json,os
  p=r"{{TO_PROCESS}}"
  ids=(json.load(open(p)).get("ids") if os.path.exists(p) else []) or []
  print("QUEUED",len(ids)); print("STOP" if not ids else "PROCEED")
  PY

If it prints STOP, end the run now.

## STEP 1 — GIT CHECKPOINT (best-effort)
Run git_daily.py. If git isn't configured or the push fails, NOTE it and CONTINUE.
SAFETY: if jobpipe exits FATAL because job_tracker.json can't be parsed, restore it from
`git show HEAD:job_tracker.json` (write the full content via bash), then continue.

## STEP 2 — BUILD THE QUEUED WORK LIST (jobpipe, NO --auto)
`jobpipe.py process-queue > tofetch.json`
Without --auto this emits {process:[...]} = ONLY the user-queued ids that still need a resume. Each item
carries category/baseType/baseResume, isLocal, salaryEst/salaryFamily, requiredSkills, userNotes, AND a
token-saver decision:
  - item.action == "template" (item.customize == false): skills already cover the JD — do NOT AI-tailor.
  - item.action == "customize" (item.customize == true): tailor with the LLM.
candidates.csv already exists — do NOT re-scrape. If process is empty, skip to STEP 5 (still rebuild and
clear the queue).

## STEP 3 — PRODUCE EACH RESUME (branch on item.action)

### action == "template"  (TOKEN SAVER — no LLM tailoring)
The classified base resume already matches this job well, so DON'T rewrite it:
- Copy resume_template/<item.baseResume> to New/<Company>/{{RESUME_PREFIX}}<trackerId>.docx UNCHANGED,
  then produce the matching .pdf (same stem). No summary or bullet rewriting, no questionnaire.
- Still do SALARY (below) and the tracker update. This is the whole point — spend no tokens tailoring.
- EXCEPTION — item.userNotes: if a template item carries non-empty userNotes, the user deliberately typed
  instructions for this posting, so a verbatim copy would ignore them. Treat that item as "customize".

### action == "customize"  (full LLM tailoring)
- CACHE BASE READS: group by item.baseType; read each baseType's skill list and base resume ONCE and reuse.

  >> ITEM.USERNOTES — THE USER'S OWN INSTRUCTIONS, HIGHEST PRIORITY (read BEFORE writing anything) <<
  A non-empty `item.userNotes` is what the user typed in the dashboard's Tailor dialog for THIS posting,
  frequently their answers to a questionnaire a previous pass wrote for the same job. Treat it as a direct
  instruction that OVERRIDES your own emphasis judgement: lead with what they say to lead with, drop what
  they say to drop. It is the ONE sanctioned source of facts beyond resume_template/ — if they assert
  experience there, you MAY use it, because they are the one asserting it. It NEVER overrides the hard
  rules below and NEVER licenses inventing anything they did not state. If a note conflicts with a hard
  rule, follow the rule and record the conflict as a questionnaire item so they can see why it wasn't
  applied. In details.json set `notesApplied: true` for any job whose userNotes you used.

- Re-weight and rephrase EXISTING bullets, front-load relevant experience, NEVER invent skills.

  >> SUMMARY OPENING — HARD RULE (config/summary_rules.json) <<
  The first phrase is the user's REAL identity, taken from summary_rules.json. NEVER adopt, restate or
  blend the target job's title into it, and never promote them into a title they never held (support roles
  too). When unsure, copy the base resume's own opener verbatim up to " with N years".

  >> STRUCTURE — HARD RULE (titles live in TABLES) <<
  Base resumes keep each job's Company/Title in a 2-column TABLE with bullets beneath. When trimming,
  NEVER leave an orphaned title. Every role that appears keeps AT LEAST TWO bullets. Verify every
  remaining title has bullets before you save.

  >> CLEAN OUTPUT — HARD RULE <<
  No notes, comments, flagged-skills lines, TODOs or meta-text in the resume — in BOTH the .pdf and the
  .docx. Missing skills go ONLY into details.json flaggedSkills and the questionnaire.

- QUESTIONNAIRE (customize items only): 2–5 specific questions about the gaps (flaggedSkills) plus
  domain/clearance/travel/certs. Ask ONLY things that change how the resume is written — the user's own
  experience in the gap areas. NEVER ask about the posting, logistics, or whether they want to apply.
  DON'T re-ask anything item.userNotes already answers; if the notes answer every prior question, write a
  shorter list or none at all. They answer on the dashboard card and the answers come back to you as
  userNotes on the next pass, so a question is only worth asking if its answer would change the next version.

### Both actions
- Save New/<Company>/{{RESUME_PREFIX}}<trackerId>.pdf AND the matching .docx (same stem); jobpipe dedupes
  the pair. DELETE only .tmp/.~lock/PREVIEW leftovers (call allow_cowork_file_delete once).
- SALARY (config/salary_bands.json — search at most once per role family): if the posting lists pay, use
  it. Else use item.salaryEst. If that is empty, WebSearch ONE market range for the family, then PERSIST it:
  `jobpipe.py salary-set --family <salaryFamily or new-name> --range "$X-$Y (est.)" [--senior-range "..."] [--match kw1,kw2]`
  and reuse it for every job in that family. Never store "Undisclosed".

## STEP 4 — UPDATE THE TRACKER (jobpipe)
details.json = { trackerId: {foundDate, applyUrl, salary, location, source, flaggedSkills, skillMatch,
questionnaire, customized(true/false), notesApplied(optional)} } per job.
processed.json = [ ids processed this run ].
run.json = { timestamp, trigger:"queue", queued_processed, resumes_created, customized, templated, git_pushed }.
`jobpipe.py update --details details.json --processed processed.json --run run.json`

## STEP 5 — REBUILD + CLEAR THE QUEUE
Write `{}` to manual.json, then `jobpipe.py rebuild --manual manual.json`. Do NOT open job_tracker.html
in a browser. Then DELETE to_process.json so the next idle run exits cheaply at STEP 0. (Deleting the file
clears its `notes` map too — that's intended; the notes were consumed this run and the user's answers
still live in the tracker.)

## RULES
- Queue-only: never tailor a job that isn't in to_process.json. Max 20 per run.
- Respect item.action: template => copy the base resume (no tokens); customize => full tailor. Salary and
  the tracker update happen for BOTH. The one override is item.userNotes on a template item, which
  promotes it to customize.
- item.userNotes outranks your own emphasis judgement; it never outranks the hard rules.
- CLEAN RESUME + SUMMARY TITLE + no orphaned titles + SALARY(est.) are non-negotiable for customize items.
- NEVER write to the user's own fields — notes, answers, applied, bookmarked, skipped_jobs in
  job_tracker.json are their record of what they did. Read them; never author them.
- Never invent skills, tools, titles or experience. Output a .pdf + .docx pair per job.
- Never hand-edit tracking.csv, candidates.csv or job_tracker.json — jobpipe owns them.
- Keep it lean: no scraping, no browser unless a step needs it (none do).
