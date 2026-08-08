# Card UI redesign — what I built + decisions for you

Built on the **staging** worktree only. No git commit, no merge, no push. Scheduler/notifications
stay disabled; dashboard still on port 8766.

## Short answer to your question
Yes — an in-window detail preview like LinkedIn/Indeed is very doable. I built it as a right-hand
**detail drawer** that slides in when you click a card, with the list still visible behind it.

## Try it
1. Boot the staging server: `python src/web/dashboard_server.py --port=8766` (or `Start Here.bat`,
   which already reads `instance.json` → 8766).
2. Open `http://127.0.0.1:8766/cards` — or click the new **Cards** tab in the top nav.

## What's on it
- **Compact cards:** company, role, a Remote/Hybrid/Onsite tag, pay, and a skill-match %. Each card
  has a **⊘ skip** and **✓ mark-applied** icon in the top-right corner (one click, no drawer needed).
- **Click a card → detail drawer** repeating the basics plus: exact location(s), found date, repost
  note (if detected), a skill-match bar, the missing/flagged skills, and a private **note** field.
- **Drawer buttons:** Apply · Tailor resume · Bookmark · Ban employer.
- Search box + filters (All / Active / Applied / Skipped / Bookmarked) + sort (match / found / pay / company).

## How it's wired (so nothing existing breaks)
- New read-only endpoint **`/api/jobs`** merges the tracked board with location (from `job_details`)
  and a derived remote/onsite tag. The classic table Tracker at `/` is **untouched**.
- Card actions reuse your existing endpoints: `/api/save` (skip/apply/bookmark/note),
  `/api/queue` (tailor), `/api/block-employer` (ban). No CSV schema change.
- `cards.html` is fully data-driven off `/api/jobs`, so it never needs a dashboard rebuild.
- `DEPENDENCY_MAP.md` updated in the same change (per the project rule).

## Tested
`/api/jobs` (204 jobs, all fields populated), `/cards` (200), `/api/queue`, `/api/block-employer`,
`/api/unblock-employer`, classic `/` still 200, and the card JS passes a syntax check.
**One caveat:** I couldn't exercise `/api/save`'s write-back inside my sandbox — the sandbox mount
blocks replace-over-existing-file, which is exactly the step `atomic_write` verifies. That's the
**same shared save path your live dashboard persists marks through every day**, so it works on your
Windows filesystem; my feature doesn't modify it. Worth one manual click-test when you boot it.

---

## Decisions I made for you (change any of these)
1. **Kept it as a separate `Cards` tab** rather than replacing the table. Both views coexist.
2. **Default filter = All.** 186 of your 204 tracked jobs are already marked *applied* and only 1 is
   *pending*, so an "active only" default would look almost empty.
3. **Apply button opens the posting AND marks the job applied** (fewer clicks). The ✓ corner icon
   still toggles applied independently.
4. **"Missing skills" = your `flaggedSkills`** (skills the matcher flagged as not evidenced in your
   resume). That's the only per-job skill-gap signal in the data.
5. **Remote/Hybrid/Onsite is derived from the location text** ("remote"/"hybrid" keyword, else onsite).
   Hovering the location tag now shows an info bubble explaining exactly how it was tagged (and, for
   older jobs with no stored location, that the "Location" tag is a placeholder).
6. **Ban is silent** (reason "banned from card view") after a confirm dialog.
7. **Notes save on click-away** (blur), no separate Save button.
8. **Match colors:** ≥70% green, 40–69% amber, <40% red.

## Update — round 2 (your follow-ups)
- **Default filter is now Pending** (you have exactly 1 pending job right now, so the board shows
  one card until you switch the filter to All / Applied / etc.).
- **Full-width auto-spanning grid.** Cards now fill the whole maximized window (auto-fill columns,
  ~300px min each). When you open a preview, the content area shrinks to the left and reflows into
  fewer columns so the drawer sits *beside* the cards and never covers them.
- **Bookmark shows as a ★ next to the employer name** (on the card and in the drawer header); the
  old "saved" chip is gone.
- **Drawer no longer clipped by the menu banner.** It now positions itself just below the sticky
  nav (measured at open time), so the header/close button are fully visible.
- **Estimated salaries are italicized** (any figure the resolver marks "(est.)" / rough band).

