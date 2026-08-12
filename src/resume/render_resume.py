#!/usr/bin/env python3
"""render_resume.py — deterministic content→docx resume renderer.

Splits resume CONTENT from resume RENDERING. The agent (or anyone) writes a small
content.json; this script emits the .docx (and .pdf via LibreOffice) with the exact
visual structure of the base resumes. ALL formatting lives here, in code — none of
it has to be remembered in prose.

Usage
    python src/resume/render_resume.py content.json                 # → New/<Company>/<prefix><Company>_<Role>.docx + .pdf
    python src/resume/render_resume.py content.json --outdir DIR    # explicit output folder
    python src/resume/render_resume.py content.json --no-pdf        # skip LibreOffice (lint runs with --no-pdf-check)
    python src/resume/render_resume.py --extract some.docx          # reverse: reconstruct content.json from a rendered resume
    python src/resume/render_resume.py --extract some.docx -o out.json

content.json schema
    {
      "company": "Alkami",                 # filename slug parts (alnum only is kept)
      "role": "PlatformSolutionEngineer",
      "name": "Jane Doe",                  # optional, defaults below
      "contact": "...",                    # optional, defaults below
      "summary": "Site reliability engineer with ...",
      "skills_groups": [{"label": "Cloud and Platform", "items": ["Azure", ...]}, ...],
      "roles": [{"company": "Acme Corp", "location": "Houston, Texas",
                 "title": "Site Reliability Engineer", "dates": "01/2024–02/2026",
                 "bullet_ids": ["acme-sre-ansible-rundeck-toil", ...],
                 "bullet_overrides": {"id": "reworded text"}}, ...],
      "education": {"institution": "UNIVERSITY OF HOUSTON", "location": "Houston, Texas",
                    "degree": "Bachelor of Science, Computer Information Systems",
                    "dates": "05/2011–12/2013"},          # or a list of such
      "volunteer": {"organization": "RUTHERFORD B.H. YATES MUSEUM", "location": "Houston, Texas",
                    "title": "Volunteer — IT Support & Historic Restoration", "dates": "",
                    "bullet_ids": [...], "bullet_overrides": {}},   # optional
      "body_pt": 11,                       # optional, clamped to the 10–11 band (never below 10)
      "spacer_lines": 0                    # optional, 0–3 blank lines before EDUCATION (page-fill lever)
    }

Structural rules locked in (cannot be violated through the schema):
  * every bullet_id must exist in resume_template/bullets.json — unknown id = HARD ERROR.
    This is what structurally enforces "never invent experience": every rendered bullet
    traces to a bank entry (bullet_overrides rewords a bank entry, it can't add one).
  * body font clamped to 10–11pt; no code path emits a run below 10pt.
  * fixed section set: SUMMARY, TECHNICAL SKILLS, EXPERIENCE, EDUCATION, VOLUNTEER
    EXPERIENCE. Banned headers (ADDITIONAL EXPERIENCE / CORE COMPETENCIES / AREAS OF
    FOCUS / ...) are rejected wherever they appear in content strings.
  * minimum two bullets per role (volunteer included).
  * trailing roles (Sr Implementation Consultant + Implementation Analyst + HP intern)
    appear together or not at all.
  * summary opener must not adopt the target job title (same logic as lint R09).
  * page-fill levers exposed are exactly the CLAUDE.md priority ones: body_pt within
    the band, richer summary (content), spacer_lines 0–3. Nothing else.

Post-render gate: the output is linted with lint_resume.py in a temp folder and only
moved into place if the linter passes. A render that can't pass the linter is a bug —
this script exits non-zero and leaves nothing in the output folder.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder


import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

from pipelib import clean_company, clean_role, resume_prefix
from pipelib import slug as _pl_slug
from tailor_local import find_soffice
from lint_resume import (
    ALLOWED_IDENTITIES,
    BANNED_HEADERS,
    DATE_RANGE,
    TITLE_HEADS,
    TRAILING_ROLES,
)

HERE = _paths.ROOT
from pipelib import BULLETS as BANK_PATH   # bullet bank lives with the DATA, not the code
LINT_PATH = _paths.script('lint_resume.py')

# The resume HEADER (name + contact line) belongs to the user's base resume in
# resume_template/, not to this file. It used to be two module constants holding the first
# user's real name, home address, phone and personal email, so ANY other user's rendered
# resume carried his contact details. This app customizes an existing template; it does not
# author a header from scratch — so the header is read from the template.
_header_cache = None


def template_header():
    """(name, contact) — the first two lines of the CORE base resume in resume_template/.

    Cached per process. Everything here is best-effort and never raises: python-docx is an
    optional dependency, so a copy without it (or with an unreadable/empty template) falls
    back to profile.json identity.full_name for the name and an EMPTY contact — and an empty
    contact renders no contact paragraph at all, rather than someone else's details."""
    global _header_cache
    if _header_cache is None:
        name = contact = ""
        try:
            import docx as docx_lib
            from pipelib import TEMPLATE_DIR
            import scoring
            base = scoring._pick_core_base()[0]
            if base:
                path = os.path.join(TEMPLATE_DIR, os.path.basename(base))
                if path.lower().endswith(".docx") and os.path.exists(path):
                    lines = []
                    for p in docx_lib.Document(path).paragraphs:
                        t = (p.text or "").strip()
                        if not t:
                            continue
                        # Stop at the first section header — everything before it is the
                        # header block (name, then the contact line).
                        if t.upper() == t and len(t.split()) <= 4 and t.isupper():
                            break
                        lines.append(t)
                        if len(lines) >= 2:
                            break
                    if lines:
                        name = lines[0]
                        if len(lines) > 1:
                            contact = lines[1]
        except Exception:
            pass
        if not name:
            try:
                import profile_lib as pl
                name = str(((pl.load_profile(clean=True) or {}).get("identity") or {})
                           .get("full_name") or "").strip()
            except Exception:
                name = ""
        _header_cache = (name, contact)
    return _header_cache


