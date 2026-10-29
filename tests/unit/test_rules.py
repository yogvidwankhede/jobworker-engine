import pytest

from worker.config import Settings
from worker.models import Posting
from worker.scoring.rules import check

S = Settings()


AI_JD = "We build LLM apps: RAG pipelines, prompt engineering, and embeddings search over vector databases."


@pytest.mark.parametrize('employment', ['Contract', 'PartTime', 'Temporary', 'Intern'])
def test_board_employment_type_rejects_even_when_title_and_jd_omit_it(employment):
    posting = Posting(company='Elicit', tier='startup', title='ML Research Resident', url='u',
                      apply_url='a', jd_text=AI_JD, location='Remote - US', employment_type=employment)
    rejection = check(posting, S)
    assert rejection is not None and 'not full-time' in rejection.reason


def p(title="AI Engineer", jd=AI_JD, location="Remote - US"):
    return Posting(company="X", tier="startup", title=title, url="u", apply_url="a", jd_text=jd, location=location)


@pytest.mark.parametrize("phrase", [
    "unable to sponsor", "no sponsorship", "must be a U.S. citizen", "will not sponsor",
    "UNABLE   TO SPONSOR", "not able to provide visa sponsorship", "US citizens only",
    "requires an active security clearance",
])
def test_sponsorship_phrases_reject(phrase):
    r = check(p(jd=f"Great role. {phrase}. Apply now."), S)
    assert r is not None and r.reason


def test_military_leave_benefit_does_not_imply_itar_restriction():
    assert check(p(jd=AI_JD + '\nWe offer leave for military service and medical events.'), S) is None
    restricted = check(p(jd=AI_JD + '\nThis role is subject to ITAR restrictions.'), S)
    assert restricted is not None and 'itar' in restricted.reason


