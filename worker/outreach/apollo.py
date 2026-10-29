"""Apollo people search: one verified recruiter/TA contact per company domain."""
from __future__ import annotations

import httpx

from worker.records.models import Contact

RECRUITER_WORDS = ["recruit", "talent", "people ops", "people operations", "sourcer", "hiring"]
HM_WORDS = ["engineering manager", "head of ai", "head of engineering", "director of engineering", "ai lead", "ml lead"]


def _search(api_key: str, domain: str) -> list[dict]:
    r = httpx.post("https://api.apollo.io/api/v1/mixed_people/search",
                   headers={"x-api-key": api_key, "Content-Type": "application/json"},
                   json={"q_organization_domains_list": [domain], "person_titles": ["recruiter", "technical recruiter", "talent acquisition", "talent partner", "engineering manager"],
                         "contact_email_status": ["verified"], "per_page": 10}, timeout=30)
    r.raise_for_status()
    return r.json().get("people", [])


def _rank(p: dict) -> int:
    t = (p.get("title") or "").lower()
    if any(w in t for w in RECRUITER_WORDS):
        return 0
    if any(w in t for w in HM_WORDS):
        return 1
    return 2


def find_contact(api_key: str, domain: str) -> Contact | None:
    people = [p for p in _search(api_key, domain) if (p.get("email_status") or "").lower() == "verified" and p.get("email")]
    if not people:
        return None
    p = sorted(people, key=_rank)[0]
    if _rank(p) == 2:
        return None  # only recruiters/TA or hiring managers, never random engineers
    return Contact(name=f"{p.get('first_name','')} {p.get('last_name','')}".strip(), company="", title=p.get("title"),
                   email=p["email"], email_status="Verified", linkedin=p.get("linkedin_url"))


def credits_left(api_key: str) -> int | None:
    try:
        r = httpx.get("https://api.apollo.io/api/v1/usage_stats/api_usage_stats", headers={"x-api-key": api_key}, timeout=15)
        data = r.json()
        for k, v in data.items():
            if "credit" in k.lower() and isinstance(v, dict) and "left" in v:
                return int(v["left"])
    except Exception:  # noqa: BLE001
        return None
    return None
