from worker.models import Posting
from worker.sourcing._html import to_text


def url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def parse(co: dict, data: list) -> list[Posting]:
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        if (j.get("workplaceType") or "").lower() == "remote" and "remote" not in loc.lower():
            loc = f"Remote{(' - ' + loc) if loc else ''}"
        description = [j.get('descriptionPlain') or to_text(j.get('description'))]
        description.extend(str(section.get('text', '')) + '\n' + to_text(section.get('content')) for section in j.get('lists', []))
        description.append(j.get('additionalPlain') or to_text(j.get('additional')))
        salary = j.get('salaryDescriptionPlain') or to_text(j.get('salaryDescription'))
        if not salary and j.get('salaryRange'):
            span = j['salaryRange']
            salary = f"{span.get('currency', 'USD')} {span.get('min', '')}–{span.get('max', '')} per {span.get('interval', 'year')}"
        out.append(Posting(company=co["name"], tier=co["tier"], title=j.get("text", ""), url=j.get("hostedUrl", ""),
                           apply_url=j.get("applyUrl") or j.get("hostedUrl", ""), location=loc,
                           jd_text='\n'.join(part for part in description if part), ats="lever", salary=salary or None,
                           employment_type=cats.get('commitment') or ''))
    return out