def reset_template_header():
    """Drop the cache — for tests and after the user changes their template folder."""
    global _header_cache
    _header_cache = None

# ---- locked formatting constants (sizes in half-points) --------------------- #
MIN_BODY_PT = 10.0          # CLAUDE.md hard floor
MAX_BODY_PT = 11.0
DEFAULT_BODY_PT = 11.0
SZ_NAME = 38                # 19pt name
SZ_HEADER = 22              # 11pt section headers
SZ_SKILLS = 21              # 10.5pt skills lines
ACCENT = "1F3864"
GRAY = "777777"
CONTACT_GRAY = "404040"
MAX_SPACER_LINES = 3        # CLAUDE.md: at most 3 blank lines to fill page 1

SECTION_ORDER = ("SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "EDUCATION",
                 "VOLUNTEER EXPERIENCE")


class RenderError(Exception):
    """Content failed validation — nothing was written."""


class LintGateError(Exception):
    """The rendered file failed lint_resume.py — nothing was moved into place."""


# --------------------------------------------------------------------------- #
# bullet bank
# --------------------------------------------------------------------------- #

def load_bank(path=BANK_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {b["id"]: b for b in data["bullets"]}


# --------------------------------------------------------------------------- #
# validation (pre-render hard errors)
# --------------------------------------------------------------------------- #

def _norm_ws(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def _banned(text):
    return _norm_ws(text).upper().rstrip(":") in BANNED_HEADERS


def _clamp_body_pt(value):
    try:
        pt = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BODY_PT, None
    clamped = min(MAX_BODY_PT, max(MIN_BODY_PT, pt))
    note = None
    if clamped != pt:
        note = f"body_pt {pt:g} outside the {MIN_BODY_PT:g}-{MAX_BODY_PT:g} band, clamped to {clamped:g}"
    return clamped, note


def _summary_title_violation(summary, company, role):
    """Same bigram logic as lint R09, run before anything is written."""
    opener = re.split(r"(?<=[.;])\s", _norm_ws(summary))[0].lower()
    for ident in ALLOWED_IDENTITIES:
        if opener.startswith(ident):
            return None
    slug = _safe_slug(role)
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|\d+", slug)
    tokens = [w.lower() for w in words]
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]} {tokens[i + 1]}"
        if tokens[i + 1] in TITLE_HEADS and bigram in opener:
            if any(bigram in ident for ident in ALLOWED_IDENTITIES):
                continue
            return bigram
    return None


