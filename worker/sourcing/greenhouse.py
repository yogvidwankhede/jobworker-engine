from worker.models import Posting
from worker.sourcing._html import to_text


def url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def canonical_apply_url(slug: str, job_id) -> str:
    """The hosted Greenhouse application form, whatever domain the company embeds it on (careers.roblox.com?gh_jid=...).
    The embed form works even where the company's own board page sits behind Cloudflare (Pinterest) or refuses direct
    board URLs (Roblox); Databricks was applied to through this same form on 2026-09-27."""
    return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"


def parse(co: dict, data: dict) -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        link = j.get("absolute_url") or ""
        apply = canonical_apply_url(co["slug"], j["id"]) if j.get("id") and co.get("slug") else link
        employment = next((m.get('value') for m in (j.get('metadata') or [])
                           if (m.get('name') or '').lower() in {'employment type', 'employment_type', 'job type'}), '')
        out.append(Posting(company=co["name"], tier=co["tier"], title=j.get("title", ""), url=link, apply_url=apply,
                           location=(j.get("location") or {}).get("name", "") or "", jd_text=to_text(j.get("content")),
                           ats="greenhouse", employment_type=employment if isinstance(employment, str) else ''))
    return out
