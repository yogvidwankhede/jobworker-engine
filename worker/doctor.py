"""`worker doctor`: one line per dependency, OK or MISSING, never prints a secret."""
from __future__ import annotations

import imaplib
import os
import re
import subprocess
import shutil
import smtplib
from pathlib import Path

import httpx

from worker.config import Config

TINYTEX_BIN = Path.home() / "Library" / "TinyTeX" / "bin" / "universal-darwin"


def inference_check(cfg: Config) -> tuple[str, bool, str]:
    from worker.llm import claude_code, claude, ollama
    try:
        if cfg.llm_backend == 'claude_code':
            backend = claude_code
            backend.configure(cfg.claude_code_model)
        elif cfg.llm_backend == 'claude':
            backend = claude
            backend.configure(cfg.anthropic_api_key, cfg.claude_model)
        else:
            backend = ollama
            backend.configure(cfg.ollama_url, cfg.ollama_model)
        result = backend.generate_json('Return {"ok": true}.', {"type": "object"}, timeout=30)
        return "Cloud model inference", result.get("ok") is True, ""
    except Exception as e:  # noqa: BLE001
        detail = type(e).__name__
        if isinstance(e, subprocess.CalledProcessError):
            detail = (e.stderr or e.stdout or detail).strip()
            for secret in (cfg.airtable_token, cfg.gmail_app_password, cfg.apollo_api_key,
                           cfg.anthropic_api_key, os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            detail = re.sub(r"sk-ant-[\w-]+", "[redacted]", detail)
            detail = re.sub(r"\s+", " ", detail)[:300]
        return "Claude cloud inference", False, detail


def pdflatex_path() -> str | None:
    found = shutil.which("pdflatex")
    if found:
        return found
    cand = TINYTEX_BIN / "pdflatex"
    return str(cand) if cand.exists() else None


def checks(cfg: Config, check_inference: bool = True, application_only: bool = False) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    out.append(("AIRTABLE_TOKEN set", bool(cfg.airtable_token), ""))
    if not application_only:
        out.append(("GMAIL_APP_PASSWORD set", bool(cfg.gmail_app_password), ""))
    out.append(("APOLLO_API_KEY set (optional)", bool(cfg.apollo_api_key), "apply-only mode if missing"))
    if cfg.llm_backend == "claude_code" and not application_only:
        out.append(("Claude Code CLI", bool(shutil.which("claude")), ""))
        out.append(("Claude cloud credential configured", bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), ""))
        if check_inference and shutil.which("claude") and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            out.append(inference_check(cfg))
    elif cfg.llm_backend == "claude" and not application_only:
        out.append(("ANTHROPIC_API_KEY set", bool(cfg.anthropic_api_key), ""))
    elif not application_only:
        try:
            v = httpx.get(f"{cfg.ollama_url}/api/version", timeout=5).json().get("version")
            out.append(("Ollama reachable", True, f"v{v}"))
            tags = httpx.get(f"{cfg.ollama_url}/api/tags", timeout=5).json().get("models", [])
            names = {m.get("name") for m in tags}
            out.append((f"Model {cfg.ollama_model} pulled", cfg.ollama_model in names, ""))
        except Exception as e:  # noqa: BLE001
            out.append(("Ollama reachable", False, type(e).__name__))
    p = pdflatex_path()
    out.append(("pdflatex", p is not None, p or "install TinyTeX"))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            b.close()
        out.append(("Playwright Chromium", True, ""))
    except Exception as e:  # noqa: BLE001
        out.append(("Playwright Chromium", False, f"{type(e).__name__}: run `playwright install chromium`"))
    try:
        r = httpx.get(
            f"https://api.airtable.com/v0/meta/bases/{cfg.airtable_base_id}/tables",
            headers={"Authorization": f"Bearer {cfg.airtable_token}"},
            timeout=10,
        )
        out.append(("Airtable base reachable", r.status_code == 200, f"HTTP {r.status_code}"))
    except Exception as e:  # noqa: BLE001
        out.append(("Airtable base reachable", False, type(e).__name__))
    if application_only:
        return out
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(cfg.gmail_user, cfg.gmail_app_password)
        out.append(("Gmail SMTP login", True, ""))
    except Exception as e:  # noqa: BLE001
        out.append(("Gmail SMTP login", False, type(e).__name__))
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(cfg.gmail_user, cfg.gmail_app_password)
        m.logout()
        out.append(("Gmail IMAP login", True, ""))
    except Exception as e:  # noqa: BLE001
        out.append(("Gmail IMAP login", False, type(e).__name__))
    return out


def report(cfg: Config) -> str:
    lines = []
    for name, ok, note in checks(cfg):
        lines.append(f"{'OK     ' if ok else 'MISSING'}  {name}{('  (' + note + ')') if note else ''}")
    return "\n".join(lines)
