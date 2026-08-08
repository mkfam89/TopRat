# Split personal data into `job_agent_profile_KP` — steps to run

Code changes are **done and tested** (234 tests pass both split and unsplit). What's left are
the git operations, which have to run on your machine — the sandbox mount can't write to
`.git` (it can't even delete its own lock files).

Run everything from `C:\Users\pkhoa\Claude\Projects\auto customize resume`.

---

## 1. Re-init the data repo locally

I copied and checksum-verified all 35 files into `job_agent_profile_KP\`, and the commit
itself succeeded — but git left undeletable `index.lock` / `HEAD.lock` files and 41 stray
temp objects behind, which would block git for you. Cleanest fix is to throw away that `.git`
and let your own git make it:

```
cd "C:\Users\pkhoa\Claude\Projects\auto customize resume\job_agent_profile_KP"
rmdir /s /q .git
git init -b main
git add -A
git commit -m "Initial import: tracker state, profile + generated config, resume source content"
```

The **files** are fine — verified byte-for-byte against the originals
(245 tracking rows: 212 applied / 31 skipped / 2 pending; `job_tracker.json`,
`profile.json`, `bullets.json` all parse). Only the `.git` metadata is suspect.

## 2. Move it next to the code repo

Resolution step 3 finds a sibling automatically, so this needs no config at all:

```
cd "C:\Users\pkhoa\Claude\Projects"
move "auto customize resume\job_agent_profile_KP" .
```

Result:

```
Claude/Projects/
├── auto customize resume/      (code)
└── job_agent_profile_KP/       (data)
```

Verify — should print the data path and `True`:

```
cd "C:\Users\pkhoa\Claude\Projects\auto customize resume"
python -c "import pipelib; print(pipelib.DATA); print(pipelib.SPLIT)"
```

> If you'd rather keep it nested, that works too (resolution step 4) — but it sits inside the
> code repo, so it's gitignored and easy to lose track of. The sibling layout is the intended one.

## 3. Untrack the data from the code repo

Files stay on disk; only the index entries go.

```
cd "C:\Users\pkhoa\Claude\Projects\auto customize resume"
git rm --cached -r --ignore-unmatch tracking.csv job_tracker.json job_tracker.html ^
  candidates.csv archive_index.csv raw_applied.json geo_cache.json scrape_meta.json ^
  details_manual.json config/profile.json config/profile.json.bak1 config/profile.json.bak2 ^
  config/profiles config/active_profile.json config/search_urls.json config/skills.json ^
  config/skill_aliases.json config/summary_rules.json config/gui_settings.json ^
  config/schedule.json config/employer_blocklist.json resume_template
```

Expect ~23 files, ~10,798 deletions from the index.

## 4. Commit the split

```
git add -A
git commit -m "Split personal data into job_agent_profile_KP: pipelib data-root resolver (JOB_AGENT_DATA / instance.json data_root / sibling / nested / HERE fallback) + cfg()/cfg_write() config overlay; repoint notify, salary_probe, scrape, profile_lib, locality, git_daily, dashboard_server off their own HERE; untrack tracker state, profile, generated config and resume_template; DEPENDENCY_MAP data-root section"
```

## 5. Now merge staging — it's clean

The 5 modify/delete conflicts are **gone**: both branches now delete those files, so there's
nothing to resolve. I verified this in a throwaway clone — *"Automatic merge went well."*

```
git merge --no-ff staging
git push origin work/2026-07-28
```

`MERGE_STAGING_STEPS.md` still documents the old conflict path — keep it only as a record of
why the split happened first. Steps 0/4 there (backing up `archive_index.csv` and
`Open Dashboard.url`) are no longer needed: both files are now untracked, so the merge can't
delete them.

## 6. Push the data repo

Create **`job_agent_profile_KP` as a PRIVATE repo** on GitHub, then:

```
cd "C:\Users\pkhoa\Claude\Projects\job_agent_profile_KP"
git remote add origin <your private repo url>
git push -u origin main
```

---

## Rollback

Nothing here is destructive — the data files are never deleted from disk, only untracked.

| To undo | Command |
|---|---|
| The untracking | `git reset --hard HEAD~1` (before pushing) |
| The whole split | `git revert <commit>` — code falls back to reading in-repo automatically |

The fallback is why this is safe: with no data repo and no env var, `resolve_data_root()`
returns the code dir and everything behaves exactly as it did before.

---

## What changed in the code

`pipelib.py` gained the resolver and the config overlay; 13 other modules were repointed at it.

```
pipelib.py         +113   resolve_data_root(), DATA/SPLIT, cfg()/cfg_write(),
                          CODE_CONFIG/DATA_CONFIG/USER_CONFIG, INSTANCE_JSON,
                          SALARY_QUOTA/GEO_CACHE/SCRAPE_META/NOTIFIED/RAW_APPLIED/BULLETS
dashboard_server.py  55   P() = code assets, D()/pipelib constants = data
profile_lib.py       26   profile + generated config -> data root; examples stay with code
scrape.py            22   imports pipelib instead of its own HERE/CONFIG
scoring.py           16   7x os.path.join(CONFIG,...) -> cfg()
notify.py            13   ntfy/GUI/candidates -> data root; instance.json stays with code
salary_probe.py      12   cache/quota/candidates/tracking -> data root
jobpipe.py            9   cfg/cfg_write imports
locality.py           6   geo_cache/listings -> data root
blocklist ghost_risk reposts git_daily  ~4 each
```

Two design points worth remembering:

- **`config/` is split down the middle**, so resolution is *per-file*, not per-directory.
  `cfg()` prefers your copy and falls back to the shipped one. `strategy.json` and
  `salary_bands.json` stay with the code because they're behaviour, not identity.
- **`config/instance.json` must stay beside `pipelib.py`** — the data root is resolved *from*
  it, so it can't live in the root it locates.

## Still worth doing

`New/`, `Applied/`, and `Skipped/` (tailored résumé binaries) still resolve off the code dir,
while `tracking.csv` — which indexes them — now lives in the data repo. They're gitignored so
nothing breaks, but the two halves of the same record are in different folders. Reuniting them
means repointing `jobpipe.py` and `dashboard_build.py`; I left it out to keep this change
reviewable. Noted in `DEPENDENCY_MAP.md`.
