# Tests

```
tools\run_tests.bat          Windows
python -m pytest -q          anywhere
```

The suite only reads the repository. The golden fixtures are in `tests/fixtures/`. Each
file that a test writes goes to the `tmp_path` folder of pytest. The suite uses no network
and no LibreOffice.

pytest is **not** part of the shipped bundle. It is for developers only, so it is in
`requirements-dev.txt` and not in `requirements.txt`. If pytest is absent,
`run_tests.bat` installs it into `python\` on the first run. Later runs skip that step. To
install it by hand, run this command:

```
python\python.exe -m pip install -r requirements-dev.txt
```

`src/ops/package.py` removes those packages again when it builds a release zip. A
downloaded copy therefore never holds the test tools.

## First run on a fresh clone: build the .docx fixtures

`.gitignore` excludes `*.docx` in the whole repository. The Word fixtures in
`tests/fixtures/docx/` are therefore **generated, never committed**. Build them one time:

```
python tests/build_docx_fixtures.py
```

Without these files, the tests that read `.docx` cannot run. This is a decision, not a
fault. The other option is a binary exception in the rule that keeps real resumes out of
the repository.

## python-docx is optional, so its tests SKIP rather than fail

`python-docx` is in `requirements.txt` (optional features), not a hard dependency: the app
degrades to "no tailored .docx" without it. `test_lint_resume.py` and `test_render_resume.py`
therefore open with `pytest.importorskip('docx', ...)`, because `lint_resume.py` imports
`docx` at module scope and would otherwise kill the whole run with a **collection error** on
any interpreter that lacks it — including one where `pytest -m "not needs_userdata"` was
supposed to give a green board, since a module that dies at import never reaches its marks.

The bundled `python\python.exe` already has python-docx, and `tools\run_tests.bat` picks it
first, so the normal way to get a full board is:

```
tools\run_tests.bat
```

Running the suite with some other interpreter (a system or Anaconda `python`) is fine — those
two modules just skip until you `pip install python-docx` into it.

## Two kinds of "needs your data", and only one of them is a mark

A fresh clone has no personal data. Two different things used to be missing, and the
suite treated them as one problem. They are now separate.

**Config DIALS are frozen.** `skills.json`, `skill_aliases.json`, `strategy.json`,
`profile.json` and `geo_cache.json` are committed under `tests/fixtures/config_frozen/`.
A test that needs them takes the `frozen_config` fixture. It runs on a fresh clone, and it
runs in CI. Your live configuration is not read.

**Real CONTENT cannot be frozen.** The bullet bank and the base resumes are your own
words in a gitignored `.docx`. A test that needs them keeps the mark `needs_userdata`.
23 tests are in this group. Skip them and the board is green:

```
pytest -m "not needs_userdata"      # 411 passed - what CI runs
```

| Test | Needs | Why it fails without it |
|---|---|---|
| all of `test_render_resume.py` (22) | `resume_template/bullets.json` | Every bullet must come from the bank. With no bank, the renderer has no content. |
| `test_scoring.py::test_golden_classify` | `resume_template/` | `classify()` returns which base resume to use. With no base resumes, there is nothing to return. |

These tests pass after you use the Setup page and put a base resume in
`resume_template/`. If a test **outside** that group fails on a fresh clone, it is a
real bug.

### Why the dials were frozen (2026-08-13)

`test_golden_skill_match` failed on 1 of the 74 rows in `tests/fixtures/scoring.json`.
The row `li_4445471875` scored 0.85 with `flagged: ['Change Management']`; the snapshot
said 0.92 and no flag. Nothing in the code had changed. "Change Management" had been
removed from `config/skills.json`, and the golden was older than that edit.

That is the failure mode. A red suite pointed at `scoring.py`, and the cause was a
settings edit. Goldens that read live configuration are snapshots of an opinion, not of
behavior. The frozen copies make the tests pin the algorithm — the shrinkage math, the
alias expansion, the substring stems — so a change to your own skills list moves nothing,
and a change to the scoring code still moves everything.

Regenerate with `python tests\make_fixtures.py`, then read the diff. The generator uses
the same frozen root, so it cannot write values the suite disagrees with. Note that it
also rewrites every row against **today's** listings, so a blind run accepts far more
than the change you meant to make.

## Tests must not read the live machine

`test_stop_board.py::test_resolve_port_prefers_env` failed **only while the dashboard was
open**. If nothing listens on the wanted port, `resolve_port()` reads
`config/runtime.json` instead. The assertion therefore measured the sockets of this
machine. The test now pins `sb.HERE` and `sb.is_up`, and the fallback branches have their
own tests.

NOTE: If a test fails only while the board is open, or only on one operating system, look
at the test before the code. The suite promises no network and no real configuration.

## Fixed: the config goldens were order-dependent (2026-08-11)

The 8 config-golden tests passed alone and failed in a full run. The cause was
`test_backlog.py::board`. That fixture reloads `pipelib`, `blocklist` and `backlog` under
a scratch `TOP_RAT_DATA`, and it never reloaded them onto the real root. These modules
resolve their paths **at import**. `monkeypatch` restores the environment variable by
itself, but that restore moved nothing. The scratch root stayed live for the rest of the
session. `test_backlog` is second in collection order, so every later test read its
configuration from a `tmp_path` folder that pytest already deleted.

Two changes correct this fault:

- `board` is now a yield fixture. It calls `monkeypatch.undo()` and then reloads those
  modules and `scheduler` onto the real root. The undo must come first. Fixture finalizers
  run in reverse dependency order, so `monkeypatch` restores the variable too late.
- `conftest.py` has an autouse guard with the name `_data_root_restored`. The guard fails a
  test at **setup** if `pipelib.DATA` is different from the value at the start of the
  session. `pytest_sessionfinish` also looks for a leak in the last test. A future leak now
  names the test that caused it, and not the golden that found it.

The earlier note here said that the cause was unknown, and that `DATA` was identical in
both runs. That was wrong, and the wrong note made the fault hard to see.
