#!/usr/bin/env python3
"""LLM crash triage via local Ollama (fail-open).

Reads ARTIFACT_DIR/crashes/* and fuzz.log, writes ARTIFACT_DIR/triage.md.

Env:
  LLM_FUZZ_ENABLED, LLM_FUZZ_BASE_URL, LLM_FUZZ_MODEL, LLM_FUZZ_TIMEOUT_S
  ARTIFACT_DIR, TARGET_ID
  LLM_TRIAGE_ENABLED — default "1" when script is invoked
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def log(msg: str) -> None:
    print(f"==> llm_crash_triage: {msg}", flush=True)


def hex_preview(data: bytes, n: int = 64) -> str:
    chunk = data[:n]
    hx = chunk.hex()
    ascii_approx = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    return f"hex={hx} ascii={ascii_approx!r} len={len(data)}"


def extract_asan_excerpt(log_text: str, max_lines: int = 50) -> str:
    lines = log_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "ERROR: AddressSanitizer" in line or "ERROR: LeakSanitizer" in line:
            start = i
            break
    if start is None:
        nonempty = [ln for ln in lines if ln.strip()]
        return "\n".join(nonempty[-max_lines:])
    return "\n".join(lines[start : start + max_lines])


def ollama_chat(base_url: str, model: str, prompt: str, timeout_s: float) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.1},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise security engineer triaging fuzzer crashes. "
                    "Reply in GitHub-flavored markdown with 3–6 short bullets: "
                    "likely root cause, severity (low/med/high), and a suggested fix or assert. "
                    "No preamble."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "clusterfuzz-llm-crash-triage"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("message") or {}).get("content") or ""
    if not content.strip():
        raise ValueError("empty triage response")
    return content.strip()


def main() -> int:
    artifact_dir = Path(env("ARTIFACT_DIR") or "./fuzz-artifacts")
    out_path = artifact_dir / "triage.md"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if env("LLM_TRIAGE_ENABLED", "1") not in ("1", "true", "TRUE", "yes", "YES"):
        log("disabled — skipping")
        return 0
    if env("LLM_FUZZ_ENABLED", "1") not in ("1", "true", "TRUE", "yes", "YES"):
        log("LLM_FUZZ_ENABLED off — skipping")
        return 0

    crash_dir = artifact_dir / "crashes"
    crashes: list[tuple[str, bytes]] = []
    if crash_dir.is_dir():
        for p in sorted(crash_dir.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                try:
                    crashes.append((p.name, p.read_bytes()))
                except OSError:
                    pass

    if not crashes:
        log("no crashes — skipping triage")
        return 0

    log_path = artifact_dir / "fuzz.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    excerpt = extract_asan_excerpt(log_text)

    crash_lines = "\n".join(f"- {name}: {hex_preview(data)}" for name, data in crashes[:10])
    prompt = f"""Target: {env("TARGET_ID") or "unknown"}
Engine: {env("FUZZ_ENGINE") or "libfuzzer"}

Crash inputs:
{crash_lines}

ASan / fuzzer log excerpt:
```
{excerpt[:3500]}
```
"""

    base_url = env("LLM_FUZZ_BASE_URL") or "http://127.0.0.1:11434"
    model = env("LLM_FUZZ_MODEL") or "Qwen2.5-Coder:1.5b"
    timeout_s = float(env("LLM_FUZZ_TIMEOUT_S") or "45")

    try:
        markdown = ollama_chat(base_url, model, prompt, timeout_s)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
        log(f"Ollama failed ({exc!r}) — fail-open")
        return 0

    body = "## LLM triage (local Ollama)\n\n" + markdown + "\n"
    out_path.write_text(body, encoding="utf-8")
    log(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
