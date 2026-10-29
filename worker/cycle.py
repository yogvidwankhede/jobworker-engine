"""Hourly cloud recovery, a durable daily allowance, and one consolidated digest."""
from __future__ import annotations

from dataclasses import asdict
from collections import Counter
import json
from pathlib import Path

import yaml

from worker.config import parse_settings, apply_saved_company_policy
from worker.local_time import now as local_now
from worker.pipeline import RunReport, run_pipeline
from worker.records.airtable import AirtableStore, canonical_url


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"date": "", "reserved": [], "pending": []}
    state = json.loads(path.read_text())
    if (not isinstance(state, dict) or not isinstance(state.get("date"), str)
            or not isinstance(state.get("reserved"), list) or not isinstance(state.get("pending"), list)):
        raise ValueError("Invalid cloud cycle state; preserve it for recovery")
    return state


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(path)


def run(cfg, model_available: bool = True, ready_only: bool = False,
        send_window: str | None = None, *, store=None, stages=None, now=None) -> RunReport:
    from worker.stages import build_stages

    now = now or local_now()
    day = now.strftime("%Y-%m-%d")
    path = cfg.proof_dir / "cloud-cycle.json"
    state = load_state(path)
    if state["date"] != day:
        state.update(date=day, reserved=[], companies={})
    prefs = yaml.safe_load((cfg.data_dir / "profile.yaml").read_text()).get("preferences", {})
    limit = prefs.get("daily_application_limit", 30)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("daily_application_limit must be a nonnegative integer")
    store = store if store is not None else AirtableStore(cfg)
    if prefs.get('reference_policy') == 'skip_required_references':
        from worker.apply.preferences import requires_reference_details
        for job in store.get_jobs('Needs you'):
            if any(requires_reference_details(q) for q in (job.questions_for_me or '').split(';')):
                store.update_job(job.id, status='Skipped', next_action='',
                                 notes='Skipped per saved policy: reference contact details required')
    settings = parse_settings(store.settings())
    apply_saved_company_policy(settings, prefs)
    remaining = max(0, limit - len(state["reserved"]))
    if ready_only or not model_available or remaining == 0:
        settings.caps = {k: 0 for k in settings.caps}
        settings.total_target = 0
    else:
        settings.total_target = min(settings.total_target, remaining)
        settings.llm_budget = min(settings.llm_budget, max(1, remaining * 2))
    if send_window:
        settings.send_window = tuple(part.strip() for part in send_window.split("-"))
    st = stages if stages is not None else build_stages(cfg, settings)
    if not model_available and st.generated_answers is not None:
        st.generated_answers.unavailable = True
    # No per-cycle emails. Daily digest drains accumulated results, including late prior-day work.
    st.digest = lambda report, cfg: None
    st.daily_company_counts = dict(Counter(state.get('companies', {}).get(key, '') for key in state['reserved']))
    st.daily_reserved_urls = set(state['reserved'])
    st.resume_reserved_retries = bool(model_available and st.retryable and any(
        canonical_url(job.apply_url) in st.daily_reserved_urls and st.retryable(job)
        for job in store.get_jobs('Needs you')))
    if remaining == 0 and st.resume_reserved_retries:
        settings.llm_budget = min(settings.llm_budget, max(1, len(st.daily_reserved_urls)))

    def reserve(chosen):
        keys = {canonical_url(item.posting.apply_url) for item in chosen}
        reserved = list(dict.fromkeys(state["reserved"] + sorted(keys)))
        if len(reserved) > limit:
            raise RuntimeError("Daily application allowance exceeded before submission")
        state["reserved"] = reserved
        state.setdefault('companies', {}).update({canonical_url(item.posting.apply_url): item.posting.company.lower() for item in chosen})
        save_state(path, state)

    st.on_select = reserve
    state.update(model_available=model_available, checked_at=now.isoformat(), daily_limit=limit)
    save_state(path, state)
    rep = run_pipeline(cfg, store, settings, st, now=now, max_duration_seconds=45 * 60)
    unsent_skips = {name for name, reason in rep.skipped if reason in {'apply: skipped_references', 'apply: skipped_closed'}}
    if unsent_skips:
        # These forms were rejected before submission; their slots are available next cycle.
        skipped_urls = {canonical_url(item['url']) for item in rep.selected_items
                        if f"{item['company']} — {item['title']}" in unsent_skips}
        state['reserved'] = [key for key in state['reserved'] if key not in skipped_urls]
    if rep.selected or rep.errors:
        saved = asdict(rep)
        # Thousands of repeated sourcing skips need not inflate every daily digest/state commit.
        saved["skipped"] = saved["skipped"][:50]
        state["pending"].append(saved)
    save_state(path, state)
    print(f"Daily new application slots used: {len(state['reserved'])}/{limit}; "
          f"Claude: {'available' if model_available else 'waiting; selected queue continues'}")
    return rep


def digest(cfg, *, store=None, sender=None, now=None) -> str:
    from worker.records.digest import send

    now = now or local_now()
    path = cfg.proof_dir / "cloud-cycle.json"
    state = load_state(path)
    store = store if store is not None else AirtableStore(cfg)
    rep = RunReport(date=now.strftime("%Y-%m-%d"))
    for item in state["pending"]:
        for key in ("sourced", "scored", "selected", "applied", "emailed", "to_submit", "queued"):
            setattr(rep, key, getattr(rep, key) + item.get(key, 0))
        for key in ("selected_items", "applied_items", "emailed_items", "errors"):
            getattr(rep, key).extend(item.get(key, []))
        rep.skipped.extend(tuple(x) for x in item.get('skipped', []) if x[1] == 'apply: skipped_references')
    # Include all unresolved forms once, rather than only those attempted in the latest hour.
    for job in store.get_jobs():
        if job.status in {"Needs you", "Submitting", "Error"}:
            rep.needs_you_items.append({"company": job.company, "title": job.role,
                                        "url": job.apply_url or job.source_url or "",
                                        "questions": job.questions_for_me or job.notes or
                                        "Submission outcome requires verification"})
    rep.needs_you = len(rep.needs_you_items)
    if state.get("model_available") is False:
        rep.errors.append("Claude is unavailable. Selected applications continue; new sourcing and scoring "
                          "will resume automatically after a successful hourly inference check.")
    # Clear only after the SMTP server confirms the consolidated email was accepted.
    message_id = (sender or send)(cfg, rep)
    if not message_id:
        raise RuntimeError("Consolidated digest was not confirmed by the mail server")
    state.update(pending=[], last_digest=now.isoformat())
    save_state(path, state)
    return f"Consolidated digest sent: {rep.applied} confirmed submissions; {rep.needs_you} unresolved applications"
