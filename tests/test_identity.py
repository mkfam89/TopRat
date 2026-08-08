"""slug / stem / camel_to_words / tracker_id / _prefer_pdf."""
import pytest

from conftest import load_fixture

GOLDEN = load_fixture('identity.json')


@pytest.mark.parametrize('raw, expected', [
    ('Sr. SRE - Release', 'SrSRERelease'),
    ('Acme, Inc.', 'AcmeInc'),
    ('data analyst', 'DataAnalyst'),
    ('', ''),
    (None, ''),
])
def test_slug(jp, raw, expected):
    assert jp.slug(raw) == expected


def test_tracker_id(jp):
    # Company legal suffix ('Inc') is stripped for the filename/id.
    assert jp.tracker_id('Acme, Inc.', 'Senior SRE') == 'Acme_SeniorSRE'


def test_tracker_id_truncates_at_80(jp):
    tid = jp.tracker_id('A' * 60, 'B' * 60)
    assert len(tid) == 80 and tid.startswith('A' * 60 + '_')


@pytest.mark.parametrize('company, role, expected', [
    # Role: drop location/timezone/remote/team/tech-stack tails, keep seniority + level.
    ('PostHog', 'Site Reliability Engineer (US, Central/Eastern Timezone)',
     'PostHog_SiteReliabilityEngineer'),
    ('Danaher', 'Technical Support Engineer Enterprise Solutions REMOTE',
     'Danaher_TechnicalSupportEngineer'),
    ('Frost Bank', 'Software Engineer III Power Platform',
     'FrostBank_SoftwareEngineerIII'),
    ('N8n', 'Senior Support Engineer Remote US', 'N8n_SeniorSupportEngineer'),
    # Company: strip legal/location suffixes, cap to the first ~3 significant words.
    ('EPAM Systems Inc', 'DevOps Platform Engineer AWS Terraform Python HPC',
     'EPAM_DevOpsPlatformEngineer'),
    ('ENGIE North America Inc', 'Performance Engineer II', 'ENGIE_PerformanceEngineerII'),
    ('Farmers Educational Cooperative Union Of America ND Division',
     'IT Business Support Analyst',
     'FarmersEducationalCooperative_ITBusinessSupportAnalyst'),
])
def test_tracker_id_shortens(jp, company, role, expected):
    assert jp.tracker_id(company, role) == expected


@pytest.mark.parametrize('raw, expected', [
    ('Site Reliability Engineer (US, Central/Eastern Timezone)', 'Site Reliability Engineer'),
    ('Software Engineer III Power Platform', 'Software Engineer III'),
    ('DevOps Engineer Remote', 'DevOps Engineer'),
    ('Cloud Engineer Product Metrics', 'Cloud Engineer'),
    ('Senior Data Analyst', 'Senior Data Analyst'),
    ('Client Support Analyst III', 'Client Support Analyst III'),
    # spaced slash = dual title -> keep the first; tight slash is preserved
    ('Senior Business Analyst / Data Quality Assurance Engineer — Federal Healthcare',
     'Senior Business Analyst'),
])
def test_clean_role(jp, raw, expected):
    assert jp.clean_role(raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('ENGIE North America Inc', 'ENGIE'),
    ('EPAM Systems Inc', 'EPAM'),
    ('Acme, Inc.', 'Acme'),
    ('Farmers Educational Cooperative Union Of America ND Division',
     'Farmers Educational Cooperative'),
])
def test_clean_company(jp, raw, expected):
    assert jp.clean_company(raw) == expected


@pytest.mark.parametrize('fname, expected', [
    ('Khoa_Pham_Resume_Acme_DevOpsEngineer.docx', 'Acme_DevOpsEngineer'),
    ('Khoa_Pham_Resume_Acme_DevOpsEngineer.pdf', 'Acme_DevOpsEngineer'),
    ('Acme_Role.docx', 'Acme_Role'),   # already stripped
])
def test_stem(jp, fname, expected):
    assert jp.stem(fname) == expected


@pytest.mark.parametrize('raw, expected', [
    ('SrSiteReliabilityEngineer', 'Sr Site Reliability Engineer'),
    ('DevOpsEngineer', 'Dev Ops Engineer'),
    ('SRERelease', 'SRE Release'),
    ('Acme', 'Acme'),
    ('', ''),
])
def test_camel_to_words(jp, raw, expected):
    assert jp.camel_to_words(raw) == expected


def test_camel_roundtrip_canon_equivalence(jp):
    # slug->camel_to_words must stay canon-equal to the original phrasing,
    # which is what the applied reconciler relies on
    role = 'Senior Site Reliability Engineer'
    assert jp._canon_tokens(jp.camel_to_words(jp.slug(role))) == jp._canon_tokens(role)


def test_golden_tracker_ids(jp):
    for row in GOLDEN:
        assert jp.tracker_id(row['company'], row['title']) == row['trackerId']
        assert jp.slug(row['company']) == row['slugCompany']


# ---- _prefer_pdf -----------------------------------------------------------

def _job(jid, resume):
    return {'id': jid, 'resume': resume}


def test_prefer_pdf_picks_pdf_twin(jp):
    jobs = [_job('A', 'New/Acme/Khoa_Pham_Resume_A.docx'),
            _job('A', 'New/Acme/Khoa_Pham_Resume_A.pdf')]
    out = jp._prefer_pdf(jobs)
    assert len(out) == 1 and out[0]['resume'].endswith('.pdf')
    # order must not matter
    out = jp._prefer_pdf(jobs[::-1])
    assert len(out) == 1 and out[0]['resume'].endswith('.pdf')


def test_prefer_pdf_keeps_docx_when_alone(jp):
    out = jp._prefer_pdf([_job('A', 'New/Acme/x.docx')])
    assert len(out) == 1 and out[0]['resume'].endswith('.docx')


def test_prefer_pdf_one_row_per_id(jp):
    jobs = [_job('A', 'a.docx'), _job('A', 'a.pdf'),
            _job('B', 'b.pdf'), _job('C', 'c.docx')]
    out = jp._prefer_pdf(jobs)
    assert sorted(j['id'] for j in out) == ['A', 'B', 'C']
