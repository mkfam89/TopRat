#!/usr/bin/env python3
"""llm_tailor.py — OPTIONAL Claude enhancement layer for the CUSTOMIZE path.

WHAT IT DOES
    Turns one queued `action == "customize"` job into a validated content.json by
    asking Claude to SELECT bullet_ids from resume_template/bullets.json (never invent)
    and write a JD-tailored SUMMARY. render_resume.render() then validates and lint-gates
    the result. Any failure (no CLI, not logged in, plan limit, bad JSON, lint reject)
    returns None, and the caller keeps the job on the deterministic path (template copy /
    Cowork agent) — nothing breaks.

FLAT RATE, NEVER METERED (2026-08-04)
    The transport is the **Claude Code CLI in non-interactive mode** (`claude -p`), NOT the
    Messages API. `claude -p` draws from the Claude subscription's usage limits, so tailoring
    costs nothing beyond the plan the user already pays for. The old `sk-ant-` API-key path was
    removed on purpose: it billed per use at API rates, which is exactly what this project
    does not want.

    Because of that, `_child_env()` STRIPS `ANTHROPIC_API_KEY` (and the auth-token / base-url
    overrides) from the subprocess environment. Claude Code prefers an API key over the
    subscription when one is present, so leaving the variable in place would silently move
    every tailoring run onto pay-as-you-go billing. Stripping it is the whole safety property
    of this module — do not "simplify" it away.

DESIGN RULE (CLAUDE.md): Claude is an optional enhancement, never a dependency.
    * No CLI / not logged in -> llm_available() False or build_content None -> template copy.
    * CLI present            -> customize jobs get AI-picked bullets + tailored summary,
                                still lint-gated by render_resume.render().

CONFIG (optional; every field has a default):
    config/claude_cli.json  { "binary", "model", "timeout", "extra_args" }
    Template: config/claude_cli.json.example.

ZERO NEW HARD DEPENDENCIES: stdlib subprocess against a CLI the user already installed.

CLI
    python src/resume/llm_tailor.py --check            # locate + ping the CLI; report connected / not
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pipelib import cfg, BULLETS, _read_json_quiet

# The ONLY keys read from config/claude_cli.json. Anything else in that file is ignored,
# so a stray field can never change how the CLI is invoked.
DEFAULTS = {
    "binary": "",          # explicit path to the claude executable; "" = auto-detect
    "model": "",           # "" = whatever the user's plan defaults to (cheapest correct choice)
    "timeout": 240,        # the CLI is slower to start than a raw HTTP call
    "extra_args": [],      # escape hatch, e.g. ["--model", "haiku"]
}

# Environment variables that would push Claude Code off the subscription and onto metered
# API billing. Removed from every child process this module starts. See the module docstring.
_METERED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

# Where the CLI usually lands per installer, checked when it is not already on PATH.
_CANDIDATE_PATHS = (
    os.path.expanduser("~/.claude/local/claude"),
    os.path.expanduser("~/.local/bin/claude"),
    os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\claude\claude.exe"),
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
)


# --------------------------------------------------------------------------- #
# CLI discovery + config
# --------------------------------------------------------------------------- #

def llm_config():
    """Merged CLI config (defaults + whitelisted keys from config/claude_cli.json)."""
    c = dict(DEFAULTS)
    raw = _read_json_quiet(cfg("claude_cli.json"))
    for key in DEFAULTS:
        if key in raw and raw[key] not in (None, ""):
            c[key] = raw[key]
    return c


def find_claude(conf=None):
    """Absolute path to the Claude Code executable, or None.

    Order: an explicit `binary` in config -> PATH -> the per-installer locations above.
    Mirrors how tailor_local finds soffice: locate once, degrade quietly when absent.
    """
    conf = conf or llm_config()
    explicit = str(conf.get("binary") or "").strip()
    if explicit:
        return explicit if os.path.exists(explicit) else None
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if found:
        return found
    for p in _CANDIDATE_PATHS:
        if p and os.path.exists(p):
            return p
    return None


def _child_env():
    """os.environ minus anything that would authenticate as the metered API.

    Claude Code picks an API key over the logged-in subscription, so an ANTHROPIC_API_KEY
    left in the environment — set by the user for some other tool, or by a stale shell —
    would quietly bill every tailoring run at API rates. This module's entire cost promise
    depends on removing it.
    """
    env = dict(os.environ)
    for var in _METERED_ENV:
        env.pop(var, None)
    return env


def llm_available():
    """True when the Claude Code CLI can be found. This is the 'connected?' switch.

    Deliberately does NOT verify the login — that would cost a round trip on every queue
    tick. A found-but-not-logged-in CLI fails inside build_content, which returns None and
    drops the job to the template copy, so the pipeline still degrades correctly.
    """
    return bool(find_claude())


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

def load_bank(path=BULLETS):
    """id -> bank entry, from resume_template/bullets.json (the never-invent source)."""
    with open(path, encoding="utf-8") as f:
        return {b["id"]: b for b in json.load(f)["bullets"]}


def _jd_text(item):
    """Best-effort job-description text for the posting. Empty string if unavailable."""
    url = item.get("applyUrl") or item.get("url") or ""
    if not url:
        return ""
    try:
        import jobdesc
        res = jobdesc.fetch(url)
        return (res or {}).get("text", "") or ""
    except Exception:
        return ""


def _summary_rules():
    r = _read_json_quiet(cfg("summary_rules.json"))
    return r or _read_json_quiet(cfg("summary_rules.example.json"))


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #

_owner_cache = None


def _owner_name():
    """Whose resume this is, for the system prompt. From profile.json identity.full_name.

    The prompt used to name the first user directly ("You tailor <real name>'s resume..."),
    which told the model the wrong person's name for every other user. Falls back to a
    neutral noun — the prompt reads fine without a name, and a wrong name is worse than
    none since rule 4 is specifically about not inventing an identity."""
    global _owner_cache
    if _owner_cache is None:
        try:
            import profile_lib as pl
            _owner_cache = str(((pl.load_profile(clean=True) or {}).get("identity") or {})
                               .get("full_name") or "").strip() or "the candidate"
        except Exception:
            _owner_cache = "the candidate"
    return _owner_cache


def build_prompt(item, bank, jd_text, rules):
    """Return (system, user) strings. Pure — safe to unit-test."""
    notes = str(item.get("userNotes") or "").strip()
    flagged = item.get("flaggedSkills") or []

    who = _owner_name()
    system = (
        "You tailor %s's resume for one job by SELECTING bullets from a fixed bank "
        "and writing a summary. You output ONE JSON object (the content.json) and nothing else.\n"
        "HARD RULES — a violation makes the output unusable:\n"
        "1. bullet_ids MUST come from the provided bank. Never invent a bullet or an id. To "
        "reword a bank bullet, put the id and new text in bullet_overrides — the id must still "
        "exist in the bank.\n"
        "2. Every role gets AT LEAST TWO bullets.\n"
        "3. The three trailing roles (Senior Implementation Consultant, Implementation Analyst, "
        "HP / Performance Engineering Intern) appear TOGETHER or not at all.\n"
        "4. The summary's opening phrase is %s's REAL identity from the summary rules. NEVER "
        "adopt, restate, or blend the target job title into it. Tailor only the sentences after "
        "the opener.\n"
        "5. No section headers other than the fixed set. Never emit ADDITIONAL EXPERIENCE, CORE "
        "COMPETENCIES, or AREAS OF FOCUS anywhere.\n"
        "6. Never invent skills, tools, titles, or experience. Re-weight and reorder existing "
        "bullets toward the job; do not fabricate.\n"
        "7. Put ZERO notes, TODOs, or flagged-skill lines in the content. Gaps are handled "
        "elsewhere.\n"
    ) % (who, who)

    schema = {
        "company": "<company slug, letters/digits>",
        "role": "<core role slug>",
        "summary": "<opener = real identity; rest tailored to the JD>",
        "skills_groups": [{"label": "<group>", "items": ["<skill>", "..."]}],
        "roles": [{
            "company": "<employer>", "location": "<city, state>",
            "title": "<title held>", "dates": "<mm/yyyy-mm/yyyy>",
            "bullet_ids": ["<id from bank>", "..."],
            "bullet_overrides": {"<id from bank>": "<reworded text>"},
        }],
        "education": {"institution": "", "location": "", "degree": "", "dates": ""},
        "body_pt": 11,
        "spacer_lines": 0,
    }

    parts = [
        "TARGET JOB",
        "company: %s" % (item.get("company") or ""),
        "role: %s" % (item.get("role") or ""),
        "",
        "JOB DESCRIPTION (may be empty):",
        (jd_text[:8000] if jd_text else "(none available — tailor from the role title)"),
        "",
        "BULLET BANK — the ONLY bullets you may use (pick by id):",
        json.dumps(list(bank.values()), ensure_ascii=False),
        "",
        "SUMMARY RULES (identity opener guardrails):",
        json.dumps(rules, ensure_ascii=False),
        "",
        "OUTPUT SCHEMA (return exactly this shape, filled in):",
        json.dumps(schema, ensure_ascii=False),
    ]
    if flagged:
        parts += ["", "Skills the JD wants the owner may lack — do NOT claim these: %s"
                  % ", ".join(map(str, flagged))]
    if notes:
        # The owner's own instructions for THIS posting — highest priority short of the hard rules.
        # The heading used to be the first user's name in caps, which told the model the wrong
        # person's name for every other user (same defect _owner_name() fixed at line 190) and
        # put a real name in a shipped source file. Falls back to a neutral noun.
        who = _owner_name()
        parts += ["", "%s INSTRUCTIONS FOR THIS JOB (follow these; they override your own "
                  "emphasis judgement, never the hard rules):"
                  % (("%s'S" % who).upper() if who else "THE OWNER'S"), notes]
    parts += ["", "Return ONLY the JSON object."]
    return system, "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI call
# --------------------------------------------------------------------------- #

def _call_cli(conf, binary, system, user):
    """Run `claude -p` once and return its reply text. Raises RuntimeError on failure.

    The prompt goes in on STDIN, not argv: the bullet bank plus 8000 characters of job
    description blows past the Windows command-line length limit, and a truncated prompt
    would fail as a confusing model error rather than an obvious one.

    The system rules are prepended to the prompt instead of passed via a flag, so this
    works on every CLI version without probing for flag support.

    cwd is a throwaway temp directory. Run from the repo, Claude Code would load this
    project's CLAUDE.md and settings and behave like a coding session — reading files and
    running tools — when all that is wanted is one JSON object back.
    """
    cmd = [binary, "-p"]
    model = str(conf.get("model") or "").strip()
    if model:
        cmd += ["--model", model]
    extra = conf.get("extra_args") or []
    if isinstance(extra, (list, tuple)):
        cmd += [str(a) for a in extra]
    prompt = system + "\n\n" + user
    workdir = tempfile.mkdtemp(prefix="tailor_")
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=int(conf.get("timeout") or 240),
                           cwd=workdir, env=_child_env(),
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise RuntimeError("The Claude Code CLI was not found at %s." % binary)
    except subprocess.TimeoutExpired:
        raise RuntimeError("The Claude Code CLI did not answer in %ss."
                           % conf.get("timeout"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().replace("\n", " ")[:300]
        raise RuntimeError("The Claude Code CLI exited %s: %s"
                           % (r.returncode, err or "no output"))
    return r.stdout or ""


def _extract_json(text):
    """First top-level JSON object in the model's reply, or None."""
    if not text:
        return None
    # Strip a ```json fence if present, then find the outermost { ... }.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #

