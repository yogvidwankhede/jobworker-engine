from worker.models import Posting


def url(slug: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def parse(co: dict, data: dict) -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"Remote{(' - ' + loc) if loc else ''}"
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append(Posting(company=co["name"], tier=co["tier"], title=j.get("title", ""), url=j.get("jobUrl", ""),
                           apply_url=j.get("applyUrl") or j.get("jobUrl", ""), location=loc,
                           jd_text=j.get("descriptionPlain") or "", ats="ashby", salary=comp,
                           employment_type=j.get('employmentType') or ''))
    return out
