#!/usr/bin/env python3
"""
lint_resume.py — deterministic checker for the resume formatting rules in CLAUDE.md.

Every rule that is currently enforced only by "Claude remembering it" is encoded here
as a pass/fail check that costs zero tokens to run.

Usage
    python src/resume/lint_resume.py <file.docx> [more.docx ...]
    python src/resume/lint_resume.py --dir New/Acme
    python src/resume/lint_resume.py --all                 # sweep New/ and Applied/
    python src/resume/lint_resume.py --all --json          # machine-readable
    python src/resume/lint_resume.py --all --errors-only   # suppress warnings

Exit code: 0 = no ERRORs, 1 = at least one ERROR, 2 = could not read a file.

Rules checked
    R01 font-min            no body text below 10.0pt
    R02 page-count          resume must not exceed 2 pages
    R03 page2-min-lines     if page 2 exists it must carry >= 5 non-empty lines
    R04 bullets-per-role    every EXPERIENCE/VOLUNTEER role needs >= 2 bullets
    R05 orphan-title        a role heading with zero bullets under it
    R06 banned-header       ADDITIONAL EXPERIENCE / CORE COMPETENCIES / AREAS OF FOCUS
    R07 required-section    TECHNICAL SKILLS and SUMMARY must be present
    R08 notes-leakage       TODO / FIXME / "Note:" / [placeholder] left in the document
    R09 summary-title       summary opener must not adopt the target job title
    R10 docx-pdf-pair       each tailored resume needs both .docx and .pdf
    R11 skills-format       no "::" ; bold covers the group label only
    R12 trailing-group      Sr Implementation Consultant + Implementation Analyst + HP
                            intern appear together or not at all
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder


import argparse
import glob
import json
import os
import re
import subprocess
import sys

try:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover
    sys.stderr.write("lint_resume.py requires python-docx (pip install python-docx)\n")
    raise

MIN_FONT_PT = 10.0
MAX_PAGES = 2
MIN_PAGE2_LINES = 5
MIN_BULLETS_PER_ROLE = 2

BANNED_HEADERS = {
    "ADDITIONAL EXPERIENCE",
    "CORE COMPETENCIES",
    "AREAS OF FOCUS",
    "KEY COMPETENCIES",
    "AREAS OF EXPERTISE",
}
REQUIRED_SECTIONS = {"TECHNICAL SKILLS", "SUMMARY"}

# Openers that are the owner's real professional identity — never flagged by R09.
ALLOWED_IDENTITIES = [
    "site reliability engineer",
    "operations and data professional",
    "cloud operations engineer",
    "operations engineer",
]

TITLE_HEADS = {
    "engineer", "engineering", "analyst", "consultant", "manager", "specialist",
    "architect", "administrator", "developer", "lead", "director", "scientist",
    "technician", "designer", "strategist", "coordinator",
}

NOTE_PATTERNS = [
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"\bFIXME\b"), "FIXME"),
    (re.compile(r"\bXXX\b"), "XXX"),
    (re.compile(r"^\s*Note\s*:", re.I), "Note: line"),
    (re.compile(r"\bflagged skills?\b", re.I), "flagged-skills note"),
    (re.compile(r"\[(?:company|role|title|insert|placeholder|tbd)[^\]]*\]", re.I), "placeholder"),
    (re.compile(r"\bLorem ipsum\b", re.I), "lorem ipsum"),
]

DATE_RANGE = re.compile(r"\d{1,2}/\d{4}\s*[–\-—]\s*(?:\d{1,2}/\d{4}|present)", re.I)

TRAILING_ROLES = {
    "senior implementation consultant": "Senior Implementation Consultant",
    "implementation analyst": "Implementation Analyst",
    "performance engineering intern": "HP Performance Engineering Intern",
}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --------------------------------------------------------------------------- #
# docx helpers
# --------------------------------------------------------------------------- #

def iter_block_items(document):
    """Yield Paragraph and Table objects in true document body order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == W + "p":
            yield Paragraph(child, document)
        elif child.tag == W + "tbl":
            yield Table(child, document)


