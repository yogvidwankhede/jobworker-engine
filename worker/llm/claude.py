"""Thin Anthropic Claude client, matching worker.llm.ollama's generate_json(prompt, schema, timeout)
interface so callers (scoring/llm.py, tailor/pick_resume.py) don't need to know which backend is live.

The Messages API has no "JSON mode" the way Ollama's /api/chat does, so structured output is obtained
via forced tool-use: the caller's JSON schema becomes a single tool's input_schema, tool_choice forces
that tool, and the tool call's already-parsed `input` is returned directly.
"""
from __future__ import annotations

import httpx

_cfg = {"api_key": "", "model": "claude-haiku-4-5-20251001"}

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def configure(api_key: str, model: str = "claude-haiku-4-5-20251001"):
    _cfg["api_key"], _cfg["model"] = api_key, model


def generate_json(prompt: str, schema: dict, timeout: float = 180.0) -> dict:
    if not _cfg["api_key"]:
        raise RuntimeError("worker.llm.claude.configure() was never called with an API key")
    tool = {"name": "extract", "description": "Return the extracted fields as structured data.", "input_schema": schema}
    body = {
        "model": _cfg["model"],
        "max_tokens": 1024,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": "extract"},
    }
    headers = {"x-api-key": _cfg["api_key"], "anthropic-version": API_VERSION, "content-type": "application/json"}
    r = httpx.post(API_URL, json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "extract":
            return block.get("input", {})
    raise ValueError("Claude response had no tool_use block for 'extract'")
