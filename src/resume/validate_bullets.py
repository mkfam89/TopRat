#!/usr/bin/env python3
"""Validate resume_template/bullets.json against bullets.schema.json.

Checks:
  1. Every entry conforms to the schema (uses `jsonschema` if installed;
     otherwise a built-in structural check covering the same rules).
  2. All bullet ids are unique.
  3. trailing_group is consistent with role (the three trailing roles ->
     trailing_group == True; the two top roles -> False).
  4. Each declared `track` has its matching file listed in `source`.

Exit code 0 = clean, 1 = any problem. Zero mandatory third-party deps.

Usage:
    python src/resume/validate_bullets.py            # default paths under resume_template/
    python src/resume/validate_bullets.py path/to/bullets.json path/to/bullets.schema.json
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import json
import sys
from pathlib import Path

ROOT = Path(_paths.ROOT)
import sys as _sys; _sys.path.insert(0, str(ROOT))
from pipelib import BULLETS, BULLETS_SCHEMA   # bullet bank lives with the DATA, not the code
DEFAULT_DATA = Path(BULLETS)
DEFAULT_SCHEMA = Path(BULLETS_SCHEMA)

ROLES = {
    "PROS Site Reliability Engineer",
    "PROS Cloud Operations Engineer",
    "PROS Senior Implementation Consultant",
    "PROS Implementation Analyst",
    "HP Performance Engineering Intern",
    "Rutherford B.H. Yates Museum Volunteer",
}
TRAILING_ROLES = {
    "PROS Senior Implementation Consultant",
    "PROS Implementation Analyst",
    "HP Performance Engineering Intern",
}
TRACKS = {"CORE", "ANALYST", "TECH_SUPPORT"}
REQUIRED = {"id", "role", "track", "text", "tags", "metrics", "source", "trailing_group"}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def track_to_files(source_files):
    """Map track name -> expected filename from the doc's source_files block."""
    return {t: source_files.get(t) for t in TRACKS if source_files.get(t)}


def builtin_checks(doc):
    """Structural validation mirroring bullets.schema.json (no deps)."""
    errors = []
    bullets = doc.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        errors.append("'bullets' must be a non-empty array.")
        return errors

    t2f = track_to_files(doc.get("source_files", {}))

    for i, b in enumerate(bullets):
        loc = f"bullets[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{loc}: not an object.")
            continue
        bid = b.get("id", f"<index {i}>")
        missing = REQUIRED - b.keys()
        if missing:
            errors.append(f"{loc} ({bid}): missing keys {sorted(missing)}.")
        extra = b.keys() - REQUIRED
        if extra:
            errors.append(f"{loc} ({bid}): unexpected keys {sorted(extra)}.")

        if not isinstance(b.get("id"), str) or not b.get("id"):
            errors.append(f"{loc} ({bid}): 'id' must be a non-empty string.")

        if b.get("role") not in ROLES:
            errors.append(f"{loc} ({bid}): role {b.get('role')!r} not in allowed roles.")

        tr = b.get("track")
        if not isinstance(tr, list) or not tr:
            errors.append(f"{loc} ({bid}): 'track' must be a non-empty array.")
        else:
            for t in tr:
                if t not in TRACKS:
                    errors.append(f"{loc} ({bid}): track {t!r} not in {sorted(TRACKS)}.")
            if len(set(tr)) != len(tr):
                errors.append(f"{loc} ({bid}): 'track' has duplicates.")

        if not isinstance(b.get("text"), str) or not b.get("text", "").strip():
            errors.append(f"{loc} ({bid}): 'text' must be a non-empty string.")

        if not isinstance(b.get("tags"), list) or not all(isinstance(x, str) for x in b.get("tags", [])):
            errors.append(f"{loc} ({bid}): 'tags' must be an array of strings.")

        m = b.get("metrics", "missing")
        if not (m is None or isinstance(m, str)):
            errors.append(f"{loc} ({bid}): 'metrics' must be a string or null.")

        src = b.get("source")
        if not isinstance(src, list) or not src or not all(isinstance(x, str) for x in src):
            errors.append(f"{loc} ({bid}): 'source' must be a non-empty array of strings.")

        if not isinstance(b.get("trailing_group"), bool):
            errors.append(f"{loc} ({bid}): 'trailing_group' must be a boolean.")

        # --- cross-field consistency (rules 3 and 4) ---
        role = b.get("role")
        if role in ROLES and isinstance(b.get("trailing_group"), bool):
            expected = role in TRAILING_ROLES
            if b["trailing_group"] != expected:
                errors.append(
                    f"{loc} ({bid}): trailing_group={b['trailing_group']} but role "
                    f"{role!r} implies {expected}."
                )
        if isinstance(tr, list) and isinstance(src, list) and t2f:
            for t in tr:
                exp_file = t2f.get(t)
                if exp_file and exp_file not in src:
                    errors.append(
                        f"{loc} ({bid}): track {t!r} declared but its file "
                        f"{exp_file!r} is not in source {src}."
                    )
    return errors


def schema_checks(doc, schema):
    """Full JSON-Schema validation if `jsonschema` is available; else skip."""
    try:
        import jsonschema  # type: ignore
    except Exception:
        print("  (jsonschema not installed — ran built-in structural checks only)")
        return []
    from jsonschema import Draft7Validator

    validator = Draft7Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    ]


def main(argv):
    data_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATA
    schema_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_SCHEMA

    for p in (data_path, schema_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}")
            return 1

    doc = load(data_path)
    schema = load(schema_path)

    problems = []
    problems += schema_checks(doc, schema)
    problems += builtin_checks(doc)

    # id uniqueness (rule 2)
    ids = [b.get("id") for b in doc.get("bullets", []) if isinstance(b, dict)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"Duplicate ids: {dupes}")

    n = len(doc.get("bullets", []))
    if problems:
        print(f"FAILED — {len(problems)} problem(s) across {n} bullet(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"OK — {n} bullets, {len(set(ids))} unique ids, schema valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
