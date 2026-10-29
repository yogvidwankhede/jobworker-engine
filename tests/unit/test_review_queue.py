from types import SimpleNamespace as NS

from worker.config import Settings
from worker.models import Posting, Scored
from worker.pipeline import _park_for_review


class Store:
    def __init__(self):
        self.rows = []

    def upsert_job(self, job):
        self.rows.append(job)
        return job


def sc(company, score, rejected=False, url="https://x/1"):
    p = Posting(company=company, tier="startup", title="ML Engineer", url=url, apply_url=url, location="Remote", ats="ashby", salary=None)
    return Scored(posting=p, score=score, reason="r", rejected=rejected)


def run(scored, chosen=(), waiting=0, **kw):
    s = Settings(**kw)
    st = Store()
    all_jobs = [NS(status="Scored")] * waiting
    rep = NS(date="2026-10-29", errors=[])
    n = _park_for_review(scored, list(chosen), s, lambda p: None, st, rep, all_jobs)
    return n, st.rows


def test_parks_mid_fit_only():
    n, rows = run([sc("A", 60, url="u1"), sc("B", 40, url="u2"), sc("C", 90, url="u3"), sc("D", 65, True, "u4")], chosen=[])
    assert {r.company for r in rows} == {"A", "C"} and n == 2
    assert all(r.status == "Scored" for r in rows)


def test_skips_chosen_and_respects_cap():
    a, b, c = sc("A", 80, url="u1"), sc("B", 70, url="u2"), sc("C", 60, url="u3")
    n, rows = run([a, b, c], chosen=[a], waiting=149, review_queue_max=150)
    assert n == 1 and rows[0].company == "B"
