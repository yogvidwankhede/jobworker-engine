"""Daily pipeline: source -> dedupe -> score -> select -> tailor -> apply -> outreach -> record -> digest.

Stages are injected so tests can stub them. Airtable is the idempotency lock:
a Job with Status Applied/Recruiter emailed/Needs you is never re-applied.
"""
from __future__ import annotations

import re

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Any

from worker.config import Settings
from worker.local_time import now as local_now
from worker.models import Posting, Scored
from worker.scoring import rules
from worker.sourcing.dedupe import retryable_error, newly_supported_portal
from worker.records.models import Job, Run

TERMINAL = {"Applied", "Submitting", "Recruiter emailed", "Needs you", "Ready to submit", "Replied", "Interview", "Rejected", "Excluded", "Skipped", "Error"}
TIER_ORDER = ["big", "mid", "startup"]


@dataclass
class RunReport:
    date: str = ""
    sourced: int = 0
    scored: int = 0
    selected: int = 0
    applied: int = 0
    emailed: int = 0
    needs_you: int = 0
    to_submit: int = 0                                           # optional preview mode only
    to_submit_items: list[dict[str, Any]] = field(default_factory=list)
    queued: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    needs_you_items: list[dict[str, Any]] = field(default_factory=list)
    applied_items: list[dict[str, Any]] = field(default_factory=list)
    emailed_items: list[dict[str, Any]] = field(default_factory=list)
    selected_items: list[dict[str, Any]] = field(default_factory=list)
    shortfall: dict[str, int] = field(default_factory=dict)
    apollo_credits_left: int | None = None
    dry_run: bool = False

    def summary(self) -> str:
        return (f"{'DRY RUN ' if self.dry_run else ''}{self.date}: sourced {self.sourced}, scored {self.scored}, "
                f"selected {self.selected}, applied {self.applied}, ready to submit {self.to_submit}, emailed {self.emailed}, needs you {self.needs_you}, "
                f"queued {self.queued}, skipped {len(self.skipped)}, errors {len(self.errors)}")


@dataclass
class Stages:
    sourcer: Callable[[Any, Any], list[Posting]]
    scorer: Callable[[Posting, Settings], Scored]
    tailor: Callable[[Posting, Any], tuple[str, str]]              # -> (resume_key, pdf_path)
    applier: Callable[[Posting, str, Any], tuple[str, list[str], str | None]]  # -> (status, unmapped, proof)
    outreach: Callable[[Posting, str, Any, Any], str | None]        # -> gmail message id or None
    digest: Callable[[RunReport, Any], None]
    batch_scorer: Callable[[list[Posting], Settings], list[Scored]] | None = None
    # lowercased company name -> the employer's own stated application-count limit. Either a plain int (a true
    # lifetime cap, never resets) or {"max": int, "window_days": int} for a limit stated as "N per M days" (Google's
    # "3 in a rolling 30 days", OpenAI's "5 in any 180 day span"). Populated from companies.yaml's `max_applications`
    # / `application_window_days` fields by worker/stages.py - any company can get an entry there once it has a
    # confirmed limit; most have none and are uncapped by default.
    company_caps: dict[str, int | dict[str, int]] = field(default_factory=dict)
    retryable: Callable[[Job], bool] | None = None
    answer_revision: str = ""
    generated_answers: Any = None
    on_select: Callable[[list[Scored]], None] | None = None
    daily_company_counts: dict[str, int] = field(default_factory=dict)
    daily_reserved_urls: set[str] = field(default_factory=set)
    resume_reserved_retries: bool = False


def in_send_window(now: datetime, s: Settings) -> bool:
    if now.weekday() not in s.send_days:
        return False
    start, end = s.send_window
    hhmm = now.strftime("%H:%M")
    return start <= hhmm < end


def _cap_parts(info: int | dict[str, int] | None) -> tuple[int | None, int | None]:
    """A `company_caps` entry is a plain int (lifetime cap) or {"max", "window_days"} (a rolling-window cap) ->
    (max, window_days), window_days None for a lifetime cap or a missing entry."""
    if info is None:
        return None, None
    if isinstance(info, dict):
        return info.get("max"), info.get("window_days")
    return info, None