def doc_default_pt(document):
    """Default run size in points from docDefaults, or None."""
    try:
        el = document.styles.element
        sz = el.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
        if sz is not None:
            return int(sz.get(W + "val")) / 2.0
    except Exception:
        pass
    return None


def style_pt(paragraph):
    """Effective size from the paragraph's style chain, or None."""
    try:
        style = paragraph.style
        seen = 0
        while style is not None and seen < 10:
            if style.font is not None and style.font.size is not None:
                return style.font.size.pt
            style = style.base_style
            seen += 1
    except Exception:
        pass
    return None


def run_sizes(paragraph, default_pt):
    """[(text, pt)] for every non-empty run, with inheritance resolved."""
    out = []
    for run in paragraph.runs:
        if not run.text.strip():
            continue
        pt = run.font.size.pt if run.font.size is not None else None
        if pt is None:
            pt = style_pt(paragraph)
        if pt is None:
            pt = default_pt
        out.append((run.text, pt))
    return out


BULLET_GLYPHS = ("•", "▪", "‣", "●", "·", "-", "–", "—", "*")


def is_bullet(paragraph):
    """List style, real numbering, or a literal bullet glyph typed into the text.

    Some generated resumes prefix plain paragraphs with "• " instead of using a
    list style, so glyph detection is required or every role reads as empty.
    """
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    if "List" in style:
        return True
    if paragraph._p.find(f"{W}pPr/{W}numPr") is not None:
        return True
    text = paragraph.text.lstrip()
    return bool(text) and text.startswith(BULLET_GLYPHS) and len(text) > 3


def is_section_header(paragraph):
    """Short, standalone, bold-or-uppercase paragraph = a section header."""
    text = paragraph.text.strip()
    if not text or is_bullet(paragraph) or len(text) > 45:
        return False
    if len(text.split()) > 4:
        return False
    if text.endswith((".", ",", ";", ":")) and not text.isupper():
        return False
    all_bold = bool(paragraph.runs) and all(r.bold for r in paragraph.runs if r.text.strip())
    return all_bold or text.isupper()


def table_pair(table):
    """(left_text, right_text) for a 1-row 2-col heading table, else None."""
    try:
        row = table.rows[0]
        cells = row.cells
        if len(cells) < 2:
            return None
        return cells[0].text.strip(), cells[1].text.strip()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# PDF helpers
# --------------------------------------------------------------------------- #

def pdf_pages(pdf_path):
    """(page_count, [non_empty_line_count_per_page]) via pdftotext, or (None, [])."""
    try:
        raw = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, timeout=60,
        )
        if raw.returncode != 0:
            return None, []
        text = raw.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None, []
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return len(pages), [len([l for l in p.splitlines() if l.strip()]) for p in pages]


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #

class Report:
    def __init__(self, path):
        self.path = path
        self.findings = []

    def add(self, level, rule, message, where=""):
        self.findings.append(
            {"level": level, "rule": rule, "message": message, "where": where}
        )

    def error(self, rule, message, where=""):
        self.add("ERROR", rule, message, where)

    def warn(self, rule, message, where=""):
        self.add("WARN", rule, message, where)

    @property
    def errors(self):
        return [f for f in self.findings if f["level"] == "ERROR"]

    @property
    def warnings(self):
        return [f for f in self.findings if f["level"] == "WARN"]


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

def role_tokens_from_filename(path):
    """Words of the role slug in <prefix><Company>_<Role>.docx."""
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split("_")
    if len(parts) < 5:
        return []
    slug = parts[4]
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|\d+", slug)
    return [w.lower() for w in words]


def check_summary_title(report, sections, path):
    """R09 — the summary opener must not adopt the target job title."""
    summary = sections.get("SUMMARY", "")
    if not summary:
        return
    opener = re.split(r"(?<=[.;])\s", summary.strip())[0].lower()
    for ident in ALLOWED_IDENTITIES:
        if opener.startswith(ident):
            return
    tokens = role_tokens_from_filename(path)
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]} {tokens[i+1]}"
        if tokens[i + 1] in TITLE_HEADS and bigram in opener:
            if any(bigram in ident for ident in ALLOWED_IDENTITIES):
                continue
            report.error(
                "R09",
                f'summary opener adopts the target job title "{bigram}"',
                opener[:80],
            )
            return


