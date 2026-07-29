#!/usr/bin/env python3
"""Generate libFuzzer seeds + dictionary via local Ollama (fail-open).

Writes:
  $ARTIFACT_DIR/llm_seeds/   — one file per seed
  $ARTIFACT_DIR/llm.dict     — libFuzzer dictionary tokens
  $ARTIFACT_DIR/llm_prepare.env — CORPUS_DIR / EXTRA_FUZZ_ARGS for sourcing

Env:
  LLM_FUZZ_ENABLED       — "1" to call Ollama (default: treat as enabled if unset when invoked)
  LLM_FUZZ_BASE_URL      — default http://127.0.0.1:11434
  LLM_FUZZ_MODEL         — default Qwen2.5-Coder:1.5b
  LLM_FUZZ_TIMEOUT_S     — HTTP timeout (default 45)
  LLM_FUZZ_MAX_SEEDS     — default 32
  LLM_FUZZ_MAX_TOKENS    — dict tokens (default 64)
  LLM_FUZZ_MAX_SEED_BYTES — reject larger seeds (default 4096)
  LLM_FUZZ_MAX_PROMPT_CHARS — default 8000
  ARTIFACT_DIR           — default ./fuzz-artifacts
  TARGET_ID              — optional label in prompt
  SOURCE_PATHS           — space-separated files to include (else CLI args)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def log(msg: str) -> None:
    print(f"==> llm_fuzz_prepare: {msg}", flush=True)


def write_outputs(artifact_dir: Path, seeds: list[bytes], tokens: list[str]) -> None:
    seed_dir = artifact_dir / "llm_seeds"
    seed_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous LLM seeds from this dir only
    for old in seed_dir.glob("seed-*"):
        try:
            old.unlink()
        except OSError:
            pass

    for i, raw in enumerate(seeds):
        (seed_dir / f"seed-{i:04d}").write_bytes(raw)

    dict_path = artifact_dir / "llm.dict"
    lines = []
    for tok in tokens:
        # libFuzzer dict: "token" with C-string escapes for non-printable
        escaped = json.dumps(tok)  # produces "..." with escapes
        lines.append(escaped)
    dict_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    env_path = artifact_dir / "llm_prepare.env"
    extra = f'-dict={dict_path}' if tokens else ""
    env_path.write_text(
        f"CORPUS_DIR={seed_dir}\n"
        f"EXTRA_FUZZ_ARGS={extra}\n",
        encoding="utf-8",
    )
    log(f"wrote {len(seeds)} seed(s), {len(tokens)} dict token(s) → {artifact_dir}")


def load_sources(paths: list[Path], max_chars: int) -> str:
    chunks: list[str] = []
    used = 0
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log(f"skip {path}: {exc}")
            continue
        header = f"\n// ===== {path} =====\n"
        remain = max_chars - used - len(header)
        if remain <= 0:
            break
        if len(text) > remain:
            text = text[:remain] + "\n/* … truncated … */\n"
        chunks.append(header + text)
        used += len(header) + len(text)
    return "".join(chunks)


def parse_llm_json(raw: str) -> dict:
    text = raw.strip()
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    # Find outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start : end + 1])


def decode_seed(item) -> bytes | None:
    if item is None:
        return None
    if isinstance(item, dict):
        if "hex" in item:
            try:
                return bytes.fromhex(str(item["hex"]).replace(" ", ""))
            except ValueError:
                return None
        if "base64" in item:
            import base64

            try:
                return base64.b64decode(item["base64"])
            except Exception:
                return None
        if "text" in item:
            return str(item["text"]).encode("utf-8", errors="replace")
        return None
    if isinstance(item, str):
        # Prefer hex: if it looks like hex
        s = item.strip()
        if re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) % 2 == 0 and len(s) >= 2:
            try:
                return bytes.fromhex(s)
            except ValueError:
                pass
        # Interpret escape sequences like \x41
        try:
            return bytes(s, "utf-8").decode("unicode_escape").encode("latin-1")
        except Exception:
            return s.encode("utf-8", errors="replace")
    return None


def normalize_payload(payload: dict, max_seeds: int, max_tokens: int, max_seed_bytes: int) -> tuple[list[bytes], list[str]]:
    seeds_raw = payload.get("seeds") or []
    tokens_raw = payload.get("dict_tokens") or payload.get("tokens") or []
    seeds: list[bytes] = []
    seen_seed: set[bytes] = set()
    for item in seeds_raw:
        if len(seeds) >= max_seeds:
            break
        raw = decode_seed(item)
        if not raw or len(raw) > max_seed_bytes:
            continue
        if raw in seen_seed:
            continue
        seen_seed.add(raw)
        seeds.append(raw)

    tokens: list[str] = []
    seen_tok: set[str] = set()
    for tok in tokens_raw:
        if len(tokens) >= max_tokens:
            break
        s = str(tok)
        if not s or len(s.encode("utf-8", errors="replace")) > 128:
            continue
        if s in seen_tok:
            continue
        seen_tok.add(s)
        tokens.append(s)
    return seeds, tokens


def ollama_chat(base_url: str, model: str, prompt: str, timeout_s: float) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You help coverage-guided fuzzers. Reply with JSON only, no prose. "
                    'Schema: {"seeds":[<string or {"text":...} or {"hex":...}>],'
                    '"dict_tokens":[<short string tokens>]}'
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "clusterfuzz-llm-fuzz-prepare"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data.get("message") or {}
    content = msg.get("content")
    if not content:
        raise ValueError(f"empty Ollama response: {data!r}"[:500])
    return content


def build_prompt(source_text: str, target_id: str, prior_crashes: list[str], max_seeds: int, max_tokens: int) -> str:
    prior = ""
    if prior_crashes:
        prior = "Already-known crash input names/hashes (generate different seeds):\n" + "\n".join(
            f"- {c}" for c in prior_crashes[:40]
        )
    return f"""Target id: {target_id or "unknown"}

