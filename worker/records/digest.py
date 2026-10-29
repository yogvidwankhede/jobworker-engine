"""The 08:00 digest: one email with applied / emailed / needs-you / skipped / errors / credits / shortfall."""
from __future__ import annotations

import html as h

from worker.outreach import smtp
from worker.pipeline import RunReport


def render(rep: RunReport) -> tuple[str, str, str]:
    # `emailed` is always 0 from a run itself now - every "Applied" row is queued with a delay and sent later
    # by send_pending, never inline here - so the subject reports how many are queued instead of "emailed 0"
    # every time, which would read as if outreach had stopped working.
    subject = (f"Job worker {rep.date}: applied {rep.applied} (recruiter emails queued), needs you {rep.needs_you}"
              + (" [DRY RUN]" if rep.dry_run else ""))
    parts, lines = [], []

    def section(title, items, fmt_html, fmt_text):
        if not items:
            return
        parts.append(f"<h3>{h.escape(title)} ({len(items)})</h3><ul>" + "".join(f"<li>{fmt_html(i)}</li>" for i in items) + "</ul>")
        lines.append(f"{title} ({len(items)})")
        lines.extend(f"  - {fmt_text(i)}" for i in items)

    section("Needs you", rep.needs_you_items,
            lambda i: f"<a href=\"{h.escape(i['url'])}\">{h.escape(i['company'])} — {h.escape(i['title'])}</a>: {h.escape(i['questions'])}",
            lambda i: f"{i['company']} — {i['title']}: {i['questions']} ({i['url']})")
    section("Selected", rep.selected_items,
            lambda i: f"<a href=\"{h.escape(i['url'])}\">{h.escape(i['company'])} — {h.escape(i['title'])}</a> [{h.escape(i['tier'])}] "
                      f"<b>{i['score']}</b> [{h.escape(i.get('family', ''))}] — {h.escape(str(i['reason']))} <i>({h.escape(i['resume'])})</i>",
            lambda i: f"{i['company']} — {i['title']} [{i['tier']}] {i['score']} [{i.get('family', '')}] — {i['reason']} ({i['resume']}) {i['url']}")
    section("Applied", rep.applied_items,
            lambda i: f"<a href=\"{h.escape(i['url'])}\">{h.escape(i['company'])} — {h.escape(i['title'])}</a>",
            lambda i: f"{i['company']} — {i['title']} ({i['url']})")
    section("Recruiter emailed", rep.emailed_items, lambda i: f"{h.escape(i['company'])} — {h.escape(i['title'])}", lambda i: f"{i['company']} — {i['title']}")
    section("Skipped", rep.skipped, lambda i: f"{h.escape(i[0])}: {h.escape(i[1])}", lambda i: f"{i[0]}: {i[1]}")
    section("Errors", rep.errors, lambda i: h.escape(i), lambda i: i)
    short = {k: v for k, v in rep.shortfall.items() if v}
    if short:
        s = ", ".join(f"{k}: {v}" for k, v in short.items())
        parts.append(f"<p><b>Shortfall</b> vs caps — {h.escape(s)}</p>")
        lines.append(f"Shortfall vs caps — {s}")
    credits = "unknown" if rep.apollo_credits_left is None else str(rep.apollo_credits_left)
    footer = f"Sourced {rep.sourced}, scored {rep.scored}, selected {rep.selected}, queued {rep.queued}. Apollo credits left: {credits}."
    if rep.applied and not rep.dry_run:
        footer += f" Recruiter emails for the {rep.applied} applied today are queued, not sent yet - they go out after a short delay, inside the send window."
    parts.append(f"<p>{h.escape(footer)}</p>")
    lines.append(footer)
    html_body = f"<div><h2>{h.escape(subject)}</h2>{''.join(parts)}</div>"
    return subject, html_body, "\n".join([subject, ""] + lines)


def send(cfg, rep: RunReport) -> str | None:
    subject, html_body, text = render(rep)
    if rep.dry_run:
        print(text)
        return None
    return smtp.send(cfg.gmail_user, cfg.gmail_app_password, cfg.gmail_user, subject, html_body, text)
