#!/usr/bin/env python3
"""Emit a minimal OTLP/HTTP span for a ClusterFuzz PR fuzz job.

No-op (exit 0) if the collector is unreachable or OTEL is disabled.
Does not require the OpenTelemetry SDK — posts JSON to the OTLP HTTP endpoint.

Env:
  OTEL_EXPORTER_OTLP_ENDPOINT — e.g. http://127.0.0.1:4318 (required to emit)
  OTEL_SERVICE_NAME           — default: clusterfuzz-pr-fuzz
  OTEL_DISABLED               — if "1"/"true", skip

Optional attributes (env):
  GITHUB_REPOSITORY / REPO, PR_NUMBER / PR, FUZZ_ENGINE / ENGINE,
  TARGET_ID, FUZZ_SECONDS, ARTIFACT_DIR (reads crash_count.txt), CRASH_COUNT
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def _disabled() -> bool:
    return (_env("OTEL_DISABLED") or "").lower() in ("1", "true", "yes")


def _crash_count() -> int:
    explicit = _env("CRASH_COUNT")
    if explicit is not None:
        try:
            return int(explicit)
        except ValueError:
            pass
    artifact = Path(_env("ARTIFACT_DIR") or "./fuzz-artifacts")
    count_file = artifact / "crash_count.txt"
    if count_file.is_file():
        try:
            return int(count_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            return 0
    crash_dir = artifact / "crashes"
    if crash_dir.is_dir():
        return sum(1 for p in crash_dir.iterdir() if p.is_file())
    return 0


def _otlp_payload(
    *,
    service: str,
    repo: str,
    pr: str,
    engine: str,
    target_id: str,
    crash_count: int,
    fuzz_seconds: int,
) -> dict:
    now_ns = time.time_ns()
    # ~fuzz_seconds span ending now
    start_ns = now_ns - max(fuzz_seconds, 1) * 1_000_000_000
    trace_id = f"{now_ns & ((1 << 64) - 1):016x}{(now_ns >> 3) & ((1 << 64) - 1):016x}"
    span_id = f"{(now_ns ^ 0xA5A5A5A5) & ((1 << 64) - 1):016x}"

    def attr(key: str, value) -> dict:
        if isinstance(value, bool):
            return {"key": key, "value": {"boolValue": value}}
        if isinstance(value, int):
            return {"key": key, "value": {"intValue": str(value)}}
        return {"key": key, "value": {"stringValue": str(value)}}

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        attr("service.name", service),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "clusterfuzz.pr_fuzz", "version": "1"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "pr_fuzz_job",
                                "kind": 1,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(now_ns),
                                "attributes": [
                                    attr("repo", repo),
                                    attr("pr", pr),
                                    attr("engine", engine),
                                    attr("target_id", target_id),
                                    attr("crash_count", crash_count),
                                    attr("fuzz_seconds", fuzz_seconds),
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def main() -> int:
    if _disabled():
        print("OTLP: disabled via OTEL_DISABLED")
        return 0

    endpoint = _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        print("OTLP: OTEL_EXPORTER_OTLP_ENDPOINT unset — skip")
        return 0

    base = endpoint.rstrip("/")
    url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
    service = _env("OTEL_SERVICE_NAME") or "clusterfuzz-pr-fuzz"
    repo = _env("GITHUB_REPOSITORY") or _env("REPO") or "unknown"
    pr = _env("PR_NUMBER") or _env("PR") or "0"
    engine = _env("FUZZ_ENGINE") or _env("ENGINE") or "unknown"
    target_id = _env("TARGET_ID") or "unknown"
    fuzz_seconds = int(_env("FUZZ_SECONDS") or "0")
    crash_count = _crash_count()

    body = json.dumps(
        _otlp_payload(
            service=service,
            repo=repo,
            pr=pr,
            engine=engine,
            target_id=target_id,
            crash_count=crash_count,
            fuzz_seconds=fuzz_seconds,
        )
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "clusterfuzz-pr-fuzz-otel",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            print(f"OTLP: exported span to {url} (HTTP {resp.status})")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"OTLP: collector unavailable ({exc}) — continuing", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — never fail the PR job
        print(f"OTLP: unexpected error ({exc}) — continuing", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
