#!/usr/bin/env python3
"""Enqueue a background LLM-assisted deep-fuzz job (fail-open).

Writes JSON under QUEUE_DIR (default /data/llm_fuzz_queue or ./data/llm_fuzz_queue).

Env (typical from Actions):
  GITHUB_REPOSITORY, PR_NUMBER, GITHUB_SHA
  ARTIFACT_DIR, BINARY, ZIP, FUZZ_ENGINE, TARGET_ID
  LLM_FUZZ_QUEUE_DIR — override queue path
  LLM_FUZZ_BG_ENABLED — default "1"
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def log(msg: str) -> None:
    print(f"==> llm_fuzz_enqueue: {msg}", flush=True)


def default_queue_dir() -> Path:
    override = env("LLM_FUZZ_QUEUE_DIR")
    if override:
        return Path(override)
    for candidate in (Path("/data/llm_fuzz_queue"), Path("data/llm_fuzz_queue"), Path("./llm_fuzz_queue")):
        parent = candidate.parent
        if parent.is_dir() or str(candidate).startswith("/data"):
            return candidate
    return Path("/data/llm_fuzz_queue")


def main() -> int:
    if env("LLM_FUZZ_BG_ENABLED", "1") not in ("1", "true", "TRUE", "yes", "YES"):
        log("background enqueue disabled")
        return 0
    if env("LLM_FUZZ_ENABLED", "1") not in ("1", "true", "TRUE", "yes", "YES"):
        log("LLM_FUZZ_ENABLED off — skip enqueue")
        return 0

    repo = env("GITHUB_REPOSITORY")
    pr = env("PR_NUMBER") or env("PULL_REQUEST_NUMBER")
    if not repo or not pr:
        log("missing GITHUB_REPOSITORY or PR_NUMBER — skip")
        return 0

    queue_dir = default_queue_dir()
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"cannot create queue dir {queue_dir}: {exc}")
        return 0

    crash_dir = Path(env("ARTIFACT_DIR") or "./fuzz-artifacts") / "crashes"
    first_crash_names: list[str] = []
    if crash_dir.is_dir():
        first_crash_names = sorted(p.name for p in crash_dir.iterdir() if p.is_file())

    job = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "repo": repo,
        "pr": int(pr),
        "sha": env("GITHUB_SHA") or "",
        "engine": env("FUZZ_ENGINE") or "libfuzzer",
        "target_id": env("TARGET_ID") or "unknown",
        "artifact_dir": str(Path(env("ARTIFACT_DIR") or "./fuzz-artifacts").resolve()),
        "binary": env("BINARY") or "",
        "zip": env("ZIP") or "",
        "first_crash_names": first_crash_names,
        "ui_url": env("CLUSTERFUZZ_UI_URL") or "http://127.0.0.1:9300",
        "source_paths": (env("SOURCE_PATHS") or "").split(),
    }

    path = queue_dir / f"job-{job['id']}.json"
    path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    log(f"enqueued {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
