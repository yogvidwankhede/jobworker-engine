"""Claude Code CLI backend for generate_json — authenticated via a Pro/Max subscription
(`claude setup-token` -> CLAUDE_CODE_OAUTH_TOKEN env var) instead of a separately metered
API key. Matches ollama.py / claude.py's generate_json(prompt, schema, timeout) interface.

No forced tool-use here: Claude Code's headless `-p` mode has no tool_choice guarantee like
the Messages API, so output is prompted-into-JSON and parsed. Callers (scoring/llm.py,
tailor/pick_resume.py) already retry once on any exception raised here, which covers a
malformed response the same way it already covers a network failure.
"""
from __future__ import annotations

import json
import subprocess

_cfg: dict[str, str | None] = {"model": None}


def configure(model: str | None = None) -> None:
    """Set which model `claude -p` should use (a CLI alias like "sonnet"/"opus"/"haiku",
    or a specific model id). Leave unset (None) to use the CLI's own default model."""
    _cfg["model"] = model


def generate_json(prompt: str, schema: dict, timeout: float = 180.0) -> dict:
    full_prompt = (
        f"{prompt}\n\n"
        "Respond with ONLY a single valid JSON object matching this JSON schema — no prose, "
        "no markdown code fences, nothing before or after the object:\n"
        f"{json.dumps(schema)}"
    )
    # Scoring is a text-only model call. It needs no tool permissions or interactive approval.
    cmd = ["claude", "-p", full_prompt, "--output-format", "text", "--tools", "", "--disallowedTools", "mcp__*"]
    if _cfg["model"]:
        cmd += ["--model", _cfg["model"]]
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    text = result.stdout.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"claude -p returned no JSON object: {text[:200]!r}")
    return json.loads(text[start:end + 1])
