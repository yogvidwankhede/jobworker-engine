"""Fill Lever's hosted application form using its question and field containers."""
import re

from worker.apply.answers import Answer
from worker.apply.base import LabelledFormFiller


CONTROLS_JS = r"""
() => {
  const out = [], seen = new Set();
  let index = Math.max(-1, ...[...document.querySelectorAll('[data-lever-worker-id]')]
    .map(el => Number(el.dataset.leverWorkerId.replace('lever','')))) + 1;
  const mark = el => {
    if (!el.dataset.leverWorkerId) el.dataset.leverWorkerId = 'lever' + index++;
    return `[data-lever-worker-id="${el.dataset.leverWorkerId}"]`;
  };
  document.querySelectorAll('input,textarea,select').forEach(el => {
    if (['hidden','submit','button'].includes(el.type) || !el.name || /captcha/i.test(el.name)) return;
    const question = el.closest('.application-question');
    const field = el.closest('.application-field') || el.parentElement;
    const heading = question && question.querySelector('.application-label');
    const label = (heading?.textContent || el.getAttribute('aria-label') || el.placeholder || el.name)
      .replace(/\s+/g, ' ').replace(/[✱*]/g, '').trim();
    const required = !!(el.required || field.classList.contains('required-field') || /[✱*]/.test(heading?.textContent || ''));
    if (['checkbox','radio'].includes(el.type)) {
      if (seen.has(field)) return; seen.add(field);
      const boxes = [...field.querySelectorAll('input[type=checkbox],input[type=radio]')];
      out.push({label, kind: el.type === 'radio' ? 'radio_group' : 'checkbox_group', selector: mark(field), required,
        options: boxes.map(box => (box.closest('label')?.textContent || box.value).replace(/\s+/g,' ').trim())});
    } else {
      out.push({label, kind: el.type === 'file' ? 'file' : el.tagName === 'SELECT' ? 'native_select' : 'text',
        selector: mark(el), required, options: [...el.querySelectorAll('option')].map(option => option.text.trim())});
    }
  });
  return out;
}
"""


class LeverFiller(LabelledFormFiller):
    SUBMIT_SELECTOR = '#btn-submit, button[type=submit]:has-text("Submit application")'
    CONFIRM_TEXT = ['Your application has been submitted', 'Application submitted', 'Thank you for applying']

    def controls(self):
        return self.page.evaluate(CONTROLS_JS)

    def fill(self):
        result = super().fill()
        if result.status == 'error' and self.detect_captcha():
            result.status = 'captcha'
            result.note = 'Employer requires a visible verification challenge'
        return result

    def answer_for(self, label, required=True, context=''):
        if re.search(r'language skills?', label, re.I):
            # No language-proficiency facts are saved; use the form's disclosure opt-out.
            return Answer('select', ['Choose not to disclose', 'Prefer not to say'], '')
        if re.search(r'share my (resume|r[eé]sum[eé]).*external.*partners', label, re.I):
            # Additional recruiting disclosure is not needed to apply; do not opt in without a saved choice.
            return Answer('yesno', self.profile.get('preferences', {}).get('share_with_external_recruiting_partners', False), '')
        if re.search(r'are you a resident of California', label, re.I):
            state = self.profile.get('contact', {}).get('state_name')
            if state:
                return Answer('yesno', state.lower() == 'california', '')
        if re.search(r'how you heard about (this|the) opportunity', label, re.I):
            company = self.posting.company if self.posting else ''
            return Answer('select', [company + ' Website', 'Company website', 'Website', 'Other'], '')
        return super().answer_for(label, required, context=context)

    def write_native_select(self, selector, answer, options):
        if super().write_native_select(selector, answer, options):
            return True
        # Lever's university list spells Saint in full; the saved school uses St.
        def normalized(value):
            return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'\bst\.?\b', 'saint', value.lower())).strip()
        values = answer.answer if isinstance(answer.answer, list) else []
        matches = [option for option in options if any(normalized(option) == normalized(value) for value in values)]
        if len(matches) != 1:
            return False
        self.page.select_option(selector, label=matches[0])
        return True
