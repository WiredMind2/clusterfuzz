"""Shared LLM chat client for Ollama and OpenRouter (fail-open at call sites).

Switch with LLM_FUZZ_PROVIDER=ollama|openrouter (default: ollama).

Env:
  LLM_FUZZ_PROVIDER   — ollama | openrouter (default ollama)
  LLM_FUZZ_BASE_URL   — override provider default base URL
  LLM_FUZZ_MODEL      — override provider default model
  LLM_FUZZ_TIMEOUT_S  — HTTP timeout seconds (default 45)
  OPENROUTER_API_KEY  — required when provider=openrouter
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENROUTER = "openrouter"

_DEFAULTS = {
    PROVIDER_OLLAMA: {
        "base_url": "http://127.0.0.1:11434",
        "model": "Qwen2.5-Coder:1.5b",
        "label": "local Ollama",
    },
    PROVIDER_OPENROUTER: {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen-2.5-coder-7b-instruct",
        "label": "OpenRouter",
    },
}

_OPENROUTER_REFERER = "https://fuzzing.tetrazero.com"
_OPENROUTER_TITLE = "clusterfuzz-local"


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    base_url: str
    model: str
    timeout_s: float
    api_key: str | None


def resolve_config() -> LlmConfig:
    raw = (_env("LLM_FUZZ_PROVIDER") or PROVIDER_OLLAMA).strip().lower()
    if raw not in _DEFAULTS:
        raise ValueError(f"unsupported LLM_FUZZ_PROVIDER={raw!r} (want ollama|openrouter)")
    defaults = _DEFAULTS[raw]
    return LlmConfig(
        provider=raw,
        base_url=(_env("LLM_FUZZ_BASE_URL") or defaults["base_url"]).rstrip("/"),
        model=_env("LLM_FUZZ_MODEL") or defaults["model"],
        timeout_s=float(_env("LLM_FUZZ_TIMEOUT_S") or "45"),
        api_key=_env("OPENROUTER_API_KEY"),
    )


def provider_label(cfg: LlmConfig | None = None) -> str:
    c = cfg or resolve_config()
    return _DEFAULTS[c.provider]["label"]


def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    cfg: LlmConfig | None = None,
) -> str:
    """Send chat messages; return assistant content string."""
    c = cfg or resolve_config()
    if c.provider == PROVIDER_OLLAMA:
        return _ollama_chat(c, messages, json_mode=json_mode, temperature=temperature)
    return _openrouter_chat(c, messages, json_mode=json_mode, temperature=temperature)


def _ollama_chat(
    cfg: LlmConfig,
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    temperature: float,
) -> str:
    url = cfg.base_url + "/api/chat"
    body: dict = {
        "model": cfg.model,
        "stream": False,
        "options": {"temperature": temperature},
        "messages": messages,
    }
    if json_mode:
        body["format"] = "json"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "clusterfuzz-llm-chat",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("message") or {}).get("content")
    if not content:
        raise ValueError(f"empty Ollama response: {data!r}"[:500])
    return content


def _openrouter_chat(
    cfg: LlmConfig,
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    temperature: float,
) -> str:
    if not cfg.api_key:
        raise ValueError("OPENROUTER_API_KEY is required when LLM_FUZZ_PROVIDER=openrouter")
    url = cfg.base_url + "/chat/completions"
    body: dict = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            "HTTP-Referer": _OPENROUTER_REFERER,
            "X-Title": _OPENROUTER_TITLE,
            "User-Agent": "clusterfuzz-llm-chat",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"empty OpenRouter response: {data!r}"[:500])
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        raise ValueError(f"empty OpenRouter content: {data!r}"[:500])
    return content
