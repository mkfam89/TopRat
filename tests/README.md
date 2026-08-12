# Tests

```
tools\run_tests.bat          Windows
python -m pytest -q          anywhere
```

The suite is read-only over the repo. Golden fixtures live in `tests/fixtures/`;
anything writable goes to pytest's `tmp_path`. No network, no LibreOffice.

pytest is **not** part of the shipped bundle — it is developer-only, so it lives
in `requirements-dev.txt` rather than the user-facing `requirements.txt`.
`run_tests.bat` installs it into `python\` the first time it finds it missing;
later runs skip that. To do it by hand:

```
python\python.exe -m pip install -r requirements-dev.txt
```

`src/ops/package.py` strips those packages back out when it builds a release
zip, so a downloaded copy never carries the test tooling.

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

### Open: one golden row has drifted

`test_golden_skill_match` fails on **1 of the 74** rows in
`tests/fixtures/scoring.json`: `li_4445471875` scores 0.85 with
`flagged: ['Change Management']` where the snapshot has 0.92 and nothing flagged.
"Change Management" is no longer in `config/skills.json` or `skill_aliases.json`
(the nearest entry is "Incident Management"), so the fixture predates that config
change. This is drift, not order dependence — it fails in isolation too. Either
put the skill back or regenerate with `python tests\make_fixtures.py` and review
the diff; do not regenerate blind, since that would bake in whatever else moved.

## Tests must not read the live machine

`test_stop_board.py::test_resolve_port_prefers_env` used to fail **only while the
dashboard was running**: `resolve_port()` falls back to `config/runtime.json` when
the wanted port is not listening, so the assertion was really about this machine's
sockets. It now pins `sb.HERE` and `sb.is_up`, and the fallback branches got their
own tests. If a test only fails when the board is up, or only on one OS, suspect
the test before the code — the suite promises no network and no real config.

## Fixed: the config goldens were order-dependent (2026-08-11)

The 8 config-golden tests passed alone and failed in a full run. The cause was
`test_backlog.py::board`, which reloads `pipelib`, `blocklist` and `backlog` under
a scratch `TOP_RAT_DATA` and never reloaded them back. Those modules resolve their
paths **at import**, so restoring the environment variable (which `monkeypatch`
does on its own) moved nothing — the scratch root stayed live for the rest of the
session, and since `test_backlog` is second in collection order, every later test
read its config out of a `tmp_path` pytest had already deleted.

Two changes close it:

- `board` is now a yield fixture. It calls `monkeypatch.undo()` and then reloads
  those modules (plus `scheduler`) onto the real root. The undo has to come first:
  fixture finalizers run in reverse dependency order, so `monkeypatch` would not
  have restored the variable yet.
- `conftest.py` has an autouse `_data_root_restored` guard that fails a test at
  **setup** if `pipelib.DATA` no longer matches where the session started, plus a
  `pytest_sessionfinish` check for a leak in the very last test. A future leak now
  blames the test that caused it instead of the golden that noticed.

The earlier note here said the cause was unknown and that `DATA` was identical in
both cases. It was not — that is what took so long to see.
