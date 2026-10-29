"""Map an apply URL to a filler class, and spot bot checks."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Text that only appears when a challenge is actually being shown to the visitor. Script/iframe presence alone is NOT
# a challenge: Greenhouse and Ashby embed invisible reCAPTCHA/hCaptcha on every form.
CAPTCHA_TEXT = [r"verify you are human", r"are you a robot", r"i'?m not a robot", r"checking your browser", r"just a moment",
                r"complete the (security )?(check|challenge)", r"please verify", r"select all (images|squares)", r"press and hold"]
# Visible challenge widgets (Playwright checks visibility + non-trivial size).
CAPTCHA_WIDGETS = ['iframe[src*="recaptcha/api2/anchor"]:not([src*="size=invisible"])', 'iframe[src*="recaptcha/api2/bframe"]',
                   'iframe[src*="hcaptcha.com"]:not([src*="invisible"])', 'iframe[src*="challenges.cloudflare.com"]',
                   'div.cf-turnstile', '#challenge-form', '#challenge-running', 'iframe[title*="challenge" i]', 'div[id^="px-captcha"]',
                   'iframe[src*="geo.captcha-delivery.com"]']
# Kept for callers that only have HTML (e.g. the Cloudflare interstitial): script names are deliberately NOT here.
CAPTCHA_MARKERS = [r"cf-challenge", r"challenge-platform", r"px-captcha", r"datadome", r"geo\.captcha-delivery\.com"] + CAPTCHA_TEXT


def filler_for(url: str):
    host = urlparse(url).netloc.lower()
    from worker.apply.ashby import AshbyFiller
    from worker.apply.greenhouse import GreenhouseFiller
    from worker.apply.talemetry import TalemetryFiller
    from worker.apply.lever import LeverFiller

    if host.endswith("greenhouse.io"):
        return GreenhouseFiller
    if host.endswith("ashbyhq.com"):
        return AshbyFiller
    if host.endswith("talemetry.com"):
        return TalemetryFiller
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return LeverFiller
    return None


def looks_like_captcha(html: str) -> bool:
    """HTML-only heuristic: an interstitial/challenge page, not the mere presence of a captcha script."""
    h = html.lower()
    return any(re.search(m, h) for m in CAPTCHA_MARKERS)


def visible_captcha(page) -> bool:
    """Playwright check: a challenge widget is on screen, or challenge text is in the rendered body."""
    try:
        for sel in CAPTCHA_WIDGETS:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                if el.is_visible():
                    box = el.bounding_box()
                    if box and box["width"] > 40 and box["height"] > 40:
                        return True
        text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 20000)").lower()
        return any(re.search(m, text) for m in CAPTCHA_TEXT)
    except Exception:  # noqa: BLE001
        return False
