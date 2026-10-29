"""Friday weekly review: counts by tier/status, reply and bounce rates, parked questions, suggestions."""
from __future__ import annotations

from collections import Counter
from worker.local_time import today as local_today

from worker.records.models import Contact, Job


def build_review(jobs: list[Job], contacts: list[Contact], week_ending: str) -> str:
    by_tier = Counter((j.tier or "unknown") for j in jobs if j.status in ("Applied", "Recruiter emailed", "Replied", "Interview", "Needs you"))
    by_status = Counter(j.status for j in jobs)
    emailed = [j for j in jobs if j.status in ("Recruiter emailed", "Replied", "Interview")]
    replied = [j for j in jobs if j.status in ("Replied", "Interview")]
    contacted = [c for c in contacts if c.last_contacted]
    bounced = [c for c in contacted if (c.email_status or "") == "Bounced"]
    parked = [(j.company, j.role, j.questions_for_me) for j in jobs if j.status == "Needs you"]
    lines = [f"Weekly review — week ending {week_ending}", "",
             "Applications by tier: " + ", ".join(f"{t}: {n}" for t, n in sorted(by_tier.items())),
             "By status: " + ", ".join(f"{s}: {n}" for s, n in sorted(by_status.items())),
             f"Replies: {len(replied)} of {len(emailed)} emailed" + (f" ({100*len(replied)//max(len(emailed),1)}%)" if emailed else ""),
             f"Bounces: {len(bounced)} of {len(contacted)} contacted"]
    if parked:
        lines += ["", f"Parked questions ({len(parked)}):"] + [f"  - {c} — {r}: {q}" for c, r, q in parked]
    sugg = []
    if by_tier.get("big", 0) < 20:
        sugg.append("Big-tech tier is thin: most big-tech portals (Workday etc.) have no filler yet; consider adding board slugs or applying by hand from the digest.")
    if emailed and len(replied) / len(emailed) < 0.05:
        sugg.append("Reply rate under 5%: try a different hook sentence per resume, or target hiring managers over recruiters.")
    if parked:
        sugg.append("Add the parked questions' answers to answers.yaml so they stop parking.")
    if sugg:
        lines += ["", "Suggestions:"] + [f"  - {s}" for s in sugg]
    return "\n".join(lines)


def run(cfg) -> str:
    from worker.outreach import smtp
    from worker.records.airtable import AirtableStore

    store = AirtableStore(cfg)
    text = build_review(store.get_jobs(), store.get_contacts(), local_today().isoformat())
    smtp.send(cfg.gmail_user, cfg.gmail_app_password, cfg.gmail_user, f"Weekly job-search review — {local_today().isoformat()}",
              "<pre>" + text + "</pre>", text)
    return text
