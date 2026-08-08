#!/usr/bin/env python3
"""
skip_job.py — manually mark jobs as SKIPPED or BOOKMARKED so BOTH the dashboard
and the nightly automation respect it.

Source of truth: job_tracker.json
  "skipped_jobs": [ "<Company_Role id>", ... ]   # never re-surfaced / re-generated
  "bookmarked":   [ "<Company_Role id>", ... ]   # protected from the weekly archive
It also updates the PIPELINE_SKIPPED / BOOKMARKED constants inside job_tracker.html
so the dashboard reflects the change immediately.

USAGE (run from this folder):
  # Skips
  python src/pipeline/skip_job.py list                      # list skipped jobs
  python src/pipeline/skip_job.py add "united health"       # skip everything matching the text
  python src/pipeline/skip_job.py add UnitedHealthGroup_ClinicalOperationsAnalyst
  python src/pipeline/skip_job.py remove "legalfy"          # un-skip matches
  python src/pipeline/skip_job.py import skipped_jobs.json   # merge a dashboard "Export skips" file

  # Bookmarks (protect a job from the weekly archive, any status)
  python src/pipeline/skip_job.py bookmark "world wide"     # bookmark matches
  python src/pipeline/skip_job.py unbookmark "world wide"   # remove bookmark
  python src/pipeline/skip_job.py bookmarks                 # list bookmarked jobs

Matching is case-insensitive against the job id, company, and role.
If a search matches several jobs it applies to all of them (they are printed).
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import sys, os, re, json

HERE = _paths.ROOT
# Tracker state lives in the DATA root once the personal data is split out.
from pipelib import JSON_PATH, HTML_PATH  # noqa: E402

# kind -> (json key, html constant name, human label)
KINDS = {
    "skip": ("skipped_jobs", "PIPELINE_SKIPPED", "skipped"),
    "bookmark": ("bookmarked", "BOOKMARKED", "bookmarked"),
}


def load_jobs():
    html = open(HTML_PATH, encoding="utf-8").read()
    m = re.search(r"const JOBS\s*=\s*(\[.*?\]);", html, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def read_json():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_json(d):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def sync_html(const_name, values):
    """Rewrite ONLY the `const <const_name> = [...];` array in the HTML."""
    if not os.path.exists(HTML_PATH):
        return False
    html = open(HTML_PATH, encoding="utf-8").read()
    arr = json.dumps(sorted(values))
    new_html, n = re.subn(
        r"const " + const_name + r"\s*=\s*\[.*?\];",
        "const " + const_name + " = " + arr + ";",
        html, flags=re.S, count=1,
    )
    if n:
        open(HTML_PATH, "w", encoding="utf-8").write(new_html)
    return bool(n)


def resolve(query, jobs):
    ids = {j["id"] for j in jobs}
    if query in ids:
        return [query]
    q = query.lower()
    hits = []
    for j in jobs:
        hay = f"{j['id']} {j.get('companyDisplay','')} {j.get('role','')}".lower()
        if q in hay:
            hits.append(j["id"])
    return hits


def do_list(kind, d, by_id):
    json_key, _, label = KINDS[kind]
    items = list(dict.fromkeys(d.get(json_key, [])))
    if not items:
        print(f"No jobs are currently {label}.")
        return
    print(f"{len(items)} {label} job(s):")
    for i in items:
        j = by_id.get(i)
        lbl = f"{j['companyDisplay']} — {j['role']}" if j else "(not in current tracker)"
        print(f"  • {i}   {lbl}")


def do_change(kind, action, query, d, jobs, by_id):
    json_key, const_name, label = KINDS[kind]
    items = list(dict.fromkeys(d.get(json_key, [])))
    match_ids = resolve(query, jobs)
    if not match_ids:
        print(f"No jobs match: {query!r}")
        return
    changed = []
    for mid in match_ids:
        j = by_id.get(mid)
        lbl = f"{j['companyDisplay']} — {j['role']}" if j else mid
        if action == "add":
            if mid not in items:
                items.append(mid); changed.append((label, mid, lbl))
        else:
            if mid in items:
                items.remove(mid); changed.append(("un-" + label, mid, lbl))
    if not changed:
        print("No change (already in the desired state).")
        return
    d[json_key] = items
    write_json(d)
    synced = sync_html(const_name, items)
    for act, mid, lbl in changed:
        print(f"{act}: {mid}   {lbl}")
    print(f"Total {label}: {len(items)}.")
    print("Dashboard synced (reload job_tracker.html)." if synced
          else f"WARNING: could not update {const_name} in HTML.")


def do_import(path, kind, d):
    json_key, const_name, label = KINDS[kind]
    add = json.load(open(path, encoding="utf-8"))
    items = list(dict.fromkeys(d.get(json_key, [])))
    before = len(items)
    for i in add:
        if i not in items:
            items.append(i)
    d[json_key] = items
    write_json(d)
    synced = sync_html(const_name, items)
    print(f"Imported {len(add)} id(s); {len(items)-before} new. Total {label}: {len(items)}.")
    print("Dashboard synced." if synced else f"WARNING: could not update {const_name}.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    cmd = args[0].lower()
    d = read_json()
    jobs = load_jobs()
    by_id = {j["id"]: j for j in jobs}

    # Back-compat skip commands + bookmark commands
    if cmd == "list":
        do_list("skip", d, by_id); return
    if cmd == "bookmarks":
        do_list("bookmark", d, by_id); return
    if cmd in ("add", "remove"):
        if len(args) < 2:
            print(f"Usage: python src/pipeline/skip_job.py {cmd} \"<company/role or id>\""); return
        do_change("skip", "add" if cmd == "add" else "remove", " ".join(args[1:]), d, jobs, by_id); return
    if cmd in ("bookmark", "unbookmark"):
        if len(args) < 2:
            print(f"Usage: python src/pipeline/skip_job.py {cmd} \"<company/role or id>\""); return
        do_change("bookmark", "add" if cmd == "bookmark" else "remove", " ".join(args[1:]), d, jobs, by_id); return
    if cmd == "skip":  # explicit form: skip add/remove/list
        sub = args[1].lower() if len(args) > 1 else "list"
        if sub == "list": do_list("skip", d, by_id); return
        do_change("skip", sub, " ".join(args[2:]), d, jobs, by_id); return
    if cmd == "import":
        path = args[1] if len(args) > 1 else os.path.join(HERE, "skipped_jobs.json")
        kind = "bookmark" if "--as" in args and "bookmark" in args else "skip"
        do_import(path, kind, d); return

    print(f"Unknown command: {cmd}\n"); print(__doc__)


if __name__ == "__main__":
    main()
