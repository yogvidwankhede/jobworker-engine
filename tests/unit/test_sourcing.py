import json
from pathlib import Path

from worker.models import Posting
from worker.records.models import Job
from worker.sourcing import ashby, dedupe, greenhouse, lever, source_all

FX = Path(__file__).resolve().parents[1] / "fixtures"
CO = {"name": "Stripe", "tier": "big", "board": "greenhouse", "slug": "stripe", "careers_url": "https://stripe.com/jobs"}


def test_greenhouse_parses_board_json():
    ps = greenhouse.parse(CO, json.loads((FX / "greenhouse_board.json").read_text()))
    assert len(ps) == 2
    p = ps[0]
    assert p.company == "Stripe" and p.tier == "big" and p.ats == "greenhouse"
    assert p.title == "AI Engineer" and p.location == "Chicago, IL"
    assert p.url == "https://job-boards.greenhouse.io/stripe/jobs/8044460"
    assert p.apply_url == "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8044460"  # hosted form, works for embedded boards too
    assert "AI agents" in p.jd_text and "<" not in p.jd_text


def test_ashby_parses_posting_api():
    co = dict(CO, name="LangChain", tier="startup", board="ashby", slug="langchain")
    ps = ashby.parse(co, json.loads((FX / "ashby_board.json").read_text()))
    assert ps[0].apply_url.endswith("/application") and ps[0].ats == "ashby"
    assert ps[0].salary == "$170K – $220K" and ps[0].location == "San Francisco"
    assert ps[0].employment_type == 'FullTime'


def test_lever_parses_list():
    co = dict(CO, name="Tovala", tier="startup", board="lever", slug="tovala")
    ps = lever.parse(co, json.loads((FX / "lever_board.json").read_text()))
    assert ps[0].title == "Machine Learning Engineer" and ps[0].ats == "lever" and ps[0].location == "Chicago, IL"


def test_lever_keeps_required_qualifications_and_salary():
    posting = lever.parse(CO, [{'text': 'ML Engineer', 'descriptionPlain': 'Build AI systems',
        'lists': [{'text': 'What We Require', 'content': '<li>5 years of experience</li><li>Python</li>'}],
        'additionalPlain': 'US only', 'categories': {'commitment': 'Full-time'},
        'salaryRange': {'currency': 'USD', 'min': 120000, 'max': 150000, 'interval': 'year'}}])[0]
    assert 'What We Require' in posting.jd_text and '5 years of experience' in posting.jd_text and 'Python' in posting.jd_text
    assert 'US only' in posting.jd_text and '<li>' not in posting.jd_text
    assert posting.salary == 'USD 120000–150000 per year'
    assert posting.employment_type == 'Full-time'


def test_dedupe_key_ignores_query_and_case():
    a = Posting(company="Stripe", tier="big", title="AI Engineer", url="u", apply_url="https://X.com/jobs/1?utm=a")
    b = Posting(company="stripe", tier="big", title="ai  engineer", url="u", apply_url="https://x.com/jobs/1")
    assert dedupe.key(a) == dedupe.key(b)


def test_filter_new_drops_existing_and_seen():
    class Store:
        def get_jobs(self, status=None):
            return [Job(company="Stripe", role="AI Engineer", apply_url="https://x.com/jobs/1", status="Applied")]
    ps = [Posting(company="Stripe", tier="big", title="AI Engineer", url="u", apply_url="https://x.com/jobs/1?x=1"),
          Posting(company="Stripe", tier="big", title="ML Engineer", url="u", apply_url="https://x.com/jobs/2"),
          Posting(company="Stripe", tier="big", title="ML Engineer", url="u", apply_url="https://x.com/jobs/2")]
    out = dedupe.filter_new(ps, Store())
    assert [p.title for p in out] == ["ML Engineer"]


def test_greenhouse_history_does_not_hide_other_employers_or_job_ids():
    old_url = 'https://job-boards.greenhouse.io/embed/job_app?for=intercom&token=8185627'
    class Store:
        def get_jobs(self, status=None):
            return [Job(company='Intercom', role='AI Engineer', apply_url=old_url, status='Applied')]
    other_employer = Posting(company='Vestmark', title='Software Engineer', tier='mid', url='u',
                            apply_url='https://job-boards.greenhouse.io/embed/job_app?for=vestmark&token=8009953')
    other_job = Posting(company='Intercom', title='Software Engineer', tier='mid', url='u',
                       apply_url=old_url.replace('8185627', '8185628'))
    same_job = Posting(company='Intercom', title='Renamed role', tier='mid', url='u',
                       apply_url='https://boards.greenhouse.io/intercom/jobs/8185627?gh_src=tracking')
    assert dedupe.filter_new([other_employer, other_job, same_job, other_employer], Store()) == [other_employer, other_job]


def test_source_all_collects_errors_and_skips_unknown(monkeypatch):
    cos = [dict(CO), dict(CO, name="Broken", board="ashby", slug="broken"), dict(CO, name="NoBoard", board="unknown", slug=None)]
    def fake_fetch(url):
        if "greenhouse" in url:
            return json.loads((FX / "greenhouse_board.json").read_text())
        raise RuntimeError("500")
    ps, errors = source_all(cos, fetch=fake_fetch)
    assert len(ps) == 2 and len(errors) == 1 and "Broken" in errors[0]


def test_newly_supported_portals_reenter_scoring_but_uncertain_submits_do_not():
    url = 'https://jobs.lever.co/example/abc/apply'
    job = Job(company='Example', role='AI Engineer', apply_url=url, status='Needs you',
              questions_for_me='Unsupported application portal — apply by hand')
    class Store:
        def get_jobs(self): return [job]
    posting = Posting(company=job.company, title=job.role, tier='startup', url=url, apply_url=url)
    assert dedupe.filter_new([posting], Store()) == [posting]
    job.questions_for_me = 'Submit did not confirm — check the form and apply by hand'
    assert dedupe.filter_new([posting], Store()) == []
    job.questions_for_me = 'Unsupported application portal — apply by hand'
    job.apply_url = 'https://unsupported.example/apply'
    assert not dedupe.newly_supported_portal(job)
