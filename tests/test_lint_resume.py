"""lint_resume.py — each synthetic fixture violates exactly one rule.

Fixtures in tests/fixtures/docx/ (rebuild: python tests/build_docx_fixtures.py).
R02/R03 are PDF-side rules: their fixtures use clean docx content plus a
monkeypatched pdf_pages, so the suite stays offline (no pdftotext/soffice).
Everything writable goes to tmp_path.
"""
import os
import shutil

import pytest

import lint_resume as lr
from conftest import FIXTURES

DOCX = os.path.join(FIXTURES, 'docx')


def rules_of(report):
    return sorted({f['rule'] for f in report.errors})


def lint(name, check_pdf=False):
    return lr.lint_file(os.path.join(DOCX, name), check_pdf=check_pdf)


# ---- the clean file must pass ----------------------------------------------

def test_clean_passes():
    report = lint('clean_pass.docx')
    assert report.findings == [], report.findings


# ---- one file, one rule ----------------------------------------------------

@pytest.mark.parametrize('name, rule', [
    ('r01_font_min.docx', 'R01'),
    ('r04_bullets_per_role.docx', 'R04'),
    ('r05_orphan_title.docx', 'R05'),
    ('r06_banned_header.docx', 'R06'),
    ('r07_required_section.docx', 'R07'),
    ('r08_notes_leakage.docx', 'R08'),
    ('Khoa_Pham_Resume_Acme_DataEngineer.docx', 'R09'),
    ('r11_skills_format.docx', 'R11'),
    ('r12_trailing_group.docx', 'R12'),
])
def test_exactly_one_rule_fires(name, rule):
    report = lint(name)
    assert rules_of(report) == [rule], (name, report.findings)


def test_r10_missing_pdf_twin():
    report = lint('r10_missing_pdf.docx', check_pdf=True)
    assert rules_of(report) == ['R10'], report.findings


# ---- R02 / R03: PDF page rules via faked pdftotext output ------------------

def _with_dummy_pdf(tmp_path, name):
    src = os.path.join(DOCX, name)
    dst = tmp_path / name
    shutil.copyfile(src, dst)
    dst.with_suffix('.pdf').write_bytes(b'%PDF-1.4 dummy')
    return str(dst)


def test_r02_page_count(tmp_path, monkeypatch):
    path = _with_dummy_pdf(tmp_path, 'r02_page_count.docx')
    monkeypatch.setattr(lr, 'pdf_pages', lambda p: (3, [40, 40, 40]))
    report = lr.lint_file(path, check_pdf=True)
    assert rules_of(report) == ['R02'], report.findings


def test_r03_page2_min_lines(tmp_path, monkeypatch):
    path = _with_dummy_pdf(tmp_path, 'r03_page2_lines.docx')
    monkeypatch.setattr(lr, 'pdf_pages', lambda p: (2, [40, 3]))
    report = lr.lint_file(path, check_pdf=True)
    assert rules_of(report) == ['R03'], report.findings


def test_two_full_pages_pass(tmp_path, monkeypatch):
    path = _with_dummy_pdf(tmp_path, 'clean_pass.docx')
    monkeypatch.setattr(lr, 'pdf_pages', lambda p: (2, [42, 18]))
    report = lr.lint_file(path, check_pdf=True)
    assert report.errors == [], report.findings


def test_pdftotext_unavailable_warns_not_errors(tmp_path, monkeypatch):
    path = _with_dummy_pdf(tmp_path, 'clean_pass.docx')
    monkeypatch.setattr(lr, 'pdf_pages', lambda p: (None, []))
    report = lr.lint_file(path, check_pdf=True)
    assert report.errors == []
    assert [w['rule'] for w in report.warnings] == ['R02']


# ---- unreadable file -> R00, exit path -------------------------------------

def test_corrupt_docx_reports_r00(tmp_path):
    bad = tmp_path / 'broken.docx'
    bad.write_bytes(b'this is not a zip archive')
    report = lr.lint_file(str(bad), check_pdf=False)
    assert rules_of(report) == ['R00']
