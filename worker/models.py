"""Shared domain objects (Posting lives here so sourcing never imports scoring)."""
from __future__ import annotations

from pydantic import BaseModel


class Posting(BaseModel):
    company: str
    tier: str
    title: str
    url: str
    apply_url: str
    location: str = ""
    jd_text: str = ""
    ats: str = "unknown"
    salary: str | None = None
    employment_type: str = ''


class Scored(BaseModel):
    posting: Posting
    score: int
    reason: str
    rejected: bool = False