def check_skills_format(report, skills_paragraphs):
    """R11 — no "::"; bold must stop at the group label."""
    for para in skills_paragraphs:
        text = para.text
        if "::" in text:
            report.error("R11", 'double colon in skills line (use " - " or ": ")', text[:70])
        bolded = "".join(r.text for r in para.runs if r.bold)
        if not bolded:
            continue
        candidates = [i for i in (text.find(":"), text.find(" - ")) if i != -1]
        if not candidates:
            continue
        label_end = min(candidates)
        overrun = len(bolded.rstrip(": ")) - len(text[:label_end].rstrip(": "))
        if overrun > 2:
            report.warn(
                "R11",
                "bold extends past the group label into the skill list",
                bolded[:70],
            )


def lint_docx(path, report):
    document = docx.Document(path)
    default_pt = doc_default_pt(document)

    section = None
    sections = {}
    skills_paragraphs = []
    seen_headers = set()

    current_role = None          # (label, bullet_count)
    roles = []
    pending_company = None
    body_text = []

    def close_role():
        if current_role is not None:
            roles.append((current_role[0], current_role[1], current_role[2]))

    for block in iter_block_items(document):
        if isinstance(block, Table):
            pair = table_pair(block)
            if not pair:
                continue
            left, right = pair
            body_text.append(f"{left} {right}")
            if DATE_RANGE.search(right) or (section == "EDUCATION" and right):
                # role / degree heading row
                close_role()
                label = f"{pending_company + ' — ' if pending_company else ''}{left}"
                current_role = [label, 0, section]
                pending_company = None
            else:
                pending_company = left
            continue

        text = block.text.strip()
        if text:
            body_text.append(text)

        # font sizes
        for run_text, pt in run_sizes(block, default_pt):
            if pt is None:
                continue
            if pt < MIN_FONT_PT and len(run_text.strip()) > 1:
                report.error(
                    "R01",
                    f"font {pt:g}pt is below the {MIN_FONT_PT:g}pt floor",
                    run_text.strip()[:60],
                )

        if not text:
            continue

        if is_bullet(block):
            if current_role is not None:
                current_role[1] += 1
            if section == "TECHNICAL SKILLS":
                skills_paragraphs.append(block)
            continue

        if is_section_header(block):
            close_role()
            current_role = None
            pending_company = None
            section = text.upper()
            seen_headers.add(section)
            sections.setdefault(section, "")
            continue

        if section:
            sections[section] = (sections[section] + " " + text).strip()
        if section == "TECHNICAL SKILLS":
            skills_paragraphs.append(block)

    close_role()

    # R06 banned headers
    for header in sorted(seen_headers & BANNED_HEADERS):
        report.error("R06", f'banned section header "{header}"')

    # R07 required sections
    for required in sorted(REQUIRED_SECTIONS - seen_headers):
        report.error("R07", f'required section "{required}" is missing')

    # R04 / R05 bullets per role
    for label, count, sec in roles:
        if sec in (None, "EDUCATION"):
            continue
        if count == 0:
            report.error("R05", f'role "{label}" has no bullets', sec or "")
        elif count < MIN_BULLETS_PER_ROLE:
            report.error(
                "R04",
                f'role "{label}" has {count} bullet, needs >= {MIN_BULLETS_PER_ROLE}',
                sec or "",
            )

    # R08 notes leakage
    for line in body_text:
        for pattern, name in NOTE_PATTERNS:
            if pattern.search(line):
                report.error("R08", f"{name} left in the document", line[:70])
                break

    # R12 trailing role group
    joined = " ".join(body_text).lower()
    present = [name for key, name in TRAILING_ROLES.items() if key in joined]
    if present and len(present) != len(TRAILING_ROLES):
        missing = [n for n in TRAILING_ROLES.values() if n not in present]
        report.error(
            "R12",
            "trailing roles must appear together or not at all; "
            f"present={present} missing={missing}",
        )

    check_summary_title(report, sections, path)
    check_skills_format(report, skills_paragraphs)


