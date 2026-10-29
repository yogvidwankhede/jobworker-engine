from worker.apply import detect
from worker.apply.ashby import AshbyFiller
from worker.apply.greenhouse import GreenhouseFiller
from worker.apply.talemetry import TalemetryFiller
from worker.apply.lever import LeverFiller


def test_detect_maps_known_hosts():
    assert detect.filler_for("https://job-boards.greenhouse.io/scaleai/jobs/4720573005") is GreenhouseFiller
    assert detect.filler_for("https://boards.greenhouse.io/embed/job_app?for=stripe&token=1") is GreenhouseFiller
    assert detect.filler_for("https://jobs.ashbyhq.com/langchain/ddf9/application") is AshbyFiller
    assert detect.filler_for("https://apply.talemetry.com/application/abc") is TalemetryFiller
    assert detect.filler_for("https://jobs.lever.co/tovala/abc/apply") is LeverFiller
    assert detect.filler_for("https://jobs.eu.lever.co/example/abc/apply") is LeverFiller


def test_detect_unknown_returns_none():
    assert detect.filler_for("https://not-lever.co/example/abc/apply") is None
    assert detect.filler_for("https://careers.google.com/jobs/1") is None


def test_detect_captcha_markers():
    assert detect.looks_like_captcha("<div class='cf-challenge'>Verify you are human</div>")
    assert detect.looks_like_captcha("<title>Just a moment...</title>")
    # the invisible reCAPTCHA/hCaptcha scripts Greenhouse and Ashby embed on every form are NOT a challenge
    assert not detect.looks_like_captcha("<script src='https://www.google.com/recaptcha/api.js'></script><iframe src='https://www.google.com/recaptcha/api2/anchor?size=invisible'>")
    assert not detect.looks_like_captcha("<div class='h-captcha' data-size='invisible'></div>")
    assert not detect.looks_like_captcha("<form><input name='first_name'></form>")