def _iter_role_like(content):
    for role in content.get("roles", []):
        yield role, role.get("title", "")
    vol = content.get("volunteer")
    if vol:
        yield vol, vol.get("title", "")


def validate_content(content, bank):
    """Every structural CLAUDE.md rule, checked before a byte is written."""
    problems, warnings = [], []

    for key in ("company", "role", "summary", "skills_groups", "roles"):
        if not content.get(key):
            problems.append(f'required field "{key}" is missing or empty')
    if problems:
        raise RenderError("; ".join(problems))

    # banned headers anywhere a header-ish string can come from content
    for group in content.get("skills_groups", []):
        if _banned(group.get("label", "")):
            problems.append(f'banned header text in skills label: "{group.get("label")}"')
    for _, title in _iter_role_like(content):
        if _banned(title):
            problems.append(f'banned header text in role title: "{title}"')
    edu = content.get("education")
    for entry in ([edu] if isinstance(edu, dict) else (edu or [])):
        for v in entry.values():
            if isinstance(v, str) and _banned(v):
                problems.append(f'banned header text in education: "{v}"')

    # bullets: bank membership (HARD), overrides subset, min two per role
    for role, title in _iter_role_like(content):
        ids = role.get("bullet_ids", [])
        overrides = role.get("bullet_overrides", {}) or {}
        for bid in ids:
            if bid not in bank:
                problems.append(f'unknown bullet_id "{bid}" (not in resume_template/bullets.json) '
                                f'under role "{title}" — bullets are picked, never invented')
        for bid in overrides:
            if bid not in ids:
                problems.append(f'bullet_overrides key "{bid}" is not in bullet_ids for role "{title}"')
        if len(ids) < 2:
            problems.append(f'role "{title}" has {len(ids)} bullet(s), minimum is 2')

    # experience roles need lintable date ranges (volunteer may be blank)
    for role in content.get("roles", []):
        if not DATE_RANGE.search(role.get("dates", "")):
            problems.append(f'role "{role.get("title")}" dates "{role.get("dates")}" must look like '
                            f'"01/2024–02/2026" (or end in "Present")')

    # trailing group: all three or none
    titles = " || ".join(t.lower() for _, t in _iter_role_like(content))
    present = [name for key, name in TRAILING_ROLES.items() if key in titles]
    if present and len(present) != len(TRAILING_ROLES):
        missing = [n for n in TRAILING_ROLES.values() if n not in present]
        problems.append(f"trailing roles move together: present={present} missing={missing}")

    # summary opener
    bigram = _summary_title_violation(content["summary"], content["company"], content["role"])
    if bigram:
        problems.append(f'summary opener adopts the target job title "{bigram}" — '
                        f"open with the owner's real identity instead")

    body_pt, note = _clamp_body_pt(content.get("body_pt", DEFAULT_BODY_PT))
    if note:
        warnings.append(note)
    spacers = int(content.get("spacer_lines", 0) or 0)
    if not 0 <= spacers <= MAX_SPACER_LINES:
        warnings.append(f"spacer_lines {spacers} clamped to 0-{MAX_SPACER_LINES}")
        spacers = min(MAX_SPACER_LINES, max(0, spacers))

    if problems:
        raise RenderError("content rejected:\n  - " + "\n  - ".join(problems))
    return body_pt, spacers, warnings


# --------------------------------------------------------------------------- #
# XML builders — every pPr/rPr matches the base resumes byte-for-byte
# --------------------------------------------------------------------------- #

