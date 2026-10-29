"""Recruiter email from a per-tier Jinja template; hook sentence from hooks.yaml only; links embedded in words."""
from __future__ import annotations

import random
import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

def identity(data_dir: Path) -> dict:
    """Name, phone and public links come from the private profile, never from code."""
    prof = yaml.safe_load((data_dir / "profile.yaml").read_text())
    c = prof["contact"]
    links = dict(prof.get("links") or {})
    site = re.sub(r"^https?://(www\.)?", "", links.get("website", "")).rstrip("/")
    return {"name": f"{c['first_name']} {c['last_name']}", "phone": c.get("phone_display", ""), "links": links, "site": site}


def resume_links(data_dir: Path) -> dict:
    """Shareable resume links per resume key, kept in the private data repo (resume_links.yaml)."""
    f = data_dir / "resume_links.yaml"
    return yaml.safe_load(f.read_text()) or {} if f.exists() else {}


def signature(data_dir: Path) -> tuple[str, str]:
    """-> (html, text) sign-off block for follow-ups."""
    i = identity(data_dir)
    html = f'{i["name"]}<br>{i["phone"]} &middot; <a href="{i["links"].get("website", "")}">{i["site"]}</a>'
    text = f'{i["name"]}\n{i["phone"]} \u00b7 {i["site"]}'
    return html, text


def hooks(data_dir: Path) -> dict:
    return yaml.safe_load((data_dir / "hooks.yaml").read_text())


def render(tier: str, ctx: dict, data_dir: Path) -> tuple[str, str, str]:
    """-> (subject, html, plaintext). ctx: first_name, company, role, resume_key, resume_link (optional)."""
    env = Environment(loader=FileSystemLoader(str(data_dir / "templates")), autoescape=select_autoescape(["html", "j2"]))
    tmpl = env.get_template(f"email_{tier if tier in ('big', 'mid', 'startup') else 'startup'}.html.j2")
    hook_list = hooks(data_dir)[ctx["resume_key"]]
    hook = random.choice(hook_list)["text"]
    ident = identity(data_dir)
    resume_link = ctx.get("resume_link") or resume_links(data_dir)[ctx["resume_key"]]
    html = tmpl.render(first_name=ctx["first_name"], company=ctx["company"], role=ctx["role"], hook=hook,
                       project_line=ctx.get("project_line", ""), links=ident["links"], resume_link=resume_link)
    html = re.sub(r"\n\s*\n", "\n", html).strip()
    subject = f"{ctx['role']} at {ctx['company']} — applied, quick intro"
    text = (f"Hi {ctx['first_name']},\n\nI applied for the {ctx['role']} role at {ctx['company']} today. Short version of the fit: {hook}\n\n"
            f"Would you have 15 minutes this week or next to talk about the role?\n\nBest,\n{ident['name']}\n{ident['phone']} · {ident['site']}\n"
            f"Resume: {resume_link}\nGitHub: {ident['links'].get('github', '')}\nLinkedIn: {ident['links'].get('linkedin', '')}")
    return subject, html, text