@pytest.mark.parametrize("title", ["Applied Scientist", "Product Manager", "Account Executive", "Recruiter"])
def test_title_out_of_scope_rejects(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("title", ["Staff AI Engineer", "Principal Machine Learning Engineer", "Senior Manager, AI", "Director of Engineering", "VP Engineering", "Senior AI Engineer", "Sr. Machine Learning Engineer", "Software Engineer III", "Lead Data Scientist"])
def test_seniority_above_mid_rejects(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("title", ["AI Engineer", "Machine Learning Engineer II", "Software Engineer, AI", "Applied AI Engineer", "Full-Stack Engineer, Evals", "Data Scientist", "Forward Deployed Engineer", "ML Engineer (New Grad)", "Software Engineer II, AI Platform"])
def test_in_scope_titles_pass(title):
    assert check(p(title=title), S) is None


@pytest.mark.parametrize("location", ["London, UK", "Bangalore, India", "Toronto, Canada", "Berlin"])
def test_non_us_location_rejects(location):
    assert check(p(location=location), S) is not None


@pytest.mark.parametrize("location", ["Remote", "San Francisco, CA", "New York, NY", "Chicago, IL", "United States", "Remote - US", "St. Louis, MO"])
def test_us_or_remote_passes(location):
    assert check(p(location=location), S) is None


# ---- fit gates added after dry run #3 ----
from worker.scoring.rules import years_required, foreign_stack


@pytest.mark.parametrize("jd,expected", [
    ("Requirements:\n- 5+ years of professional software engineering experience\n- Python", 5),
    ("What you'll need\n- 3-5 years building ML systems", 3),
    ("Minimum of 4 years of experience with Python.", 4),
    ("At least 2 years of relevant experience.", 2),
    ("You have 6+ yrs of hands-on backend experience.", 6),
    ("Bachelor's degree in CS or 2+ years of equivalent experience.", 2),
    ("Requirements:\n- Python and SQL\nNice to have:\n- 8+ years of experience with Kubernetes", None),
    ("Our founders have 20 years of experience in fintech. We are hiring an AI Engineer.", None),
    ("We are looking for someone eager to learn.", None),
    ("Experience: 7+ years in software development", 7),
])
def test_years_required_parses(jd, expected):
    assert years_required(jd) == expected


def test_years_gate_uses_setting():
    jd = "Requirements:\n- 4+ years of professional experience building LLM apps"
    assert check(p(jd=jd), Settings(max_years=3)) is not None
    assert check(p(jd=jd), Settings(max_years=5)) is None


@pytest.mark.parametrize("title", ["Machine Learning Engineer Intern", "AI Engineering Internship (Summer 2027)", "Software Engineer Co-op", "ML Engineer, Spring 2027"])
def test_internships_rejected_by_default(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("title", ["Machine Learning Engineer Intern", "Software Engineer Co-op"])
def test_internships_allowed_when_enabled(title):
    assert check(p(title=title), Settings(include_internships=True)) is None


@pytest.mark.parametrize("title", ["AI Engineer (Brazilian Portuguese speaking)", "Bilingual AI Engineer", "Software Engineer, AI - UK", "Machine Learning Engineer - India", "AI Engineer, EMEA"])
def test_language_or_region_in_title_rejected(title):
    assert check(p(title=title), S) is not None


def test_us_in_title_is_fine():
    assert check(p(title="AI Engineer - US Remote"), S) is None


@pytest.mark.parametrize("title", ["iOS Engineer, AI", "Embedded ML Engineer", "Frontend Engineer, AI Products", "Site Reliability Engineer, ML Platform", "Robotics Software Engineer", "GPU Kernel Engineer, AI", "Solutions Architect, AI"])
def test_off_domain_titles_rejected(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("jd", ["PhD required in machine learning.", "Must have a Ph.D. in Computer Science.", "A doctoral degree is required."])
def test_phd_required_rejected(jd):
    assert check(p(jd=jd), S) is not None


def test_phd_preferred_passes():
    assert check(p(jd="PhD preferred but not required. " + AI_JD), S) is None


@pytest.mark.parametrize("jd,expected", [
    ("Strong C++ skills; you will write high-performance kernels.", "c++"),
    ("We are a Go shop. Experience with Golang required.", "go"),
    ("Deep Java and Spring Boot expertise.", "java"),
    ("Python or Go; we use both.", None),
    ("TypeScript and Node.js backend; C++ a plus.", None),
    ("We build LLM apps.", None),
])
def test_foreign_stack(jd, expected):
    assert foreign_stack(jd) == expected


def test_stack_mismatch_rejected():
    assert check(p(jd="Requirements: 2+ years of professional Rust experience. We love Rust."), S) is not None


def test_remote_outside_us_rejected():
    assert check(p(location="Remote - Canada"), S) is not None
    assert check(p(location="Remote - United States"), S) is None


@pytest.mark.parametrize("title", ["Post-Doctoral AI Researcher", "AI Research Scientist, New Grad", "Software Engineer, Public Sector - New Grad",
                                   "AI Engineer, U.S. Public Sector (Federal Focus)", "RVP - AI Natives", "AI Deployment Strategist",
                                   "AI Field Engineer - Strategic Partnerships", "Agent Reliability Engineer, GTM Engineering",
                                   "Software Engineer, Agent (New Grad 2027)", "AI Solutions Consultant"])
def test_dry_run_3_escapees_now_rejected(title):
    assert check(p(title=title), S) is not None


def test_new_grad_2026_and_plain_new_grad_pass():
    assert check(p(title="Software Engineer - New Grad"), S) is None
    assert check(p(title="AI Engineer, New Grad 2026"), S) is None


@pytest.mark.parametrize("title", ["AI Engineer (Contract)", "Machine Learning Engineer - Part-Time", "AI Engineer, 6-month Fixed-Term", "Contractor: AI Engineer", "AI Research Fellowship"])
def test_not_full_time_titles_rejected(title):
    assert check(p(title=title), S) is not None


def test_not_full_time_jd_rejected():
    assert check(p(jd="Employment type: Contract. We build LLM apps."), S) is not None
    assert check(p(jd="This is a 6-month contract position."), S) is not None
    assert check(p(jd="Employment type: Full-time. " + AI_JD), S) is None


@pytest.mark.parametrize("title", ["Software Engineer, ML Infrastructure", "Software Engineer, Growth Infrastructure", "Software Engineer - Apollo Systems", "Backend Engineer, IAM"])
def test_infra_titles_rejected(title):
    assert check(p(title=title), S) is not None


def test_remote_middle_east_rejected():
    assert check(p(location="Remote - Middle East"), S) is not None
    assert check(p(location="Remote - Korea"), S) is not None


def test_language_requirement_in_jd_rejected():
    assert check(p(jd="We build LLM apps. Fluency in English and Korean language."), S) is not None
    assert check(p(jd="We build LLM apps. Japanese language skills required."), S) is not None
    assert check(p(jd=AI_JD + " Fluency in English."), S) is None


from worker.scoring.rules import title_family


@pytest.mark.parametrize("title,rank", [
    ("AI Engineer", 0), ("Applied AI Engineer", 0), ("Forward Deployed Engineer", 0), ("Software Engineer, AI Enablement", 0), ("AI Product Engineer", 0),
    ("LLM Engineer", 1), ("Software Engineer, Agent", 1), ("FullStack Engineer, AI Observability & Evals Platform", 0), ("Software Engineer, Agent Harness", 1),
    ("Machine Learning Engineer", 2), ("ML Engineer II", 2), ("Research Engineer, Machine Learning Systems", 2),
    ("Data Scientist II", 3),
    ("Software Engineer, Content Platform", 4), ("Backend Engineer, Link", 4),
])
def test_title_family_ranks(title, rank):
    got = title_family(p(title=title))[0]
    assert got == rank


def test_plain_swe_without_ai_signal_rejected():
    assert check(p(title="Software Engineer, Content Platform", jd="Build backend services in Go. Postgres, Redis."), S) is not None
    assert check(p(title="Software Engineer, Content Platform", jd="Build backend services powering our LLM features. " + AI_JD), S) is None
    assert check(p(title="Backend Engineer, Link", jd="Payments backend."), S) is not None


def test_plain_swe_needs_strong_ai_signal_not_boilerplate():
    boiler = "We are an AI-first company. Build backend services in Go. Postgres, Redis. AI is core to our mission."
    assert check(p(title="Software Engineer, Content Platform", jd=boiler), S) is not None
    real = "You will build LLM-powered features: RAG over user content, prompt engineering, and embeddings search with vector databases."
    assert check(p(title="Software Engineer, Content Platform", jd=real), S) is None


@pytest.mark.parametrize("title", ["Research Engineer - Distributed Training", "Software Engineer, Model Training Platform", "Software Engineer (AI-Native), Database Engineering", "Software Engineer, Query Engine"])
def test_training_and_database_engineering_titles_rejected(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("title", ["Software Engineer - Edge AI Systems", "Operations Analytics Engineer", "AI Engineer, Hardware Integration"])
def test_edge_ops_hardware_titles_rejected(title):
    assert check(p(title=title), S) is not None


@pytest.mark.parametrize("title", ["Research Engineer, Code RL (Reinforcement Learning)", "Research Engineer, Life Sciences", "Research Engineer, Pretraining", "Alignment Research Engineer"])
def test_lab_research_titles_rejected(title):
    assert check(p(title=title), S) is not None


def test_research_engineer_with_evals_or_agents_passes():
    assert check(p(title="Research Engineer, Evals"), S) is None
    assert check(p(title="Research Engineer - Agents"), S) is None


def test_accented_non_us_location_rejected():
    assert check(p(location="Remote - São Paulo"), S) is not None
    assert check(p(location="Zürich, Switzerland"), S) is not None
