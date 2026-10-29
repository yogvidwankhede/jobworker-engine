"""Talemetry 7-step application (Kirkland & Ellis): agree, upload, review parsed data, prequal, tell-us-more, self-ID, submit.

The parser mis-dates degrees and turns certifications into 'education'; step_review corrects both from profile.yaml.
"""
from __future__ import annotations

import re

from worker.apply.base import BaseFiller, FillResult

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


class TalemetryFiller(BaseFiller):
    CONFIRM_TEXT = ["already been completed", "Thank you"]

    # ---- steps
    def step_agree(self):
        self.page.get_by_role("button", name=re.compile("I agree to the Terms", re.I)).click()
        self.page.wait_for_timeout(300)

    def step_upload(self):
        inp = self.page.locator("#resume_upload_file_input, input[type=file]").first
        for _ in range(2):  # the real site errored once and succeeded on retry
            inp.set_input_files(str(self.resume_pdf))
            self.page.wait_for_timeout(800)
            if inp.count() == 0 or "error occurred" not in self.page.evaluate("document.body.innerText").lower():
                break
        nxt = self.page.get_by_role("button", name=re.compile(r"^Next$", re.I))
        if nxt.count():
            nxt.first.click()
            self.page.wait_for_timeout(300)

    def _set_select_by_text(self, locator, text: str):
        opts = [o.strip() for o in locator.locator("option").all_text_contents()]
        match = next((o for o in opts if o.lower() == text.lower()), None) or next((o for o in opts if text.lower() in o.lower()), None)
        if match is None:
            return False
        locator.select_option(label=match)
        return True

    def _set_ym(self, form, cls_y: str, cls_m: str, ym: str):
        y, m = ym.split("-")
        form.locator(f"select.{cls_y}, select[aria-label=Year]").nth(0 if cls_y == "sy" else 1).select_option(value=y)
        msel = form.locator(f"select.{cls_m}, select[aria-label=Month]").nth(0 if cls_m == "sm" else 1)
        if not self._set_select_by_text(msel, MONTHS[int(m) - 1]):
            msel.select_option(value=str(int(m)))

    def step_review(self):
        c = self.profile["contact"]
        for sel, val in [("#city", c["city"]), ("#hp", c["phone"]), ("input[aria-label='City']", c["city"]), ("input[aria-label='Home Phone']", c["phone"])]:
            loc = self.page.locator(sel)
            if loc.count():
                loc.first.fill(val)
        st = self.page.locator("#state, select[aria-label='State']")
        if st.count():
            st.first.select_option(value=c["state"])
        # education entries: correct dates for known degrees, delete anything that is not a degree in profile
        known = {e["school"].lower(): e for e in self.profile["education"]}
        for entry in self.page.locator(".entry").all():
            head = entry.locator("b").first.text_content() or ""
            school = head.split(" - ")[0].strip().lower()
            if school in known:
                e = known[school]
                entry.locator("button[data-edit], button[aria-label^='Edit']").first.click()
                form = entry.locator("[id$='-form']").first
                self._set_ym(form, "sy", "sm", e["start"])
                self._set_ym(form, "ey", "em", e["end"])
                form.locator("button[data-done], button:has-text('Done')").first.click()
            else:
                entry.locator("button[data-delete], button[aria-label^='Delete']").first.click()
                yes = self.page.get_by_role("button", name=re.compile(r"^Yes$", re.I))
                if yes.count():
                    yes.first.click()
        self.page.wait_for_timeout(200)
        nxt = self.page.get_by_role("button", name=re.compile(r"^Next$", re.I))
        if nxt.count():
            nxt.first.click()
            self.page.wait_for_timeout(300)

    def step_prequal(self):
        agree = self.page.locator("#agree, input[type=checkbox]").first
        if agree.count() and not agree.is_checked():
            agree.check()
        groups = self.page.evaluate(r"""() => [...document.querySelectorAll('.pq, fieldset, .radio-group')].map((g,i) => {
            g.dataset.jwpq = 'pq'+i; const lbl = (g.querySelector('p, legend, .label') || {}).textContent || '';
            return {label: lbl.replace(/\s+/g,' ').trim(), selector: `[data-jwpq="pq${i}"]`, options: [...g.querySelectorAll('input[type=radio]')].map(r => (r.closest('label')||{}).textContent?.trim() || r.value)}; })
            .filter(g => g.options.length)""")
        for g in groups:
            a = self.answer_for(g["label"])
            if not a:
                self.mark_unmapped(g["label"])
                continue
            if a.kind == "yesno":
                want = next((o for o in g["options"] if o.lower().startswith("yes" if a.answer else "no")), None)
            else:
                want = a.choose(g["options"])
            if want is None:
                self.mark_unmapped(g["label"])
                continue
            self.page.locator(f"{g['selector']} label", has_text=re.compile(rf"^\s*{re.escape(want)}\s*$")).first.click()
        self.page.get_by_role("button", name=re.compile(r"^Next$", re.I)).first.click()
        self.page.wait_for_timeout(300)

    def step_selects(self):
        """Steps 5 and 6 share the pattern: labelled <select>s and checkbox groups."""
        for sel in self.page.locator("select:visible").all():
            label = self.page.evaluate("el => { const l = el.closest('label') || (el.labels && el.labels[0]); return l ? l.textContent.replace(el.textContent, '') : (el.getAttribute('aria-label')||''); }", sel.element_handle())
            a = self.answer_for(label)
            if not a:
                if "*" in (label or ""):
                    self.mark_unmapped(label)
                continue
            opts = [o.strip() for o in sel.locator("option").all_text_contents()]
            choice = a.choose(opts) if a.kind in ("select", "checkbox") else next((o for o in opts if o.lower().startswith("yes" if a.answer else "no")), None)
            if choice:
                sel.select_option(label=choice)
            elif "*" in (label or ""):
                self.mark_unmapped(label)
        for ta in self.page.locator("textarea:visible").all():
            label = self.page.evaluate("el => { const l = el.closest('label'); return l ? l.textContent.replace(el.textContent, '') : (el.getAttribute('aria-label')||''); }", ta.element_handle())
            a = self.answer_for(label)
            if a and a.kind == "text":
                ta.fill(str(a.answer))
        for fs in self.page.locator("fieldset:visible").all():
            legend = fs.locator("legend").first.text_content() if fs.locator("legend").count() else ""
            a = self.answer_for(legend or "")
            if a and a.kind in ("select", "checkbox"):
                opts = [o.strip() for o in fs.locator("label").all_text_contents()]
                choice = a.choose(opts)
                if choice:
                    fs.locator("label", has_text=re.compile(rf"^\s*{re.escape(choice)}\s*$")).first.click()

    def fill(self) -> FillResult:
        if self.detect_captcha():
            return FillResult("captcha", proof_png=self.screenshot("captcha"))
        restriction = self.preflight()
        if restriction:
            return restriction
        self.step_agree()
        self.step_upload()
        self.step_review()
        restriction = self.preflight()
        if restriction:
            return restriction
        self.step_prequal()
        restriction = self.preflight()
        if restriction:
            return restriction
        if self.unmapped:
            return FillResult("needs_you", unmapped=list(self.unmapped), proof_png=self.screenshot("needs_you"))
        self.step_selects()                                   # step 5
        self.page.get_by_role("button", name=re.compile(r"^Next$", re.I)).first.click()
        self.page.wait_for_timeout(300)
        restriction = self.preflight()
        if restriction:
            return restriction
        if self.unmapped:
            return FillResult("needs_you", unmapped=list(self.unmapped), proof_png=self.screenshot("needs_you"))
        self.step_selects()                                   # step 6 (self-ID, optional)
        self.page.get_by_role("button", name=re.compile(r"^Submit$", re.I)).first.click()   # -> final page
        self.page.wait_for_timeout(300)
        restriction = self.preflight()
        if restriction:
            return restriction
        final = self.page.get_by_role("button", name=re.compile(r"^Submit$", re.I)).first
        ok = self.submit_and_confirm(final, self.CONFIRM_TEXT)
        return FillResult("applied" if ok else "error", proof_png=self.screenshot("confirmation" if ok else "submit_failed"))
