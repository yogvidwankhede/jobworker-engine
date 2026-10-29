from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr

from tests.fixtures.ashby_form import build as ashby_form
from tests.fixtures.gh_form import build as greenhouse_form
from worker.apply.ashby import AshbyFiller
from worker.apply.greenhouse import GreenhouseFiller
from worker.config import Config, Settings
from worker.models import Posting
from worker.stages import build_stages
from worker.apply.answers import AnswerBank
from worker.apply.base import LabelledFormFiller
import yaml


@pytest.mark.parametrize('filler,form', [(AshbyFiller, ashby_form), (GreenhouseFiller, greenhouse_form)])
@pytest.mark.parametrize('auto_submit', [True, False])
def test_production_wiring_submits_without_approval(tmp_path, monkeypatch, filler, form, auto_submit):
    html = tmp_path / 'form.html'
    html.write_text(form())
    pdf = tmp_path / 'Yogvid_Wankhede_Resume.pdf'
    pdf.write_bytes(b'%PDF-1.4\n%fixture\n')
    cfg = Config(airtable_token_secret=SecretStr(''), gmail_user='',
                 gmail_app_password_secret=SecretStr(''), proof_dir=tmp_path / 'proof')
    monkeypatch.setattr('worker.stages.filler_for', lambda url: filler)
    monkeypatch.setattr('worker.apply.cover.cover_letter_path', lambda cfg, posting: None)
    posting = Posting(company='Fixture', tier='startup', title='AI Engineer', url=html.as_uri(), apply_url=html.as_uri())
    # The session browser fixture owns a sync loop on the main thread; production owns its own loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        status, answers, proof = pool.submit(build_stages(cfg, Settings(auto_submit=auto_submit)).applier,
                                             posting, str(pdf), cfg).result()
    assert status == ('applied' if auto_submit else 'prefilled')
    assert proof and Path(proof).is_file()
    if not auto_submit:
        assert answers


def test_unsaved_screening_preferences_do_not_inherit_general_consent(page, tmp_path):
    data = Path(__file__).resolve().parents[2] / 'worker' / 'data'
    profile = yaml.safe_load((data / 'profile.yaml').read_text())
    for key in ('allow_interview_recording', 'future_opening_emails'):
        profile['preferences'].pop(key, None)
    page.set_content('''
        <div class="field"><label for="recording">I consent to recording my interview *</label>
          <input id="recording" type="checkbox" required></div>
        <div class="field"><label for="emails">I agree to receive emails about future openings *</label>
          <input id="emails" role="combobox" required></div>
        <div class="field"><label for="privacy">I acknowledge the privacy policy *</label>
          <input id="privacy" type="checkbox" required></div>
        <div class="field"><label for="privacy-radio">I agree to the privacy policy *</label>
          <input id="privacy-radio" type="radio" required></div>
    ''')
    filler = LabelledFormFiller(page, profile, AnswerBank.load(data / 'answers.yaml'), tmp_path / 'cv.pdf', tmp_path)
    filler.fill_fields()
    assert len(filler.unmapped) == 2
    assert not page.locator('#recording').is_checked()
    assert page.locator('#emails').input_value() == ''
    assert page.locator('#privacy').is_checked()
    assert page.locator('#privacy-radio').is_checked()

    profile['preferences']['allow_interview_recording'] = False
    page.locator('#recording').check()
    filler.unmapped.clear()
    filler.fill_fields()
    assert not page.locator('#recording').is_checked()


def test_location_radio_uses_saved_relocation_choice(page, tmp_path):
    data = Path(__file__).resolve().parents[2] / 'worker' / 'data'
    profile = yaml.safe_load((data / 'profile.yaml').read_text())
    page.set_content('''<fieldset><legend>Are you currently located in the Bay Area and open to an onsite schedule? *</legend>
      <label><input id="local" type="radio" name="location" required>Yes, I am currently in the Bay Area</label>
      <label><input id="relocate" type="radio" name="location">I'm not currently in the Bay Area but am open to relocation and 4-5 days onsite</label>
      <label><input id="remote" type="radio" name="location">No, I am looking for remote opportunities</label>
    </fieldset>''')
    filler = LabelledFormFiller(page, profile, AnswerBank.load(data / 'answers.yaml'), tmp_path / 'cv.pdf', tmp_path)
    filler.fill_fields()
    assert not filler.unmapped
    assert page.locator('#relocate').is_checked()
    assert not page.locator('#remote').is_checked()


