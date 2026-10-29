"""Secrets from .env and behavior switches from Airtable Settings."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
TRUE_WORDS = {"true", "on", "yes", "1"}


class Config(BaseModel):
    """Secrets and machine paths. Secrets are SecretStr so repr never leaks them."""

    airtable_token_secret: SecretStr = Field(repr=False)
    airtable_base_id: str = ""
    gmail_user: str
    gmail_app_password_secret: SecretStr = Field(repr=False)
    apollo_api_key_secret: SecretStr | None = Field(default=None, repr=False)
    anthropic_api_key_secret: SecretStr | None = Field(default=None, repr=False)
    ollama_model: str = "qwen2.5:14b"
    ollama_url: str = "http://localhost:11434"
    llm_backend: str = "claude_code"   # cloud subscription CLI; "claude" API or "ollama" are optional alternatives
    claude_model: str = "claude-haiku-4-5-20251001"  # used by llm_backend="claude" (raw Messages API, needs a full model id)
    claude_code_model: str = "sonnet"  # used by llm_backend="claude_code" ("sonnet"/"opus"/"haiku" alias, or a full model id)
    repo_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = Path(__file__).resolve().parent / "data"
    proof_dir: Path = Path.home() / "JobWorker" / "proof"

    @property
    def airtable_token(self) -> str:
        return self.airtable_token_secret.get_secret_value()

    @property
    def gmail_app_password(self) -> str:
        return self.gmail_app_password_secret.get_secret_value()

    @property
    def apollo_api_key(self) -> str | None:
        return self.apollo_api_key_secret.get_secret_value() if self.apollo_api_key_secret else None

    @property
    def anthropic_api_key(self) -> str | None:
        return self.anthropic_api_key_secret.get_secret_value() if self.anthropic_api_key_secret else None


def _opt(v: str | None) -> SecretStr | None:
    return SecretStr(v) if v else None


def load(env_path: Path | None = None) -> Config:
    """Load Config from a .env file (default: ~/JobWorker/.env, then ./.env)."""
    candidates = [env_path] if env_path else [Path.home() / "JobWorker" / ".env", Path(".env")]
    values: dict[str, str | None] = {}
    for c in candidates:
        if c and c.exists():
            values = dotenv_values(c)
            break
    # Actions supplies secrets through the environment, never in the persisted state directory.
    values.update({k: v for k, v in os.environ.items() if k in {
        "AIRTABLE_TOKEN", "AIRTABLE_BASE_ID", "GMAIL_USER", "GMAIL_APP_PASSWORD",
        "APOLLO_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_MODEL", "OLLAMA_URL",
        "WORKER_LLM_BACKEND", "CLAUDE_MODEL", "CLAUDE_CODE_MODEL",
    }})
    pw = (values.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    return Config(
        airtable_token_secret=SecretStr(values.get("AIRTABLE_TOKEN") or ""),
        airtable_base_id=values.get("AIRTABLE_BASE_ID") or "",
        gmail_user=values.get("GMAIL_USER") or "",
        gmail_app_password_secret=SecretStr(pw),
        apollo_api_key_secret=_opt(values.get("APOLLO_API_KEY")),
        anthropic_api_key_secret=_opt(values.get("ANTHROPIC_API_KEY")),
        ollama_model=values.get("OLLAMA_MODEL") or "qwen2.5:14b",
        ollama_url=values.get("OLLAMA_URL") or "http://localhost:11434",
        llm_backend=values.get("WORKER_LLM_BACKEND") or "claude_code",
        claude_model=os.environ.get("CLAUDE_MODEL") or values.get("CLAUDE_MODEL") or "claude-haiku-4-5-20251001",
        claude_code_model=os.environ.get("CLAUDE_CODE_MODEL") or values.get("CLAUDE_CODE_MODEL") or "sonnet",
    )


class Settings(BaseModel):
    """Behavior switches read from the Airtable Settings table (Key/Value rows)."""

    caps: dict[str, int] = {"big": 10, "mid": 5, "startup": 15}
    fit_threshold: int = 70
    send_window: tuple[str, str] = ("09:00", "12:00")
    send_days: set[int] = {0, 1, 2, 3, 4, 5, 6}
    daily_email_cap: int = 15
    cold_email_paused: bool = False
    claude_fallback: bool = False
    claude_daily_cap_usd: float = 3.0
    llm_budget: int = 600
    llm_workers: int = 2
    max_years: int = 3            # reject postings requiring more than this many years
    max_per_company: int = 2      # per run
    company_cooldown_days: int = Field(default=0, ge=0)
    company_cooldown_started_on: str | None = None
    include_internships: bool = False
    # Mid-fit roles (review_floor <= score < fit_threshold) are parked as 'Scored' for the phone swipe deck.
    review_floor: int = 55
    review_queue_max: int = 150
    total_target: int = 30        # fill to this many across tiers when a tier runs short
    # Yogvid's standing authorization covers matching applications without per-application approval.
    auto_submit: bool = True
    # Applications run as soon as tailoring finishes on send days; only outreach waits for send_window.
    prefill_anytime: bool = True
    # An "Applied" row's recruiter email never fires the instant the form went in - it always waits at least this
    # long first (plus still needs the send window open), so a submit and its outreach never land back to back.
    email_delay_minutes: int = 30


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None else v.strip().lower() in TRUE_WORDS


def parse_settings(kv: dict[str, str]) -> Settings:
    """Turn raw Key->Value strings into typed Settings; unknown keys are ignored."""
    s = Settings()
    caps = dict(s.caps)
    for tier in caps:
        if kv.get(f"cap_{tier}"):
            caps[tier] = int(kv[f"cap_{tier}"])
    send_days = s.send_days
    if kv.get("send_days"):
        send_days = {DAY_INDEX[d.strip()[:3].lower()] for d in kv["send_days"].split(",") if d.strip()[:3].lower() in DAY_INDEX}
    window = s.send_window
    if kv.get("send_window"):
        a, b = [x.strip() for x in kv["send_window"].split("-")]
        window = (a, b)
    return Settings(
        caps=caps,
        fit_threshold=int(kv.get("fit_threshold") or s.fit_threshold),
        send_window=window,
        send_days=send_days,
        daily_email_cap=int(kv.get("daily_email_cap") or s.daily_email_cap),
        cold_email_paused=_bool(kv.get("cold_email_paused"), False),
        auto_submit=_bool(kv.get("auto_submit"), True),
        prefill_anytime=_bool(kv.get("prefill_anytime"), True),
        claude_fallback=_bool(kv.get("claude_fallback"), False),
        claude_daily_cap_usd=float(kv.get("claude_daily_cap_usd") or s.claude_daily_cap_usd),
        llm_budget=int(kv.get("llm_budget") or s.llm_budget),
        llm_workers=int(kv.get("llm_workers") or s.llm_workers),
        max_years=int(kv.get("max_years") or s.max_years),
        max_per_company=int(kv.get("max_per_company") or s.max_per_company),
        company_cooldown_days=int(kv.get('company_cooldown_days') or 0),
        company_cooldown_started_on=kv.get('company_cooldown_started_on'),
        include_internships=_bool(kv.get("include_internships"), False),
        review_floor=int(kv.get("review_floor") or s.review_floor),
        review_queue_max=int(kv.get("review_queue_max") or s.review_queue_max),
        total_target=int(kv.get("total_target") or s.total_target),
        email_delay_minutes=int(kv.get("email_delay_minutes") or s.email_delay_minutes),
    )


def apply_saved_company_policy(settings: Settings, prefs: dict):
    days = prefs.get('company_application_cooldown_days', settings.company_cooldown_days)
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError('company_application_cooldown_days must be a nonnegative integer')
    settings.company_cooldown_days = days
    settings.company_cooldown_started_on = prefs.get('company_cooldown_started_on', settings.company_cooldown_started_on)
    if settings.company_cooldown_started_on:
        from datetime import date
        date.fromisoformat(settings.company_cooldown_started_on)