def _within_window(applied_date: str | None, now: datetime, window_days: int) -> bool:
    """True if `applied_date` (YYYY-MM-DD, or missing/unparsable - not yet applied, so it's "now") is still inside
    the employer's rolling window."""
    if not applied_date:
        return True
    try:
        d = datetime.strptime(applied_date, "%Y-%m-%d")
    except ValueError:
        return True
    return d >= now - timedelta(days=window_days)


def _title_key(title: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title.lower())
    t = re.split(r"\s+[-–|,]\s+", t)[0]
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _company_key(company: str) -> str:
    key = company.strip().lower()
    return 'google / deepmind' if key in {'google', 'deepmind', 'google deepmind', 'google / deepmind'} else key


def _counts_as_submission(job: Job) -> bool:
    return (job.status in {'Applied', 'Submitting', 'Recruiter emailed', 'Replied', 'Interview'} or
            (job.status == 'Rejected' and bool(job.applied_date)) or
            (job.status == 'Needs you' and any(term in (job.questions_for_me or '').lower()
             for term in ('submit did not confirm', 'submission interrupted', 'verify the outcome', 'employer rejected', 'human verification'))))


def company_cooldown_until(jobs, company, now, settings, exclude_id=None):
    if not settings.company_cooldown_days:
        return None
    until = None
    for prior in jobs:
        if prior.id == exclude_id or _company_key(prior.company) != _company_key(company) or not _counts_as_submission(prior):
            continue
        values = re.findall(r'company_contact_date=(\d{4}-\d{2}-\d{2})', prior.notes or '')
        if prior.applied_date:
            values.append(prior.applied_date[:10])
        dates = []
        for value in values:
            try:
                dates.append(datetime.strptime(value, '%Y-%m-%d').date())
            except ValueError:
                pass
        if not dates:
            # Undated legacy attempts could have sent an application. Hold
            # them from a fixed activation date, never a moving daily date.
            fallback = settings.company_cooldown_started_on or prior.date_found or now.strftime('%Y-%m-%d')
            try:
                dates.append(datetime.strptime(fallback[:10], '%Y-%m-%d').date())
            except ValueError:
                dates.append(now.date())
        candidate = max(dates) + timedelta(days=settings.company_cooldown_days)
        if candidate > now.date() and (until is None or candidate > until):
            until = candidate
    return until.isoformat() if until else None


def _normalized_counts(counts: dict[str, int]) -> dict[str, int]:
    out = {}
    for name, count in counts.items():
        key = _company_key(name)
        out[key] = out.get(key, 0) + count
    return out


