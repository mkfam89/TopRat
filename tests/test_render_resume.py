"""render_resume.py — the rules are locked in code and cannot be violated.

Offline: no LibreOffice (the pdf step is monkeypatched), no network. The only
subprocess spawned is lint_resume.py via sys.executable, which is local and offline.
Read-only over the repo; all output goes to tmp_path.
"""
import json
import os
import re

import pytest

import render_resume as rr

# Every test here renders from resume_template/bullets.json — the user's own
# bullet bank, which a fresh clone does not have. Marked at module scope so CI
# and new clones can run `pytest -m "not needs_userdata"` and get a green board
# instead of 19 errors that look like a broken build but are missing data.
pytestmark = pytest.mark.needs_userdata

ALKAMI_DOCX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "New", "Alkami", "Khoa_Pham_Resume_Alkami_PlatformSolutionEngineerMANTL.docx")


@pytest.fixture(scope="module")
def bank():
    return rr.load_bank()


def make_content(**kw):
    content = {
        "company": "Globex",
        "role": "PlatformEngineer",
        "summary": "Site reliability engineer with 7 years of hands-on experience.",
        "skills_groups": [{"label": "Cloud and Platform", "items": ["Azure", "Linux"]}],
        "roles": [{
            "company": "PROS", "location": "Houston, Texas",
            "title": "Site Reliability Engineer", "dates": "01/2024–02/2026",
            "bullet_ids": ["pros-sre-ansible-rundeck-toil", "pros-sre-critsit-pagerduty-sla"],
        }],
        "education": {"institution": "UNIVERSITY OF HOUSTON", "location": "Houston, Texas",
                      "degree": "Bachelor of Science, Computer Information Systems",
                      "dates": "05/2011–12/2013"},
    }
    content.update(kw)
    return content


def build_xml(content, bank):
    body_pt, spacers, _ = rr.validate_content(content, bank)
    return rr.build_document_xml(content, bank, body_pt, spacers)


# --------------------------------------------------------------------------- #
# bank enforcement — bullets are picked, never invented
# --------------------------------------------------------------------------- #

def test_bad_bullet_id_raises(bank):
    content = make_content()
    content["roles"][0]["bullet_ids"] = ["pros-sre-ansible-rundeck-toil", "made-up-bullet"]
    with pytest.raises(rr.RenderError, match="made-up-bullet"):
        rr.validate_content(content, bank)


def test_override_key_must_be_a_selected_bullet(bank):
    content = make_content()
    content["roles"][0]["bullet_overrides"] = {"pros-cloudops-bash-python-30k": "reworded"}
    with pytest.raises(rr.RenderError, match="bullet_overrides"):
        rr.validate_content(content, bank)


def test_override_rewords_bank_entry(bank):
    content = make_content()
    content["roles"][0]["bullet_overrides"] = {
        "pros-sre-ansible-rundeck-toil": "Automated ops workflows with Ansible and Rundeck."}
    xml = build_xml(content, bank)
    assert "Automated ops workflows with Ansible and Rundeck." in xml


# --------------------------------------------------------------------------- #
# structural CLAUDE.md rules
# --------------------------------------------------------------------------- #

def test_min_two_bullets_per_role(bank):
    content = make_content()
    content["roles"][0]["bullet_ids"] = ["pros-sre-ansible-rundeck-toil"]
    with pytest.raises(rr.RenderError, match="minimum is 2"):
        rr.validate_content(content, bank)


def test_trailing_group_split_rejected(bank):
    content = make_content()
    content["roles"].append({
        "company": "PROS", "location": "Houston, Texas",
        "title": "Implementation Analyst", "dates": "02/2014–01/2017",
        "bullet_ids": ["pros-ia-setup-test-environments", "pros-ia-data-quality-50pct"],
    })
    with pytest.raises(rr.RenderError, match="trailing roles move together"):
        rr.validate_content(content, bank)


def test_banned_header_rejected_in_skills_label(bank):
    content = make_content(skills_groups=[{"label": "Core Competencies", "items": ["X"]}])
    with pytest.raises(rr.RenderError, match="banned header"):
        rr.validate_content(content, bank)


def test_banned_header_rejected_in_role_title(bank):
    content = make_content()
    content["roles"][0]["title"] = "ADDITIONAL EXPERIENCE"
    with pytest.raises(rr.RenderError, match="banned header"):
        rr.validate_content(content, bank)


def test_only_whitelisted_headers_emitted(bank):
    xml = build_xml(make_content(), bank)
    headers = re.findall(r"<w:caps/>.*?<w:t[^>]*>([^<]*)</w:t>", xml)
    assert set(h.upper() for h in headers) <= set(rr.SECTION_ORDER)
    for banned in rr.BANNED_HEADERS:
        assert banned not in xml.upper()


def test_summary_title_guard(bank):
    content = make_content(
        summary="Platform engineer passionate about cloud infrastructure.")
    with pytest.raises(rr.RenderError, match="adopts the target job title"):
        rr.validate_content(content, bank)


# --------------------------------------------------------------------------- #
# font floor — no code path emits below 10pt
# --------------------------------------------------------------------------- #