def build_content(item):
    """content.json dict for a customize job, or None (caller then uses the fallback).

    Never raises: any failure is logged and returns None so the deterministic path stays intact.
    The returned dict is NOT yet validated — render_resume.render() is the lint gate.
    """
    conf = llm_config()
    binary = find_claude(conf)
    if not binary:
        return None
    try:
        bank = load_bank()
    except Exception as e:
        print("llm_tailor: cannot load bullet bank (%s: %s)" % (type(e).__name__, e))
        return None
    try:
        system, user = build_prompt(item, bank, _jd_text(item), _summary_rules())
        raw = _call_cli(conf, binary, system, user)
    except Exception as e:
        # Not logged in, plan limit reached, CLI removed mid-run — all land here, and all
        # mean the same thing to the caller: use the template copy for this job.
        print("llm_tailor: the Claude Code call failed (%s: %s)" % (type(e).__name__, e))
        return None
    content = _extract_json(raw)
    if not isinstance(content, dict):
        print("llm_tailor: model did not return a JSON object")
        return None
    # Filename slug parts are required by the renderer; fill from the item if the model omitted them.
    content.setdefault("company", item.get("company", ""))
    content.setdefault("role", item.get("role", ""))
    return content


def check():
    """Locate the CLI and ask it one tiny question. Returns (ok, message).

    Two distinguishable failures, because the fixes differ: no binary means install Claude
    Code, a non-zero exit usually means log in or wait for the usage limit to reset.
    """
    conf = llm_config()
    binary = find_claude(conf)
    if not binary:
        return False, ("Claude Code was not found. Install it, then run 'claude login' and "
                       "sign in with your Claude plan.")
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Not fatal — _child_env strips it — but the user should know their environment
        # would otherwise have moved this onto metered billing.
        note = (" (ANTHROPIC_API_KEY is set in your environment; this app removes it before "
                "calling Claude Code, so tailoring stays on your plan.)")
    else:
        note = ""
    try:
        txt = _call_cli(conf, binary, "Reply with the single word: OK.", "Say OK.")
    except Exception as e:
        return False, ("Claude Code was found at %s but the call failed (%s). Run "
                       "'claude login' and check your plan's usage limit." % (binary, e))
    return True, "Connected through Claude Code at %s. It replied: %s%s" % (
        binary, (txt or "").strip()[:40], note)


if __name__ == "__main__":
    if "--check" in sys.argv:
        ok, msg = check()
        print(("OK  " if ok else "FAIL  ") + msg)
        sys.exit(0 if ok else 1)
    print("usage: python src/resume/llm_tailor.py --check")
