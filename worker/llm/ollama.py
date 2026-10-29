"""Thin Ollama client. JSON mode only; callers validate everything."""
from __future__ import annotations

import json

import httpx

_cfg = {"url": "http://localhost:11434", "model": "qwen2.5:14b"}


def configure(url: str, model: str):
    _cfg["url"], _cfg["model"] = url, model


def generate_json(prompt: str, schema: dict, timeout: float = 180.0) -> dict:
    body = {"model": _cfg["model"], "messages": [{"role": "user", "content": prompt}],
            "format": schema or "json", "stream": False, "options": {"temperature": 0}}
    r = httpx.post(f"{_cfg['url']}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    content = r.json().get("message", {}).get("content", "")
    return json.loads(content)
