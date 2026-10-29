"""Sourcing: public board APIs for every company in companies.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx
import yaml

from worker.models import Posting
from worker.sourcing import ashby, greenhouse, lever

BOARDS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever}


def default_fetch(url: str):
    r = httpx.get(url, timeout=20, headers={"User-Agent": "job-search-worker/0.1"})
    r.raise_for_status()
    return r.json()


def load_companies(data_dir: Path) -> list[dict]:
    return yaml.safe_load((data_dir / "companies.yaml").read_text())["companies"]


def source_all(companies: list[dict], fetch: Callable = default_fetch) -> tuple[list[Posting], list[str]]:
    postings, errors = [], []
    for co in companies:
        mod = BOARDS.get(co.get("board"))
        if not mod or not co.get("slug"):
            continue
        try:
            postings.extend(mod.parse(co, fetch(mod.url(co["slug"]))))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{co['name']}: {type(e).__name__}: {e}")
    return postings, errors
