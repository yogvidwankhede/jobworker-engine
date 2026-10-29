"""The captcha detector must ignore the invisible widgets every Greenhouse/Ashby form embeds, and flag a shown challenge."""
from worker.apply import detect

INVISIBLE = ("<html><body><form><input name='first_name'></form>"
             "<script src='https://www.google.com/recaptcha/api.js'></script>"
             "<iframe src='https://www.google.com/recaptcha/api2/anchor?size=invisible' style='width:0;height:0;position:absolute'></iframe>"
             "<div class='h-captcha' data-size='invisible'></div></body></html>")
SHOWN = ("<html><body><p>Please verify you are human to continue.</p>"
         "<iframe src='https://www.google.com/recaptcha/api2/anchor?k=x' style='width:304px;height:78px'></iframe></body></html>")


def test_invisible_widgets_are_not_a_captcha(page):
    page.set_content(INVISIBLE)
    assert detect.visible_captcha(page) is False


def test_shown_challenge_is_a_captcha(page):
    page.set_content(SHOWN)
    assert detect.visible_captcha(page) is True


def test_real_fixtures_are_not_flagged(page):
    from tests.fixtures.gh_form import build as gh_build
    from tests.fixtures.ashby_form import build as ashby_build
    for html in (gh_build(), ashby_build()):
        page.set_content(html)
        assert detect.visible_captcha(page) is False
