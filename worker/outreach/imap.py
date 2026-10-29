"""Gmail IMAP: label sent mail, search new mail since a date."""
from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass
from datetime import date
from email.header import decode_header, make_header


@dataclass
class Mail:
    uid: str
    message_id: str
    in_reply_to: str
    from_addr: str
    subject: str
    date: str
    text: str


def _conn(user: str, app_password: str) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    m.login(user, app_password)
    return m


def label_by_message_id(user: str, app_password: str, message_id: str, label: str, folder: str = '"[Gmail]/Sent Mail"') -> bool:
    m = _conn(user, app_password)
    try:
        m.select(folder)
        typ, data = m.uid("search", None, f'(HEADER Message-ID "{message_id}")')
        uids = data[0].split() if typ == "OK" else []
        if not uids:
            return False
        m.uid("store", uids[0], "+X-GM-LABELS", f'"{label}"')
        return True
    finally:
        m.logout()


def search_since(user: str, app_password: str, since: date, folder: str = "INBOX") -> list[Mail]:
    m = _conn(user, app_password)
    out: list[Mail] = []
    try:
        m.select(folder, readonly=True)
        typ, data = m.uid("search", None, f'(SINCE "{since.strftime("%d-%b-%Y")}")')
        for uid in (data[0].split() if typ == "OK" else []):
            typ, msgdata = m.uid("fetch", uid, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msgdata[0][1])
            text = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    text += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            out.append(Mail(uid=uid.decode(), message_id=msg.get("Message-ID", ""), in_reply_to=msg.get("In-Reply-To", ""),
                            from_addr=str(make_header(decode_header(msg.get("From", "")))),
                            subject=str(make_header(decode_header(msg.get("Subject", "")))), date=msg.get("Date", ""), text=text))
    finally:
        m.logout()
    return out
