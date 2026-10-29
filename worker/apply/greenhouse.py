"""Greenhouse job-boards form: labelled controls, React-select comboboxes, file inputs, '*' required markers."""
import re

from worker.apply.base import FillResult, LabelledFormFiller


class GreenhouseFiller(LabelledFormFiller):
    CONFIRM_TEXT = ["Thank you for applying", "application has been received"]
    SUBMIT_SELECTOR = "button[type=submit]:has-text('Submit'), button:has-text('Submit application')"

    def fill(self):
        result = super().fill()
        if result.status == 'error':
            text = self.page.locator('body').inner_text()
            if (re.search(r'confirm you(?: are|\u2019re|\x27re) (?:a )?human', text, re.I)
                    and re.search(r'security code|verification code', text, re.I)):
                return FillResult('human_verification', proof_png=result.proof_png,
                                  note='Employer requires an emailed code to confirm a human; application not confirmed')
        return result
