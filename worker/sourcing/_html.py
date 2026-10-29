import html
import re


def to_text(s: str | None) -> str:
    """Strip HTML (possibly entity-escaped, as Greenhouse returns) to plain text."""
    if not s:
        return ""
    t = html.unescape(html.unescape(s))
    t = re.sub(r"<(br|/p|/li|/h\d|/div)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()
