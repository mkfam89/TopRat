"""jobdesc.assess() — the unreadable-page gate, plus the Workday redirect follow.

A page that fetches fine can still carry no job: a sign-in wall, a taken-down posting, a
JavaScript shell, or a redirect blob. Those all clear a naive length check, so the gate is
what stands between the user and a drawer full of page metadata. Every junk sample below is
a trimmed copy of text this app actually cached from that site; every good sample is real
posting prose. No network — assess() and the URL/JSON parsers are pure.
"""
import json

import jobdesc as jd


# ---------- samples ----------
GOOD = (
    "Duetto's platform runs 24/7 for hotels and casinos around the world.\n\n"
    "About the role\n\nWe are looking for a Site Reliability Engineer to own our "
    "production platform.\n\nResponsibilities\n\n"
    "\u2022 Own the reliability of a multi-tenant SaaS platform on AWS\n"
    "\u2022 Build automation in Python and Terraform to reduce manual toil\n\n"
    "Qualifications\n\n\u2022 5+ years of experience in an SRE or DevOps role\n"
    "\u2022 Ability to debug distributed systems under pressure\n\n"
    "Benefits\n\nCompetitive compensation, remote-first, and equity."
)

WORKDAY_STUB = ('{"widget":"redirect","url":"/regions_careers/job/Hoover-AL/'
                'Cloud-Solutions-Architect_R104343-1","externalSpa":true}')

LINKEDIN_WALL = (
    "DevOps Engineer II\n\nExpand Energy\n\nSpring, TX\n\nApply\n\n"
    "Join or sign in to find your next job\n\nJoin to apply for the DevOps Engineer II "
    "role at Expand Energy\n\nEmail or phone\n\nPassword\n\nShow\n\nForgot password?\n\n"
    "Sign in\n\nor\n\nNew to LinkedIn? Join now\n\nBy clicking Continue to join or sign "
    "in, you agree to LinkedIn's User Agreement, Privacy Policy, and Cookie Policy.\n\n"
    "Sign in to evaluate your skills\n\nEmail or phone\n\nPassword\n\nSign in\n"
)

EXPIRED_BARE = ("Senior DevOps Engineer (Terraform)\n\nThis job has expired\n\n"
                "Sorry, this job has expired\nshare this job\n\nShare to WeChat\n"
                "Use Scan QR Code in WeChat and click to share.\nCopy to clipboard\n"
                "Google Chrome\nMicrosoft Edge\nApple Safari\nMozilla Firefox\n")

TALEO_SHELL = ("Beginning of the main content section.Return to the home page\n"
               "Printable Format\n\nReturn to previous position on page\n\nDescription\n\n"
               "Qualifications\n\nJob Posting\n:\n:\n:\n:\nSchedule\n:\n\n"
               "Refer a friend for this job\nRefer a friend\nRefer a candidate\n"
               "Submit a candidate's profile\n")

ANGULAR_SHELL = ("An error occured. Please try again\n\n{{company.name}}\n\n"
                 "About {{company.name}}\n\nThanks for your interest in {{company.name}}.\n\n"
                 "We're sorry, but there are currently no open positions.\n\n"
                 "Contact us on our website.\n\n{{selectedPositionFilter}}\n")

NAV_CHROME = ("Skip to main content\n\nLanguage\n\n\u2022\nDeutsch\n\u2022\nEnglish\n"
              "\u2022\nFran\u00e7ais\n\nView Profile\n\nProducts\n\nAnalytics Platform\n"
              "API\n\nData Marketplace\n\nOur Data\n\nCompany\n\nAbout\n\nCareers\n\n"
              "Blog\n\nContact\n\nPrivacy Policy\n\nTerms of Use\n\nCookie Settings\n"
              "\u00a9 2026 All rights reserved.\n\nSearch by keyword\n\nSearch by location\n")


# ---------- the gate ----------
def test_real_posting_passes_clean():
    assert jd.assess(GOOD) == ('', '')