def test_font_floor_holds_under_hostile_body_pt(bank):
    content = make_content(body_pt=6)          # request far below the floor
    body_pt, _, warnings = rr.validate_content(content, bank)
    assert body_pt == rr.MIN_BODY_PT
    assert any("clamped" in w for w in warnings)
    xml = rr.build_document_xml(content, bank, body_pt, 0)
    sizes = [int(v) for v in re.findall(r'<w:sz w:val="(\d+)"/>', xml)]
    assert min(sizes) >= int(rr.MIN_BODY_PT * 2)


def test_body_pt_clamped_high_too(bank):
    body_pt, _, _ = rr.validate_content(make_content(body_pt=14), bank)
    assert body_pt == rr.MAX_BODY_PT


def test_default_render_has_no_small_fonts(bank):
    xml = build_xml(make_content(), bank)
    sizes = [int(v) for v in re.findall(r'<w:sz w:val="(\d+)"/>', xml)]
    assert min(sizes) >= 20 and max(sizes) == rr.SZ_NAME


def test_spacer_lines_capped_at_three(bank):
    content = make_content(spacer_lines=9)
    _, spacers, warnings = rr.validate_content(content, bank)
    assert spacers == rr.MAX_SPACER_LINES
    assert any("spacer_lines" in w for w in warnings)


# --------------------------------------------------------------------------- #
# the lint gate
# --------------------------------------------------------------------------- #

def test_lint_gate_fires(bank, tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_lint", lambda path, no_pdf: (1, "forced lint failure"))
    outdir = tmp_path / "out"
    with pytest.raises(rr.LintGateError, match="lint gate failed"):
        rr.render(make_content(), bank, outdir=str(outdir), no_pdf=True)
    assert not outdir.exists()                 # nothing moved into place


def test_render_passes_real_linter(bank, tmp_path):
    docx_path, pdf_path = rr.render(make_content(), bank,
                                    outdir=str(tmp_path), no_pdf=True)
    assert os.path.exists(docx_path) and pdf_path is None
    assert os.path.basename(docx_path) == "Khoa_Pham_Resume_Globex_PlatformEngineer.docx"


def test_pdf_step_monkeypatched(bank, tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "find_soffice", lambda: "/fake/soffice")

    def fake_convert(soffice, docx_path):
        pdf = os.path.splitext(docx_path)[0] + ".pdf"
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        return pdf

    monkeypatch.setattr(rr, "_convert_pdf", fake_convert)
    docx_path, pdf_path = rr.render(make_content(), bank, outdir=str(tmp_path))
    assert os.path.exists(docx_path) and os.path.exists(pdf_path)


def test_missing_soffice_is_an_error(bank, tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "find_soffice", lambda: None)
    with pytest.raises(rr.RenderError, match="soffice"):
        rr.render(make_content(), bank, outdir=str(tmp_path))


# --------------------------------------------------------------------------- #
# round-trip + Alkami fidelity
# --------------------------------------------------------------------------- #

def test_extract_roundtrip(bank, tmp_path):
    content = make_content()
    docx_path, _ = rr.render(content, bank, outdir=str(tmp_path), no_pdf=True)
    back = rr.extract_content(docx_path, bank)
    assert back["summary"] == content["summary"]
    assert back["roles"][0]["bullet_ids"] == content["roles"][0]["bullet_ids"]
    assert back["roles"][0]["bullet_overrides"] == {}
    assert back["skills_groups"] == content["skills_groups"]
    assert back["education"]["institution"] == "UNIVERSITY OF HOUSTON"


def _body_stream(path):
    """Normalized (element, style, alignment, spacing, runs) stream of a docx body."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def psig(p):
        pf = p.paragraph_format
        style = (p.style.name if p.style is not None else None) or "Normal"
        return ("P", None if style == "Normal" else style, str(pf.alignment),
                pf.space_before.pt if pf.space_before else 0,
                pf.space_after.pt if pf.space_after else 0,
                p._p.find(f"{W}pPr/{W}numPr") is not None,
                tuple((r.text, bool(r.bold), bool(r.italic), bool(r.font.all_caps),
                       str(r.font.color.rgb) if r.font.color and r.font.color.rgb else None,
                       r.font.size.pt if r.font.size else None) for r in p.runs))

    document = docx.Document(path)
    section = document.sections[0]
    out = [("SECT", section.page_width, section.page_height, section.left_margin,
            section.right_margin, section.top_margin, section.bottom_margin)]
    for child in document.element.body.iterchildren():
        if child.tag == W + "p":
            out.append(psig(Paragraph(child, document)))
        elif child.tag == W + "tbl":
            table = Table(child, document)
            out.append(("TBL", len(table.rows), len(table.columns),
                        tuple((cell.width, tuple(psig(p) for p in cell.paragraphs))
                              for row in table.rows for cell in row.cells)))
    return out


def test_fidelity_against_alkami(bank, tmp_path):
    """Acceptance test: fixture content renders structurally identical to the
    known-clean Alkami resume the fixture was extracted from."""
    if not os.path.exists(ALKAMI_DOCX):
        pytest.skip("Alkami reference resume moved/archived")
    from conftest import load_fixture
    content = load_fixture("content_alkami.json")
    docx_path, _ = rr.render(content, bank, outdir=str(tmp_path), no_pdf=True)
    assert _body_stream(docx_path) == _body_stream(ALKAMI_DOCX)
