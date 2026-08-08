# Tests

```
tools\run_tests.bat          Windows
python -m pytest -q          anywhere
```

The suite is read-only over the repo. Golden fixtures live in `tests/fixtures/`;
anything writable goes to pytest's `tmp_path`. No network, no LibreOffice.

## First run on a fresh clone: build the .docx fixtures

`.gitignore` excludes `*.docx` repo-wide, so the Word fixtures under
`tests/fixtures/docx/` are **generated, never committed**. Build them once:

```
python tests/build_docx_fixtures.py
```

Without them the `.docx`-reading tests cannot run. This is deliberate — the
alternative is carving a binary exception into the rule that keeps real resumes
out of the repo.

## Tests that need YOUR data

A fresh clone ships no personal data — no resume templates, no geocode cache, no
skills config. 28 tests read those. They are marked `needs_userdata`, so skip them
and the board is green:

```
pytest -m "not needs_userdata"      # 258 passed - what CI runs
```

| Test | Needs | Why it fails without it |
|---|---|---|
| all of `test_render_resume.py` | `resume_template/bullets.json` | Every bullet must trace to the bank; no bank, nothing to render. |
| `test_local.py::test_golden_locations` | `geo_cache.json` | Locality resolves from the cache; an empty cache reads every town as non-local. |
| 4 in `test_scoring.py`, 3 in `test_parse_card.py`, 1 in `test_blocklist.py` | live `skills.json`, `skill_aliases.json`, `strategy.json`, blocklist | The goldens were snapshotted against the owner's config, so different config means different scores. |

They start passing once you have run the Setup page and put a base resume in
`resume_template/`. Everything else runs on committed fixtures.

If a test **outside** that set fails on a fresh clone, that is a real bug.

## Known defect: the config goldens are order-dependent

The 8 config-golden tests pass when their file runs alone and fail when the whole
suite runs, even though `DATA`, the resolved config path and the working directory
are identical in both cases. The `needs_userdata` marker keeps them out of CI, but
it is **not** a fix and the cause is still unknown. Do not read a green CI run as
evidence this went away.