FONTS = '<w:rFonts w:ascii="Calibri" w:cs="Calibri" w:eastAsia="Calibri" w:hAnsi="Calibri"/>'


def _rpr(sz, bold=False, italic=False, caps=False, color=None):
    parts = [FONTS]
    if bold:
        parts.append("<w:b/><w:bCs/>")
    if italic:
        parts.append("<w:i/><w:iCs/>")
    if caps:
        parts.append("<w:caps/>")
    if color:
        parts.append(f'<w:color w:val="{color}"/>')
    parts.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    return "<w:rPr>" + "".join(parts) + "</w:rPr>"


def _run(text, sz, **kwargs):
    rpr = _rpr(sz, **kwargs)
    if not text:
        return f"<w:r>{rpr}</w:r>"                      # empty run (e.g. blank dates cell)
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _p(ppr, runs):
    return f"<w:p><w:pPr>{ppr}</w:pPr>{''.join(runs)}</w:p>"


def _header_p(text):
    ppr = ('<w:pBdr><w:bottom w:val="single" w:color="%s" w:sz="6" w:space="1"/></w:pBdr>'
           '<w:spacing w:after="60" w:before="160"/><w:jc w:val="center"/>' % ACCENT)
    return _p(ppr, [_run(text, SZ_HEADER, bold=True, caps=True, color=ACCENT)])


def _bullet_p(text, sz):
    ppr = ('<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/>'
           '<w:numId w:val="2"/></w:numPr><w:spacing w:after="40"/>')
    return _p(ppr, [_run(text, sz)])


def _skills_p(label, items):
    ppr = '<w:spacing w:after="40"/>'
    return _p(ppr, [_run(label.rstrip(": ") + ": ", SZ_SKILLS, bold=True),
                    _run(", ".join(items), SZ_SKILLS)])


_TBLPR = ('<w:tblPr><w:tblW w:w="10800" w:type="dxa"/><w:tblBorders>'
          '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
          '</w:tblBorders><w:tblCellMar>'
          '<w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
          '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/>'
          '</w:tblCellMar></w:tblPr>'
          '<w:tblGrid><w:gridCol w:w="7560"/><w:gridCol w:w="3240"/></w:tblGrid>')


def _pair_table(left_p, right_p):
    tc = ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>%s</w:tc>')
    return (f"<w:tbl>{_TBLPR}<w:tr>{tc % (7560, left_p)}{tc % (3240, right_p)}</w:tr></w:tbl>")


def _company_table(company, location, sz):
    ppr_l = '<w:spacing w:before="140" w:after="0"/>'
    ppr_r = '<w:spacing w:before="140" w:after="0"/><w:jc w:val="right"/>'
    return _pair_table(_p(ppr_l, [_run(company, sz, bold=True)]),
                       _p(ppr_r, [_run(location, sz, color=GRAY)]))


def _title_table(title, dates, sz):
    ppr_l = '<w:spacing w:before="0" w:after="40"/>'
    ppr_r = '<w:spacing w:before="0" w:after="40"/><w:jc w:val="right"/>'
    return _pair_table(_p(ppr_l, [_run(title, sz, bold=True, italic=True)]),
                       _p(ppr_r, [_run(dates, sz, color=GRAY)]))


