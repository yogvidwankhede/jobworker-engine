"""Close out a hand-off application once its Chrome submit showed the board's confirmation.

Called by the hand-off Claude session as `worker mark-applied <job_id>`, one call per confirmed submit.
Sets the row Applied and queues the recruiter email - it is never sent from here, and never immediately,
even if the send window happens to be open right then. The email always waits at least `email_delay_minutes`
(default 30) after the row went Applied, so a submit and its recruiter email never land back to back looking
automated. `send_pending` (run by the twice-daily sweep, and by `worker send-pending`) sends it once both that
delay and the send window (09:00-12:00 by default) have passed. Same caps and pauses as the daily run; one
email per company per 30 days.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from worker.config import Config, parse_settings
from worker.local_time import now as local_now
from worker.models import Posting
from worker.pipeline import _plus_bd, in_send_window
from worker.records.airtable import AirtableStore

PENDING_PREFIX = "Recruiter email pending @ "


def queue_marker(now: datetime) -> str:
    """The `next_action` value that marks a row as queued for a delayed recruiter email, timestamped so
    `send_pending` knows how long it has been waiting."""
    return f"{PENDING_PREFIX}{now.isoformat()}"


def _queued_at(next_action: str | None) -> datetime | None:
    if not next_action or not next_action.startswith(PENDING_PREFIX):
        return None
    try:
        return datetime.fromisoformat(next_action[len(PENDING_PREFIX):].strip())
    except ValueError:
        return None


def _default_outreach(cfg):
    from worker.outreach.stage import outreach as _outreach
    from worker.sourcing import load_companies
    from worker.tailor.pick_resume import keyword_pick

    companies = load_companies(cfg.data_dir)
    # The lightweight cloud email workflow needs neither a local model nor the Claude CLI.
    return lambda p, st: _outreach(p, keyword_pick(p), cfg, st, companies)


def _posting(job) -> Posting:
    return Posting(company=job.company, tier=job.tier or "startup", title=job.role, url=job.source_url or job.apply_url,
                   apply_url=job.apply_url, location=job.location or "", ats=job.source or "unknown")


def _email(job, store, settings, outreach, now: datetime) -> str:
    today = now.date().isoformat()
    emails_today = sum(1 for c in store.get_contacts() if (c.last_contacted or "") == today)
    if settings.cold_email_paused or emails_today >= settings.daily_email_cap:
        return "held: cap/pause"
    try:
        msg_id = outreach(_posting(job), store)
    except Exception as e:  # noqa: BLE001
        return f"failed: {type(e).__name__}"
    if not msg_id:
        store.update_job(job.id, next_action="")
        return "none: no verified recruiter / already contacted"
    store.update_job(job.id, status="Recruiter emailed", email_sent_date=today,
                     next_action_date=_plus_bd(now, 4).strftime("%Y-%m-%d"), next_action="Follow-up #1 if no reply (+4 bd)")
    return "sent"


def mark_applied(cfg: Config, job_id: str, store=None, outreach=None, now: datetime | None = None) -> str:
    """Called once per confirmed Chrome submit. Always queues the recruiter email for later - never sends it here,
    so closing out a submission is never itself the thing that fires the email."""
    store = store or AirtableStore(cfg)
    now = now or local_now()
    job = next((j for j in store.get_jobs() if j.id == job_id), None)
    if job is None:
        return f"no job {job_id}"
    if job.status in ("Applied", "Recruiter emailed", "Replied", "Interview"):
        return f"{job.company} — {job.role}: already {job.status}"
    store.update_job(job_id, status="Applied", applied_date=now.date().isoformat(), next_action=queue_marker(now))
    return f"{job.company} — {job.role}: Applied (recruiter email queued, sends after a short delay)"


def send_pending(cfg: Config, store=None, outreach=None, now: datetime | None = None) -> int:
    """Send queued post-submit recruiter emails once both `email_delay_minutes` has elapsed since they were
    queued and the send window is open. -> number sent."""
    store = store or AirtableStore(cfg)
    now = now or local_now()
    settings = parse_settings(store.settings())
    if not in_send_window(now, settings):
        return 0
    outreach = outreach or _default_outreach(cfg)
    delay = timedelta(minutes=settings.email_delay_minutes)
    sent = 0
    for job in [j for j in store.get_jobs() if j.status == "Applied"]:
        queued_at = _queued_at(job.next_action)
        if queued_at is None or now - queued_at < delay:
            continue
        if _email(job, store, settings, outreach, now) == "sent":
            sent += 1
    return sent
