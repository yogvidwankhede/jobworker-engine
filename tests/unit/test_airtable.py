from worker.records.airtable import AirtableStore, F_JOBS, normalize_title, canonical_url
from worker.records.models import Job, Run


class FakeTable:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.updates = []
        self.created = []

    def all(self, **kw):
        # pyairtable returns field NAMES unless asked for IDs; the store must ask, or every read comes back empty.
        assert kw.get("use_field_ids") is True, "store must read fields by ID"
        return self.rows

    def update(self, rid, fields, typecast=False):
        self.updates.append((rid, fields))
        return {"id": rid, "fields": fields}

    def create(self, fields, typecast=False):
        rec = {"id": f"rec{len(self.created)}", "fields": fields}
        self.created.append(rec)
        return rec


def make_store(jobs_rows=None, settings_rows=None):
    s = AirtableStore.__new__(AirtableStore)
    s.jobs = FakeTable(jobs_rows)
    s.contacts = FakeTable()
    s.settings_t = FakeTable(settings_rows)
    s.runs = FakeTable()
    return s


def row(company, role, url, status="Applied"):
    return {"id": "recX", "fields": {F_JOBS["company"]: company, F_JOBS["role"]: role,
                                     F_JOBS["apply_url"]: url, F_JOBS["status"]: status}}


def test_normalize_title_collapses_case_and_punctuation():
    assert normalize_title("AI Engineer II (AI Platform)") == normalize_title("ai engineer ii ai platform")


def test_find_job_matches_on_normalized_title():
    s = make_store([row("Stripe", "AI Engineer", "https://job-boards.greenhouse.io/stripe/jobs/8044460?src=x")])
    j = s.find_job("stripe", "ai engineer", "https://job-boards.greenhouse.io/stripe/jobs/8044460")
    assert j is not None and j.status == "Applied"
    assert s.find_job("Stripe", "Data Scientist", "https://other") is None


def test_greenhouse_job_identity_distinguishes_embedded_forms():
    old = 'https://job-boards.greenhouse.io/embed/job_app?for=intercom&token=8185627'
    new = 'https://job-boards.greenhouse.io/embed/job_app?for=vestmark&token=8009953'
    assert canonical_url(old) != canonical_url(new)
    assert canonical_url(old) != canonical_url(old.replace('8185627', '8185628'))
    assert canonical_url(old) != canonical_url(old.replace('intercom', 'anotherboard'))


def test_greenhouse_identity_matches_direct_and_legacy_links_without_tracking():
    embedded = 'https://job-boards.greenhouse.io/embed/job_app?gh_src=tracking&token=8009953&for=vestmark'
    direct = 'https://job-boards.greenhouse.io/vestmark/jobs/8009953/?utm_source=tracking'
    legacy = 'https://boards.greenhouse.io/vestmark/jobs/8009953#apply'
    assert canonical_url(embedded) == canonical_url(direct) == canonical_url(legacy)


def test_greenhouse_identity_keeps_regional_boards_separate():
    us = 'https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1'
    eu = 'https://job-boards.eu.greenhouse.io/embed/job_app?for=acme&token=1'
    assert canonical_url(us) != canonical_url(eu)
    assert canonical_url(eu) == canonical_url('https://boards.eu.greenhouse.io/acme/jobs/1')


def test_find_job_does_not_match_other_greenhouse_employers_but_matches_same_job():
    old = 'https://job-boards.greenhouse.io/embed/job_app?for=intercom&token=8185627'
    s = make_store([row('Intercom', 'Forward Deployed Data Scientist', old)])
    assert s.find_job('Vestmark', 'Software Engineer',
                      'https://job-boards.greenhouse.io/embed/job_app?for=vestmark&token=8009953') is None
    assert s.find_job('Intercom', 'Renamed role',
                      'https://boards.greenhouse.io/intercom/jobs/8185627') is not None


def test_update_job_uses_field_ids_not_names():
    s = make_store()
    s.update_job("recX", status="Applied", applied_date="2026-09-28")
    rid, fields = s.updates_of_jobs()[0]
    assert rid == "recX"
    assert set(fields) == {F_JOBS["status"], F_JOBS["applied_date"]}
    assert "Status" not in fields


def test_write_run_serializes_counts():
    s = make_store()
    s.write_run(Run(run_id="2026-09-28-run", run_type="Morning run", mode="Live",
                    sourced=120, scored=80, selected=30, applied=28, emailed=15, needs_you=2,
                    errors=["x"], notes="ok"))
    fields = s.runs.created[0]["fields"]
    assert fields[s.F_RUNS["run_id"]] == "2026-09-28-run"
    assert fields[s.F_RUNS["errors"]] == "x"


def test_settings_reads_key_value_rows():
    s = make_store(settings_rows=[{"id": "r", "fields": {"fldRc5FI7rj4AtId4": "fit_threshold", "fldY426c5TBGivqkA": "75"}}])
    assert s.settings() == {"fit_threshold": "75"}


def test_get_jobs_skips_blank_rows():
    s = make_store([{"id": "recBlank", "fields": {}}, row("Stripe", "AI Engineer", "https://x/1")])
    jobs = s.get_jobs()
    assert [j.company for j in jobs] == ["Stripe"]