def build_document_xml(content, bank, body_pt, spacers):
    sz = int(round(body_pt * 2))
    assert sz >= int(MIN_BODY_PT * 2), "font floor breached — refusing to render"
    out = []

    hdr_name, hdr_contact = template_header()
    out.append(_p('<w:spacing w:after="0"/><w:jc w:val="center"/>',
                  [_run(content.get("name") or hdr_name, SZ_NAME, bold=True, color=ACCENT)]))
    # No contact anywhere = no contact paragraph. An empty centered line would just burn a
    # line of vertical space, and the wrong-but-present alternative is worse.
    contact = content.get("contact") or hdr_contact
    if contact:
        out.append(_p('<w:spacing w:after="60"/><w:jc w:val="center"/>',
                      [_run(contact, sz, color=CONTACT_GRAY)]))

    out.append(_header_p("Summary"))
    out.append(_p('<w:spacing w:after="60"/><w:jc w:val="both"/>',
                  [_run(_norm_ws(content["summary"]), sz)]))

    out.append(_header_p("Technical Skills"))
    for group in content["skills_groups"]:
        out.append(_skills_p(group["label"], group["items"]))

    out.append(_header_p("Experience"))
    for role in content["roles"]:
        out.append(_company_table(role["company"], role.get("location", ""), sz))
        out.append(_title_table(role["title"], role.get("dates", ""), sz))
        overrides = role.get("bullet_overrides", {}) or {}
        for bid in role["bullet_ids"]:
            out.append(_bullet_p(_norm_ws(overrides.get(bid) or bank[bid]["text"]), sz))

    for _ in range(spacers):
        out.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')

    edu = content.get("education")
    entries = [edu] if isinstance(edu, dict) else (edu or [])
    if entries:
        out.append(_header_p("Education"))
        for entry in entries:
            out.append(_company_table(entry["institution"], entry.get("location", ""), sz))
            out.append(_title_table(entry["degree"], entry.get("dates", ""), sz))

    vol = content.get("volunteer")
    if vol:
        out.append(_header_p("Volunteer Experience"))
        out.append(_company_table(vol["organization"], vol.get("location", ""), sz))
        out.append(_title_table(vol["title"], vol.get("dates", ""), sz))
        overrides = vol.get("bullet_overrides", {}) or {}
        for bid in vol["bullet_ids"]:
            out.append(_bullet_p(_norm_ws(overrides.get(bid) or bank[bid]["text"]), sz))

    out.append('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
               '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" '
               'w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>')

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>" + "".join(out) + "</w:body></w:document>")


# --------------------------------------------------------------------------- #
# docx packaging (fixed parts; document.xml is the only variable one)
# --------------------------------------------------------------------------- #

_STYLES_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               "<w:docDefaults><w:rPrDefault><w:rPr>" + FONTS +
               '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
               "<w:pPrDefault/></w:docDefaults>"
               '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
               '<w:name w:val="Normal"/><w:qFormat/></w:style>'
               '<w:style w:type="paragraph" w:styleId="ListParagraph">'
               '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/></w:style>'
               "</w:styles>")

_NUMBERING_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="hybridMultilevel"/>'
                  '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                  '<w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
                  '<w:pPr><w:ind w:left="360" w:hanging="240"/></w:pPr></w:lvl></w:abstractNum>'
                  '<w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num></w:numbering>')

_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                  '<Default Extension="xml" ContentType="application/xml"/>'
                  '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                  '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                  '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                  '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                  '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                  "</Types>")

_ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
              '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
              "</Relationships>")

_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
             "</Relationships>")

def _core_xml():
    # dc:creator is embedded document metadata a recruiter can read in File > Properties,
    # so it followed the header off the hardcoded constant. Built per render, not at import,
    # because template_header() needs the resolved template folder.
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Resume</dc:title><dc:creator>" + escape(template_header()[0])
            + "</dc:creator></cp:coreProperties>")

_APP_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            "<Application>render_resume.py</Application></Properties>")


def write_docx(document_xml, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES_XML)
        zf.writestr("word/numbering.xml", _NUMBERING_XML)
        zf.writestr("docProps/core.xml", _core_xml())
        zf.writestr("docProps/app.xml", _APP_XML)


# --------------------------------------------------------------------------- #
# pdf + lint gate
# --------------------------------------------------------------------------- #

def _convert_pdf(soffice, docx_path):
    """LibreOffice headless docx→pdf, same call tailor_local.py makes."""
    outdir = os.path.dirname(docx_path)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                    "--outdir", outdir, docx_path],
                   capture_output=True, timeout=150, check=False)
    pdf = os.path.splitext(docx_path)[0] + ".pdf"
    return pdf if os.path.exists(pdf) else None


