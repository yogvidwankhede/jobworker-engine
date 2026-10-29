"""Outreach rules: one email per company per 30 days; max 3 touches per person."""
from __future__ import annotations

from datetime import date, datetime

from worker.records.models import Contact
from worker.local_time import today as local_today


def _d(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def may_email_company(contacts: list[Contact], today: date | None = None) -> bool:
    today = today or local_today()
    for c in contacts:
        lc = _d(c.last_contacted)
        if lc and (today - lc).days < 30:
            return False
    return True


def may_touch(contact: Contact) -> bool:
    return (contact.touch_count or 0) < 3