### ✅ Job-description body — DONE (on-demand fetch, no bulk storage)
Implemented as you preferred: nothing is stored in bulk. The drawer has a **⬇ Load full
description** button that fetches the posting on demand via a new `/api/jobdesc` endpoint
(`jobdesc.py`). It tries clean JSON APIs first (Greenhouse, Lever), then schema.org JSON-LD
(covers Ashby/Workday/Workable/iCIMS and many more), then a generic HTML→text strip, and
caches each result for 14 days so re-opens are instant and repeat calls stay low. If a site is
JavaScript-only or blocks the fetch, it falls back to a "view original posting" link. Extraction
is unit-tested (`tests/test_jobdesc.py`, 11 tests). **One caveat:** I couldn't exercise a live
fetch in my sandbox (no outbound network), so the extraction logic is fixture-tested but the
real-site fetch is worth a quick check when you run the staging dashboard — click **Load full
description** on a Greenhouse/Lever/Ashby posting and confirm the text comes through.

### (original note, superseded) About the job-description body
The original posting's description body is **not stored anywhere**. The scraper *does* fetch it, but
only to extract skills, then throws the text away — `job_details` keeps foundDate/applyUrl/salary/
location/source/skills, no description. So the drawer currently shows a "not stored — View original
posting ↗" note instead of the body. To actually show it in-window, pick one:
1. **Persist it going forward** — small change to `scrape.py` to save `description` into `job_details`
   (I've already wired the drawer + `/api/jobs` to display it the moment it exists). Only *new* jobs
   would get it; existing 204 wouldn't, unless —
2. **Backfill** by re-fetching each stored posting (slower, and JS-rendered ATS pages like Workday/
   hiringcafe often won't return clean text server-side).
3. **Live fetch on open** — fetch the posting when you click a card. Same JS-rendering reliability
   caveat; adds a network call per open.
My recommendation: option 1 (persist forward) now, and decide on backfill later. Tell me which and
I'll wire it next session.

## Questions for you to review
1. ✅ **Done — cards replaced the table as the default board.** `/` now serves the card view (nav tab
   **Jobs**). The classic table is still reachable at `/table` (nav tab **Table**) — not deleted,
   since it has a couple of features the cards don't yet. Say the word if you want the table removed
   entirely.
2. ✅ **Done — candidate pool now included.** `candidates.csv` (not-yet-tailored jobs) are merged
   into the board, deduped against tracked jobs, and marked with an **✎ untailored** chip. New
   **Untailored (candidates)** filter isolates them. Added 30 candidate cards (234 total).
3. ✅ **Done — dummy location placeholder.** The 111 older jobs with no stored location now show a
   muted **"Location"** chip instead of nothing (verified: 0 empty locations left). Real locations
   still get their Remote/Hybrid/Onsite tag.
4. ✅ **Done — Apply no longer auto-marks.** Clicking Apply opens the posting and quietly remembers
   the click. The next time you open that card (if it isn't already applied), an in-card banner —
   not a popup — asks "did you apply?" with Yes / No. Yes marks it applied; either answer stops the
   banner. The pending click is remembered across visits (browser localStorage).
5. ✅ **Done — table extras added back.** 👻 ghost-risk badge now shows next to the employer name
   when risk is **high** (note: no job currently scores high — 146 are "low", which by design get no
   badge, matching the table). The salary in the drawer has a hover bubble explaining the estimate
   source (posted / Adzuna predictor / salary band), and a **↻ Retry salary lookup** button
   (`/api/salary-probe`) is back.
6. ✅ **Done — ban reason optional.** Banning now prompts for a reason but a blank reason still bans;
   Cancel aborts.
7. **Anything missing from the card or drawer** you expected to see?

---

## Update — round 3 (2026-07-29, readability + filters)

- **Board is now one left-hand column + a docked preview pane.** Cards got bigger (16.5px company,
  15px role, 12.5px chips) so they read on a phone; the whole right side belongs to the JD preview.
  Column width is the `--listw` CSS var and the pane's left edge is derived from it, so they can't
  overlap. Under 900px the column goes full width and the pane becomes a full-screen overlay.
- **Move to applied** is a filled green button (was a ghost chip) and swaps its label for a ✓ on
  hover — at a fixed min-width/height, so the card never resizes under the cursor.
- **Skip is now 👎** (was ⊘, which read like "banned").
- **Found date** shows on the mini card.
- **Skill match is a soft tint**, not a solid block — it was drowning out the work-type and pay chips.
- **`✎ untailored` chip removed** (it only restated the filter). Replaced by real state:
  `⏳ queued to tailor` or `✓ resume ready`.
- **Sort:** added Job title; every sort now has an asc/desc toggle beside the dropdown; default is
  found date, newest first.
- **Filters:** local (show all / only / hide) and arrangement (Remote / Hybrid / Onsite).
- **Tailor resume** asks for optional notes for Claude — they ride to `to_process.json → notes`
  and out of `process-queue` as `userNotes`. Without Claude, Tailor queues silently and the script
  path is untouched.
  **2026-08-05 — the toggle that used to gate this is gone.** There was a Settings switch
  (*Claude assistant* → `gui_settings.claudeEnabled`) deciding whether the notes box appeared. A
  stored preference about a capability can only ever be wrong in one of two directions: on with no
  Claude Code installed offered a box nothing would read, off with it installed hid a working
  feature behind a setting nobody knew to look for. The box is now shown whenever the thing that
  READS it exists — `/api/jobs.claudeEnabled` is derived from `_claude_cli_present()` (the Claude
  Code CLI, 60s-cached). Setup's two Claude rows merged into one, **AI resume tailoring**, and the
  auto-tailor caps moved there from `/schedule` — with the cap now counting **AI resumes only**,
  since a template copy costs no tokens and capping it drops resumes for no saving.

- **Questionnaire is back — and now closes the loop.** It was never broken: the LLM pass has been
  writing `job_details[id].questionnaire` all along (the 2026-07-28 run produced 5 questions each for
  Reco and Mydayforce), but only the deprecated table page rendered it, so nothing showed once cards
  became the default board. The drawer now has a 📋 section with the questions + an answers box
  (persisted to the tracker's existing `answers` store / tracking.csv column), a 📋 chip on the mini
  card that goes green once answered, and a **✎ Use as tailoring notes →** button that seeds the
  Tailor dialog with those answers. Answer a gap question → it becomes `userNotes` → the next pass
  reads it. Both tailoring SKILL.mds were updated to treat `userNotes` as outranking their own
  emphasis judgement, to promote a `template` item to `customize` when notes are present, and to
  stop re-asking questions the notes already answer.

---

## Status update — 2026-07-29 (recovery into live)

This document was written while the card UI was a **separate tab alongside the table**. That is no
longer the arrangement, so read decision #1 ("Kept it as a separate `Cards` tab") as superseded:

- **Cards is now the default board at `/`** (also reachable at `/cards`). The classic table moved to
  `/table` (`/job_tracker.html`) and is **deprecated** — kept reachable, not maintained.
- Everything described below was built in the staging worktree and **never committed there**. The
  route + docs were committed in `f884c92` (Jul 23, 16:32) but the actual files landed on disk at
  19:07-19:08 and no commit followed, so `git merge staging` into live carried the references
  without the code — leaving `/` serving a blank page and `/api/jobdesc` dead (`jobdesc.py` missing).
- Recovered into live on 2026-07-29 by 3-way merging `pipelib.py` / `dashboard_server.py` /
  `salary_probe.py` / `salary.py` (base `f884c92`, clean, no conflicts) and copying in `cards.html`,
  `jobdesc.py`, and this file. Also brought over: `/api/salary-edit` (manual pay override),
  `pipelib.detect_employment_type()` + the card's employment-type badge, and
  `salary_probe.parse_jsonld_salary()` (schema.org `baseSalary`, cleanest-source-first probe order).

---

## Status update — 2026-07-29 (card/table parity is now a rule)

"The table is deprecated, kept reachable, not maintained" (above) is **superseded**. The table is a
second *view* of the same board, and `CLAUDE.md` now carries a hard rule: **any GUI feature change
in the card view gets adapted to the table view and vice versa; pause and ask when it isn't clear.**

Landed in this pass (both views): résumé **path shown + click-to-preview** as an overlay on the JD
with a Résumé/JD toggle, the private note **floating above the action row**, **customization notes
split out** and shown only once Tailor has been used, a **queue / unqueue / re-queue** split button
(`/api/queue {remove:[…]}`, notes survive an unqueue), and the local + arrangement dropdowns merged
into **one combinable filter section** that also filters by ⏳ Queued and ✓ Resume ready.

**Amended same day:** the tailoring box moved *into* the JD pane, directly under Missing / flagged
skills (beside the questionnaire it usually answers) and is now the single input — the modal notes
prompt is gone, and it's called **Tailoring instructions**, not notes, because "note" collided with
the job note. The job note itself became a **yellow sticky note** pinned bottom-right over the JD:
a small square with a peek of the text that expands to a scrollable square on click. Instructions
only render when the Claude assistant is on. Table view reaches both through the shared drawer.

---

## Table view: the detail pane opens *inside* the table — 2026-07-30

The table used to answer a row click by sliding a pane in from the right, over the table, behind a
scrim. It was borrowed from the card view and it fought the table: the pane covered the columns you
were comparing, and the row you clicked was hidden behind the thing it opened.

Now the row **expands**. Clicking a row inserts an expander row (`tr.rowexp`, a single full-width
`td`) directly beneath it and the detail pane slides down inside it; clicking the same row again
collapses it. The row stays highlighted and in place, the rows around it just move down, and the
scrim is gone because nothing is covered any more.

- **It is the same drawer, not a copy.** `#drawer` is *moved* into the expander row (and back to
  `<body>` on close). No second copy of the detail markup exists, so the résumé/JD toggle, the
  questionnaire, the tailoring box, the sticky note and every handler behave identically in both
  views — which is what the card ↔ table parity rule in `CLAUDE.md` demands.
- **Height is capped** at `min(72vh, 760px)`: the description scrolls inside the pane rather than
  pushing the rest of the table kilometres down the page. The pane's body drops its card-view
  max-width so its scrollbar sits at the row's right edge, but the text blocks inside stay capped
  at ~1040px so a wide window doesn't produce 150-character lines.
- **The expander is re-docked, not rebuilt, on every re-render** (a status flip, a save). If the
  open job falls out of the current filter there is no row to sit under, so the pane closes instead
  of orphaning itself.
- Opening scrolls the clicked row clear of the sticky nav + header only when the pane wouldn't
  otherwise fit on screen.

---

## Review loop: the pane advances to the next job — 2026-07-30

Deciding on a job is the one action that removes it from the list you're looking at: skip it while
in **Active**, mark it applied, un-bookmark it inside **Bookmarked** — the card is gone and, until
now, the pane either closed or sat on a job that no longer belonged there. Every decision then cost
a hunt for the next card.

The pane now **steps to the job that took its place**. `listPos()` records where the job sat in
`currentList()` *before* the status is changed; `afterStatusChange()` re-renders and, if the job
left the list, opens whatever now occupies that index — the next one down, or the last card when
you were already at the end — closing the pane only when the list runs out.

- **Only when the pane was open on that job.** A 👎 pressed on a mini card or a table row means "not
  this one", not "show me the next one" — opening a pane there would be the UI answering a question
  nobody asked.
- **Same index, not "next id".** The list is filtered and sorted; the slot is what the user was
  looking at, and after the removal that slot already holds the next job.
- **Both views, by construction.** The handlers are shared; card view scrolls the new card into the
  column (`revealCard`), table view already had `revealRow` inside `dockDrawer`.
- Un-skipping / un-applying uses the same path — under the **Skipped** or **Applied** filter,
  reversing a decision also removes the job, and the pane advances the same way.

---

## Ghost flags: recording a judgement, not guessing one — 2026-07-30

There were already two ghosts on the board and neither was the user's. `ghost_risk.py` guesses from
heuristics and shows a 👻 icon on high-risk rows; the blocklist bans an employer outright. Nothing
sat in between — no way to say *"this specific posting looks fake"* at the moment you notice it.

A **👻 flag button** lives in the detail card's action row *(2026-07-30: only there — see "Who owns
which control" below; it used to sit on the mini card and in the table's action cell too)*.
Pressing it records the flag in `config/ghost_flags.json` via
`ghostflags.py` and **changes nothing else** — Khoa's instruction: no auto-skip, no unqueue, no
filtering; he sets the status himself. The card and row just go **ghost gray**, which is the whole
point: the judgement has to be visible on a later pass down the list.

- **Two ghosts, kept apart.** The heuristic 👻 icon still means "the scorer thinks this smells";
  the gray card means "you said so". They are separate fields (`ghost` vs `ghostFlagged`) and
  separate CSS classes (`.ghost` vs `.gflag`) — reusing `.ghost` for the button inherited
  `cursor:help` and read as a tooltip rather than a control.
- **The second flag is where a ban gets offered.** One bad posting is a bad posting; two from the
  same employer is a pattern, and the blocklist is the deterministic answer to a pattern. So the
  offer fires at 2, pre-filled with the reason, and bans with `tactic:'ghost'`.
- **"No, keep them" is remembered forever; Escape is not.** An explicit no writes
  `banPrompt:"declined"` server-side and the offer never returns for that employer. Dismissing the
  dialog with Escape or a click outside is not an answer — treating it as one would let a stray
  keypress silently switch off a prompt the user never read.
- **Counts are per employer, normalized the same way a ban is** (`blocklist.norm_employer`), so
  "Felix", "Felix, Inc." and "felix technologies" accumulate together — and the tag on the card
  reads `👻 ghost ×2` once more than one of their postings is flagged.

**Banned moved next to the employer name.** It was a chip in the card's chip row and a chip in the
table's flag strip, sitting among pay, match and location — a fact about *the employer* buried in a
row of facts about *the job*, and skimmed past accordingly. Both it and the ghost tag now ride
beside the company name in all three places the name is drawn.

**The job note went back to a bar.** As a floating sticky square it sat on top of the job
description — the thing you're reading while you decide — and collapsed it hid the note inside a
tooltip. It's now `.notebar`: a yellow strip **in flow**, showing the note text itself on one line
when collapsed and growing into the textarea when clicked. Hidden still leaves a small inline
"📝 Show note" pin, because hiding must never lose the note.

**Amended 2026-07-30 — the bar moved BELOW the action row**, to the bottom edge of the card. Above
the buttons, pressing − only looked like it was shrinking the textarea: the yellow header stayed
put in the middle of the card and the row of buttons didn't move, so nothing read as "minimized."
At the bottom edge the collapse is unmistakable — the whole yellow block becomes the card's last
line — and opening the note grows it downward instead of shoving the action row around. The
"📝 Show note" pin is **left-aligned** to the same 24px inset as the Apply button, so it reads as a
label belonging to the card rather than a fourth action, and its 📝 is **50% larger than the label**
— once the bar is hidden that pin is the only way back to the note. The `.dbody`'s old ~124px
bottom padding (clearance for the floating square) went with it.

**Collapsed height is pinned, not computed.** `−` kept leaving the bar half-open: the state flipped
to `mini` and the textarea hid, but the strip stayed as tall as the block it replaced, so the
button looked broken. `.notebar.mini` now sets an explicit 32px head, a 16px line-height on the
peek and `overflow:hidden` — one line by construction, whatever the note contains.

**The note on a small card is a line, not an overlay.** `.hovernote` (a dark panel that dropped
over the bottom of the card on hover) and the 📝 chip beside it are gone. A card with a note is
simply one line taller: a yellow `.cardnote` strip with the note's **first line**, ellipsized.
Clicking it calls `openNote()` — open that job, expand the note footer in the detail card, cursor
in the textarea — so the strip is a shortcut into the note rather than a second place to read it.
Nothing is covered, and a card with no note is unchanged.

**Skip / applied / ban now auto-dequeue.** All three mean "I'm done deciding about this job," so a
queued tailoring pass no longer makes sense — `toggleSkip`/`toggleApplied` pull the job back out of
`to_process.json` the moment its status moves TO skipped/applied (not on the undo path), and
`banEmployer` does the same for every OTHER queued job from that employer, since a ban is
company-wide. The **tailoring instructions are kept** (`dequeueSilently` is the same
`/api/queue {remove:[…]}` a manual unqueue uses) — reverse the status and queue the job again, and
nothing needs retyping. The toast names the removal inline, but only when there was actually
something queued to remove.

---

## Who owns which control — 2026-07-30

Controls had been accumulating in both places at once. The split is now by *when you use it*:

**Mini card / table row — what you do while scanning.** 📝 quick note, ☆ bookmark, ⏳ queued badge,
👎 skip, Move to applied. All icon-only in the card's corner cluster (30px each, one rhythm), in
that order, so the queued badge sits right beside skip.

- **📝 quick note** is back and is a shortcut, not a second editor: it calls `openNote()`, which
  opens the job and expands the note footer of the detail card. A card that already has a note
  *also* shows the one-line `.cardnote` strip; the icon covers the cards that don't.
- **☆ bookmark moved off the drawer entirely.** It flips **optimistically** (`render()` before the
  save resolves) — waiting on the write made a scan-speed control feel like it had missed the click.
- **⏳ queued is a badge, not a button**, and carries no text: the wording ("⏳ Queued", plus the
  unqueue/instructions menu) stays in the detail card where there's room to explain it.
- The table row gets 📝 and ☆ in its action cell for the same reason — the drawer used to be the
  table's only way to bookmark, so removing it there would have taken the feature away.

**Detail card only — judgements you make after reading the posting.** 👻 ghost flag and ⊘ ban,
both **icon-only** (`.btn.ico`, 46px) so they don't compete with Apply and Tailor for width; the
wording lives in the tooltip. They were crowding the card corner and the table's action cell, where
you'd never actually decide "this employer is fake" — you decide that after reading the description.

`refreshDrawer()` used to detect "is the drawer showing this job?" by probing for the bookmark
button's id; that button no longer exists there, so it checks `OPEN_ID` directly.

---

## Filters: one Local toggle + a Resume status group, and a back-to-top — 2026-07-30

**"Not local" is gone; Local is a single toggle.** The pair was modelled as mutually exclusive
opposites, which made the off state ambiguous — three states (all / only local / hide local) out of
two chips, where the third was never what anyone wanted. `📍 Local` on now means *only* local, off
means the board isn't filtered by locality at all. Saved filter sets containing `nonlocal` are
migrated by dropping it, so a returning user isn't left with an invisible filter that hides Houston
jobs (which sort first — see the Houston priority rule) with no chip lit to explain it.

**`✓ Resume ready` became `✓ Tailored`, inside a labelled `Resume status` group** with two new
chips. "Ready" only ever described one of three states a job's résumé can be in, so the other two
were unfilterable:

- **✓ Tailored** — a tailored résumé exists (`hasResume || resumePath`).
- **📄 Templated** — none yet, but `skillMatch >= CUSTOMIZE_THR`, so the deterministic path uses a
  ready-made base résumé from `resume_template/` and no AI tailoring is needed. This is the same
  test the detail card's `TEMPLATE` badge already rendered from; both now read `resumeState(j)` so
  the chip and the badge can't disagree.
- **❔ Undetermined** — none yet and below the threshold: it needs an AI-tailored résumé.

The three **partition** the board, so selecting all three equals no filter by construction, and the
group can be reasoned about as "which of these do I want" rather than as a set of overlapping flags.
The chip label change is carried into the card chip and the table's résumé cell (`✓ tailored` in
both) so the same word names the same state everywhere.

`⏳ Queued` stayed in its own group ahead of the label: it's a queue position, not a state of a
résumé file, and it's orthogonal — a queued job is still templated or undetermined underneath.

---

## Status: one control instead of six buttons — 2026-07-31

The board had a separate button per transition — 👎 skip, ✓ mark applied, "Move to pending",
"Un-skip", Ban, Unban — and two of them were the same move wearing different words: the
**Un-skip** button on a skipped job and the **Move to pending** button on an applied job both
just set the status to pending. Worse, *which* buttons existed depended on the status the job was
already in, so the control moved around under the cursor as you worked down a list, and each view
had grown its own arrangement of them (card corner, table action cell, drawer action row).

There is now **one `Status ▾` button**, in all three places a job is drawn. It wears the job's
current list as its label and tint, and opens a menu of the four lists to move it to.

- **It is the readout AND the control.** The table's Status column was a read-only chip; it is
  now the button. That's the table's idiom for the card's corner control, so the status actions
  left the table's action cell entirely rather than being duplicated in two cells of one row.
- **The current status stays in the menu**, greyed and marked "· current", instead of being
  omitted. A menu that hides where you already are makes you infer the current state from what's
  missing.
- **Banned is a peer, not a special case.** It isn't a value of `j.status` — it's `j.blocked`, an
  employer-level fact — but from the board's point of view it's simply the list the job sits in,
  so `statusOf()` reports it as one and the menu lists it with the rest.
- **Unknown status = Pending.** `statusOf()` falls back rather than rendering a status nobody can
  name, so a value the UI doesn't recognize can't produce an unstyled, unnameable card.
- **One writer.** Every move goes through `setStatus()`, so the auto-dequeue, the ban/unban side
  effects and the auto-advance can't drift apart per-button the way they had started to.
- **Leaving Banned asks first.** Moving a banned job to any other list *necessarily* unbans its
  employer — a job can't sit in Pending while its company is blocklisted. That's a company-wide
  consequence of a single-job click, and it silently returns every other posting from that
  employer to the board, so the confirm names both halves and counts the siblings coming back.

The detail card's leading button stopped shape-shifting too: it used to become "Move to pending"
on an applied or skipped job. Opening the posting is useful in *every* state, so it always does
that now and only the wording follows the state (**Apply** → **View Posting** once you've
applied) — moving between lists is the Status button's job, right beside it.

---

## Résumé status: one bubble, and it stops queuing on a click — 2026-07-31

Same disease as the status buttons, different organ. The mini card only ever said something here
when a résumé already existed (blank otherwise); the table's Resume column was a cluster of
distinct buttons — 📄 View, ✎ re-tailor, ✕ unqueue, or a bare **Tailor** button — and that last one,
with the Claude assistant switched off, **queued the job the instant it was clicked.** A status
readout that quietly writes on a stray click is the same trap the old per-transition status
buttons were: something meant to be read gets pressed instead.

Both listings now show ONE bubble, plain text, no icon: **Tailored / Templated / None / Queued** —
literally `F_LABEL[resumeState(j)]`, the same lookup the Resume filter chips read, so the bubble
and the filter can never disagree about what state a job is in. ("None" replaced "Undetermined"
here too, and the filter chips dropped their own icons in the same pass — ✓/📄/❔/⏳ were doing
less work than the words already do.)

Clicking it never queues anything by itself:

- **Queued** → opens the big card. The instructions box, and the unqueue option beside it, are
  right there — nothing about this needs a confirmation on the way in.
- **Has a résumé** → toasts its file name. A peek, not a trip anywhere; View / preview / the
  `.docx` download all still live in the big card's résumé row for when you actually want them.
- **Neither** → asks first — *"Tailor a resume for this job?"* — and only on yes does it open the
  big card scrolled into the tailoring-instructions section, via `promptTailor()`, the exact same
  landing spot the card's own **Tailor resume** button uses. Queuing stays a deliberate press
  inside the drawer either way; the bubble's job is to get you there, not to act for you.

`resumeCell(j)` (table) is now a one-line call to the same `resumeBubble(j)` the mini card renders
— parity by construction, not by keeping two implementations in step by hand.

---

**Back to top** is a single fixed round button (`.totop`), shown past 400px of scroll. The window is
the scroller in **both** views — the table only scrolls sideways, inside `.tscroll` — so one
page-level control serves cards and table alike and the parity rule needs no second implementation.
It parks at the bottom-right of whatever is actually holding the list: inside the fixed-width card
column in card view, at the window edge in table view. It hides behind the card view's full-screen
drawer overlay (the page underneath isn't what you're scrolling) but stays up in table view, where
the expander opens in flow and the page keeps scrolling. Honors `prefers-reduced-motion` rather than
forcing a smooth fly-back.

---

## Card view: the detail pane takes over the screen on a narrow window — 2026-07-31

The card column is a fixed `--listw` (460px) and the drawer's left edge is *derived* from it, so
the pane got whatever was left. On a ~1000px window that's ~400px — the job description, and worse
the résumé PDF preview, were unreadable in a column that narrow. Full-screen takeover only kicked
in at the 900px phone breakpoint, leaving a 200px band of unusable side-by-side.

Below **1100px** the card-view drawer stops being a side panel and becomes a full-screen layer over
the list (`left:0`, body scroll locked). The card column keeps its
`--listw` width — widening it to the window made every mini card change shape the moment the
overlay closed, so the list looks identical before and after. The "pick a card" preview hint is
hidden (nothing renders beside the list any more).

A **left-edge rail** (`.dback`, 30px, dark grey, "‹ VIEW JOB CARDS ‹") runs the full height of the pane in takeover
mode: the sliver of the list still showing through, and the affordance that this is a layer you
can step back out of. Clicking it is `closeDetail()` — the same one path as × and Esc, not a
second close route. Closing (× or Esc) drops the overlay and the mini cards are back;
that's `closeDetail()` unchanged, no new close path.

**Table view is deliberately excluded** (every rule is scoped `body:not(.view-table)`). The parity
rule asks for the same *feature* in the other view's idiom, and table view has no squeeze to fix:
the same `#drawer` docks into an expander row spanning the full window width and must keep
scrolling with the page. A fixed overlay there would break the row-expander model.

**The × is now a real button** in both views: a 34px bordered circle (38px in takeover mode) that
goes red on hover, instead of a borderless grey glyph. Under takeover it's the only way back to the
list, so it has to read as a control rather than decoration.

---
## 2026-08-07 — abbreviated pay, a quieter résumé bubble, fixed-height mini cards

**Salary reads as `K` / `M` everywhere.** Pay arrives from the scrapers, `salary_bands.json` and the
Adzuna probe in whatever shape the source wrote it — `$110,000-$145,000 (est.)`, `$120k-$160k/yr`,
`$93,600-$98,800`. Spelled out in full it was the widest thing in a 460px card's chip row and
regularly pushed the match and résumé chips onto a second line. `fmtPay()` (cards.html, beside
`salaryTip`) abbreviates every figure in the string and leaves the separator, `/yr` and `(est.)`
exactly as written: `$110K-$145K (est.)`, `$93.6K-$98.8K`, `$1.2M`. A figure that isn't a whole K
keeps one decimal rather than rounding away a real difference between two bands, and anything under
1,000 is left verbatim — that's an hourly rate (`$45/hr`), not a truncated salary.

**Display only, in all four places pay is printed** (card chip, table Salary cell, drawer chip,
drawer Salary row) plus the archived board. The stored value is untouched: `tracking.csv`,
`job_tracker.json`, the drawer's ✎ manual override and `payNum()` sorting all still see the raw
string, so the abbreviation can never change what the pipeline reads. Every abbreviated figure
carries the exact original in its `title`, so nothing is unrecoverable.
`archived.html` keeps its own copy of the function (standalone page, shares no script with
cards.html) — change the rule in both.

**The résumé bubble no longer says "None".** "None" is the *default* state — no file, nothing
queued, nothing wrong — and it's the state most of the board is in, so the bubble was spending a
slot in the busiest row of the card to say "nothing has happened here yet", which its absence says
just as well. The other three states all report something that HAPPENED (a file exists, it's
queued, it failed) and still speak. Nothing is lost: the Resume filter chip "None" still selects
these jobs, the drawer's Resume row still spells the state out, and starting a tailoring run was
always the drawer's Tailor button, never this bubble.
*Table parity:* a table CELL can't go empty without the column looking broken, so `resumeCell()`
prints the same faint em-dash the table already uses for a missing date or salary. Same information
reduction, each view's own convention.

**Every mini card is the same height (`--cardh`, 140px).** The column was a ragged stack — a
two-line role stood 20px taller than a one-line one — so nothing lined up and the eye had to
re-find the chip row on each card. Cards are uniform now, with the chip row pinned to the bottom
(`.card .chips{margin-top:auto}`) so pay/match/résumé sit on one scan line down the column. The role
is a single ellipsised line (below) and `.card > *{flex:none}` stops an over-long card squashing
every line at once.

**The role is ONE line, ellipsised, with the full title as its `title=` tooltip.** It used to wrap
to two lines while most of the line sat empty, because `.card .role` reserved `padding-right:245px`
for a corner cluster it doesn't actually collide with: `.quick` is 30px tall at `top:12px`, so it
ends at y=43 while the role box started at y=39 — a 4px overlap, on the first line only. Pushing the
role down 4px (`margin-top:6px`) clears the cluster outright, so the reserve drops to a 10px gutter
and the role gets the full 416px of card width. On a 67-job board exactly ONE role is still long
enough to truncate, and hovering it shows the whole string; the drawer spells it out in full either
way. Losing the second role line is also what took `--cardh` from 158 to 140.

**`--cardh` is a FLOOR (`min-height`), sized to the tallest ORDINARY card — not to the worst case.**
The first attempt used a hard `height:200px` chosen from a paper estimate of the worst case
(two-line role + applied date + two chip rows + note). On the real board that was ~50px of dead air
on every card and Khoa called it immediately. Measured against a live 67-job board, natural heights
run 136–156px with a wrapping role and settle at a flat 140px once the role is one line — every card
fits its chips on one row, and 140px makes all 67 exactly uniform with no waste. As a floor it also can't clip: a card that later carries a note or an applied date grows by
that one line instead of hiding it.
*Lesson for the next one of these:* measure the real board (`getBoundingClientRect()` over
`.card` with the constraint temporarily off) before picking a pixel value. The estimate was 40% off,
and the 245px reserve it was partly sized around turned out to be protecting against a 4px overlap.
*Not on narrow windows:* under the 900px breakpoint `.quick` drops into flow as an extra row and the
card is full-width, so the floor and the bottom pin would only add whitespace — cards size to their
content there.

Suppressing the "None" bubble and shortening the pay chip each removed a chip, which is why one
chip row is now enough on all 67 cards.

No wiring change (no script, config or state file added, renamed or re-pointed), so
`DEPENDENCY_MAP.md` is unaffected.
