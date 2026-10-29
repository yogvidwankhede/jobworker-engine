"""Pipeline outreach stage: find one verified contact, send the templated email, label it, record the contact."""
from __future__ import annotations

from worker.local_time import today as local_today

from worker.outreach import apollo, imap, policy, smtp, templates
from worker.records.models import Contact


def company_domain(p, companies: list[dict]) -> str | None:
    for c in companies:
        if c["name"].lower() == p.company.lower():
            return c.get("domain")
    return None


def outreach(p, resume_key: str, cfg, store, companies: list[dict], send=smtp.send, label=imap.label_by_message_id) -> str | None:
    """-> Message-ID when an email was sent, else None. Never sends twice to a company within 30 days."""
    existing = store.get_contacts(p.company)
    if not policy.may_email_company(existing):
        return None
    contact = next((c for c in existing if c.email_status == "Verified" and policy.may_touch(c)), None)
    if contact is None:
        if not cfg.apollo_api_key:
            return None
        domain = company_domain(p, companies)
        if not domain:
            return None
        found = apollo.find_contact(cfg.apollo_api_key, domain)
        if found is None:
            return None
        contact = store.upsert_contact(found.model_copy(update={"company": p.company}))
    first = contact.name.split()[0] if contact.name else "there"
    subject, html, text = templates.render(p.tier, dict(first_name=first, company=p.company, role=p.title, resume_key=resume_key), cfg.data_dir)
    msg_id = send(cfg.gmail_user, cfg.gmail_app_password, contact.email, subject, html, text)
    try:
        label(cfg.gmail_user, cfg.gmail_app_password, msg_id, "Sent")
        label(cfg.gmail_user, cfg.gmail_app_password, msg_id, "Job Search")
    except Exception:  # noqa: BLE001
        pass
    today = local_today().isoformat()
    store.update_contact(contact.id, last_contacted=today, touch_count=(contact.touch_count or 0) + 1)
    if hasattr(store, "note_email"):
        store.note_email(p, contact, msg_id)
    return msg_id
