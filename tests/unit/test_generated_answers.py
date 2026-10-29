import json
import pytest

from worker.apply.generated import GroundedAnswers, InferenceDeferred, evidence


PROFILE = {'preferences': {'motivation': 'Building production AI products with real user impact'}}


def test_standout_question_can_select_verified_strengths_without_new_facts():
    from worker.apply.generated import eligible
    assert eligible('What makes you stand out for this position?')
    assert not eligible('What makes you stand out regarding your security clearance?')
LEDGER = {'experience': [{'id': 'euno', 'employer': 'EUNO', 'verified': True, 'facts': [
    {'id': 'euno.pipeline', 'claim': '6-stage LLM pipeline using LangGraph.js', 'verified': True},
    {'id': 'euno.docs', 'claim': 'authored operational runbooks', 'verified': True},
    {'id': 'unverified', 'claim': '99% invented impact', 'verified': False},
    {'id': 'conflicted', 'claim': 'unresolved architecture', 'verified': True, 'conflict': 'unresolved'},
]}], 'projects': [{'id': 'project', 'name': 'Verified Project', 'verified': True,
                   'facts': [{'claim': 'retrieval pipeline using Python'}]}]}


def test_only_verified_nonconflicting_facts_enter_context():
    facts = evidence(PROFILE, LEDGER)
    assert 'unverified' not in facts and 'conflicted' not in facts
    assert facts['project.project.0'] == 'My work on Verified Project includes retrieval pipeline using Python.'


def test_new_routine_question_uses_relevant_facts_and_persistent_cache(tmp_path):
    calls = []
    def generate(prompt, schema):
        calls.append(prompt)
        return {'status': 'answerable', 'fact_ids': ['euno.pipeline', 'euno.docs'],
                'answer': 'I invented a 99% improvement.'}
    path = tmp_path / 'generated-answers.json'
    provider = GroundedAnswers(PROFILE, LEDGER, generate, path)
    question = 'How have you built systems for reliable knowledge extraction?'
    answer = provider(question)
    assert 'LangGraph.js' in answer.answer and 'runbooks' in answer.answer
    assert '99%' not in answer.answer and len(calls) == 1
    restored = GroundedAnswers(PROFILE, LEDGER, lambda *_: pytest.fail('Cached answer must not need model quota'), path)
    assert restored(question).answer == answer.answer
    assert next(iter(json.loads(path.read_text()).values()))['fact_ids'] == ['euno.pipeline', 'euno.docs']


@pytest.mark.parametrize('question', [
    'How many years of professional Python experience do you have?',
    'Describe an accommodation you need.',
    'Describe a conflict with your manager.',
    'What is your citizenship?',
    'Describe your professional references.',
])
def test_unknown_personal_facts_are_not_invented(tmp_path, question):
    provider = GroundedAnswers(PROFILE, LEDGER, lambda *_: pytest.fail('This question must use saved explicit facts'), tmp_path / 'answers.json')
    assert provider(question) is None


def test_invented_evidence_defers_unsent_form_without_caching(tmp_path):
    path = tmp_path / 'answers.json'
    provider = GroundedAnswers(PROFILE, LEDGER, lambda *_: {'status': 'answerable', 'fact_ids': ['fabricated']}, path)
    with pytest.raises(InferenceDeferred):
        provider('Describe your technical strengths')
    assert not path.exists()


def test_quota_failure_reuses_existing_answers_and_retries_after_reset(tmp_path):
    path = tmp_path / 'answers.json'
    def unavailable(*_):
        raise RuntimeError('Model quota unavailable')
    provider = GroundedAnswers(PROFILE, LEDGER, unavailable, path)
    with pytest.raises(InferenceDeferred):
        provider('Describe your technical strengths')
    restored = GroundedAnswers(PROFILE, LEDGER, lambda *_: {'status': 'answerable', 'fact_ids': ['euno.docs']}, path)
    assert 'runbooks' in restored('Describe your technical strengths').answer


def test_unknown_is_cached_and_short_fields_do_not_truncate_claims(tmp_path):
    provider = GroundedAnswers(PROFILE, LEDGER, lambda *_: {'status': 'unknown', 'fact_ids': []}, tmp_path / 'answers.json')
    assert provider('Describe your approach to compiler development') is None
    provider.generator = lambda *_: pytest.fail('Unknown answer is already evaluated for this evidence revision')
    assert provider('Describe your approach to compiler development') is None
    short = GroundedAnswers(PROFILE, LEDGER, lambda *_: {'status': 'answerable', 'fact_ids': ['euno.pipeline']}, tmp_path / 'short.json')
    assert short('Describe your technical strengths', max_length=10) is None


def test_changed_verified_facts_invalidate_cached_answer(tmp_path):
    path = tmp_path / 'answers.json'
    provider = GroundedAnswers(PROFILE, LEDGER, lambda *_: {'status': 'answerable', 'fact_ids': ['euno.docs']}, path)
    provider('Describe your technical strengths')
    updated = {'experience': [{'id': 'euno', 'employer': 'EUNO', 'verified': True,
                              'facts': [{'id': 'euno.docs', 'claim': 'authored updated runbooks', 'verified': True}]}]}
    calls = []
    fresh = GroundedAnswers(PROFILE, updated, lambda *_: calls.append(1) or {'status': 'answerable', 'fact_ids': ['euno.docs']}, path)
    assert 'updated runbooks' in fresh('Describe your technical strengths').answer and calls == [1]