def _lint(docx_path, no_pdf):
    """Run lint_resume.py on the output; returns (exit_code, combined_output)."""
    cmd = [sys.executable, LINT_PATH, docx_path]
    if no_pdf:
        cmd.append("--no-pdf-check")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _safe_slug(text):
    return "".join(ch for ch in (text or "") if ch.isalnum()) or "X"


def _co_slug(content):
    # Short, recruiter-friendly filename parts — same shortening tracker_id uses,
    # so the render output matches the pipeline's cid (no rename needed downstream).
    return _pl_slug(clean_company(content["company"])) or "X"


def output_name(content):
    role = _pl_slug(clean_role(content["role"])) or "X"
    return f"{resume_prefix()}{_co_slug(content)}_{role}.docx"


def render(content, bank=None, outdir=None, no_pdf=False):
    """Validate → build → write to temp → pdf → lint gate → move into place.

    Returns (docx_path, pdf_path_or_None). Raises RenderError / LintGateError.
    """
    bank = bank if bank is not None else load_bank()
    body_pt, spacers, warnings = validate_content(content, bank)
    for note in warnings:
        print(f"render_resume: WARN {note}", file=sys.stderr)

    name = output_name(content)
    outdir = outdir or os.path.join(HERE, "New", _co_slug(content))

    tmpdir = tempfile.mkdtemp(prefix="render_resume_")
    try:
        tmp_docx = os.path.join(tmpdir, name)
        write_docx(build_document_xml(content, bank, body_pt, spacers), tmp_docx)

        tmp_pdf = None
        if not no_pdf:
            soffice = find_soffice()
            if not soffice:
                raise RenderError("LibreOffice (soffice) not found — install it or pass --no-pdf")
            tmp_pdf = _convert_pdf(soffice, tmp_docx)
            if not tmp_pdf:
                raise RenderError("soffice did not produce a PDF")

        code, output = _lint(tmp_docx, no_pdf=tmp_pdf is None)
        if code != 0:
            raise LintGateError(f"lint gate failed — output NOT written to {outdir}\n{output}")

        os.makedirs(outdir, exist_ok=True)
        final_docx = os.path.join(outdir, name)
        shutil.move(tmp_docx, final_docx)
        final_pdf = None
        if tmp_pdf:
            final_pdf = os.path.splitext(final_docx)[0] + ".pdf"
            shutil.move(tmp_pdf, final_pdf)
        # paranoia: the moved file must still be a readable zip (mount-truncation guard)
        with zipfile.ZipFile(final_docx) as zf:
            if zf.testzip() is not None:
                raise LintGateError(f"written docx failed zip verification: {final_docx}")
        return final_docx, final_pdf
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# extraction — rendered/base docx → content.json (the migration path)
# --------------------------------------------------------------------------- #

