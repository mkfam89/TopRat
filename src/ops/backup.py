#!/usr/bin/env python3
"""backup.py — snapshot code + tracking data into Backups/snapshots/<timestamp>/, keep newest 5.
Run before any risky change and at the end of each pipeline run."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, shutil, time, glob
HERE = _paths.ROOT
SNAP=os.path.join(HERE,'Backups','snapshots')
KEEP=5
# files/dirs that constitute the recoverable state of the system
ITEMS=['src','job_tracker.html','job_tracker.json',
       'tracking.csv','archive_index.csv','config']
def main():
    os.makedirs(SNAP,exist_ok=True)
    ts=time.strftime('%Y%m%d_%H%M%S')
    dest=os.path.join(SNAP,ts); os.makedirs(dest,exist_ok=True)
    saved=[]
    for it in ITEMS:
        src=os.path.join(HERE,it)
        if not os.path.exists(src): continue
        d=os.path.join(dest,it)
        if os.path.isdir(src): shutil.copytree(src,d,dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(d),exist_ok=True); shutil.copy2(src,d)
        saved.append(it)
    # prune to newest KEEP
    snaps=sorted([d for d in glob.glob(os.path.join(SNAP,'*')) if os.path.isdir(d)])
    removed=[]
    for old in snaps[:-KEEP]:
        shutil.rmtree(old,ignore_errors=True); removed.append(os.path.basename(old))
    print(f"snapshot {ts}: saved {len(saved)} items -> {dest}")
    if removed: print(f"pruned old snapshots: {removed}")
    print("kept snapshots:", [os.path.basename(x) for x in sorted(glob.glob(os.path.join(SNAP,'*'))) if os.path.isdir(x)])
if __name__=='__main__': main()
