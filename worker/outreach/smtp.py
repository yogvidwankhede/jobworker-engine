"""Send via Gmail SMTP with the App Password; returns the Message-ID."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid


def send(user: str, app_password: str, to: str, subject: str, html: str, text: str, in_reply_to: str | None = None) -> str:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, app_password)
        s.send_message(msg)
    return msg["Message-ID"]
