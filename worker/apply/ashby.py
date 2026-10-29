"""Ashby application form: labelled inputs, Yes/No button pairs (aria-pressed), consent radio via label click."""
from __future__ import annotations

import re
import time
import json

from worker.apply.base import FillResult, LabelledFormFiller

YESNO_GROUPS_JS = r"""
() => [...document.querySelectorAll('.ashby-application-form-input-yesno-option')].map(b => b.parentElement)
  .filter((g, i, a) => a.indexOf(g) === i).map((g, i) => {
    g.dataset.jwyn = 'yn' + i;
    let label = '', labelEl = null;
    const lb = g.getAttribute('aria-labelledby'); if (lb) { labelEl = document.getElementById(lb); }
    if (!labelEl && g.previousElementSibling && g.previousElementSibling.tagName === 'LABEL') labelEl = g.previousElementSibling;
    if (!labelEl) { const q = g.closest('.ashby-application-form-field-entry'); labelEl = q && q.querySelector('label, .ashby-application-form-field-title'); }
    if (labelEl) label = labelEl.textContent;
    const req = /\*\s*$/.test(label) || (labelEl && /required/i.test(labelEl.className)) || (g.closest('[data-required]') || {}).dataset?.required === '1';
    return {label: label.replace(/\s+/g,' ').trim(), selector: `[data-jwyn="yn${i}"]`, required: !!req};
  })
"""


class AshbyFiller(LabelledFormFiller):
    CONFIRM_TEXT = ["successfully submitted"]
    SUBMIT_SELECTOR = "button[type=submit], button:has-text('Submit Application'), button:has-text('Submit application')"
    PROFILE_LABELS = LabelledFormFiller.PROFILE_LABELS + [(r"^(full )?name$", "full_name")]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from worker.apply.ashby_state import FormRequests
        self.requests = FormRequests(self.page)
        self.submit_problem = ''

    def wait_for_saved_fields(self, timeout_ms=60000, quiet_ms=1000):
        # Moving focus commits inputs that save on blur. Ashby refuses a submit
        # while a field/file save is active, although the submit button is enabled.
        self.page.locator(self.SUBMIT_SELECTOR).first.focus()
        self.requests.changed_at = time.monotonic()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
            if self.requests.save_failed:
                return False
            if not self.requests.pending and (time.monotonic() - self.requests.changed_at) * 1000 >= quiet_ms:
                return True
        return False

    def submit_and_confirm(self, submit_locator, expect_text, timeout_ms=120000):
        self.submit_problem = ''
        if not self.wait_for_saved_fields():
            self.submit_problem = 'Form answers or files did not finish saving; no submission sent'
            return False
        previous_errors = self.page_errors()
        self.requests.result = None
        event_start = len(self.requests.events)
        submit_locator.click()
        deadline = time.monotonic() + timeout_ms / 1000
        pattern = re.compile('|'.join(re.escape(t) for t in expect_text), re.I)
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
            if self.requests.result == 'applied':
                return True
            if self.requests.result in {'blocked', 'validation'}:
                return False
            success = self.page.locator('.ashby-application-form-success-container')
            if success.count() and success.first.is_visible():
                return True
            text = self.page.locator('body').inner_text()
            if pattern.search(text):
                return True
            if self.detect_captcha():
                self.submit_problem = 'CAPTCHA challenge appeared during submission'
                return False
            errors = self.page_errors()
            if errors and errors != previous_errors and re.search(
                    "needs corrections|couldn't submit|flagged|spam|verifying this submission|updating your application", errors, re.I):
                if 'updating your application' in errors.lower() and not any(
                        event['operation'].startswith('ApiSubmit') for event in self.requests.events[event_start:]):
                    self.submit_problem = 'Form answers or files did not finish saving; no submission sent'
                return False
        self.submit_problem = 'Submission outcome unknown after waiting 120 seconds'
        return False

    def contact(self, key: str) -> str:
        if key == "full_name":
            return f"{self.profile['contact']['first_name']} {self.profile['contact']['last_name']}"
        return super().contact(key)

    def fill_yesno_groups(self):
        for g in self.page.evaluate(YESNO_GROUPS_JS):
            a = self.answer_for(g["label"], bool(g.get("required", True)))
            if not a or a.kind != "yesno":
                if g["required"]:
                    self.mark_unmapped(g["label"])
                continue
            want = "Yes" if a.answer else "No"
            btn = self.page.locator(f"{g['selector']} button", has_text=re.compile(rf"^\s*{want}\s*$", re.I)).first
            btn.click()
            if btn.get_attribute("aria-pressed") != "true":
                btn.click()
            if btn.get_attribute("aria-pressed") != "true" and g["required"]:
                self.mark_unmapped(g["label"])

    def write_checkboxes(self, sel, answer, options):
        # consent radios/checkboxes: click the label (Ashby ignores programmatic checks)
        if isinstance(answer.answer, bool) and answer.answer and len(options) == 1:
            self.page.locator(f"{sel} label").first.click()
            box = self.page.locator(f"{sel} input").first
            if not box.is_checked():
                box.check()
            return box.is_checked()
        return super().write_checkboxes(sel, answer, options)

    def fill_fields(self):
        super().fill_fields()
        self.fill_yesno_groups()

    def answer_sheet(self) -> list[dict]:
        rows = super().answer_sheet()
        for g in self.page.evaluate(YESNO_GROUPS_JS):
            pressed = self.page.locator(f"{g['selector']} button[aria-pressed=true]")
            if pressed.count():
                rows.append({"label": g["label"].replace("*", "").strip(), "kind": "yesno", "value": pressed.first.inner_text().strip(),
                             "required": bool(g.get("required"))})
        return rows

    def fill(self) -> FillResult:
        res = super().fill()
        if res.status == "error" and "needs corrections" in self.page.evaluate("document.body.innerText"):
            # re-read values Ashby may have dropped after the resume upload, then retry once
            self.fill_fields()
            if self.unmapped:
                return FillResult("needs_you", unmapped=list(self.unmapped), proof_png=self.screenshot("needs_you"))
            ok = self.submit_and_confirm(self.page.locator(self.SUBMIT_SELECTOR).first, self.CONFIRM_TEXT)
            res = FillResult("applied" if ok else "error", proof_png=self.screenshot("confirmation" if ok else "submit_failed"),
                             note=self.submit_problem or self.page_errors())
        if res.status == 'error' and self.submit_problem.startswith('Form answers'):
            res.status = 'deferred'
            res.note = self.submit_problem
        elif res.status == 'error' and self.detect_captcha():
            res.status = 'captcha'
        elif res.status == 'error' and 'needs corrections' in self.page.locator('body').inner_text().lower():
            labels = self.page.locator('[role=alert] button').all_text_contents()
            res.status = 'needs_you'
            res.unmapped = [self.clean_label(label) for label in labels] or [self.page_errors()]
        elif res.status == 'error' and (self.requests.result == 'blocked' or re.search(
                "couldn't submit your application|flagged as possible spam", self.page.locator('body').inner_text(), re.I)):
            res.status = 'blocked'
            res.note = self.page_errors() or 'Employer rejected the submission'
        self.proof_dir.mkdir(parents=True, exist_ok=True)
        (self.proof_dir / 'submission.json').write_text(json.dumps({
            'status': res.status, 'result': self.requests.result, 'problem': self.submit_problem,
            'events': self.requests.events[-80:]}, indent=2))
        return res