Given this fuzz harness / target source, propose up to {max_seeds} short seed inputs
and up to {max_tokens} dictionary tokens that help a mutational fuzzer reach interesting
branches quickly (magic strings, format tokens, edge values, delimiters).

Prefer seeds as plain text when printable; use hex for binary.
Keep each seed under 4KiB. Do not invent secrets or credentials.

{prior}

Source:
{source_text}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        help="Source files to ground the prompt (or set SOURCE_PATHS)",
    )
    parser.add_argument(
        "--github-env",
        action="store_true",
        help="Also append CORPUS_DIR/EXTRA_FUZZ_ARGS to $GITHUB_ENV if set",
    )
    args = parser.parse_args(argv)

    artifact_dir = Path(env("ARTIFACT_DIR") or "./fuzz-artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    enabled = env("LLM_FUZZ_ENABLED", "1")
    if enabled not in ("1", "true", "TRUE", "yes", "YES"):
        log("LLM_FUZZ_ENABLED is off — writing empty outputs")
        write_outputs(artifact_dir, [], [])
        return 0

    source_paths: list[Path] = list(args.sources)
    if env("SOURCE_PATHS"):
        source_paths.extend(Path(p) for p in env("SOURCE_PATHS", "").split())

    max_prompt = int(env("LLM_FUZZ_MAX_PROMPT_CHARS") or "8000")
    max_seeds = int(env("LLM_FUZZ_MAX_SEEDS") or "32")
    max_tokens = int(env("LLM_FUZZ_MAX_TOKENS") or "64")
    max_seed_bytes = int(env("LLM_FUZZ_MAX_SEED_BYTES") or "4096")
    timeout_s = float(env("LLM_FUZZ_TIMEOUT_S") or "45")
    base_url = env("LLM_FUZZ_BASE_URL") or "http://127.0.0.1:11434"
    model = env("LLM_FUZZ_MODEL") or "Qwen2.5-Coder:1.5b"
    target_id = env("TARGET_ID") or "unknown"

    prior_crashes: list[str] = []
    crash_dir = artifact_dir / "crashes"
    if crash_dir.is_dir():
        prior_crashes = sorted(p.name for p in crash_dir.iterdir() if p.is_file())

    source_text = load_sources(source_paths, max_prompt)
    if not source_text.strip():
        log("no source text — writing empty outputs (fail-open)")
        write_outputs(artifact_dir, [], [])
        _maybe_github_env(artifact_dir, args.github_env)
        return 0

    prompt = build_prompt(source_text, target_id, prior_crashes, max_seeds, max_tokens)

    try:
        raw = ollama_chat(base_url, model, prompt, timeout_s)
        payload = parse_llm_json(raw)
        seeds, tokens = normalize_payload(payload, max_seeds, max_tokens, max_seed_bytes)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        log(f"Ollama failed ({exc!r}) — fail-open with empty outputs")
        write_outputs(artifact_dir, [], [])
        _maybe_github_env(artifact_dir, args.github_env)
        return 0

    write_outputs(artifact_dir, seeds, tokens)
    _maybe_github_env(artifact_dir, args.github_env)
    return 0


def _maybe_github_env(artifact_dir: Path, enabled: bool) -> None:
    if not enabled:
        return
    gh_env = env("GITHUB_ENV")
    if not gh_env:
        return
    env_file = artifact_dir / "llm_prepare.env"
    if not env_file.is_file():
        return
    with open(gh_env, "a", encoding="utf-8") as fh:
        fh.write(env_file.read_text(encoding="utf-8"))
    log(f"appended prepare env to GITHUB_ENV")


if __name__ == "__main__":
    raise SystemExit(main())
