"""Shared filler machinery on top of Playwright's sync API."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from worker.apply.answers import AnswerBank
from worker.local_time import today as local_today
from worker.apply.detect import looks_like_captcha, visible_captcha


@dataclass
class FillResult:
    status: str                       # applied | prefilled | needs_you | captcha | error
    unmapped: list[str] = field(default_factory=list)
    proof_png: Path | None = None
    note: str = ""
    sheet: list[dict] = field(default_factory=list)   # label -> value actually entered (prefill hand-off)


class BaseFiller:
    CONFIRM_TEXT: list[str] = []
    # Optional preview mode; production submits directly using the candidate's standing authorization.
    prefill_only: bool = False

    def __init__(self, page, profile: dict, answers: AnswerBank, resume_pdf: Path, proof_dir: Path, cover_letter: Path | None = None, posting=None, answer_provider=None):
        self.page, self.profile, self.answers, self.resume_pdf, self.proof_dir = page, profile, answers, resume_pdf, proof_dir
        self.posting = posting
        self.answer_provider = answer_provider
        self.waiting_for_ai: set[str] = set()
        # A cover letter written for this posting (PDF, optionally a .txt sibling for textarea fields). Never generated here.
        self.cover_letter = cover_letter if cover_letter and cover_letter.exists() else None
        self.unmapped: list[str] = []

    # ---- helpers used by concrete fillers
    def clean_label(self, label: str) -> str:
        return re.sub(r"\s+", " ", (label or "").replace("*", "")).strip()

    # Boards phrase the same standard questions in many ways; fold the common variants so one bank rule covers them.
    LABEL_FOLDS = [
        (r"(\d+)\+\s*years", r"\1 years"), (r"how would you describe your ", ""), (r"gender identity", "gender"),
        (r"racial/ethnic(ity)? background", "race"), (r"race/ethnicity", "race"), (r"\(mark all that apply\)", ""),
        (r"please review and acknowledge ", "acknowledge "), (r"at the time of application, ", ""),
        (r"right to work in the country", "legal right to work in the country"), (r"former \S+ employee", "former employee of"),
        (r"are you a former employee", "former employee of"), (r"^current company$", "current employer"),
        (r"open to working \d+ days (a week )?(from|in) ", "open to working in person in "),
        (r"authori[sz]ed to work (lawfully|legally) (in|for)", "authorized to work in"), (r"authori[sz]ed to work for \S+ in", "authorized to work in"),
        (r"eligible to work legally in", "eligible to work in"), (r"^current location$", "city and state"),
        (r"what country are you based in", "country of residence"),
        (r"\bsponorship\b", "sponsorship"),
        (r"^how do you identify\?\s*\(gender\)$", "gender"),
    ]

    # "When can you start?" / "Earliest month you could join": Yogvid's answer is two weeks from an offer, so the
    # earliest concrete date is two weeks from today (a month-only question gets the month of that date).
    AVAILABILITY = re.compile(r"when (can|could|are you able to|would you be able to) (you )?start|earliest .*(start|join|available)|"
                              r"(available|availability) to start|available to (start|begin|join)|(desired|preferred|target) start date|"
                              r"how soon (can|could) you (start|join)", re.I)

    def availability_answer(self, label: str) -> str:
        from datetime import timedelta
        d = local_today() + timedelta(days=14)
        return d.strftime("%B %Y") if re.search(r"\bmonth\b", label, re.I) else d.strftime("%m/%d/%Y")

    def folded_label(self, label: str) -> str:
        l = self.clean_label(label)
        for pat, rep in self.LABEL_FOLDS:
            l = re.sub(pat, rep, l, flags=re.I)
        return re.sub(r"\s+", " ", l).strip()

    def answer_for(self, label: str, required: bool = True, context: str = ''):
        # Saved US authorization cannot answer a question about another country.
        if re.search(r'(authori[sz]ed|eligible|right).*work.*\b(Sweden|Canada|United Kingdom|UK|Germany|France|Australia|Singapore)\b', label, re.I):
            return None
        from worker.apply.preferences import careers_source, interview_policy_answer
        saved = careers_source(label, self.posting)
        if saved is not None:
            return saved
        saved = interview_policy_answer(label, context, self.profile.get('preferences', {}))
        if saved is not None:
            return saved
        from worker.apply.answers import Answer
        privacy = self.answers.lookup('privacy policy')
        if privacy and privacy.answer is True:
            if re.fullmatch(r'processing of personal data\s*\*?', label.strip(), re.I):
                return Answer('select', ['Acknowledge/Confirm', 'I acknowledge', 'I agree', 'Yes'], '')
            if re.search(r'^California law requires.*notice.*Our Privacy Policy', label, re.I):
                return Answer('text', 'I acknowledge this privacy notice.', '')
        if re.search(r'^By applying to this job, I affirm that the information provided.*true and complete to the best of my knowledge', label, re.I):
            return Answer('select', ['I agree', 'Yes'], '')
        # These are personal choices, independent of standing authorization to apply.
        key = self.preference_key(label)
        if key:
            value = self.profile.get("preferences", {}).get(key)
            if not isinstance(value, bool):
                return None
            # A saved willingness/consent is not evidence about past experience or broader schedules.
            if key == 'rotating_on_call' and re.search(r'\b(experience|years|previously|history|ever)\b', label, re.I):
                return None
            if key == 'occasional_evenings_weekends' and re.search(r'\b(every|each|all|always|regularly|regular|permanent|full-time)\b', label, re.I):
                return None
            if key.startswith('allow_') and re.search(r'have you (ever|previously)|previous (agreement|consent)|history of', label, re.I):
                return None
            if key == 'can_perform_essential_functions' and re.search(r'without.*accommodation|ever.*(unable|difficulty)', label, re.I):
                return None
            if key.startswith('allow_') and re.search(r'opt[- ]?out|decline.*consent|do not consent', label, re.I):
                value = not value
            if key == 'has_disclosable_convictions' and re.search(r'no criminal convictions|never (been )?convicted|not been convicted', label, re.I):
                value = not value
            from worker.apply.answers import Answer
            return Answer("yesno", value, "")
        if self.AVAILABILITY.search(label):
            from worker.apply.answers import Answer
            choices = ['Within two weeks', 'Within 2 weeks', 'Two weeks', '2 weeks']
            if 'full-time' in self.profile.get('preferences', {}).get('job_search_reason', '').lower():
                choices.append('Immediately/next few months, full-time')
            return Answer('select', choices, '', avoid=r'not |part[- ]time|upon graduation|after graduation')
        from worker.apply.preferences import text_answer
        if re.search(r'did you use an ai tool.*(tailor|r[eé]sum[eé]|responses|application)', label, re.I):
            from worker.apply.answers import Answer
            disclosure = self.profile.get('_application_ai_disclosure')
            if disclosure:
                return Answer('text', disclosure, '')
        saved = text_answer(label, self.profile.get("preferences", {}), self.posting, self.profile.get('contact', {}))
        if saved is not None:
            return saved
        from worker.apply.preferences import professional_example
        saved = professional_example(label, self.profile.get('_verified_experience', []))
        if saved is not None and self.profile.get('preferences', {}).get('project_answer_policy') == 'match_job_from_verified_projects':
            return saved
        from worker.apply.preferences import project_answer
        saved = project_answer(label, self.posting, self.profile.get('_verified_projects', []))
        if saved is not None and self.profile.get('preferences', {}).get('project_answer_policy') == 'match_job_from_verified_projects':
            return saved
        if re.fullmatch(r'I identify as\s*:?\s*', self.clean_label(label), re.I):
            from worker.apply.preferences import IdentityChoiceAnswer
            return IdentityChoiceAnswer(self.answers.lookup('sexual orientation'), self.answers.lookup('Do you identify as transgender?'))
        ans = self.answers.lookup(self.folded_label(label)) or self.answers.lookup(self.clean_label(label))
        if ans and ans.answer is False and self.posting and re.search(
                r'ever.*(employed|worked)|previously.*work|have you been employed|former employee|contract work|worked at.*employee', label, re.I):
            company = re.sub(r'[^a-z0-9]+', ' ', self.posting.company.lower()).strip()
            if len(company) >= 4 and any(company in re.sub(r'[^a-z0-9]+', ' ', w.get('employer', '').lower())
                                          for w in self.profile.get('work', [])):
                from worker.apply.answers import Answer
                return Answer('yesno', True, '')
        if ans is not None and ans.only_if_required and not required:
            return None   # answer only when the form insists (e.g. GPA)
        return ans

    PREFERENCE_LABELS = [
        (r"text messages?|\bsms\b|texting consent", "allow_recruiting_sms"),
        (r"essential (job )?functions|perform .*reasonable accommodation", "can_perform_essential_functions"),
        (r"(record|transcrib).*interview|interview.*(record|transcrib)", "allow_interview_recording"),
        (r"(email|contact|notify|notification|communication|hear).*(future|other|new).*(opening|opportunit|position|role|job)|"
         r"(future|other|new).*(opening|opportunit|position|role|job).*(email|contact|notif|communication)", "future_opening_emails"),
    ]

    PREFERENCE_LABELS += [
        (r"(need|require|request).*accommodation.*(interview|assessment)|(interview|assessment).*(need|require|request).*accommodation", "needs_interview_accommodation"),
        (r"on[- ]call", "rotating_on_call"),
        (r"willing.*(evening|weekend)|able.*work.*(evening|weekend)|occasional (evening|weekend)", "occasional_evenings_weekends"),
        (r"(authori[sz]e|consent|agree|permission).*(background|reference).*(check|investig)|(background|reference).*(check|investig).*(authori[sz]|consent|agree)", "allow_background_checks"),
        (r"arbitrat", "allow_arbitration_consent"),
        (r"point of data transfer|consent.*(transfer|process).*outside|data.*transfer.*(international|outside|overseas)", "allow_international_data_transfer"),
        (r"(consent|agree|allow|permission).*(automated|ai-assisted|ai based|artificial intelligence).*screen", "allow_automated_screening"),
        (r"willing.*(assessment|take-home|coding test)|agree.*(assessment|coding test)", "willing_to_complete_assessments"),
        (r"criminal.*(history|convict)|ever.*convict|convict.*(disclos|report)", "has_disclosable_convictions"),
    ]

    def preference_key(self, label: str) -> str | None:
        return next((key for pattern, key in self.PREFERENCE_LABELS if re.search(pattern, label, re.I)), None)

    def mark_unmapped(self, label: str):
        lbl = self.clean_label(label) or "(unlabelled required field)"
        if lbl not in self.unmapped:
            self.unmapped.append(lbl)

    def preflight(self) -> FillResult | None:
        text = self.page.locator('body').inner_text()
        if re.search(r'page not found|the (job|position|posting).{0,40}(no longer available|closed)|job not found', text, re.I):
            return FillResult('skipped_closed', proof_png=self.screenshot('closed'))
        from worker.apply.preferences import requires_reference_details
        prefs = self.profile.get('preferences', {})
        if prefs.get('reference_policy') == 'skip_required_references':
            controls = self.controls() if hasattr(self, 'controls') else self.page.evaluate(CONTROLS_JS)
            if any(c['required'] and requires_reference_details(c['label']) for c in controls):
                return FillResult('skipped_references', proof_png=self.screenshot('skipped_references'))
        if prefs.get('restricted_ai_application_policy') == 'personally_supplied_only':
            text = text + '\n' + (self.posting.jd_text if self.posting else '')
            if re.search(r'(do not|did not|must not|may not|have not|not to|prohibit|without).{0,80}\b(AI|artificial intelligence|ChatGPT|LLMs?)\b.{0,100}(answer|application|response|cover letter)|'
                         r'\bno\s+(AI|artificial intelligence)\b.{0,100}(answer|application|response|cover letter)|'
                         r'\b(AI|artificial intelligence|ChatGPT|LLMs?)\b.{0,80}(prohibited|not permitted|not allowed).{0,80}(application|answer|response)|'
                         r'(application|answer|response).{0,80}(without|prohibit).{0,80}\b(AI|artificial intelligence)\b', text, re.I):
                return FillResult('needs_you', unmapped=['Employer prohibits AI-assisted application answers; personally supplied employer-specific answers required'],
                                  proof_png=self.screenshot('personally_supplied_answers_required'))
        return None

    def salary_answer(self, label: str, selector: str) -> str | None:
        if not re.search(r"expected salary|salary expectation|desired salary|salary range|compensation expectation|desired compensation", label, re.I):
            return None
        prefs = self.profile.get("preferences", {})
        amounts = []
        if prefs.get("use_advertised_salary_range") and self.posting:
            for value in re.findall(r"\d{2,3}(?:,\d{3})+|\d{5,6}|\d{2,3}(?:\.\d+)?\s*[kK]\b", self.posting.salary or ""):
                value = value.replace(",", "").replace(" ", "").lower()
                amounts.append(int(float(value.rstrip("k")) * (1000 if value.endswith("k") else 1)))
        if not amounts and prefs.get("salary_min_usd") and prefs.get("salary_max_usd"):
            amounts = [prefs["salary_min_usd"], prefs["salary_max_usd"]]
        if not amounts:
            return None
        if self.page.locator(selector).first.get_attribute("type") == "number":
            return str(amounts[0])
        return "-".join(f"${v:,}" for v in amounts[:2])

    def detect_captcha(self) -> bool:
        """True only for a challenge the visitor would actually have to solve."""
        try:
            title = (self.page.title() or "").lower()
            if "just a moment" in title or "attention required" in title or "access denied" in title:
                return True
            return visible_captcha(self.page)
        except Exception:  # noqa: BLE001
            return False

    def screenshot(self, name: str) -> Path | None:
        try:
            self.proof_dir.mkdir(parents=True, exist_ok=True)
            out = self.proof_dir / f"{name}.png"
            self.page.screenshot(path=str(out), full_page=True)
            return out
        except Exception:  # noqa: BLE001
            return None

    def submit_and_confirm(self, submit_locator, expect_text: list[str], timeout_ms: int = 20000) -> bool:
        submit_locator.click()
        pattern = re.compile("|".join(re.escape(t) for t in expect_text), re.I)
        try:
            self.page.wait_for_function(
                "(re) => new RegExp(re, 'i').test(document.body.innerText)", arg=pattern.pattern, timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- template method
    def fill(self) -> FillResult:  # pragma: no cover - concrete fillers implement
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Generic labelled-control walker shared by Greenhouse-style forms.
# Each control: {"label", "kind": text|select|file|checkbox_group, "selector", "required", "options"}
CONTROLS_JS = r"""
() => {
  const seen = new Set(); const out = [];
  const labelOf = (el) => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.textContent; }
    if (!t) { const l = el.closest('label'); if (l) t = l.textContent; }
    if (!t) { const q = el.closest('.question, fieldset, .field, div'); const l = q && q.querySelector('label, legend'); if (l) t = l.textContent; }
    if (!t) { const q = fieldOf(el); const l = q && q.querySelector('label, legend, [class*="question-title"]'); if (l) t = l.textContent; }
    const group = el.closest('fieldset');
    const heading = group && group.querySelector(':scope > legend');
    if (heading && /references?|referee/i.test(heading.textContent) && !/references?|referee/i.test(t)) t = heading.textContent + ' - ' + t;
    return (t || '').replace(/\s+/g, ' ').trim();
  };
  // Ashby (and similar) wrap each question in a field-entry whose <label> is two levels above the input
  const fieldOf = (el) => el.closest('.ashby-application-form-field-entry, [class*="field-entry"], [class*="fieldEntry"], .question, fieldset, .field');
  const containerOf = (el) => {
    const field = el.closest('.question, fieldset, .field');
    if (field) return field;
    // Consent radios can live under Phone rather than a separate field entry.
    // Group siblings by their shared name before falling back to the question.
    if (el.type === 'radio' && el.name) {
      let parent = el.parentElement;
      while (parent && parent.tagName !== 'FORM' && parent !== document.body) {
        const peers = [...parent.querySelectorAll('input[type=radio]')].filter(b => b.name === el.name);
        if (peers.length > 1) return parent;
        parent = parent.parentElement;
      }
    }
    return fieldOf(el) || el.parentElement;
  };
  const isReq = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) ||
     (containerOf(el) && (containerOf(el).dataset.required === '1' || /\*/.test((containerOf(el).querySelector('label, legend') || {}).textContent || ''))) ||
     (fieldOf(el) && /required/i.test(((fieldOf(el).querySelector('label, legend') || {}).className || '') + ' ' + (fieldOf(el).className || '')));
  // React can replace an input after another answer changes. Prefer its stable,
  // unique ID; marker-only selectors are lost when that node is replaced.
  let n = 1 + [...document.querySelectorAll('[data-jwid]')].reduce((highest, el) => {
    const match = /^jw(\d+)$/.exec(el.dataset.jwid);
    return match ? Math.max(highest, Number(match[1])) : highest;
  }, -1);
  const mark = (el) => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    if (!el.dataset.jwid || document.querySelectorAll(`[data-jwid="${CSS.escape(el.dataset.jwid)}"]`).length !== 1) {
      el.dataset.jwid = 'jw' + (n++);
    }
    return `[data-jwid="${el.dataset.jwid}"]`;
  };
  const contextOf = (el) => ((fieldOf(el) || el.closest('[class*="field"]') || containerOf(el) || {}).innerText || '').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('input, textarea').forEach(el => {
    if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;
    if (el.getAttribute('aria-hidden') === 'true' || el.tabIndex === -1 && !el.id && !el.name && el.type === 'text') return;  // react-select mirror inputs
    const label = labelOf(el);
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (el.closest('[class*="yesno"]')) return;  // Ashby Yes/No button pairs: handled by AshbyFiller.fill_yesno_groups
      const c = containerOf(el); if (!c || seen.has(c)) return; seen.add(c);
      const boxes = [...c.querySelectorAll('input[type=checkbox], input[type=radio]')];
      const legend = (c.querySelector('legend, label') || {}).textContent || label;
      const opts = boxes.map(b => (b.closest('label') ? b.closest('label').textContent : (document.querySelector(`label[for="${CSS.escape(b.id)}"]`)||{}).textContent || b.value).replace(/\s+/g,' ').trim());
      out.push({label: legend.replace(/\s+/g,' ').trim(), kind: el.type === 'radio' ? 'radio_group' : 'checkbox_group', selector: mark(c), required: isReq(el, legend), options: opts});
      return;
    }
    if (el.type === 'file') {
      // Greenhouse labels the input "Attach"; the field's own title (Resume/CV, Cover Letter) sits in the enclosing field
      const fld = el.closest('.field, [class*="field"], fieldset');
      const title = fld ? ((fld.querySelector('label:not([for="' + el.id + '"]), legend, [class*="label"], [class*="title"]') || {}).textContent || '') : '';
      const hint = [label, title, el.id, el.name].join(' ').replace(/\s+/g, ' ').trim();
      out.push({label: hint, kind: 'file', selector: mark(el), required: isReq(el, label) || /required/i.test((fld||{}).textContent||''), options: []}); return; }
    if (el.getAttribute('role') === 'combobox') { out.push({label, kind: 'select', selector: mark(el), required: isReq(el, label), options: [], context: contextOf(el)}); return; }
    out.push({label, kind: 'text', selector: mark(el), required: isReq(el, label), options: []});
  });
  document.querySelectorAll('select').forEach(el => {
    const label = labelOf(el);
    out.push({label, kind: 'native_select', selector: mark(el), required: isReq(el, label), options: [...el.options].map(o => o.text.trim()), context: contextOf(el)});
  });
  return out;
}
"""


SHEET_VALUE_JS = r"""
({sel, kind}) => {
  const el = document.querySelector(sel); if (!el) return '';
  const txt = (e) => (e && (e.innerText || e.textContent) || '').replace(/\s+/g, ' ').trim();
  if (kind === 'file') {
    if (el.files && el.files.length) return [...el.files].map(f => f.name).join(', ');
    const up = el.closest('.file-upload, [class*="file-upload"], .field, [class*="fieldEntry"], [class*="field-entry"]');
    const m = up && (up.innerText || '').match(/[^\s\/]+\.(pdf|docx?|txt|rtf)\b/i);   // Greenhouse swaps the input for a filename chip
    return m ? m[0] : '';
  }
  if (kind === 'native_select') return el.selectedIndex > 0 ? el.options[el.selectedIndex].text.trim() : '';
  if (kind === 'checkbox_group' || kind === 'radio_group') {
    return [...el.querySelectorAll('input:checked')].map(b => txt(b.closest('label')) || txt(document.querySelector(`label[for="${CSS.escape(b.id)}"]`)) || b.value).join('; ');
  }
  if (kind === 'select') {
    const ctl = el.closest('[class*="select__control"], [class*="-control"], [class*="control"]');
    if (ctl) {
      const chosen = ctl.querySelectorAll('[class*="single-value"], [class*="singleValue"], [class*="multi-value__label"], [class*="multiValue"] > div:first-child');
      if (chosen.length) return [...chosen].map(txt).filter(Boolean).join('; ');
      const shown = txt(ctl);                                 // react-select without value classes: the visible text
      if (shown && !/^(select|select\.\.\.|start typing\.\.\.|\+\d+)$/i.test(shown)) return shown;
    }
    return (el.value || '').trim();
  }
  return (el.value || '').trim();
}
"""


class LabelledFormFiller(BaseFiller):
    """Fills any form whose controls carry labels: profile fields first, then the answers bank."""

    PROFILE_LABELS = [
        (r"country.*citizenship|^citizenship$|^nationality$", "citizenship"),
        (r"preferred first (and|&) last name", "full_name"),
        (r"^first name|legal first name", "first_name"), (r"^last name|legal last name|^preferred last name", "last_name"), (r"preferred (first )?name", "preferred_name"),
        (r"^e-?mail", "email"), (r"^(home |mobile )?phone|^contact number$", "phone"), (r"^city$", "city"), (r"^zip|postal code|zip code of your (primary )?residence", "zip"),
        (r"^street|address line 1|^address$|^legal address$", "street"),
        (r"^location \(city\)|^location$|where are you (currently )?located|current location|city and state|where do you (currently )?(live|reside)", "location_display"),
        (r"^(legal |full )?name$|legal name|full legal name", "full_name"),
        (r"which (u\.?s\.? )?state|state .*reside|^state$", "state_name"),
    ]
    # Derived from profile.education[0] / profile.work[0]; used for the education and employment blocks some boards require.
    HISTORY_LABELS = [
        (r"^school|university|institution", ("education", "school")), (r"^degree$|degree type|level of degree", ("education", "degree")),
        (r"discipline|field of study|^major", ("education", "major")),
        (r"^company( name)?$|^employer( name)?$|current\s*(?:\(?or (?:most recent|previous)\)?\s*)?(employer|company)|most recent employer", ("work", "employer")),
        (r"^(job )?title$|current (title|position)", ("work", "title")),
        (r"start date month|start month", ("work", "start_month")), (r"start date year|start year", ("work", "start_year")),
    ]
    # Attestations Yogvid authorised the worker to accept (privacy notices, arbitration acknowledgement, truth certifications).
    ATTESTATION = re.compile(r"^i (acknowledge|confirm|certify|have read|agree|understand|consent|authori[sz]e|accept)|acknowledge|certif(y|ication)|"
                             r"privacy (notice|policy)|arbitration|terms (and|&) conditions|review(ed)? the linked document|have read (and|the)", re.I)
    SUBMIT_SELECTOR = "button[type=submit], input[type=submit], button:has-text('Submit')"

    def controls(self) -> list[dict]:
        return self.page.evaluate(CONTROLS_JS)

    def contact(self, key: str) -> str:
        c = self.profile["contact"]
        if key == "full_name":
            return f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        return str(c.get(key, ""))

    def profile_value(self, label: str) -> str | None:
        key = self.preference_key(label)
        signed_name = re.search(r"(sign|typing|type).*full legal|full legal.*(first|last).*name", label, re.I)
        if key and not (signed_name and self.profile.get("preferences", {}).get(key) is True):
            return None
        l = self.clean_label(label).lower()
        if re.search(r'undergrad|undergad', l):
            education = next((e for e in self.profile.get('education', []) if e.get('degree_level') == 'bachelors'), None)
            if education:
                key = 'major' if re.search(r'degree in|major|field of study', l) else 'school' if re.search(r'attend|school|college|university', l) else None
                if key:
                    return education.get(key) or None
        if re.search(r'^graduation (date|month|year)$', l):
            education = self.profile.get('education') or []
            end = str(education[0].get('end', '')) if education else ''
            if re.fullmatch(r'\d{4}-\d{2}', end):
                return end[:4] if l.endswith('year') else end[5:] if l.endswith('month') else end
            return None
        for pat, key in self.PROFILE_LABELS:
            if re.search(pat, l):
                v = self.contact(key)
                return v if v else None
        for pat, (section, key) in self.HISTORY_LABELS:
            if re.search(pat, l):
                entries = self.profile.get(section) or []
                if not entries:
                    return None
                e = entries[0]
                if key in ("start_month", "start_year"):
                    ym = str(e.get("start", ""))
                    return (ym[5:7] if key == "start_month" else ym[:4]) or None
                if section == "education" and key == "degree":
                    return "Master's Degree"
                v = e.get(key)
                return str(v) if v else None
        return None

    def profile_choices(self, label: str, value: str) -> list[str]:
        if re.search(r'(start|end|graduation) (date )?month', label, re.I) and value.isdigit() and 1 <= int(value) <= 12:
            from calendar import month_name
            return [month_name[int(value)], value, str(int(value))]
        if re.search(r'discipline|field of study|^major', label, re.I) and re.search(r'statistic', value, re.I):
            return [value, 'Statistics', 'Statistics & Decision Theory', 'Data Analytics', 'Other']
        return [value]

    # --- per-kind writers
    def write_text(self, sel: str, value: str):
        from datetime import datetime
        loc = self.page.locator(sel).first
        if loc.get_attribute('type') == 'date':
            for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
                try:
                    value = datetime.strptime(value, fmt).strftime('%Y-%m-%d')
                    break
                except ValueError:
                    pass
        self.page.fill(sel, value)

    def pick_suggestion(self, sel: str):
        """After typing into an autocomplete (Ashby location, Greenhouse school), accept the first suggestion if one appears."""
        try:
            el = self.page.locator(sel).first
            is_combo = (el.get_attribute("role") or "") == "combobox" or bool(el.get_attribute("aria-autocomplete")) \
                or (el.get_attribute("aria-controls") or "") != ""
            if not is_combo:
                return  # plain input: Enter would submit the form
            opts = self._options_for(el)
            for _ in range(12):  # remote suggestions (Ashby/Greenhouse location, school) take ~0.5-2.5 s
                self.page.wait_for_timeout(250)
                opts = self._options_for(el)
                texts = [t.strip().lower() for t in opts.all_text_contents()]
                if texts and not any(t.startswith(("loading", "type to search", "no options")) for t in texts):
                    break
            if opts.count():
                opts.first.click()
            else:
                self.page.keyboard.press("ArrowDown")
                self.page.keyboard.press("Enter")
        except Exception:  # noqa: BLE001
            pass

    def _options_for(self, loc):
        """The option elements that belong to THIS control (react-select: input[aria-controls] -> its listbox),
        so a country-code menu elsewhere on the page is never mistaken for the location suggestions."""
        try:
            ctl = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns")
        except Exception:  # noqa: BLE001
            ctl = None
        if ctl:
            scoped = self.page.locator(f"[id='{ctl}'] [role=option]:visible, [id='{ctl}'][role=listbox] [role=option]:visible")
            if scoped.count():
                return scoped
            return self.page.locator(f"[id='{ctl}'] [role=option]:visible")
        return self.page.locator("[role=option]:visible")

    def write_select(self, sel: str, answer) -> bool:
        """React-select style: open, read options, click the matching one; fallback type+Enter."""
        loc = self.page.locator(sel)
        loc.click()
        self.page.wait_for_timeout(150)
        options = [re.sub(r'\s+', ' ', t).strip() for t in self._options_for(loc).all_text_contents()]
        choice = answer.choose(options) if hasattr(answer, "choose") else (answer if answer in options else None)
        if choice is None and getattr(answer, 'options_required', False):
            if getattr(answer, 'search_options', False) and (not options or all(
                    o.lower().startswith(('loading', 'type to search', 'no options')) for o in options)):
                loc.fill(str(answer.answer))
                for _ in range(12):
                    self.page.wait_for_timeout(250)
                    options = [re.sub(r'\s+', ' ', t).strip() for t in self._options_for(loc).all_text_contents()]
                    choice = answer.choose(options)
                    if choice:
                        break
            if choice is None:
                self.page.keyboard.press('Escape')
                return False
        if choice is None:
            cands = answer.answer if hasattr(answer, "answer") and isinstance(answer.answer, list) else [str(getattr(answer, "answer", answer))]
            loc.fill(str(cands[0]))
            for _ in range(12):  # remote autocompletes (Greenhouse location) answer in ~0.5-2.5 s
                self.page.wait_for_timeout(250)
                options = [re.sub(r'\s+', ' ', t).strip() for t in self._options_for(loc).all_text_contents()]
                if options and not any(o.lower().startswith(("loading", "type to search", "no options")) for o in options):
                    break
            options = [o for o in options if not o.lower().startswith(("loading", "no options"))]
            choice = answer.choose(options) if hasattr(answer, "choose") and options else None
            if choice is None and options and not getattr(answer, "avoid", ""):
                key = str(cands[0]).split(",")[0].lower()
                choice = next((o for o in options if o.lower().startswith(key)), None)
        if choice is None and options:
            SYN = {"male": ["man", "he/him"], "female": ["woman"], "no": ["none", "not applicable", "i am not", "not a"], "yes": ["i agree", "i acknowledge", "consent", "i consent"]}
            cands = answer.answer if isinstance(getattr(answer, "answer", None), list) else [str(getattr(answer, "answer", answer))]
            for cnd in cands:
                for syn in SYN.get(str(cnd).lower(), []):
                    choice = next((o for o in options if o.strip().lower() == syn or o.strip().lower().startswith(syn + " ")), None)
                    if choice:
                        break
                if choice:
                    break
        if choice is None:
            self.page.keyboard.press("Escape")
            return False
        option_text = r'^\s*' + r'\s+'.join(re.escape(word) for word in choice.split()) + r'\s*$'
        self._options_for(loc).filter(has_text=re.compile(option_text)).first.click()
        return True

    def write_checkboxes(self, sel: str, answer, options: list[str]) -> bool:
        wanted = answer.choose(options)
        boxes = self.page.locator(f"{sel} input[type=checkbox], {sel} input[type=radio]")
        if isinstance(answer.answer, bool):
            if boxes.count() == 1 and (answer.answer or boxes.first.get_attribute("type") == "checkbox"):
                boxes.first.set_checked(answer.answer)
                return True
            # yes/no radios
            target = wanted
            for i, o in enumerate(options):
                if o == target:
                    boxes.nth(i).check()
                    return True
            return False
        if wanted is None:
            return False
        for i, o in enumerate(options):
            if o == wanted:
                boxes.nth(i).check()
                return True
        return False

    def write_native_select(self, sel: str, answer, options: list[str]) -> bool:
        if getattr(answer, 'options_required', False):
            choice = answer.choose(options)
        elif isinstance(answer.answer, bool):
            choice = next((o for o in options if o.strip().lower().startswith("yes" if answer.answer else "no")), None)
        else:
            choice = answer.choose(options)
        if choice is None:
            return False
        self.page.select_option(sel, label=choice)
        return True

    def fill_fields(self):
        for c in self.controls():
            label, kind, sel, req = c["label"], c["kind"], c["selector"], c["required"]
            try:
                if kind == "file":
                    if re.search(r"cover\s*letter", label, re.I):
                        if self.cover_letter:
                            self.page.set_input_files(sel, str(self.cover_letter))
                        elif req:
                            self.mark_unmapped("Cover letter (required)")
                    elif re.search(r"resume|cv", label, re.I):
                        self.page.set_input_files(sel, str(self.resume_pdf))
                    elif req:
                        self.mark_unmapped(label or "required file upload")
                    continue
                if kind == "text" and re.search(r"cover\s*letter", label, re.I):
                    txt = self.cover_letter.with_suffix(".txt") if self.cover_letter else None
                    if txt and txt.exists():
                        self.page.fill(sel, txt.read_text().strip())
                    elif req:
                        self.mark_unmapped("Cover letter (required)")
                    continue
                if kind == "text" and self.AVAILABILITY.search(label):
                    el = self.page.locator(sel).first
                    picker = el.evaluate("e => !!e.closest('[class*=datepicker], [class*=date-picker], [class*=DatePicker]')")
                    if picker:
                        el.click()
                        self.page.wait_for_timeout(300)
                    self.write_text(sel, self.availability_answer(label))
                    if picker:
                        self.page.wait_for_timeout(300)
                        el.press("Enter")   # commit the typed date in the picker (Escape would revert it)
                        self.page.wait_for_timeout(400)
                    if req and not el.input_value().strip():
                        self.mark_unmapped(label)
                    continue
                if kind == "text":
                    pv = self.profile_value(label)
                    if pv is not None:
                        self.write_text(sel, pv)
                        if re.search(r"locat|city|school|university", label, re.I):
                            self.pick_suggestion(sel)
                        continue
                    a = self.answer_for(label, req)
                    if a is None and self.answer_provider and self.page.locator(sel).first.get_attribute('type') in {None, 'text', 'search'}:
                        from worker.apply.generated import InferenceDeferred
                        limit = self.page.locator(sel).first.get_attribute('maxlength')
                        try:
                            a = self.answer_provider(label, self.posting, max_length=int(limit) if limit and limit.isdigit() else 1500)
                        except InferenceDeferred:
                            self.waiting_for_ai.add(self.clean_label(label))
                    if a and a.kind == "text":
                        self.write_text(sel, self.salary_answer(label, sel) or str(a.answer))
                    elif a and isinstance(a.answer, list) and a.answer:      # a dropdown answer asked as free text
                        if getattr(a, 'options_required', False):
                            if req:
                                self.mark_unmapped(label)
                            continue
                        value = a.answer[0]
                        if re.search(r'travel', label, re.I) and self.profile.get('preferences', {}).get('business_travel') == 'any':
                            value = '100' if self.page.locator(sel).first.get_attribute('type') == 'number' else 'Any amount required for the role.'
                        self.write_text(sel, str(value))
                    elif a and isinstance(a.answer, bool) and a.kind == "yesno":
                        self.write_text(sel, "Yes" if a.answer else "No")
                    elif req:
                        self.mark_unmapped(label)
                    continue
                if kind == "select":
                    pv = self.profile_value(label)
                    a = self.answer_for(label, req, context=c.get('context', ''))
                    if a and getattr(a, 'options_required', False):
                        ok = self.write_select(sel, a)
                    elif re.search(r"^country", label, re.I) and not a:
                        ok = self.write_select(sel, _Static(["United States", "United States +1", "US", "USA", "United States of America"]))
                    elif pv is not None and re.search(r"location", label, re.I):
                        ok = self.write_select(sel, _Static([pv, self.contact("city")]))
                    elif pv is not None:
                        ok = self.write_select(sel, _Static(self.profile_choices(label, pv)))
                    elif re.search(r"discipline|field of study", label, re.I):
                        ok = self.write_select(sel, _Static(["Data Science", "Statistics", "Data Analytics", "Computer Science", "Other"]))
                    elif self.ATTESTATION.search(label or "") and not self.preference_key(label) and not re.search(r"sponsor|citizen|clearance|felony|convict", label or "", re.I):
                        ok = self.write_select(sel, _Static(["I acknowledge", "I agree", "Yes", "I have read", "Acknowledged", "Accept", "Consent", "I consent"]))
                    elif re.search(r"start date month|end date month", label, re.I) and pv is not None:
                        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                        ok = self.write_select(sel, _Static([months[int(pv) - 1], pv]))
                    elif a and (a.kind == "yesno" or isinstance(a.answer, bool)):   # a consent phrased as a Yes/No dropdown
                        ok = self.write_select(sel, _Static(["Yes", "I agree", "I consent"] if a.answer else ["No"]))
                    elif a and a.kind in ("select", "checkbox"):
                        ok = self.write_select(sel, a)
                    else:
                        ok = False
                    if not ok and req:
                        self.mark_unmapped(label)
                    continue
                if kind in ("checkbox_group", "radio_group"):
                    a = self.answer_for(label, req)
                    pv = self.profile_value(label)
                    if pv is not None:
                        candidates = [pv]
                        if re.search(r'degree', label, re.I) and pv == "Master's Degree":
                            candidates += ["Master's", 'Masters', 'Master of Science']
                        a = _Static(candidates)
                    # "No, looking for remote work" is not equivalent to "not local, willing to relocate".
                    if a and isinstance(a.answer, bool) and re.search(r"(based|located|living) in|work.*office", label, re.I):
                        relocation = self.answer_for("Are you willing to relocate?")
                        if relocation and relocation.answer is True:
                            option = next((o for o in c["options"] if
                                           re.search(r"(open|willing).*relocat", o, re.I) and
                                           not re.search(r'\b(not\s+(open|willing)|unwilling|unable)\b', o, re.I)), None)
                            if option:
                                a = _Static([option])
                            elif any(re.search(r"remote opportunit|only remote", o, re.I) for o in c["options"]):
                                if req:
                                    self.mark_unmapped(label)
                                continue
                    if a is None and kind == "checkbox_group" and len(c["options"]) <= 1 and self.ATTESTATION.search(label or "") and not self.preference_key(label) \
                            and not re.search(r"sponsor|citizen|clearance|felony|convict", label or "", re.I):
                        a = _Static(True)
                    ok = self.write_checkboxes(sel, a, c["options"]) if a else False
                    if not ok and req:
                        self.mark_unmapped(label)
                    continue
                if kind == "native_select":
                    a = self.answer_for(label, req, context=c.get('context', ''))
                    pv = self.profile_value(label)
                    if pv is not None:
                        a = _Static(self.profile_choices(label, pv))
                    ok = self.write_native_select(sel, a, c["options"]) if a else False
                    if not ok and req:
                        self.mark_unmapped(label)
            except Exception as e:  # noqa: BLE001
                if req:
                    self.mark_unmapped(f"{label} (error: {type(e).__name__})")

    def fill(self) -> FillResult:
        if self.detect_captcha():
            return FillResult("captcha", proof_png=self.screenshot("captcha"))
        restriction = self.preflight()
        if restriction:
            return restriction
        self.fill_fields()
        if self.unmapped:
            if set(self.unmapped) <= self.waiting_for_ai:
                return FillResult('deferred', unmapped=['Routine answer generation is waiting for model availability; no submission sent'],
                                  proof_png=self.screenshot('waiting_for_ai'))
            return FillResult("needs_you", unmapped=list(self.unmapped), proof_png=self.screenshot("needs_you"))
        if self.prefill_only:
            return FillResult("prefilled", proof_png=self.screenshot("prefilled"), sheet=self.answer_sheet())
        ok = self.submit_and_confirm(self.page.locator(self.SUBMIT_SELECTOR).first, self.CONFIRM_TEXT)
        proof = self.screenshot("confirmation" if ok else "submit_failed")
        if ok:
            return FillResult("applied", proof_png=proof)
        return FillResult("error", proof_png=proof, note="no confirmation after submit: " + self.page_errors())

    def answer_sheet(self) -> list[dict]:
        """Every field as it now reads on the page: what a person (or Claude in Chrome) must re-enter to submit."""
        rows = []
        for c in self.controls():
            label, kind, sel = self.clean_label(c["label"]), c["kind"], c["selector"]
            try:
                value = self.page.evaluate(SHEET_VALUE_JS, {"sel": sel, "kind": kind})
            except Exception:  # noqa: BLE001
                value = ""
            if value:
                rows.append({"label": label or "(unlabelled)", "kind": kind, "value": value, "required": bool(c["required"])})
        return rows

    def page_errors(self) -> str:
        """The form's own complaint after a failed submit (required field, invalid value, spam flag), for the Notes column."""
        try:
            errors = self.page.locator('[role=alert], .ashby-application-form-failure-container, '
                                       '.ashby-application-form-blocked-application-container, '
                                       '.error-message, [class*="errorMessage"], [class*="validation-error"]')
            messages = [el.inner_text().strip() for el in errors.all() if el.is_visible()]
            if messages:
                return '; '.join(dict.fromkeys(messages))[:1000]
            txt = self.page.evaluate("document.body.innerText")
        except Exception:  # noqa: BLE001
            return ""
        lines = [l.strip() for l in txt.splitlines()
                 if re.search(r"missing.*required|invalid|error|please (correct|fix)|needs corrections|flagged|spam|try again|unable to|couldn't submit|verifying this submission|updating your application", l, re.I)
                 and len(l.strip()) < 200 and "indicates a required" not in l.lower()]
        return "; ".join(dict.fromkeys(lines))[:500]


class _Static:
    """Adapter so plain candidate lists behave like an Answer in write_select."""

    def __init__(self, cands: list[str]):
        self.answer = cands
        self.kind = "select"

    def choose(self, options: list[str]):
        from worker.apply.answers import Answer

        return Answer("select", self.answer, "").choose(options)
