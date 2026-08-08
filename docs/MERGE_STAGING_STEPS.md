# Merge `staging` → `work/2026-07-28`

Run from `C:\Users\pkhoa\Claude\Projects\auto customize resume`.

There is **no `main`/`master`** in this repo — the trunk is the dated `work/*` chain.
Target is `work/2026-07-28` (current branch, holds commit `181f610` + the staged title-lane work).

Merge base: `95887c6`. Staging adds exactly one commit: `f884c92`
(*Isolate tracker data per-worktree; make launchers port-agnostic*).

---

## What conflicts and why

All 5 conflicts are **modify/delete on data files** — no code conflicts.

```
UD candidates.csv   UD job_tracker.html   UD job_tracker.json
UD raw_applied.json UD tracking.csv
```

`f884c92`'s whole point is to stop version-controlling tracker data so staging test
entries can't pollute live. It `git rm --cached`'d those files and gitignored them.
`work/2026-07-28` meanwhile has real live updates to the same files (209 applied).

**Correct resolution: accept staging's untrack, keep the files on disk** (`git rm --cached`).
That is literally what staging's own `.gitignore` comment instructs.

> Resolving the other way — keeping them tracked — silently reverts staging's entire
> feature. That's the trap.

## The silent one — read this

Two more files are deleted by staging but **do NOT raise a conflict**, because
`work/2026-07-28` never touched them. Git resolves the delete cleanly and
**removes them from your working directory**:

- `archive_index.csv` (82 bytes — your archive index)
- `Open Dashboard.url` (46 bytes)

Staging's intent is that these live per-copy on disk (both are gitignored there).
Step 0 backs them up; step 4 restores them.

---

## Commands

### 0. Safety net

```
git tag premerge-2026-07-28
copy archive_index.csv archive_index.csv.bak
copy "Open Dashboard.url" "Open Dashboard.url.bak"
```

### 1. Commit the staged title-lane work

Do **not** use `git commit -a` — the `showcase_job_agent` submodule is dirty
(`debb967...-dirty`) and unstaged. Leave it out.

```
git commit -m "Title-relevance lane gate: off-lane titles multiply skillMatch by title_offlane_factor (0.35) so keyword-matching off-lane postings stop outranking in-lane ones; in-lane terms default to profile search.titles, unrecognized titles never penalized, off-lane wins over in-lane; jobpipe match --title; tests + DEPENDENCY_MAP"
```

### 2. Merge

```
git merge --no-ff staging
```

Expect the 5 modify/delete conflicts above. Anything else — stop, `git merge --abort`.

### 3. Resolve — untrack, keep on disk

```
git rm --cached tracking.csv job_tracker.json job_tracker.html candidates.csv raw_applied.json
```

### 4. Restore the two silently-deleted files

```
copy archive_index.csv.bak archive_index.csv
copy "Open Dashboard.url.bak" "Open Dashboard.url"
```

They're gitignored post-merge, so they stay out of the index.

### 5. Verify BEFORE committing

```
git status --short
git diff --cached --stat HEAD
```

Check all of:

- no `UU` / `UD` / `AA` lines remain
- `tracking.csv`, `job_tracker.json`, `job_tracker.html`, `candidates.csv`,
  `raw_applied.json`, `archive_index.csv`, `Open Dashboard.url` all still **on disk**
- staged changes are code only: `dashboard_server.py`, `.gitignore`, `DEPENDENCY_MAP.md`,
  `STAGING.md`, `Start Dashboard.bat`, `Install Autostart.bat`, `start_dashboard_hidden.vbs`
  (+ the data-file deletions)

### 6. Commit the merge

```
git commit -m "Merge staging: per-worktree tracker data isolation + port-agnostic launchers. Tracker data (tracking.csv/job_tracker.*/candidates.csv/archive_index.csv/raw_applied.json) untracked and kept on disk, resolving modify/delete against live tracker updates"
```

### 7. Confirm data survived, then push

```
git status --short
git push origin work/2026-07-28
```

`git status` should show the tracker files as ignored (not deleted, not modified).

### 8. Clean up

```
del archive_index.csv.bak
del "Open Dashboard.url.bak"
git tag -d premerge-2026-07-28
```

---

## Rollback

| When | Command |
|---|---|
| Mid-merge (before step 6) | `git merge --abort` |
| After the merge commit | `git reset --hard premerge-2026-07-28` |

Neither touches the on-disk tracker data, which is the point of resolving this way.

---

## Also worth knowing

Your staging worktree at `C:/Users/pkhoa/Claude/Projects/job_agent_staging` shows as
**prunable** — git thinks the directory is gone. If you're done with it:

```
git worktree prune
```

If it still exists and you want it, leave it; but note you cannot check out `staging`
in the main repo while a worktree holds it.

After this merge, staging and `work/2026-07-28` have identical code, so the staging
worktree can be recreated fresh from the merged tip whenever you next need it.
