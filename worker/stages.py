"""Wire the real stages for production runs. Nothing here is imported by unit tests."""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from worker.apply.answers import AnswerBank
from worker.apply.detect import filler_for
from worker.apply.handoff import HANDOFF_FILLERS
from worker.apply.retry import answer_revision, resolved
from worker.config import Config, Settings, apply_saved_company_policy
from worker.llm import claude, claude_code, ollama
from worker.pipeline import Stages
from worker.scoring.score import score as _score
from worker.sourcing import load_companies, source_all
from worker.tailor.build_pdf import tailor as _tailor
from worker.tailor.pick_resume import keyword_pick


def build_stages(cfg: Config, settings: Settings) -> Stages:
    import worker.scoring.llm as scoring_llm
    import worker.tailor.pick_resume as pick_resume

    if cfg.llm_backend == "claude":
        if not cfg.anthropic_api_key:
            raise RuntimeError("llm_backend=claude but ANTHROPIC_API_KEY is not set")
        claude.configure(cfg.anthropic_api_key, cfg.claude_model)
        scoring_llm.generate_json = claude.generate_json
        pick_resume.generate_json = claude.generate_json
    elif cfg.llm_backend == "claude_code":
        # Uses whatever `claude` CLI auth is in the environment (CLAUDE_CODE_OAUTH_TOKEN from
        # `claude setup-token`, run against a Pro/Max plan).
        claude_code.configure(cfg.claude_code_model)
        scoring_llm.generate_json = claude_code.generate_json
        pick_resume.generate_json = claude_code.generate_json
    else:
        ollama.configure(cfg.ollama_url, cfg.ollama_model)
        scoring_llm.generate_json = ollama.generate_json
        pick_resume.generate_json = ollama.generate_json
    generator = scoring_llm.generate_json
    # Bound a transient provider failure so a cloud cycle still reaches the selected queue.
    scoring_llm.generate_json = lambda prompt, schema: generator(prompt, schema, timeout=30)
    pick_resume.generate_json = scoring_llm.generate_json
    companies = load_companies(cfg.data_dir)
    # `max_applications` alone is a true lifetime cap; paired with `application_window_days` it's a rolling-window
    # cap (Google: 3 per 30 days, OpenAI: 5 per 180 days - both straight from what the employer itself states).
    company_caps = {c["name"].strip().lower(): {"max": c["max_applications"], "window_days": c.get("application_window_days")}
                    for c in companies if c.get("max_applications")}
    profile = yaml.safe_load((cfg.data_dir / "profile.yaml").read_text())
    apply_saved_company_policy(settings, profile.get('preferences', {}))
    ledger = yaml.safe_load((cfg.data_dir / 'ledger.yaml').read_text())
    profile['_verified_projects'] = ledger.get('projects', [])
    profile['_verified_experience'] = ledger.get('experience', [])
    tool_name = {'claude_code': 'Claude Code', 'claude': 'Claude API', 'ollama': 'Ollama'}.get(cfg.llm_backend, cfg.llm_backend)
    profile['_application_ai_disclosure'] = (
        f"I used {tool_name} ({cfg.claude_code_model if cfg.llm_backend == 'claude_code' else cfg.claude_model if cfg.llm_backend == 'claude' else cfg.ollama_model}) "
        "to extract job requirements and choose relevant verified résumé content. A Python/Playwright worker enters my saved profile and screening answers. "
        "The main prompt instructions were: extract the posting's level, minimum required experience, verbatim qualifications, and technical duties as JSON; "
        "and pick the single best base résumé for the job using one key from the supplied résumé menu. "
        "Routine answer generation selects relevant verified fact IDs; the worker renders the underlying saved facts rather than inventing new claims.")
    answer_provider = None
    if profile.get('preferences', {}).get('routine_answer_policy') == 'derive_from_saved_profile_and_verified_projects':
        from worker.apply.generated import GroundedAnswers
        answer_provider = GroundedAnswers(profile, ledger, scoring_llm.generate_json, cfg.proof_dir / 'generated-answers.json')
    bank = AnswerBank.load(cfg.data_dir / "answers.yaml")
    revision = answer_revision(cfg)

    def sourcer(cfg, store):
        from worker.sourcing.dedupe import filter_new

        postings, errors = source_all(companies)
        sourcer.errors = errors
        return filter_new(postings, store, retryable=lambda job: settings.auto_submit and resolved(job, profile, bank, revision))

    sourcer.errors = []

    def applier(p, pdf, cfg):
        from urllib.parse import urlsplit
        from worker.apply.cover import cover_letter_path, slug
        from playwright.sync_api import sync_playwright

        cls = filler_for(p.apply_url)
        if cls is None:
            return "no filler for this ATS", [], None
        proof_dir = cfg.proof_dir / "apps" / slug(p.company) / slug(p.title)
        cover = cover_letter_path(cfg, p)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(30000)
            submission_responses = []
            def response_seen(response):
                # Record transport outcomes only, never request payloads or applicant answers.
                if response.request.method == "POST":
                    submission_responses.append(f"POST {urlsplit(response.url).path}: HTTP {response.status}")
            page.on("response", response_seen)
            try:
                page.goto(p.apply_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                filler = cls(page, profile, bank, Path(pdf), proof_dir, cover_letter=cover, posting=p, answer_provider=answer_provider)
                filler.prefill_only = not settings.auto_submit and cls.__name__ in HANDOFF_FILLERS
                res = filler.preflight()
                if res is None and not filler.cover_letter and hasattr(filler, 'controls') and any(
                        c['required'] and re.search(r'cover\s*letter', c['label'], re.I)
                        for c in filler.controls()):
                    from worker.apply.cover import ensure_letter
                    filler.cover_letter = ensure_letter(cfg, p, profile)
                if res is None:
                    res = filler.fill()
            finally:
                browser.close()
        if res.status == "prefilled":
            return res.status, res.sheet, str(res.proof_png) if res.proof_png else None
        if res.status == "error" and submission_responses:
            res.note += "; responses: " + "; ".join(submission_responses[-8:])
        extra = [res.note] if res.status in {'error', 'deferred', 'blocked'} and res.note else res.unmapped
        return res.status, extra, str(res.proof_png) if res.proof_png else None

    def outreach(p, key, cfg, store):
        from worker.outreach.stage import outreach as _outreach

        return _outreach(p, key, cfg, store, companies)

    def digest(report, cfg):
        from worker.records.digest import send as _send

        _send(cfg, report)

    def batch_scorer(postings, s):
        from worker.scoring.cache import batch
        return batch(postings, s, cfg)

    def tailor(p, cfg):
        # Already-selected applications do not need model quota to rebuild a missing PDF.
        ready_only = settings.total_target == 0 and not any(settings.caps.values())
        return _tailor(p, cfg, key=keyword_pick(p) if ready_only else None)

    return Stages(sourcer=sourcer, scorer=_score, tailor=tailor, applier=applier, outreach=outreach, digest=digest,
                  batch_scorer=batch_scorer, company_caps=company_caps,
                  retryable=lambda job: resolved(job, profile, bank, revision), answer_revision=revision,
                  generated_answers=answer_provider)