def select_by_tier(scored: list[Scored], s: Settings, company_caps: dict[str, int] | None = None,
                   applied_counts: dict[str, int] | None = None,
                   daily_company_counts: dict[str, int] | None = None) -> tuple[list[Scored], dict[str, int]]:
    """Fill each tier's cap by (title family, score); at most `max_per_company` per company and one per (company, title).
    If the tiers together fall short of `total_target`, top up from the remaining qualifying postings of any tier.
    `company_caps` (lowercased company -> lifetime limit, e.g. Google: 3, OpenAI: 5) is an employer-stated ceiling on
    total applications ever, never just this run: `applied_counts` (from the tracker) plus what this run is about to
    pick are never allowed past it, even though `max_per_company` alone would allow more per run."""
    chosen, shortfall = [], {}
    per_company: dict[str, int] = {}
    daily_company_counts = _normalized_counts(daily_company_counts or {})
    seen_titles: set[tuple[str, str]] = set()
    chosen_ids: set[int] = set()
    company_caps = {_company_key(k): v for k, v in (company_caps or {}).items()}
    applied_counts = _normalized_counts(applied_counts or {})
    target = max(0, s.total_target)
    max_per_company = min(s.max_per_company, 1) if s.company_cooldown_days else s.max_per_company

    def eligible(x: Scored) -> bool:
        return not x.rejected and x.score >= s.fit_threshold and id(x) not in chosen_ids

    def under_employer_cap(company_key: str) -> bool:
        cap = company_caps.get(company_key)
        return cap is None or applied_counts.get(company_key, 0) + per_company.get(company_key, 0) < cap

    def take(pool: list[Scored], cap: int) -> int:
        taken = 0
        for x in pool:
            if taken >= cap or len(chosen) >= target:
                break
            key = (_company_key(x.posting.company), _title_key(x.posting.title))
            if key in seen_titles or per_company.get(key[0], 0) + daily_company_counts.get(key[0], 0) >= max_per_company:
                continue
            if not under_employer_cap(key[0]):
                continue
            seen_titles.add(key)
            per_company[key[0]] = per_company.get(key[0], 0) + 1
            chosen.append(x)
            chosen_ids.add(id(x))
            taken += 1
        return taken

    order = lambda x: (rules.title_family(x.posting)[0], -x.score)  # noqa: E731
    for tier in TIER_ORDER:
        cap = s.caps.get(tier, 0)
        pool = sorted([x for x in scored if x.posting.tier == tier and eligible(x)], key=order)
        taken = take(pool, cap)
        shortfall[tier] = max(0, cap - taken)
    target = getattr(s, "total_target", sum(s.caps.values()))
    if len(chosen) < target:
        rest = sorted([x for x in scored if eligible(x)], key=order)
        take(rest, target - len(chosen))
    return chosen, shortfall


