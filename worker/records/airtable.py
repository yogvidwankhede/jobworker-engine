"""The only writer to Airtable. Field names -> field IDs live here and nowhere else."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pyairtable import Api

from worker.config import Config
from worker.records.models import Contact, Job, Run

T_JOBS, T_CONTACTS, T_SETTINGS, T_RUNS = "tblovSc8jNq9tYUer", "tblGbyAZH8ivjOma6", "tbl0xz5t3TRbK8HIR", "tblFt2Mjq6lkWGjV7"

F_JOBS = {
    "company": "fldEICGK1eYk9XqCk", "role": "fldD0Y0tsgyOFbhCh", "tier": "fld8TFhbrnG7inZdq",
    "source_url": "fldyWOk7DiCupwwZA", "apply_url": "fldk91QtvCrpYw4zm", "source": "fldqMO5Z3Dhl5sIsZ",
    "date_found": "fld3ycSHkoDzlagbf", "fit_score": "fld4aS2JTkrdzLBwm", "fit_reasons": "fldTB1qHeI4sicDNr",
    "status": "fldsiYAoov5ojXhkk", "resume_link": "fldu5TAVPbhSBRoQZ", "cover_note": "fldmNNJ6YFgoCgXhG",
    "recruiter_name": "fldJheO0FubSZ0CDE", "recruiter_title": "fldjub2psNZZQfskV", "recruiter_email": "fldzJghclcpIjqtlr",
    "hook": "fldfZG4j3BZVwhQsi", "gmail_ids": "fldurD1kq6ue3OXTw", "email_sent_date": "fldw9mOR9vEA46zGZ",
    "applied_date": "fld2zRCazyIjxRGXy", "next_action_date": "fldChEaetY55hSSuI", "next_action": "fldrmCDH2hxIJtC0F",
    "questions_for_me": "fldgMXgYSssxkT4nH", "salary_listed": "fldKnBfYc0eN8tJiY", "location": "fldJkHm2bEpp9MADx",
    "notes": "fldJBfWaDHQ2MPVTC",
}
F_CONTACTS = {
    "name": "fldX31eAv5cvUUddt", "company": "flddKxh36iIscuHxl", "title": "fld6e5up9OnuBDNyD",
    "email": "fldbNybtvtwBwVfUX", "email_status": "fldU0lYU33H2FVixo", "linkedin": "fldfYrvBcJLm2yTaM",
    "last_contacted": "fldCbPMNONp1MOydW", "touch_count": "fldg1t3EVDdEUgXyU", "notes": "fldg3H3UvfFKcSNfE",
}
F_SETTINGS = {"key": "fldRc5FI7rj4AtId4", "value": "fldY426c5TBGivqkA"}
F_RUNS = {
    "run_id": "fld9Kz9vGRU34UBac", "run_type": "fldHE20atbVBzhRdZ", "started": "fld4RhBDSjQxCGUMB",
    "mode": "fldYbBeCRYLxV8wRQ", "errors": "fldEWclgCj5jfBkzY", "notes": "fldWHFthWwATMsUDT",
}


def normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def canonical_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlsplit(u.strip())
    greenhouse_hosts = {
        'boards.greenhouse.io': 'job-boards.greenhouse.io',
        'job-boards.greenhouse.io': 'job-boards.greenhouse.io',
        'boards.eu.greenhouse.io': 'job-boards.eu.greenhouse.io',
        'job-boards.eu.greenhouse.io': 'job-boards.eu.greenhouse.io',
    }
    host = greenhouse_hosts.get(p.netloc.lower())
    if host:
        # Embedded forms share a path; the board and token identify the job.
        # Use the same identity as its direct posting, regardless of tracking links.
        board, token = None, None
        if p.path.rstrip('/') == '/embed/job_app':
            query = dict(parse_qsl(p.query))
            board, token = query.get('for'), query.get('token')
        elif match := re.fullmatch(r'/([A-Za-z0-9_-]+)/jobs/(\d+)', p.path.rstrip('/')):
            board, token = match.groups()
        if board and token and re.fullmatch(r'[A-Za-z0-9_-]+', board) and token.isdigit():
            return urlunsplit(('https', host, f'/{board.lower()}/jobs/{token}', '', ''))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def _val(v):
    return v.get("name") if isinstance(v, dict) and "name" in v else v


def _from_fields(model, fmap, rec):
    f = rec.get("fields", {})
    data = {k: _val(f.get(fid)) for k, fid in fmap.items() if fid in f}
    data["id"] = rec.get("id")
    return model(**data)


def _to_fields(fmap, **kw):
    out = {}
    for k, v in kw.items():
        if k not in fmap:
            raise KeyError(f"unknown field {k}")
        out[fmap[k]] = v
    return out


class AirtableStore:
    F_JOBS, F_CONTACTS, F_SETTINGS, F_RUNS = F_JOBS, F_CONTACTS, F_SETTINGS, F_RUNS

    def __init__(self, cfg: Config):
        api = Api(cfg.airtable_token)
        base = cfg.airtable_base_id
        if not base:
            raise RuntimeError("AIRTABLE_BASE_ID is not set")
        self.jobs = api.table(base, T_JOBS)
        self.contacts = api.table(base, T_CONTACTS)
        self.settings_t = api.table(base, T_SETTINGS)
        self.runs = api.table(base, T_RUNS)

    # ---- jobs
    def get_jobs(self, status: str | None = None) -> list[Job]:
        jobs = [_from_fields(Job, F_JOBS, r) for r in self.jobs.all(use_field_ids=True)
                if r.get("fields", {}).get(F_JOBS["company"]) and r.get("fields", {}).get(F_JOBS["role"])]
        return [j for j in jobs if status is None or j.status == status]

    def find_job(self, company: str, title: str, apply_url: str | None) -> Job | None:
        c, t, u = company.strip().lower(), normalize_title(title), canonical_url(apply_url)
        for j in self.get_jobs():
            if u and canonical_url(j.apply_url) == u:
                return j
            if j.company.strip().lower() == c and normalize_title(j.role) == t:
                return j
        return None

    def upsert_job(self, job: Job) -> Job:
        fields = _to_fields(F_JOBS, **{k: v for k, v in job.model_dump().items() if k != "id" and v is not None})
        if job.id:
            self.jobs.update(job.id, fields, typecast=True)
            return job
        rec = self.jobs.create(fields, typecast=True)
        return job.model_copy(update={"id": rec["id"]})

    def update_job(self, job_id: str, **fields):
        self.jobs.update(job_id, _to_fields(F_JOBS, **fields), typecast=True)

    def note_email(self, job: Job, contact: Contact, message_id: str):
        """Record who was emailed and the Gmail Message-ID so the sweep can follow up and match replies."""
        prev = (job.gmail_ids or "").strip()
        self.update_job(job.id, recruiter_name=contact.name, recruiter_title=contact.title or "", recruiter_email=contact.email,
                        gmail_ids=f"{prev} {message_id}".strip())

    def updates_of_jobs(self):  # test helper on the fake table
        return getattr(self.jobs, "updates", [])

    # ---- contacts
    def get_contacts(self, company: str | None = None) -> list[Contact]:
        cs = [_from_fields(Contact, F_CONTACTS, r) for r in self.contacts.all(use_field_ids=True)
              if r.get("fields", {}).get(F_CONTACTS["name"]) and r.get("fields", {}).get(F_CONTACTS["email"])]
        return [c for c in cs if company is None or c.company.strip().lower() == company.strip().lower()]

    def upsert_contact(self, contact: Contact) -> Contact:
        fields = _to_fields(F_CONTACTS, **{k: v for k, v in contact.model_dump().items() if k != "id" and v is not None})
        if contact.id:
            self.contacts.update(contact.id, fields, typecast=True)
            return contact
        rec = self.contacts.create(fields, typecast=True)
        return contact.model_copy(update={"id": rec["id"]})

    def update_contact(self, contact_id: str, **fields):
        self.contacts.update(contact_id, _to_fields(F_CONTACTS, **fields), typecast=True)

    # ---- settings / runs
    def settings(self) -> dict[str, str]:
        out = {}
        for r in self.settings_t.all(use_field_ids=True):
            f = r.get("fields", {})
            if f.get(F_SETTINGS["key"]):
                out[str(f[F_SETTINGS["key"]]).strip()] = str(f.get(F_SETTINGS["value"], "")).strip()
        return out

    def set_setting(self, key: str, value: str):
        for r in self.settings_t.all(use_field_ids=True):
            if r.get("fields", {}).get(F_SETTINGS["key"]) == key:
                self.settings_t.update(r["id"], {F_SETTINGS["value"]: value})
                return
        self.settings_t.create({F_SETTINGS["key"]: key, F_SETTINGS["value"]: value})

    def write_run(self, run: Run):
        counts = f"sourced={run.sourced} scored={run.scored} selected={run.selected} applied={run.applied} emailed={run.emailed} needs_you={run.needs_you}"
        self.runs.create(_to_fields(F_RUNS, run_id=run.run_id, run_type=run.run_type, mode=run.mode,
                                    started=run.started, errors="\n".join(run.errors),
                                    notes=f"{counts}\n{run.notes}".strip()), typecast=True)
