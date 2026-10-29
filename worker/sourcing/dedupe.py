from worker.models import Posting
from worker.records.airtable import canonical_url, normalize_title

TERMINAL = {"Applied", "Submitting", "Recruiter emailed", "Needs you", "Replied", "Interview", "Rejected", "Excluded", "Skipped", "Error"}


def retryable_error(job) -> bool:
    # The legacy Mac worker sometimes failed before it could even launch a browser.
    return job.status == "Error" and "BrowserType.launch" in (job.notes or "") and "Executable doesn't exist" in (job.notes or "")


def newly_supported_portal(job) -> bool:
    # This exact exception is raised before opening a form, so it cannot have submitted.
    # Re-enter sourcing/scoring with the live full posting, rather than trusting the old score.
    if job.status != 'Needs you' or job.questions_for_me != 'Unsupported application portal — apply by hand':
        return False
    from worker.apply.detect import filler_for
    return bool(job.apply_url and filler_for(job.apply_url) is not None)


def key(p: Posting) -> str:
    return f"{p.company.strip().lower()}|{normalize_title(p.title)}|{canonical_url(p.apply_url)}"


def filter_new(postings: list[Posting], store, retryable=None) -> list[Posting]:
    """Drop postings already in the tracker (by URL or company+title) and duplicates within the batch."""
    seen_urls, seen_ct = set(), set()
    for j in store.get_jobs():
        if j.status not in TERMINAL or retryable_error(j) or newly_supported_portal(j) or (retryable and retryable(j)):
            continue
        seen_urls.add(canonical_url(j.apply_url))
        seen_ct.add((j.company.strip().lower(), normalize_title(j.role)))
    out, batch = [], set()
    for p in postings:
        k = key(p)
        if k in batch or canonical_url(p.apply_url) in seen_urls or (p.company.strip().lower(), normalize_title(p.title)) in seen_ct:
            continue
        batch.add(k)
        out.append(p)
    return out