def run_pipeline(cfg, store, settings: Settings, stages: Stages, now: datetime | None = None,
                 dry_run: bool = False, wait_for_window: bool = False,
                 max_duration_seconds: int | None = None) -> RunReport:
    deadline = time.monotonic() + max_duration_seconds if max_duration_seconds is not None else None
    fixed_now = now is not None and not wait_for_window
    now = now or local_now()
    rep = RunReport(date=now.strftime("%Y-%m-%d"), dry_run=dry_run)
    run = Run(run_id=f"{rep.date}-{now.strftime('%H%M%S')}-run{'-dry' if dry_run else ''}", run_type="Morning run", mode="Dry run" if dry_run else "Live",
              started=now.isoformat())
    all_jobs = store.get_jobs()                      # one read; indexed below instead of per-posting scans
    raw_caps = {_company_key(k): v for k, v in (stages.company_caps or {}).items()}
    company_caps = {k: _cap_parts(v)[0] for k, v in raw_caps.items()}
    applied_counts = {}
    for job in all_jobs:
        key = _company_key(job.company)
        if key not in raw_caps or not (_counts_as_submission(job) or job.status in {'Ready', 'Ready to submit'}):
            continue
        _, days = _cap_parts(raw_caps[key])
        if not days or _within_window(job.applied_date, now, days):
            applied_counts[key] = applied_counts.get(key, 0) + 1
    daily_counts = _normalized_counts(stages.daily_company_counts)
    by_url = {_cu(j.apply_url): j for j in all_jobs if j.apply_url}
    by_ct = {(j.company.strip().lower(), _nt(j.role)): j for j in all_jobs}

    def lookup(p: Posting) -> Job | None:
        return by_url.get(_cu(p.apply_url)) or by_ct.get((p.company.strip().lower(), _nt(p.title)))

    # 0. Resume already-selected queued work, including legacy previews, without another approval.
    # Newly resolved Needs-you forms go through current sourcing and scoring below.
    ready: list[tuple[Job, Posting]] = []
    if not dry_run:
        for j in all_jobs:
            if j.apply_url and (j.status == "Ready" or (settings.auto_submit and j.status == "Ready to submit")):
                ready.append((j, Posting(company=j.company, tier=j.tier or "startup", title=j.role, url=j.source_url or j.apply_url,
                                         apply_url=j.apply_url, location=j.location or "", ats=j.source or "unknown",
                                         salary=j.salary_listed)))

    # 1-2. source + dedupe against the tracker (skipped when the run is only meant to flush Ready rows)
    ready_only = getattr(settings, "total_target", 1) == 0 and not any(settings.caps.values())
    try:
        postings = [] if ready_only and not stages.resume_reserved_retries else stages.sourcer(cfg, store)
    except Exception as e:  # noqa: BLE001
        postings = []
        rep.errors.append(f"sourcing: {type(e).__name__}: {e}")
    rep.sourced = len(postings)
    fresh: list[Posting] = []
    for p in postings:
        existing = lookup(p)
        retry = settings.auto_submit and existing and stages.retryable and stages.retryable(existing)
        reserved_retry = bool(retry and _cu(p.apply_url) in stages.daily_reserved_urls)
        if ready_only and not reserved_retry:
            continue
        if existing and existing.status in TERMINAL and not (retryable_error(existing) or newly_supported_portal(existing) or retry):
            rep.skipped.append((f"{p.company} — {p.title}", "already in tracker"))
            continue
        if existing and existing.status == "Ready":
            continue  # handled via the ready list
        key = _company_key(p.company)
        cooldown = company_cooldown_until(all_jobs, p.company, now, settings)
        if cooldown:
            rep.skipped.append((f'{p.company} — {p.title}', f'company cooldown until {cooldown}'))
            continue
        if settings.company_cooldown_days and any(_company_key(j.company) == key for j, _ in ready):
            rep.skipped.append((f'{p.company} — {p.title}', 'company already has a queued application'))
            continue
        if key in company_caps and applied_counts.get(key, 0) >= company_caps[key]:
            _, days = _cap_parts(raw_caps[key])
            window = f' in the last {days}d' if days else ''
            rep.skipped.append((f'{p.company} — {p.title}',
                                f'employer application limit reached ({applied_counts.get(key, 0)}/{company_caps[key]}{window})'))
            continue
        if daily_counts.get(key, 0) >= settings.max_per_company and not reserved_retry:
            rep.skipped.append((f'{p.company} — {p.title}', 'daily company allowance used; defer new scoring'))
            continue
        fresh.append(p)

    # 3. score (batch scorer if provided, else per-posting)
    scored: list[Scored] = []
    if stages.batch_scorer is not None:
        try:
            scored = stages.batch_scorer(fresh, settings)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"scoring: {type(e).__name__}: {e}")
    else:
        for p in fresh:
            try:
                scored.append(stages.scorer(p, settings))
            except Exception as e:  # noqa: BLE001
                rep.errors.append(f"scoring {p.company}: {type(e).__name__}: {e}")
    for sc in scored:
        if sc.rejected:
            rep.skipped.append((f"{sc.posting.company} — {sc.posting.title}", sc.reason))
    rep.scored = len(scored)

    # A selected form stopped before Submit can resume its existing daily slot.
    # Re-score the live posting first; neither an old fit score nor a changed
    # answer file permits retrying a rejection or an uncertain submission.
    resumed = []
    if not dry_run:
        for sc in scored:
            job = lookup(sc.posting)
            if (job and _cu(sc.posting.apply_url) in stages.daily_reserved_urls and stages.retryable
                    and stages.retryable(job) and not sc.rejected and sc.score >= settings.fit_threshold):
                ready.append((job, sc.posting))
                resumed.append(sc)
                store.update_job(job.id, fit_score=sc.score, fit_reasons=sc.reason)
                rep.selected_items.append({'company': sc.posting.company, 'title': sc.posting.title,
                                           'tier': sc.posting.tier, 'score': sc.score,
                                           'reason': sc.reason, 'url': sc.posting.apply_url,
                                           'resume': job.resume_link, 'resumed': True})
        scored = [sc for sc in scored if sc not in resumed]

    # 4. select (ready rows count against the caps first)
    for tier in TIER_ORDER:
        settings_cap = settings.caps.get(tier, 0)
        settings.caps[tier] = max(0, settings_cap - sum(1 for j, _ in ready if (j.tier or "startup") == tier))
    settings.total_target = max(0, settings.total_target - len(ready))
    # employers that cap total applications (companies.yaml `max_applications`, e.g. Google: 3/30d, OpenAI: 5/180d)
    # are tracked off the tracker itself, so the count survives across every run, not just this one. A cap with a
    # `window_days` only counts applications still inside that rolling window (Job.applied_date vs `now`); a plain
    # int is a true lifetime cap that never resets.
    chosen, rep.shortfall = select_by_tier(scored, settings, company_caps=company_caps, applied_counts=applied_counts,
                                        daily_company_counts=stages.daily_company_counts)
    if chosen and not dry_run and stages.on_select:
        # Persist the daily allowance before tailoring or opening a submission browser.
        stages.on_select(chosen)
    for x in scored:
        if x not in chosen and not x.rejected:
            key = _company_key(x.posting.company)
            cap = company_caps.get(key)
            if cap is not None and applied_counts.get(key, 0) >= cap:
                _, window_days = _cap_parts(raw_caps.get(key))
                window_note = f" in the last {window_days}d" if window_days else ""
                reason = f"employer application limit reached ({applied_counts.get(key, 0)}/{cap}{window_note})"
            else:
                reason = f"below cap/threshold (score {x.score})"
            rep.skipped.append((f"{x.posting.company} — {x.posting.title}", reason))
    rep.selected = len(chosen) + len(ready)

    # 5. tailor everything now (06:00), before waiting for the send window
    work: list[tuple[Job | None, Posting, str | None, str | None]] = [(j, p, None, j.resume_link) for j, p in ready]
    for x in chosen:
        p = x.posting
        try:
            key, pdf = stages.tailor(p, cfg)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"tailor {p.company}: {type(e).__name__}: {e}")
            continue
        job = None
        if not dry_run:
            job = lookup(p) or store.upsert_job(Job(
                company=p.company, role=p.title, tier=p.tier, source_url=p.url, apply_url=p.apply_url,
                source=p.ats, date_found=rep.date, fit_score=x.score, fit_reasons=x.reason, status="Scored",
                location=p.location, salary_listed=p.salary))
            if job and lookup(p):
                store.update_job(job.id, fit_score=x.score, fit_reasons=x.reason, location=p.location, salary_listed=p.salary)
        work.append((job, p, key, pdf))
        rep.selected_items.append({"company": p.company, "title": p.title, "tier": p.tier, "score": x.score, "family": rules.title_family(p)[1],
                                   "reason": x.reason, "url": p.apply_url, "resume": key})

    if dry_run:
        rep.queued = len(work)
        store.write_run(run.model_copy(update=dict(sourced=rep.sourced, scored=rep.scored, selected=rep.selected, notes="dry run: nothing written")))
        _digest(stages, rep, cfg)
        return rep

    # 6-7. apply + outreach. Classic mode: only inside the send window. Hand-off mode (prefill_anytime): forms are
    # filled right away on send days. Either way, a recruiter email is never sent from this loop: an "Applied" row
    # is always queued with a timestamp and picked up later by `send_pending`, once both the send window and the
    # `email_delay_minutes` buffer have passed — so outreach never looks like it fired the instant a form went in.
    anytime = getattr(settings, "prefill_anytime", False)
    if wait_for_window and not anytime and not in_send_window(now, settings):
        now = _sleep_until_window(now, settings)
    for job, p, key, pdf in work:
        t = now if fixed_now else local_now()
        contact_note = f'company_contact_date={t.strftime("%Y-%m-%d")}'
        live = (t.weekday() in settings.send_days) if anytime else in_send_window(t, settings)
        if not live or (deadline is not None and time.monotonic() >= deadline):
            store.update_job(job.id, status="Ready", resume_link=pdf,
                             notes="queued for next cloud cycle" if live else "queued for send window")
            rep.queued += 1
            continue
        try:
            company_key = _company_key(p.company)
            history = store.get_jobs() if settings.company_cooldown_days or company_key in raw_caps else all_jobs
            cooldown = company_cooldown_until(history, p.company, t, settings, exclude_id=job.id)
            if cooldown:
                store.update_job(job.id, status='Ready', resume_link=pdf,
                                 next_action=f'Company cooldown until {cooldown}', next_action_date=cooldown)
                rep.queued += 1
                continue
            if company_key in raw_caps:
                # Queued and resolved forms must obey the same limits as new selections.
                # Re-read immediately before claiming, including uncertain prior outcomes.
                cap, window_days = _cap_parts(raw_caps[company_key])
                used = sum(1 for prior in history
                           if _company_key(prior.company) == company_key and _counts_as_submission(prior)
                           and (not window_days or _within_window(prior.applied_date, t, window_days)))
                if used >= cap:
                    reason = f'employer application limit reached ({used}/{cap})'
                    store.update_job(job.id, status='Skipped', next_action='', questions_for_me='', notes=reason)
                    rep.skipped.append((f'{p.company} — {p.title}', reason))
                    continue
            # Legacy Mac paths or missing runner files are rebuilt from the saved cloud source data.
            if cfg is not None and (not pdf or not Path(pdf).is_file()):
                key, pdf = stages.tailor(p, cfg)
            # Claim before any submit. Interrupted/uncertain attempts must never be blindly retried.
            store.update_job(job.id, status="Submitting", resume_link=pdf,
                             notes=contact_note, next_action="Cloud submission in progress", next_action_date=None)
            status, unmapped, proof = stages.applier(p, pdf, cfg)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"apply {p.company}: {type(e).__name__}: {e}")
            q = "Submission interrupted; verify the outcome before retrying"
            _safe(store.update_job, rep, job.id, status="Needs you", next_action="",
                  questions_for_me=q,
                  notes=f"apply failed: {type(e).__name__}: {e}; {contact_note}")
            rep.needs_you += 1
            rep.needs_you_items.append({"company": p.company, "title": p.title, "url": p.apply_url, "questions": q})
            continue
        if status == "applied":
            from worker.handoff_done import queue_marker

            _safe(store.update_job, rep, job.id, status="Applied", applied_date=rep.date, resume_link=pdf,
                  notes=f"proof: {proof}; {contact_note}" if proof else contact_note, next_action=queue_marker(t), next_action_date=None)
            rep.applied += 1
            rep.applied_items.append({"company": p.company, "title": p.title, "url": p.apply_url})
            continue  # the recruiter email is never sent from here — it always waits its delay, sent later by send_pending
        elif status == 'deferred':
            _safe(store.update_job, rep, job.id, status='Ready', resume_link=pdf,
                  notes='; '.join(unmapped), next_action='Retry unsent form in the next cloud cycle')
            rep.queued += 1
            continue
        elif status == 'blocked':
            q = 'Employer rejected the submission: ' + '; '.join(unmapped)
            _safe(store.update_job, rep, job.id, status='Needs you', questions_for_me=q,
                  resume_link=pdf, notes=f'proof: {proof}; {contact_note}', next_action='Saved in exception digest; continue other roles')
            rep.needs_you += 1
            rep.needs_you_items.append({'company': p.company, 'title': p.title, 'url': p.apply_url, 'questions': q})
            continue
        elif status == "prefilled":
            from worker.apply.handoff import save as _save_sheet
            try:
                sheet_path = str(_save_sheet(cfg, job.id, p, pdf, unmapped, day=rep.date))
            except Exception as e:  # noqa: BLE001
                sheet_path = ""
                rep.errors.append(f"handoff sheet {p.company}: {type(e).__name__}: {e}")
            _safe(store.update_job, rep, job.id, status="Ready to submit", resume_link=pdf,
                  notes=f"preview sheet: {sheet_path}", next_action="Preview mode; enable auto_submit to submit in the cloud")
            rep.to_submit += 1
            rep.to_submit_items.append({"company": p.company, "title": p.title, "url": p.apply_url})
            continue
        elif status == "needs_you":
            q = "; ".join(unmapped)
            _safe(store.update_job, rep, job.id, status="Needs you", questions_for_me=q, resume_link=pdf,
                  notes=f"answers={stages.answer_revision}; proof: {proof}", next_action="Missing saved answers")
            rep.needs_you += 1
            rep.needs_you_items.append({"company": p.company, "title": p.title, "url": p.apply_url, "questions": q})
            continue
        elif status == 'human_verification':
            q = 'Human verification required: enter the emailed security code in the application form by hand; submission not confirmed'
            _safe(store.update_job, rep, job.id, status='Needs you', questions_for_me=q, resume_link=pdf,
                  notes=f'proof: {proof}; {contact_note}', next_action='Employer human verification')
            rep.needs_you += 1
            rep.needs_you_items.append({'company':p.company, 'title':p.title, 'url':p.apply_url, 'questions':q})
            continue
        elif status in ("captcha", "no filler for this ATS", "error"):
            # a real fit the worker cannot submit itself → park for Yogvid with the link, never lose it as "Skipped"
            q = {"captcha": "CAPTCHA shown — apply by hand",
                 "no filler for this ATS": "Unsupported application portal — apply by hand"}.get(
                status, "Submit did not confirm — check the form and apply by hand" + (f" ({'; '.join(unmapped)})" if unmapped else ""))
            _safe(store.update_job, rep, job.id, status="Needs you", questions_for_me=q, resume_link=pdf,
                  notes=f"proof: {proof}; {contact_note}" if proof else contact_note, next_action="Portal exception")
            rep.needs_you += 1
            rep.needs_you_items.append({"company": p.company, "title": p.title, "url": p.apply_url, "questions": q})
            continue
        else:
            _safe(store.update_job, rep, job.id, status="Skipped", notes=f"apply: {status}", questions_for_me='', next_action='')
            rep.skipped.append((f"{p.company} — {p.title}", f"apply: {status}"))
            continue

    run.sourced, run.scored, run.selected = rep.sourced, rep.scored, rep.selected
    run.applied, run.emailed, run.needs_you, run.errors = rep.applied, rep.emailed, rep.needs_you, rep.errors
    run.notes = f"queued={rep.queued} skipped={len(rep.skipped)} shortfall={rep.shortfall}"
    _safe(store.write_run, rep, run)
    _digest(stages, rep, cfg)
    return rep


