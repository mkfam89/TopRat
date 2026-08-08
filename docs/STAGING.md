# Staging worktree — test new features without touching live

The live copy runs your real scrape/tailor/notify/git schedule. To try changes safely, use a
**git worktree** on a `staging` branch: one repo, no code duplication, and you promote by merging.
A git-ignored `config/instance.json` neutralizes the staging copy (no phone pings, no scheduler,
its own port) — and because it's git-ignored, those "off" settings can **never** leak to live on a
merge.

## One-time setup (run in your terminal, from the live repo folder)

```bash
# 1. Commit current work so the worktree inherits it (a worktree branches from the last commit).
git add -A
git commit -m "Checkpoint before staging worktree"

# 2. Create the staging worktree as a sibling folder on a new `staging` branch.
git worktree add -b staging "../job_agent_staging"

# 3. Neutralize the staging copy: label + no notifications + no scheduler + its own port.
cd "../job_agent_staging"
cp config/instance.example.json config/instance.json   # {label:STAGING, disable_notifications, disable_scheduler, port:8766}

# 4. Launch staging (reads instance.json → port 8766, amber STAGING badge, scheduler + pushes OFF).
python dashboard_server.py
```

Live stays on http://127.0.0.1:8765 ; staging runs on http://127.0.0.1:8766 — you can run both at once.

## Mark staging resumes as non-real: SBX prefix

Use the wizard field we built: in the **STAGING** dashboard open **Settings → About you → Tailored-resume
filename prefix**, set it to `SBX_Khoa_Pham_Resume_`, and Save. Every tailored file in staging then comes
out as `SBX_Khoa_Pham_Resume_<Company>_<Role>.docx`, so it's obvious it isn't for a real application.

> Do NOT commit `config/profile.json` or `config/profiles/*` from staging — that would carry the SBX
> prefix back to live. The prefix change is meant to stay a local edit in the staging worktree.

## Testing the first-run onboarding

Only staging can test the "no profile yet" flow (live already has a profile):

```bash
# in the staging worktree
rm config/profile.json config/active_profile.json    # (keep the named copy in config/profiles/ if you want)
```

Then load http://127.0.0.1:8766/ — it should redirect to the welcome wizard; "Skip for now" should
show the once-a-day reminder banner on later visits.

## Promote staging → live

```bash
cd "/path/to/live/repo"        # the original folder
git merge staging              # brings the code across; instance.json is git-ignored so live stays live
```

## Remove staging when done

```bash
git worktree remove "../job_agent_staging"
git branch -D staging          # optional
```

## What's isolated vs shared

- **Isolated (safe):** working files per branch, `config/instance.json` (git-ignored, per-copy),
  the dashboard port, the scheduler (off in staging), phone pushes (off in staging), and any
  profile edits you don't commit. **Tracker data is also isolated:** `tracking.csv`,
  `job_tracker.json`, `job_tracker.html`, `candidates.csv`, `archive_index.csv`, and
  `raw_applied.json` are git-ignored, so each worktree keeps its own copy on disk and staging
  test entries can never merge into live's real tracker (and vice versa). Resume binaries
  (`New/`, `Applied/`, `Skipped/`, `*.docx`, `*.pdf`) were already git-ignored the same way.
- **Shared:** one git history/`.git` (code + committed config only — no tracker data or resumes).
  Windows scheduled tasks point at the **live** folder only, so staging never
  auto-scrapes/tailors/pushes even if you forget the instance.json.
