#!/usr/bin/env python3
"""Post ClusterFuzz timed-fuzz crash findings as GitHub PR review comments.

Reads crash artifacts (from run_timed_fuzz.sh or run_timed_atheris.sh) and
creates a PR review via the GitHub API (GitHub App or Actions).

Required env:
  GITHUB_TOKEN   — PAT, App installation token, or Actions token
  GITHUB_REPOSITORY — owner/repo
  PR_NUMBER      — pull request number (or GITHUB_REF / event payload)

Optional env:
  ARTIFACT_DIR, CLUSTERFUZZ_UI_URL, GITHUB_API_URL, COMMENT_MAX_CRASHES
  REVIEW_TITLE, DRY_RUN
  COMMENT_SOURCE — github-app | actions (default: actions if GITHUB_ACTIONS else github-app)
  SMOKE_DEMO — 1 for expected demo/BOOM labeling
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from asan_helpers import is_demo_boom_crash, parse_asan  # noqa: E402
from llm_chat import provider_label  # noqa: E402


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


def comment_source() -> str:
    explicit = (env("COMMENT_SOURCE") or "").strip().lower()
    if explicit in ("github-app", "app"):
        return "GitHub App"
    if explicit in ("actions", "gha"):
        return "GitHub Actions"
    if env("GITHUB_ACTIONS") == "true":
        return "GitHub Actions"
    return "GitHub App"


def split_triage(triage_md: str) -> tuple[str, bool]:
    """Return (body, is_llm). LLM sections are collapsed by the caller."""
    text = triage_md.strip()
    if not text:
        return "", False
    is_llm = text.lstrip().startswith("## LLM triage")
    return text, is_llm


def triage_summary_label(triage_md: str) -> str:
    """Prefer label embedded in triage.md header; else current provider_label()."""
    text = triage_md.lstrip()
    if text.startswith("## LLM triage ("):
        end = text.find(")", len("## LLM triage ("))
        if end > 0:
            return text[len("## LLM triage (") : end]
    try:
        return provider_label()
    except ValueError:
        return "LLM"


def build_review_body(
    crashes: list[tuple[str, bytes]],
    log_excerpt: str,
    log_text: str,
    ui_url: str,
    repo: str,
    pr_number: str,
    triage_md: str = "",
    title: str | None = None,
    smoke_demo: bool = False,
) -> str:
    info = parse_asan(log_text or log_excerpt)
    source = comment_source()
    if title:
        heading = title
    elif smoke_demo or any(is_demo_boom_crash(d) for _, d in crashes):
        heading = "ClusterFuzz smoke (expected demo crash)"
        smoke_demo = True
    else:
        heading = "ClusterFuzz local — fuzz findings"

    parts = [
        f"## {heading}",
        "",
        f"Timed fuzz via **{source}** for `{repo}` PR #{pr_number}.",
        "",
    ]

    if smoke_demo and crashes:
        parts += [
            "This matches the intentional `demo-target` / `BOOM` harness — "
            "**not** treated as a new regression for unrelated PR changes.",
            "",
        ]

    if not crashes:
        # Skip / no-crash path: fuzz.log often holds the reason
        reason = (log_text or log_excerpt or "").strip().splitlines()
        lead = reason[0] if reason else "No crash artifacts were collected in this run."
        parts += [lead, ""]
        if len(reason) > 1:
            parts += [
                "<details><summary>Details</summary>",
                "",
                "```",
                truncate("\n".join(reason), 2000),
                "```",
                "",
                "</details>",
                "",
            ]
        return "\n".join(parts).rstrip() + "\n"

    # Lead with structured ASan signal
    if info.bug_type:
        parts.append(f"**{info.bug_type}** — {info.display_frame()}")
    else:
        parts.append(f"**{len(crashes)} crash artifact(s)** found.")
    parts.append("")

    if info.summary:
        parts.append(f"- SUMMARY: `{info.summary}`")
    for name, data in crashes:
        parts.append(f"- `{name}`: `{hex_preview(data)}`")
    parts.append("")

    if ui_url and not smoke_demo:
        parts.append(
            f"Local ClusterFuzz UI (reachable on the farm host only): `{ui_url.rstrip('/')}`"
        )
        parts.append("")

    triage_body, is_llm = split_triage(triage_md)
    if triage_body:
        if is_llm and not smoke_demo:
            label = triage_summary_label(triage_md)
            parts += [
                f"<details><summary>LLM triage ({label})</summary>",
                "",
                triage_body,
                "",
                "</details>",
                "",
            ]
        else:
            parts += [triage_body, ""]

    if log_excerpt.strip() and not smoke_demo:
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
    elif log_excerpt.strip() and smoke_demo:
        parts += [
            "<details><summary>ASan excerpt</summary>",
            "",
            "```",
            truncate(log_excerpt, 1500),
            "```",
            "",
            "</details>",
            "",
        ]

    parts.append(
        f"_Posted by ClusterFuzz local glue ({source} — not upstream ClusterFuzz)._"
    )
    return "\n".join(parts)


def resolve_pr_number() -> str:
    explicit = env("PR_NUMBER") or env("PULL_REQUEST_NUMBER")
    if explicit:
        return explicit
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
    smoke_demo = env("SMOKE_DEMO") in ("1", "true", "TRUE", "yes")

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
        crashes,
        excerpt,
        log_text,
        ui_url,
        repo,
        pr_number,
        triage_md=triage_md,
        title=title,
        smoke_demo=smoke_demo,
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