def _digest(stages, rep, cfg):
    try:
        stages.digest(rep, cfg)
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"digest: {type(e).__name__}: {e}")


def _safe(fn, rep, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"airtable: {type(e).__name__}: {e}")


def _cu(u):
    from worker.records.airtable import canonical_url
    return canonical_url(u)


def _nt(t):
    from worker.records.airtable import normalize_title
    return normalize_title(t or "")


def _plus_bd(d: datetime, n: int) -> datetime:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _sleep_until_window(now: datetime, s: Settings) -> datetime:
    start = datetime.strptime(s.send_window[0], "%H:%M").time()
    target = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if now < target and now.weekday() in s.send_days:
        while local_now() < target:
            time.sleep(min(60, max(1, (target - local_now()).total_seconds())))
        return local_now()
    return now


def run(cfg, dry_run: bool = False, send_window: str | None = None, ready_only: bool = False) -> RunReport:
    """Production entry: wires real stages. Imported lazily so tests never touch the network.
    `send_window` ("21:00-23:59") overrides the Airtable setting for this run only."""
    from worker.config import parse_settings
    from worker.records.airtable import AirtableStore
    from worker.stages import build_stages

    store = AirtableStore(cfg)
    settings = parse_settings(store.settings())
    if send_window:
        a, b = [x.strip() for x in send_window.split("-")]
        settings.send_window = (a, b)
    if ready_only:  # apply/email the rows already queued as Ready; source nothing new
        settings.caps = {k: 0 for k in settings.caps}
        settings.total_target = 0
    # Queue outside-window work instead of billing runner hours waiting for a local clock.
    return run_pipeline(cfg, store, settings, build_stages(cfg, settings), dry_run=dry_run)
