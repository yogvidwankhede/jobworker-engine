"""Reply sweep: classify new mail, record replies/bounces/interviews, send follow-ups that are due.

Humans are never auto-answered. Follow-ups: +4 business days, then +10, max 3 touches per person,
only inside the send window, and never while cold email is paused.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from worker.config import Settings, parse_settings
from worker.local_time import now as local_now, today as local_today
from worker.outreach import smtp, templates
from worker.outreach.imap import Mail
from worker.pipeline import in_send_window
from worker.records.models import Contact, Job, Run

BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery|delivery status", re.I)
BOUNCE_SUBJ = re.compile(r"delivery status|undeliverable|delivery failure|failure notice|returned mail", re.I)
INTERVIEW = re.compile(r"interview|phone screen|schedule a (call|chat|time)|calendly|book a time|next steps? call|technical screen", re.I)


@dataclass
class SweepReport:
    replies: int = 0
    interviews: int = 0
    bounces: int = 0
    followups_sent: int = 0
    cold_email_paused: bool = False
    items: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"sweep: replies {self.replies}, interviews {self.interviews}, bounces {self.bounces}, "
                f"follow-ups {self.followups_sent}, cold email paused {self.cold_email_paused}")


def _addr(s: str) -> str:
    m = re.search(r"<([^>]+)>", s or "")
    return (m.group(1) if m else (s or "")).strip().lower()


def classify(m: Mail, contact_emails: set[str]) -> str:
    if BOUNCE_FROM.search(m.from_addr or "") or BOUNCE_SUBJ.search(m.subject or ""):
        return "bounce"
    if _addr(m.from_addr) in contact_emails:
        return "interview" if INTERVIEW.search((m.subject or "") + " " + (m.text or "")) else "reply"
    return "other"


def business_days_after(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def followups_due(jobs: list[Job], contacts: list[Contact], today: date) -> list[tuple[Job, Contact, int]]:
    """Jobs in 'Recruiter emailed' whose next follow-up date has arrived; returns (job, contact, touch_number)."""
    by_email = {c.email.lower(): c for c in contacts if c.email}
    out = []
    for j in jobs:
        if j.status != "Recruiter emailed" or not j.email_sent_date or not j.recruiter_email:
            continue
        c = by_email.get(j.recruiter_email.lower())
        if not c or (c.touch_count or 0) >= 3:
            continue
        sent = datetime.strptime(j.email_sent_date[:10], "%Y-%m-%d").date()
        first = business_days_after(sent, 4)
        second = business_days_after(first, 10)
        touch = (c.touch_count or 0) + 1
        due = first if touch == 2 else second if touch == 3 else None
        if due and today >= due:
            out.append((j, c, touch))
    return out


FOLLOWUP_HTML = ("<div>Hi {first},<br><br>Following up on my note about the {role} role at {company}. I'm still very interested, and happy to "
                 "share anything that would help. Would a 15-minute call this week or next work?<br><br>Best,<br>{signature_html}</div>")
FOLLOWUP_TEXT = ("Hi {first},\n\nFollowing up on my note about the {role} role at {company}. I'm still very interested, and happy to share "
                 "anything that would help. Would a 15-minute call this week or next work?\n\nBest,\n{signature_text}")


def sweep(cfg, store, settings: Settings, mails: list[Mail], now: datetime | None = None, send=smtp.send) -> SweepReport:
    now = now or local_now()
    rep = SweepReport(cold_email_paused=settings.cold_email_paused)
    contacts = store.get_contacts()
    jobs = store.get_jobs()
    emails = {c.email.lower() for c in contacts if c.email}
    by_email = {c.email.lower(): c for c in contacts if c.email}

    # 1. classify new mail
    for m in mails:
        kind = classify(m, emails)
        if kind == "bounce":
            hit = next((e for e in emails if e in (m.text or "").lower()), None)
            if hit and (by_email[hit].email_status or "") != "Bounced":
                rep.bounces += 1
                by_email[hit].email_status = "Bounced"
                if by_email[hit].id:
                    store.update_contact(by_email[hit].id, email_status="Bounced")
                rep.items.append(f"bounce: {hit}")
        elif kind in ("reply", "interview"):
            c = by_email[_addr(m.from_addr)]
            job = next((j for j in jobs if (j.recruiter_email or "").lower() == c.email.lower()), None)
            rep.replies += 1
            if kind == "interview":
                rep.interviews += 1
            if job and job.id:
                store.update_job(job.id, status="Interview" if kind == "interview" else "Replied",
                                 next_action=f"Reply to {c.name.split()[0] if c.name else 'contact'} (human) — Yogvid writes this",
                                 next_action_date=now.strftime("%Y-%m-%d"))
            rep.items.append(f"{kind}: {c.name} <{c.email}> — {m.subject}")

    # 2. bounce-rate gate (>10% of contacted -> pause)
    contacted = [c for c in contacts if c.last_contacted]
    if contacted:
        bounced = sum(1 for c in contacted if (c.email_status or "") == "Bounced")
        if bounced / max(len(contacted), 1) > 0.10 and not rep.cold_email_paused:
            rep.cold_email_paused = True
            store.set_setting("cold_email_paused", "true")
            rep.items.append(f"cold email paused: bounce rate {bounced}/{len(contacted)}")

    # 3. follow-ups (send window only, never when paused)
    if not rep.cold_email_paused and in_send_window(now, settings):
        sig_html, sig_text = templates.signature(cfg.data_dir) if cfg else ("", "")
        for job, c, touch in followups_due(jobs, contacts, now.date()):
            first = c.name.split()[0] if c.name else "there"
            try:
                msg_id = send(cfg.gmail_user if cfg else "", cfg.gmail_app_password if cfg else "", c.email,
                              f"Re: {job.role} at {job.company} — following up", FOLLOWUP_HTML.format(first=first, role=job.role, company=job.company, signature_html=sig_html),
                              FOLLOWUP_TEXT.format(first=first, role=job.role, company=job.company, signature_text=sig_text), in_reply_to=(job.gmail_ids or "").split()[0] if job.gmail_ids else None)
            except Exception as e:  # noqa: BLE001
                rep.errors.append(f"follow-up {job.company}: {type(e).__name__}: {e}")
                continue
            rep.followups_sent += 1
            store.update_contact(c.id, last_contacted=now.strftime("%Y-%m-%d"), touch_count=touch)
            nxt = business_days_after(now.date(), 10) if touch == 2 else None
            store.update_job(job.id, next_action_date=nxt.isoformat() if nxt else None,
                             next_action=f"Follow-up #{touch} sent {now:%Y-%m-%d}" + (" — final touch" if touch == 3 else ""),
                             gmail_ids=f"{job.gmail_ids or ''} {msg_id}".strip())
            rep.items.append(f"follow-up #{touch}: {c.name} @ {job.company}")

    store.write_run(Run(run_id=f"{now:%Y-%m-%d-%H%M}-sweep", run_type="Reply sweep", started=now.isoformat(),
                        errors=rep.errors, notes=rep.summary() + "\n" + "\n".join(rep.items)))
    return rep


def run(cfg) -> SweepReport:
    from datetime import timedelta as _td

    from worker.outreach.imap import search_since
    from worker.records.airtable import AirtableStore

    store = AirtableStore(cfg)
    settings = parse_settings(store.settings())
    mails = search_since(cfg.gmail_user, cfg.gmail_app_password, local_today() - _td(days=1))
    rep = sweep(cfg, store, settings, mails)
    try:  # recruiter emails queued by `worker mark-applied` before the send window opened
        from worker.handoff_done import send_pending

        n = send_pending(cfg, store=store)
        if n and hasattr(rep, "notes"):
            rep.notes = (getattr(rep, "notes", "") + f" pending recruiter emails sent: {n}").strip()
    except Exception:  # noqa: BLE001
        pass
    return rep
