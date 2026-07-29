#!/usr/bin/env python3
"""LLM crash triage via Ollama or OpenRouter (fail-open), grounded in ASan + source.

Reads ARTIFACT_DIR/crashes/* and fuzz.log, writes ARTIFACT_DIR/triage.md.
Skips on SMOKE_DEMO / SKIP_LLM_TRIAGE. Falls back to structured ASan fields
when the model invents symbols or fails.

Env:
  LLM_FUZZ_ENABLED, LLM_FUZZ_PROVIDER, LLM_FUZZ_BASE_URL, LLM_FUZZ_MODEL,
  LLM_FUZZ_TIMEOUT_S, OPENROUTER_API_KEY
  ARTIFACT_DIR, TARGET_ID, SOURCE_PATHS (space-separated harness sources)
  LLM_TRIAGE_ENABLED, SKIP_LLM_TRIAGE, SMOKE_DEMO
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from asan_helpers import parse_asan, triage_mentions_unknown_symbols  # noqa: E402
from llm_chat import chat, provider_label, resolve_config  # noqa: E402


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


def load_sources(paths: str, limit_chars: int = 4000) -> str:
    chunks: list[str] = []
    remaining = limit_chars
    for raw in paths.split():
        if remaining <= 0:
            break
        p = Path(raw)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = text[:remaining]
        chunks.append(f"### {p}\n```\n{snippet}\n```")
        remaining -= len(snippet)
    return "\n\n".join(chunks)


def allowed_symbols(source_text: str, asan_excerpt: str, info_func: str) -> set[str]:
    ids = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", source_text + "\n" + asan_excerpt))
    if info_func:
        ids.add(info_func)
    # Always allow common sanitizer / language terms
    ids.update(
        {
            "LLVMFuzzerTestOneInput",
            "AddressSanitizer",
            "heap-buffer-overflow",
            "stack-buffer-overflow",
            "use-after-free",
        }
    )
    return ids


def structured_fallback(info, crash_lines: str) -> str:
    parts = [
        "## Crash triage (structured)",
        "",
        f"- **Bug type**: `{info.bug_type or 'unknown'}`",
        f"- **Top frame**: {info.display_frame()}",
    ]
    if info.summary:
        parts.append(f"- **ASan SUMMARY**: `{info.summary}`")
    parts.append(f"- **Inputs**:\n{crash_lines}")
    parts.append("")
    parts.append("_LLM triage skipped or rejected (ungrounded / unavailable)._")
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    artifact_dir = Path(env("ARTIFACT_DIR") or "./fuzz-artifacts")
    out_path = artifact_dir / "triage.md"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if env("SMOKE_DEMO") in ("1", "true", "TRUE", "yes") or env("SKIP_LLM_TRIAGE") in (
        "1",
        "true",
        "TRUE",
        "yes",
    ):
        log("smoke/skip — writing structured note only")
        log_path = artifact_dir / "fuzz.log"
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        info = parse_asan(log_text)
        out_path.write_text(
            "## Expected demo smoke\n\n"
            "This crash matches the intentional `demo-target` / `BOOM` harness "
            f"({info.bug_type or 'asan'} at {info.display_frame()}). "
            "Not treated as a new PR regression.\n",
            encoding="utf-8",
        )
        return 0

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
    info = parse_asan(log_text)
    source_blob = load_sources(env("SOURCE_PATHS") or "")
    crash_lines = "\n".join(f"- {name}: {hex_preview(data)}" for name, data in crashes[:10])

    prompt = f"""Target: {env("TARGET_ID") or "unknown"}
Engine: {env("FUZZ_ENGINE") or "libfuzzer"}
ASan bug type: {info.bug_type or "unknown"}
SUMMARY: {info.summary or "n/a"}
Top frame: {info.display_frame()}

Crash inputs:
{crash_lines}

Harness / related source (may be truncated):
{source_blob or "(none provided)"}

ASan / fuzzer log excerpt:
```
{excerpt[:3500]}
```
"""

    allowed = allowed_symbols(source_blob, excerpt, info.frame_func)

    markdown = ""
    label = "LLM"
    try:
        cfg = resolve_config()
        label = provider_label(cfg)
        log(f"provider={cfg.provider} model={cfg.model}")
        markdown = chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a concise security engineer triaging fuzzer crashes. "
                        "Use ONLY symbols and files present in the provided source and ASan stack. "
                        "If unsure, say so. Reply in GitHub-flavored markdown with 3–6 short bullets: "
                        "likely root cause, severity (low/med/high), and a suggested fix grounded in "
                        "the shown code. Do not invent APIs (no strncpy/buffer unless in the source). "
                        "No preamble."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            cfg=cfg,
        ).strip()
        if not markdown:
            raise ValueError("empty triage response")
        if triage_mentions_unknown_symbols(markdown, allowed):
            log("LLM output looked ungrounded — using structured fallback")
            markdown = ""
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
        log(f"{label} failed ({exc!r}) — structured fallback")

    if markdown:
        body = f"## LLM triage ({label})\n\n" + markdown + "\n"
    else:
        body = structured_fallback(info, crash_lines)

    out_path.write_text(body, encoding="utf-8")
    log(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