def lint_pair(path, report, check_pdf=True):
    """R02 / R03 / R10 — the PDF-side checks."""
    pdf = os.path.splitext(path)[0] + ".pdf"
    if not os.path.exists(pdf):
        if check_pdf:
            report.error("R10", "no matching .pdf beside the .docx (ATS uploads need both)")
        return
    pages, per_page = pdf_pages(pdf)
    if pages is None:
        report.warn("R02", "pdftotext unavailable — page checks skipped")
        return
    if pages > MAX_PAGES:
        report.error("R02", f"{pages} pages, maximum is {MAX_PAGES}")
    if pages == 2 and per_page[1] < MIN_PAGE2_LINES:
        report.error(
            "R03",
            f"page 2 has only {per_page[1]} lines, minimum is {MIN_PAGE2_LINES}",
        )


def lint_file(path, check_pdf=True):
    report = Report(path)
    try:
        lint_docx(path, report)
    except Exception as exc:  # unreadable / corrupt docx
        report.error("R00", f"could not read document: {exc.__class__.__name__}: {exc}")
        return report
    lint_pair(path, report, check_pdf=check_pdf)
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def collect(args):
    files = []
    for target in args.files:
        if os.path.isdir(target):
            files += glob.glob(os.path.join(target, "**", "*.docx"), recursive=True)
        else:
            files.append(target)
    if args.dir:
        files += glob.glob(os.path.join(args.dir, "**", "*.docx"), recursive=True)
    if args.all:
        for root in ("New", "Applied"):
            files += glob.glob(os.path.join(root, "**", "*.docx"), recursive=True)
    seen, out = set(), []
    for f in files:
        base = os.path.basename(f)
        if base.startswith("~$"):
            continue
        if args.resumes_only and "Resume" not in base:
            continue
        key = os.path.abspath(f)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return sorted(out)


def main():
    parser = argparse.ArgumentParser(
        description="Check tailored resumes against the CLAUDE.md formatting rules."
    )
    parser.add_argument("files", nargs="*", help=".docx files or folders")
    parser.add_argument("--dir", help="lint every .docx under this folder")
    parser.add_argument("--all", action="store_true", help="sweep New/ and Applied/")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--errors-only", action="store_true", help="hide warnings")
    parser.add_argument("--no-pdf-check", action="store_true", help="skip the .pdf pair rule")
    parser.add_argument(
        "--resumes-only",
        action="store_true",
        default=True,
        help="skip cover letters (default on)",
    )
    parser.add_argument(
        "--include-letters",
        dest="resumes_only",
        action="store_false",
        help="also lint cover letters",
    )
    parser.add_argument("--quiet", action="store_true", help="only list files with findings")
    args = parser.parse_args()

    targets = collect(args)
    if not targets:
        sys.stderr.write("lint_resume: no .docx files matched\n")
        return 2

    reports = [lint_file(f, check_pdf=not args.no_pdf_check) for f in targets]
    total_errors = sum(len(r.errors) for r in reports)
    total_warnings = sum(len(r.warnings) for r in reports)

    if args.json:
        print(json.dumps(
            {
                "checked": len(reports),
                "errors": total_errors,
                "warnings": total_warnings,
                "files": [
                    {"path": r.path, "findings": r.findings}
                    for r in reports
                    if r.findings or not args.quiet
                ],
            },
            indent=2,
        ))
    else:
        for report in reports:
            shown = report.errors if args.errors_only else report.findings
            if not shown:
                if not args.quiet:
                    print(f"PASS  {report.path}")
                continue
            print(f"FAIL  {report.path}")
            for finding in shown:
                where = f"  <- {finding['where']}" if finding["where"] else ""
                print(f"      [{finding['level']}] {finding['rule']} {finding['message']}{where}")
        print(
            f"\n{len(reports)} checked · {total_errors} error(s) · {total_warnings} warning(s)"
        )

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
