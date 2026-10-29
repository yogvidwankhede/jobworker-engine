from worker.config import Settings
from worker.models import Posting
from worker.scoring import llm, score, skills
import pytest

JD = ("We are hiring an AI Engineer to build RAG pipelines and evaluation harnesses for LLM apps.\n"
      "Requirements:\n- 2+ years of experience with Python\n- Experience with LLMs and retrieval\n"
      "- Familiarity with Docker and GCP\n- BS in Computer Science or related field\nNice to have:\n- Rust")
P = Posting(company="X", tier="startup", title="AI Engineer", url="u", apply_url="a", jd_text=JD, location="Remote")
MUST = ["2+ years of experience with Python", "Experience with LLMs and retrieval", "Familiarity with Docker and GCP",
        "BS in Computer Science or related field"]


def _model(level="entry", years=2, must=None, level_quote="", years_quote="2+ years of experience"):
    must = must if must is not None else MUST
    return lambda prompt, schema: {"level": level, "level_quote": level_quote, "years_required": years, "years_quote": years_quote, "must_have": must}


def test_full_coverage_scores_high(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", _model())
    sc, reason = llm.score(P)
    assert sc == 100 and reason.startswith("3/3 technical must-haves covered")  # the degree is a soft item


def test_prompt_has_no_candidate_profile(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "generate_json", lambda prompt, schema: seen.setdefault("p", prompt) and _model()(prompt, schema))
    llm.score(P)
    assert "EUNO" not in seen["p"] and "LangGraph" not in seen["p"] and "fit" not in llm.SCHEMA["properties"]


def test_profile_echo_is_dropped(monkeypatch):
    """The failure from dry run #4: items that read like the candidate profile, not the JD."""
    echo = ["Proficiency in Python and TypeScript/JavaScript", "Experience with cloud platforms (GCP, AWS)",
            "Strong background in AI/ML engineering"]
    monkeypatch.setattr(llm, "generate_json", _model(must=echo))
    sc, reason = llm.score(P)
    assert sc == 60 and reason.startswith("no verifiable technical requirements")


def test_paraphrase_without_verbatim_run_is_dropped(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", _model(must=["Kubernetes operator development", "Experience with LLMs and retrieval"]))
    sc, reason = llm.score(P)
    assert reason.startswith("1/1 technical must-haves covered") and sc == 70


def test_missing_hard_skill_lowers_score(monkeypatch):
    jd = JD + "\n- Strong C++ skills"
    p = P.model_copy(update={"jd_text": jd})
    monkeypatch.setattr(llm, "generate_json", _model(must=MUST[:3] + ["Strong C++ skills"]))
    sc, reason = llm.score(p)
    assert sc < 70 and "missing: Strong C++ skills" in reason


def test_soft_missing_items_are_not_disqualifiers(monkeypatch):
    jd = JD + "\n- Experience working in small teams with minimal supervision"
    p = P.model_copy(update={"jd_text": jd})
    monkeypatch.setattr(llm, "generate_json", _model(must=MUST[:3] + ["Experience working in small teams with minimal supervision"]))
    sc, reason = llm.score(p)
    assert sc == 100


@pytest.mark.parametrize('interest', [
    'You care deeply about data quality and finding high-quality information',
    'You care about the problem of finding high quality knowledge',
])
def test_interest_statements_do_not_count_as_missing_technical_skills(monkeypatch, interest):
    p = P.model_copy(update={'jd_text': JD + '\n- ' + interest})
    monkeypatch.setattr(llm, 'generate_json', _model(must=MUST[:2] + [interest]))
    sc, reason = llm.score(p)
    assert sc == 85 and reason.startswith('2/2 technical must-haves covered')


def test_interest_statement_does_not_hide_a_real_missing_hard_skill(monkeypatch):
    interest = 'You care deeply about data quality and have expert C++ skills'
    p = P.model_copy(update={'jd_text': JD + '\n- ' + interest})
    monkeypatch.setattr(llm, 'generate_json', _model(must=MUST[:2] + [interest]))
    sc, reason = llm.score(p)
    assert sc < 70 and interest in reason


SENIOR_JD = JD + "\nWe want a seasoned engineer with extensive production experience."


def test_model_senior_needs_a_verified_quote(monkeypatch):
    p = P.model_copy(update={"jd_text": SENIOR_JD})
    monkeypatch.setattr(llm, "generate_json", _model(level="senior", level_quote="extensive production experience"))
    assert llm.score(p) == (0, "model reads role as senior-level")
    # quote not in JD, or quote with no senior signal → the call does not stand
    monkeypatch.setattr(llm, "generate_json", _model(level="senior", level_quote="we love ownership"))
    assert llm.score(p)[0] == 100
    monkeypatch.setattr(llm, "generate_json", _model(level="senior", level_quote="build RAG pipelines"))
    assert llm.score(p)[0] == 100


def test_model_years_need_a_verified_quote(monkeypatch):
    p = P.model_copy(update={"jd_text": JD + "\nYou have 5+ years of experience shipping software."})
    monkeypatch.setattr(llm, "generate_json", _model(years=5, years_quote="5+ years of experience shipping software"))
    assert llm.score(p)[0] == 0
    assert llm.score(p, max_years=5)[0] > 0
    assert score.score(p, Settings()).rejected is True
    monkeypatch.setattr(llm, "generate_json", _model(years=5, years_quote="4+ years"))  # number disagrees → ignored
    assert llm.score(p)[0] == 100
    monkeypatch.setattr(llm, "generate_json", _model(years=5, years_quote=""))
    assert llm.score(p)[0] == 100


def test_requirements_excerpt_finds_heading_not_disclaimer():
    jd = ("About Lyft. " * 120 + "\nResponsibilities:\n- build models\n" + "Do things. " * 300 +
          "\nExperience:\n- BS/MS in Computer Science\n- 2+ years of experience in machine learning modeling\n" + "More. " * 400 +
          "\nThe expected range depends on qualifications, experience and geographic location.")
    ex = llm.requirements_excerpt(jd, limit=3000)
    assert "2+ years of experience in machine learning modeling" in ex and len(ex) <= 3100


def test_requirements_excerpt_returns_whole_short_jd():
    assert llm.requirements_excerpt(JD) == JD


def test_intern_level_penalised(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", _model(level="intern"))
    assert llm.score(P)[0] == 80


def test_llm_invalid_output_falls_back(monkeypatch):
    for bad in [{"level": "guru", "years_required": 1, "must_have": []},
                {"level": "entry", "years_required": 1, "must_have": "python"}, {"nope": 1}, None]:
        monkeypatch.setattr(llm, "generate_json", lambda prompt, schema, bad=bad: bad)
        assert llm.score(P) is None
    monkeypatch.setattr(llm, "generate_json", lambda prompt, schema: (_ for _ in ()).throw(TimeoutError()))
    assert llm.score(P) is None
    sc = score.score(P, Settings())
    assert sc.rejected is False and sc.reason.startswith("rules-only") and sc.score < Settings().fit_threshold  # unverified → never selectable


def test_bad_years_value_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", _model(years="five"))
    assert llm.score(P)[0] > 0


def test_rules_rejection_short_circuits_llm(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "generate_json", lambda prompt, schema: called.append(1) or _model()(prompt, schema))
    bad = P.model_copy(update={"jd_text": JD + " We are unable to sponsor visas."})
    sc = score.score(bad, Settings())
    assert sc.rejected and not called


def test_prompt_uses_requirements_section(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "generate_json", lambda prompt, schema: seen.setdefault("p", prompt) and _model()(prompt, schema))
    long_jd = "About us. " * 200 + "\nRequirements:\n- 2+ years Python\n- LLMs" + " filler" * 50
    llm.score(P.model_copy(update={"jd_text": long_jd}))
    assert "Requirements:" in seen["p"] and "2+ years Python" in seen["p"]


def test_beyond_budget_is_never_selectable():
    ps = [P.model_copy(update={"url": str(i), "jd_text": JD + " llm rag langchain python gcp docker eval agent vector"}) for i in range(4)]
    out = score.score_batch(ps, Settings(fit_threshold=70), llm_score=lambda p: (90, "ok"), llm_budget=1, workers=1)
    unchecked = [x for x in out if x.reason.startswith("rules-only (beyond")]
    assert len(unchecked) == 3 and all(x.score < 70 for x in unchecked)


def test_skills_classify_examples():
    assert skills.classify("Proficiency in Python, Go, or Rust")[0] is True
    assert skills.classify("Strong C++ skills")[0] is False
    assert skills.classify("Excellent communication skills")[0] is True
    assert skills.classify("Experience with Spark")[0] is False
    assert skills.classify("Experience building ML models")[0] is True
    assert skills.classify("Underwater basket weaving")[0] is False
    assert skills.classify("Prior experience post-training and deploying LLMs in production")[0] is False
    assert skills.classify("Know and prefer configuration management systems (Chef, Ansible, Terraform)")[0] is False


def test_phd_with_or_is_not_an_alternative_unless_masters_named():
    assert skills.classify("PhD (or expected completion by start date) in Computer Science or a related field")[0] is False
    assert skills.classify("MS or PhD in Computer Science")[0] is True


def test_senior_wording_in_requirement_is_not_credited():
    assert skills.classify("Proven track record owning production-grade real-time, large-scale systems")[0] is False
    assert skills.classify("Deep expertise in distributed systems")[0] is False
    assert skills.classify("5+ years of Python")[0] is False
    assert skills.classify("2+ years of Python")[0] is True


@pytest.mark.parametrize('range_text', ['2–4+', '2-4+', '2 to 4', '2—4'])
def test_year_ranges_use_the_minimum_in_both_extraction_and_skill_matching(monkeypatch, range_text):
    requirement = f'Tenure: {range_text} years of experience as a professional software engineer'
    p = P.model_copy(update={'jd_text': JD + '\n' + requirement})
    monkeypatch.setattr(llm, 'generate_json', _model(level='senior', years=2, must=MUST[:3] + [requirement],
                                                  level_quote=requirement, years_quote=requirement))
    ext = llm.extract(p)
    assert ext['years_required'] == 2 and ext['level'] == 'unclear'
    assert skills.classify(requirement)[0] is True
    assert llm.decide(ext, max_years=3)[0] == 100
    assert skills.classify('5–8+ years of Python')[0] is False


def test_general_tool_judgment_is_not_a_missing_technical_requirement(monkeypatch):
    judgment = ('You have a bias for using the right tools to get a job done with maximum efficiency. '
                'You have experience making tradeoffs between speed and accuracy.')
    p = P.model_copy(update={'jd_text': JD + '\n' + judgment})
    monkeypatch.setattr(llm, 'generate_json', _model(must=MUST[:2] + [judgment]))
    assert llm.score(p)[0] == 85
    unsupported = 'You have a bias for using the right tools, including required C++ expertise.'
    p = P.model_copy(update={'jd_text': JD + '\n' + unsupported})
    monkeypatch.setattr(llm, 'generate_json', _model(must=MUST[:2] + [unsupported]))
    assert llm.score(p)[0] < 70


def test_role_without_technical_requirements_can_match_verified_responsibilities(monkeypatch):
    duty = 'Build software engineering tools and production LLM applications using Python.'
    p = P.model_copy(update={'jd_text': duty})
    response = {'level': 'unclear', 'level_quote': '', 'years_required': None, 'years_quote': '',
                'must_have': [], 'responsibilities': [duty]}
    monkeypatch.setattr(llm, 'generate_json', lambda prompt, schema: response)
    score, reason = llm.score(p)
    assert score == 70 and 'documented technical responsibilities' in reason
    response['responsibilities'] = [duty + ' and CUDA GPU kernels.']
    assert llm.score(p)[0] == 60  # a shared fragment cannot validate an invented duty
    response['responsibilities'] = ['Experience from EUNO with LangGraph.js']
    assert llm.score(p)[0] == 60  # candidate echo is absent from this posting


def test_duties_do_not_override_missing_or_unverified_required_qualifications(monkeypatch):
    duty = 'Build production LLM applications using Python.'
    required = 'Strong C++ skills'
    p = P.model_copy(update={'jd_text': duty + '\nRequirements: ' + required})
    response = {'level': 'entry', 'level_quote': '', 'years_required': None, 'years_quote': '',
                'must_have': [required], 'responsibilities': [duty]}
    monkeypatch.setattr(llm, 'generate_json', lambda prompt, schema: response)
    assert llm.score(p)[0] == 0
    response['must_have'] = ['Undocumented invented qualification']
    assert llm.score(p)[0] == 60


def test_thin_must_have_list_scores_lower(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", _model(must=["Experience with LLMs and retrieval"]))
    one, _ = llm.score(P)
    monkeypatch.setattr(llm, "generate_json", _model())
    three, _ = llm.score(P)
    assert one < three and one >= 70


def test_alternatives_with_eg_and_commas():
    assert skills.classify("2+ years applying machine learning methods (e.g., recommender systems, reinforcement learning)")[0] is True
    assert skills.classify("Experience building projects through coursework, internships, research, or personal work.")[0] is True
    assert skills.classify("Experience with reinforcement learning")[0] is False


def test_soft_asks_with_suffixes_are_not_disqualifiers(monkeypatch):
    soft = ["Deep excitement about AI tools and strong opinions about how they should be used.",
            "Comfort driving adoption, not just writing code.", "High adaptability and ownership."]
    jd = JD + "\n" + "\n".join("- " + s for s in soft)
    monkeypatch.setattr(llm, "generate_json", _model(must=MUST[:3] + soft))
    assert llm.score(P.model_copy(update={"jd_text": jd}))[0] == 100


def test_software_engineering_fundamentals_are_held():
    assert skills.classify("Strong software engineering fundamentals and the ability to ship quickly.")[0] is True
    assert skills.classify("Solid full-stack fundamentals with production ownership of features you've shipped")[0] is True
    assert skills.classify("Proven ability to apply data science in real-world business contexts")[0] is True


def test_not_held_inside_examples_or_tool_alternatives():
    assert skills.classify("Working knowledge of Kubernetes and experience deploying services through CI/CD.")[0] is False
    assert skills.classify("Exposure to large-scale systems, ideally across thousands of nodes")[0] is False
    assert skills.classify("IAM backend experience in either authorization or authentication domain")[0] is False
    assert skills.classify("Distributed processing (Spark / PySpark, Ray, Databricks, or equivalent)")[0] is False
    assert skills.classify("2+ years applying machine learning methods (e.g., recommender systems, reinforcement learning)")[0] is True
    assert skills.classify("Strong programming skills in languages such as Python, Go, or JavaScript.")[0] is True
    assert skills.classify("Strong Python skills; experience with PyTorch or equivalent deep learning frameworks")[0] is True
    assert skills.classify("Deep ML theory knowledge with extremely strong fundamentals")[0] is False
    assert skills.classify("Built RAG and agentic applications using patterns like ReAct or Plan-and-Execute")[0] is True


def test_speech_domain_not_held():
    assert skills.classify("Strong experience with the machine learning research pipeline, particularly in STT or related speech domains")[0] is False


def test_tool_parenthetical_with_held_core_tool_is_forgiven():
    assert skills.classify("Experience with cloud platforms (e.g., AWS, GCP, Azure), containerization (Docker, Kubernetes)")[0] is True
    assert skills.classify("Working knowledge of Kubernetes and experience deploying services through CI/CD.")[0] is False
    assert skills.classify("Experience using AI tools to accelerate development")[0] is True
    assert skills.classify("Strong understanding of GPU architecture, profiling")[0] is False


def test_training_llms_is_not_held():
    assert skills.classify("Demonstrated experience training and evaluating large language models")[0] is False
    assert skills.classify("Hands-on experience with training and fine-tuning large language models, including SFT, distillation, preference optimization, reinforcement learning, or other post-training techniques")[0] is False
    assert skills.classify("Experience fine-tuning models with LoRA or PEFT")[0] is True
    assert skills.classify("Experience building LLM applications")[0] is True
