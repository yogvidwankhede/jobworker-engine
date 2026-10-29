"""worker.llm.claude: matches ollama's generate_json(prompt, schema, timeout) interface,
using Anthropic's forced-tool-use to get structured JSON back instead of Ollama's JSON mode."""
from __future__ import annotations

import httpx
import pytest

from worker.llm import claude


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def test_generate_json_extracts_tool_use_input(monkeypatch):
    claude.configure("sk-test-key", "claude-haiku-4-5-20251001")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"], captured["json"], captured["headers"], captured["timeout"] = url, json, headers, timeout
        return _FakeResponse({
            "content": [
                {"type": "text", "text": "thinking..."},
                {"type": "tool_use", "name": "extract", "input": {"level": "mid", "years_required": 3}},
            ]
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    out = claude.generate_json("some prompt", {"type": "object", "properties": {}}, timeout=30.0)

    assert out == {"level": "mid", "years_required": 3}
    assert captured["url"] == claude.API_URL
    assert captured["headers"]["x-api-key"] == "sk-test-key"
    assert captured["headers"]["anthropic-version"] == claude.API_VERSION
    assert captured["json"]["model"] == "claude-haiku-4-5-20251001"
    assert captured["json"]["tool_choice"] == {"type": "tool", "name": "extract"}
    assert captured["json"]["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert captured["timeout"] == 30.0


def test_generate_json_raises_without_configure(monkeypatch):
    claude._cfg["api_key"] = ""  # simulate configure() never having been called
    with pytest.raises(RuntimeError, match="configure"):
        claude.generate_json("prompt", {})


def test_generate_json_raises_when_no_tool_use_block(monkeypatch):
    claude.configure("sk-test-key")

    def fake_post(url, json, headers, timeout):
        return _FakeResponse({"content": [{"type": "text", "text": "I refuse to use the tool"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ValueError, match="tool_use"):
        claude.generate_json("prompt", {})


def test_generate_json_raises_on_http_error(monkeypatch):
    claude.configure("sk-test-key")

    def fake_post(url, json, headers, timeout):
        return _FakeResponse({}, status=500)

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        claude.generate_json("prompt", {})
