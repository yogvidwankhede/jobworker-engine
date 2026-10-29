"""Typed mirrors of the Airtable tracker rows."""
from __future__ import annotations

from pydantic import BaseModel


class Job(BaseModel):
    id: str | None = None
    company: str
    role: str
    tier: str | None = None
    source_url: str | None = None
    apply_url: str | None = None
    source: str | None = None
    date_found: str | None = None
    fit_score: int | None = None
    fit_reasons: str | None = None
    status: str = "New"
    resume_link: str | None = None
    recruiter_name: str | None = None
    recruiter_title: str | None = None
    recruiter_email: str | None = None
    hook: str | None = None
    gmail_ids: str | None = None
    email_sent_date: str | None = None
    applied_date: str | None = None
    next_action_date: str | None = None
    next_action: str | None = None
    questions_for_me: str | None = None
    salary_listed: str | None = None
    location: str | None = None
    notes: str | None = None


class Contact(BaseModel):
    id: str | None = None
    name: str
    company: str
    title: str | None = None
    email: str
    email_status: str | None = None
    linkedin: str | None = None
    last_contacted: str | None = None
    touch_count: int = 0
    notes: str | None = None


class Run(BaseModel):
    run_id: str
    run_type: str
    mode: str = "Live"
    started: str | None = None
    sourced: int = 0
    scored: int = 0
    selected: int = 0
    applied: int = 0
    emailed: int = 0
    needs_you: int = 0
    errors: list[str] = []
    notes: str = ""