def test_school_autocomplete_searches_beyond_initial_unrelated_options(page, tmp_path):
    data = Path(__file__).resolve().parents[2] / 'worker' / 'data'
    profile = yaml.safe_load((data / 'profile.yaml').read_text())
    page.set_content('''<label for="school">School *</label>
      <input id="school" role="combobox" aria-controls="schools" required>
      <div id="schools" role="listbox"><div role="option">Unrelated School</div></div>
      <script>
        const school = document.getElementById('school');
        const schools = document.getElementById('schools');
        school.addEventListener('input', () => {
          schools.innerHTML = '<div role="option">Washington University in St. Louis</div>';
        });
        schools.addEventListener('click', e => { school.value = e.target.textContent; schools.innerHTML = ''; });
      </script>''')
    filler = LabelledFormFiller(page, profile, AnswerBank.load(data / 'answers.yaml'), tmp_path / 'cv.pdf', tmp_path)
    filler.fill_fields()
    assert not filler.unmapped
    assert page.locator('#school').input_value() == profile['education'][0]['school']


def test_office_willingness_uses_relocation_without_claiming_current_residence(page, tmp_path):
    data = Path(__file__).resolve().parents[2] / 'worker' / 'data'
    profile = yaml.safe_load((data / 'profile.yaml').read_text())
    page.set_content('''<fieldset><legend>Are you able to work from a Sierra office? *</legend>
      <label><input id="local" type="radio" name="office" required>Yes, I am based in one of Sierra's office locations.</label>
      <label><input id="relocate" type="radio" name="office">While I am not currently based in one of Sierra's office locations, I am open to relocation.</label>
      <label><input id="remote" type="radio" name="office">No, I am not able to work from a Sierra office.</label>
    </fieldset>''')
    filler = LabelledFormFiller(page, profile, AnswerBank.load(data / 'answers.yaml'), tmp_path / 'cv.pdf', tmp_path)
    filler.fill_fields()
    assert not filler.unmapped
    assert page.locator('#relocate').is_checked()
    assert not page.locator('#local').is_checked()


def test_question_help_text_is_not_reported_as_a_submission_error(page, tmp_path):
    page.set_content('''<label>Are you fluent in any of these languages?</label>
      <p>We are curious about your language skills. Please select all that apply.</p>''')
    filler = LabelledFormFiller(page, {}, AnswerBank([]), tmp_path / 'cv.pdf', tmp_path)
    assert filler.page_errors() == ''
    page.set_content('''<div role="alert">We're updating your application (e.g. uploading files), please try again when they're finished.</div>''')
    assert 'updating your application' in filler.page_errors()


def test_closed_application_page_is_skipped_without_filling_or_submitting(page, tmp_path):
    page.set_content('<h1>Page not found</h1><p>The page you requested was not found</p>')
    res = AshbyFiller(page, {}, AnswerBank([]), tmp_path / 'cv.pdf', tmp_path).fill()
    assert res.status == 'skipped_closed'


def test_ai_prohibition_attestation_is_caught_before_any_fields_are_filled(page, tmp_path):
    page.set_content('<label>Do you agree not to use AI tools to fill out this application?</label><input id="name">')
    res = AshbyFiller(page, {'preferences': {'restricted_ai_application_policy': 'personally_supplied_only'}},
                      AnswerBank([]), tmp_path / 'cv.pdf', tmp_path).fill()
    assert res.status == 'needs_you'
    assert 'prohibits AI' in res.unmapped[0]
    assert page.locator('#name').input_value() == ''