def test_redirect_stub_is_metadata_not_a_description():
    reason, msg = jd.assess(WORKDAY_STUB * 3)      # padded past the length floor
    assert reason == 'metadata' and 'page data' in msg


def test_sign_in_wall_is_named_as_such():
    reason, msg = jd.assess(LINKEDIN_WALL)
    assert reason == 'login' and 'sign-in wall' in msg


def test_wall_page_that_still_carries_the_posting_is_kept():
    # LinkedIn prints the description below the wall often enough that rejecting on the
    # wall alone would throw away readable jobs. Vocabulary wins over the wall.
    reason, _ = jd.assess(LINKEDIN_WALL + '\n' + GOOD)
    assert reason == ''


def test_expired_with_no_body_is_reported_as_taken_down():
    reason, msg = jd.assess(EXPIRED_BARE)
    assert reason == 'expired' and 'no longer accepting' in msg


def test_expired_with_a_readable_body_warns_but_keeps_the_text():
    reason, msg = jd.assess('This job has expired\nThis job posting is no longer '
                            'active and is not accepting applications.\n\n' + GOOD)
    assert reason == '' and msg == jd.NOTICE_EXPIRED


def test_unrendered_template_reads_as_a_javascript_shell():
    assert jd.assess(ANGULAR_SHELL)[0] == 'js'


def test_field_labels_with_no_values_are_not_a_description():
    assert jd.assess(TALEO_SHELL)[0] == 'chrome'


def test_site_menus_and_legal_text_are_not_a_description():
    assert jd.assess(NAV_CHROME)[0] == 'chrome'


def test_too_short_to_read():
    assert jd.assess('Apply now.')[0] == 'empty'


def test_every_reason_has_a_message():
    for code in ('empty', 'metadata', 'js', 'login', 'expired', 'chrome'):
        assert jd.MESSAGES[code].strip()


# ---------- Workday ----------
def test_workday_url_maps_to_the_cxs_endpoint():
    got = jd._workday_api('https://regions.wd5.myworkdayjobs.com/regions_careers/job/'
                          'Hoover-AL/Cloud-Solutions-Architect_R104343-1')
    assert got == ('https://regions.wd5.myworkdayjobs.com/wday/cxs/regions/regions_careers/'
                   'job/Hoover-AL/Cloud-Solutions-Architect_R104343-1')


def test_workday_url_with_a_language_prefix():
    got = jd._workday_api('https://gtlaw.wd1.myworkdayjobs.com/en-US/gtlaw/job/Atlanta/'
                          'AI-Platform-Engineer_JR202601347')
    assert got and '/wday/cxs/gtlaw/gtlaw/job/Atlanta/' in got


def test_non_workday_url_is_left_alone():
    assert jd._workday_api('https://boards.greenhouse.io/acme/jobs/123') is None


def test_redirect_target_is_absolutised():
    got = jd.redirect_target('https://regions.wd5.myworkdayjobs.com/regions_careers/job/x',
                             WORKDAY_STUB)
    assert got == ('https://regions.wd5.myworkdayjobs.com/regions_careers/job/Hoover-AL/'
                   'Cloud-Solutions-Architect_R104343-1')


def test_redirect_target_ignores_a_normal_body():
    assert jd.redirect_target('https://x.wd1.myworkdayjobs.com/s/job/y', '<html>hi</html>') == ''


def test_parse_workday_reads_the_cxs_record():
    rec = json.dumps({'jobPostingInfo': {
        'title': 'Cloud Solutions Architect',
        'jobDescription': '<p>About the role</p><ul><li>Design AWS landing zones</li></ul>',
        'timeType': 'Full time'}})
    text = jd.parse_workday(rec)
    assert 'Cloud Solutions Architect' in text
    assert '\u2022 Design AWS landing zones' in text
    assert '<' not in text                      # html stripped, not passed through


def test_parse_workday_survives_an_unexpected_shape():
    assert jd.parse_workday('{"jobPostingInfo": null}') == ''
