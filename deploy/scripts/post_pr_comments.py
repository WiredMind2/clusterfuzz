#!/usr/bin/env python3
"""Post ClusterFuzz timed-fuzz crash findings as GitHub PR review comments.

Reads crash artifacts (from run_timed_fuzz.sh or run_timed_atheris.sh) and
creates a PR review via the GitHub API. Intended for the self-hosted fuzz-pr
workflow (libFuzzer or Atheris).

Required env:
  GITHUB_TOKEN   — PAT or Actions token with pull-requests: write
  GITHUB_REPOSITORY — owner/repo (set automatically in Actions)
  PR_NUMBER      — pull request number (or GITHUB_REF / event payload)

Optional env:
  ARTIFACT_DIR   — directory with crashes/ and fuzz.log (default: ./fuzz-artifacts)
  CLUSTERFUZZ_UI_URL — base URL for local CF UI links (default: http://localhost:9300)
  GITHUB_API_URL — API base (default: https://api.github.com)
  COMMENT_MAX_CRASHES — max crash files to include (default: 10)
  REVIEW_TITLE   — override review heading
  DRY_RUN        — if "1", print payload and exit without posting
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


def require(name: str) -> str:
    val = env(name)
    if not val:
        print(f"error: missing required env {name}", file=sys.stderr)
        sys.exit(2)
    return val


def github_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clusterfuzz-local-pr-comments",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"GitHub API {method} {url} failed: {exc.code}\n{detail}", file=sys.stderr)
        sys.exit(1)


def truncate(text: str, limit: int = 3500) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated) …\n"


def collect_crashes(artifact_dir: Path, limit: int) -> list[tuple[str, bytes]]:
    crash_dir = artifact_dir / "crashes"
    files: list[Path] = []
    if crash_dir.is_dir():
        files = sorted(
            p
            for p in crash_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    # Fallback: artifact_prefix files in artifact_dir root
    if not files:
        files = sorted(
            p
            for p in artifact_dir.iterdir()
            if p.is_file()
            and p.name.startswith(("crash-", "oom-", "timeout-", "leak-"))
        )
    out: list[tuple[str, bytes]] = []
    for path in files[:limit]:
        try:
            out.append((path.name, path.read_bytes()))
        except OSError as exc:
            print(f"warning: could not read {path}: {exc}", file=sys.stderr)
    return out


def extract_asan_excerpt(log_text: str, max_lines: int = 40) -> str:
    lines = log_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "ERROR: AddressSanitizer" in line or "ERROR: LeakSanitizer" in line:
            start = i
            break
        if "SUMMARY:" in line and start is None:
            start = max(0, i - 15)
    if start is None:
        # Last non-empty chunk
        nonempty = [ln for ln in lines if ln.strip()]
        return "\n".join(nonempty[-max_lines:])
    return "\n".join(lines[start : start + max_lines])


def hex_preview(data: bytes, n: int = 64) -> str:
    chunk = data[:n]
    hx = chunk.hex()
    try:
        ascii_approx = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    except Exception:
        ascii_approx = ""
    return f"hex={hx} ascii={ascii_approx!r} len={len(data)}"


def build_review_body(
    crashes: list[tuple[str, bytes]],
    log_excerpt: str,
    ui_url: str,
    repo: str,
    pr_number: str,
    triage_md: str = "",
    title: str | None = None,
) -> str:
    heading = title or "ClusterFuzz local — fuzz findings"
    parts = [
        f"## {heading}",
        "",
        f"Timed fuzz run on self-hosted runner for `{repo}` PR #{pr_number}.",
        "",
    ]
    if not crashes:
        parts += [
            "No crash artifacts were collected in this run.",
            "",
            "<details><summary>Fuzzer log excerpt</summary>",
            "",
            "```",
            truncate(log_excerpt, 2000) or "(empty)",
            "```",
            "",
            "</details>",
        ]
        if triage_md.strip():
            parts += ["", triage_md.strip(), ""]
        return "\n".join(parts)

    parts.append(f"**{len(crashes)} crash artifact(s)** found.")
    parts.append("")
    if ui_url:
        parts.append(f"Local ClusterFuzz UI: {ui_url.rstrip('/')}")
        parts.append("")

    for name, data in crashes:
        parts.append(f"### `{name}`")
        parts.append("")
        parts.append(f"- Input: `{hex_preview(data)}`")
        parts.append("")

    if triage_md.strip():
        parts += [triage_md.strip(), ""]

    if log_excerpt.strip():
        parts += [
            "<details><summary>Fuzzer log excerpt</summary>",
            "",
            "```",
            truncate(log_excerpt),
            "```",
            "",
            "</details>",
            "",
        ]

    parts.append(
        "_Posted by ClusterFuzz local glue "
        "(`post_pr_comments.py` / GitHub App — not upstream ClusterFuzz)._"
    )
    return "\n".join(parts)


def resolve_pr_number() -> str:
    explicit = env("PR_NUMBER") or env("PULL_REQUEST_NUMBER")
    if explicit:
        return explicit
    # Actions: refs/pull/123/merge
    ref = env("GITHUB_REF", "") or ""
    if ref.startswith("refs/pull/") and "/merge" in ref:
        return ref.split("/")[2]
    event_path = env("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        pr = payload.get("pull_request") or {}
        if "number" in pr:
            return str(pr["number"])
        if "number" in payload:
            return str(payload["number"])
    print(
        "error: could not resolve PR number "
        "(set PR_NUMBER or run in a pull_request Actions context)",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> int:
    token = require("GITHUB_TOKEN")
    repo = require("GITHUB_REPOSITORY")
    pr_number = resolve_pr_number()
    api = (env("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    artifact_dir = Path(env("ARTIFACT_DIR") or "./fuzz-artifacts")
    ui_url = env("CLUSTERFUZZ_UI_URL") or "http://localhost:9300"
    max_crashes = int(env("COMMENT_MAX_CRASHES") or "10")
    dry_run = env("DRY_RUN") == "1"

    crashes = collect_crashes(artifact_dir, max_crashes)
    log_path = artifact_dir / "fuzz.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    excerpt = extract_asan_excerpt(log_text)
    triage_path = artifact_dir / "triage.md"
    triage_md = (
        triage_path.read_text(encoding="utf-8", errors="replace") if triage_path.is_file() else ""
    )
    title = env("REVIEW_TITLE")

    body = build_review_body(
        crashes, excerpt, ui_url, repo, pr_number, triage_md=triage_md, title=title
    )
    payload = {"body": body, "event": "COMMENT"}

    url = f"{api}/repos/{repo}/pulls/{pr_number}/reviews"
    print(f"==> Posting PR review comment to {repo}#{pr_number} ({len(crashes)} crash file(s))")
    if dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    result = github_request("POST", url, token, payload)
    html = result.get("html_url") or result.get("id")
    print(f"Posted review: {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
