from worker.outreach import apollo


def test_apollo_skips_unverified_and_prefers_recruiters(monkeypatch):
    people = [
        {"first_name": "A", "last_name": "One", "title": "Software Engineer", "email": "a@x.com", "email_status": "verified", "linkedin_url": "l1"},
        {"first_name": "B", "last_name": "Two", "title": "Senior Technical Recruiter", "email": "b@x.com", "email_status": "unverified", "linkedin_url": "l2"},
        {"first_name": "C", "last_name": "Three", "title": "Talent Acquisition Partner", "email": "c@x.com", "email_status": "verified", "linkedin_url": "l3"},
    ]
    monkeypatch.setattr(apollo, "_search", lambda key, domain: people)
    c = apollo.find_contact("key", "x.com")
    assert c.name == "C Three" and c.email == "c@x.com" and c.email_status == "Verified"


def test_apollo_returns_none_when_no_verified(monkeypatch):
    monkeypatch.setattr(apollo, "_search", lambda key, domain: [{"first_name": "B", "last_name": "Two", "title": "Recruiter", "email": "b@x.com", "email_status": "unverified"}])
    assert apollo.find_contact("key", "x.com") is None


def test_one_contact_per_company_enforced_by_store():
    from datetime import date
    from worker.outreach.policy import may_email_company
    from worker.records.models import Contact
    recent = Contact(name="R", company="X", email="r@x.com", last_contacted="2026-09-20", touch_count=1)
    assert may_email_company([recent], today=date(2026, 9, 28)) is False
    old = Contact(name="R", company="X", email="r@x.com", last_contacted="2026-08-01", touch_count=1)
    assert may_email_company([old], today=date(2026, 9, 28)) is True
    assert may_email_company([], today=date(2026, 9, 28)) is True