def extract_content(docx_path, bank=None):
    """Reconstruct a content.json from an existing tailored resume.

    Bullets are matched back to the bank: exact text → bullet_id; near match →
    bullet_id + bullet_overrides entry; no plausible match → RenderError, because a
    bullet that traces to no bank entry is exactly what this system forbids.
    """
    import docx as docx_lib
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    bank = bank if bank is not None else load_bank()
    by_text = {_norm_ws(b["text"]): bid for bid, b in bank.items()}
    texts = list(by_text)

    def match_bullet(text):
        t = _norm_ws(text)
        if t in by_text:
            return by_text[t], None
        close = difflib.get_close_matches(t, texts, n=1, cutoff=0.5)
        if close:
            return by_text[close[0]], t
        raise RenderError(f"bullet not traceable to any bank entry: {t[:80]!r}")

    document = docx_lib.Document(docx_path)
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    stem = os.path.splitext(os.path.basename(docx_path))[0]
    parts = stem.split("_")
    content = {
        "company": parts[3] if len(parts) > 3 else "Company",
        "role": parts[4] if len(parts) > 4 else "Role",
        "name": template_header()[0], "contact": template_header()[1],
        "summary": "", "skills_groups": [], "roles": [],
        "education": [], "volunteer": None, "spacer_lines": 0,
    }
    section, pending, current, preamble, spacers = None, None, None, [], 0

    for child in document.element.body.iterchildren():
        if child.tag == W + "tbl":
            table = Table(child, document)
            row = table.rows[0].cells
            left, right = row[0].text.strip(), row[1].text.strip() if len(row) > 1 else ""
            if pending is None:
                pending = (left, right)
            else:
                if section == "EDUCATION":
                    content["education"].append({"institution": pending[0], "location": pending[1],
                                                 "degree": left, "dates": right})
                elif section == "VOLUNTEER EXPERIENCE":
                    content["volunteer"] = current = {"organization": pending[0], "location": pending[1],
                                                      "title": left, "dates": right,
                                                      "bullet_ids": [], "bullet_overrides": {}}
                else:
                    current = {"company": pending[0], "location": pending[1],
                               "title": left, "dates": right,
                               "bullet_ids": [], "bullet_overrides": {}}
                    content["roles"].append(current)
                pending = None
            continue
        if child.tag != W + "p":
            continue
        para = Paragraph(child, document)
        text = para.text.strip()
        upper = text.upper()
        if upper in SECTION_ORDER:
            if upper == "EDUCATION":
                content["spacer_lines"] = spacers
            section, pending, current = upper, None, None
            continue
        if not text:
            if section == "EXPERIENCE":
                spacers += 1
            continue
        is_bullet = ("List" in ((para.style.name if para.style is not None else "") or "")
                     or child.find(f"{W}pPr/{W}numPr") is not None)
        if is_bullet and current is not None:
            bid, override = match_bullet(text)
            current["bullet_ids"].append(bid)
            if override:
                current["bullet_overrides"][bid] = override
        elif section == "SUMMARY":
            content["summary"] = _norm_ws(content["summary"] + " " + text)
        elif section == "TECHNICAL SKILLS":
            label = "".join(r.text for r in para.runs if r.bold).rstrip(": ")
            items = "".join(r.text for r in para.runs if not r.bold).strip()
            content["skills_groups"].append(
                {"label": label, "items": [i.strip() for i in items.split(",") if i.strip()]})
        elif section is None:
            preamble.append(text)

    if preamble:
        content["name"] = preamble[0]
        if len(preamble) > 1:
            content["contact"] = preamble[1]
    if len(content["education"]) == 1:
        content["education"] = content["education"][0]
    return content


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a tailored resume (.docx + .pdf) from a small content.json; "
                    "formatting rules are locked in code and lint-gated.")
    parser.add_argument("input", help="content.json to render, or a .docx with --extract")
    parser.add_argument("--outdir", help="output folder (default New/<Company>/)")
    parser.add_argument("--no-pdf", action="store_true", help="skip the LibreOffice PDF step")
    parser.add_argument("--extract", action="store_true",
                        help="reverse mode: reconstruct content.json from a rendered .docx")
    parser.add_argument("-o", "--output", help="(--extract) write JSON here instead of stdout")
    parser.add_argument("--bank", default=BANK_PATH, help="bullet bank path (tests)")
    args = parser.parse_args(argv)

    try:
        bank = load_bank(args.bank)
        if args.extract:
            content = extract_content(args.input, bank)
            blob = json.dumps(content, indent=2, ensure_ascii=False)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(blob + "\n")
                print(f"render_resume: extracted → {args.output}")
            else:
                print(blob)
            return 0
        with open(args.input, encoding="utf-8") as f:
            content = json.load(f)
        docx_path, pdf_path = render(content, bank, outdir=args.outdir, no_pdf=args.no_pdf)
        print(f"render_resume: PASS lint gate\n  {docx_path}" + (f"\n  {pdf_path}" if pdf_path else ""))
        return 0
    except (RenderError, LintGateError) as exc:
        print(f"render_resume: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"render_resume: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
