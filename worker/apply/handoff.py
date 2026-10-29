"""Hand-off sheets: applications the worker filled but may not submit itself (bot-checked boards).

These legacy preview sheets remain available for inspection. Cloud auto-submit resumes their tracker rows
using the saved profile and answer bank, without a Chrome session or another approval.
"""
from __future__ import annotations

import json
import shutil
from worker.local_time import today as local_today
from pathlib import Path

from worker.apply.cover import slug

# Boards whose bot checks reject automated submits (Ashby: "flagged as possible spam"; Greenhouse: 428 captcha-failed)
HANDOFF_FILLERS = {"AshbyFiller", "GreenhouseFiller", "LeverFiller"}


def handoff_dir(cfg, day: str | None = None) -> Path:
    return cfg.proof_dir.parent / "handoff" / (day or local_today().isoformat())


def save(cfg, job_id: str, posting, resume_pdf: str | None, sheet: list[dict], day: str | None = None) -> Path:
    d = handoff_dir(cfg, day)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{slug(posting.company)}__{slug(posting.title)}"
    attach = None
    if resume_pdf and Path(resume_pdf).exists():   # the file a recruiter sees is always the neutral name
        rd = d / name
        rd.mkdir(exist_ok=True)
        attach = rd / "Yogvid_Wankhede_Resume.pdf"
        shutil.copy(resume_pdf, attach)
    out = d / f"{name}.json"
    out.write_text(json.dumps({
        "job_id": job_id, "company": posting.company, "role": posting.title, "tier": posting.tier,
        "apply_url": posting.apply_url, "resume_pdf": str(attach) if attach else resume_pdf, "answers": sheet,
    }, indent=2))
    return out


def load_day(cfg, day: str | None = None) -> list[dict]:
    d = handoff_dir(cfg, day)
    if not d.exists():
        return []
    items = []
    for f in sorted(d.glob("*.json")):
        item = json.loads(f.read_text())
        item["sheet_path"] = str(f)
        items.append(item)
    return items
