#!/usr/bin/env python3
"""Background worker: LLM seed refresh + longer timed fuzz; follow-up PR comments.

Polls QUEUE_DIR for job-*.json, claims by rename to .running, processes one at a time.

Env:
  LLM_FUZZ_QUEUE_DIR     — default /data/llm_fuzz_queue
  LLM_FUZZ_BG_SECONDS    — longer fuzz budget (default 600)
  LLM_FUZZ_BG_MAX_SEEDS  — default 128
  LLM_FUZZ_POLL_S        — poll interval (default 15)
  LLM_FUZZ_DEPLOY_DIR    — path to deploy/ (default: parent of this script)
  GITHUB_TOKEN           — for PR status check + follow-up comments
  LOCAL_GCS_ROOT         — for ingest (default /data/local_gcs)
  Same LLM_FUZZ_* as prepare/triage
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def log(msg: str) -> None:
    print(f"==> llm_fuzz_worker: {msg}", flush=True)


def queue_dir() -> Path:
    return Path(env("LLM_FUZZ_QUEUE_DIR") or "/data/llm_fuzz_queue")


def deploy_dir() -> Path:
    override = env("LLM_FUZZ_DEPLOY_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def github_request(method: str, url: str, token: str, body: dict | None = None) -> dict | None:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clusterfuzz-llm-fuzz-worker",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        log(f"GitHub API {method} {url} → {exc.code}: {detail[:300]}")
        return None


def pr_is_open(repo: str, pr: int, token: str | None) -> bool:
    if not token:
        return True  # proceed; posting may fail later
    api = (env("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    result = github_request("GET", f"{api}/repos/{repo}/pulls/{pr}", token)
    if result is None:
        return True
    return result.get("state") == "open"


def claim_job(path: Path) -> Path | None:
    running = path.with_suffix(path.suffix + ".running")
    try:
        path.rename(running)
        return running
    except OSError:
        return None


def finish_job(running: Path, ok: bool) -> None:
    dest_suffix = ".done" if ok else ".failed"
    dest = running.with_name(running.name.replace(".json.running", f".json{dest_suffix}"))
    try:
        running.rename(dest)
    except OSError:
        try:
            running.unlink()
        except OSError:
            pass


def collect_crash_names(artifact_dir: Path) -> set[str]:
    crash_dir = artifact_dir / "crashes"
    if not crash_dir.is_dir():
        return set()
    return {p.name for p in crash_dir.iterdir() if p.is_file()}


def process_job(job_path: Path) -> bool:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    repo = job["repo"]
    pr = int(job["pr"])
    token = env("GITHUB_TOKEN")
    deploy = deploy_dir()
    prepare = deploy / "scripts" / "llm_fuzz_prepare.py"
    timed = deploy / "scripts" / "run_timed_fuzz.sh"
    ingest = deploy / "scripts" / "ingest_build.sh"
    post = deploy / "scripts" / "post_pr_comments.py"

    if not pr_is_open(repo, pr, token):
        log(f"PR {repo}#{pr} not open — drop job")
        return True

    binary = job.get("binary") or ""
    if not binary or not Path(binary).is_file():
        log(f"binary missing ({binary!r}) — cannot deep-fuzz")
        return False

    work = Path(job.get("artifact_dir") or "/tmp/llm-fuzz-bg") / f"bg-{job.get('id', 'job')}"
    work.mkdir(parents=True, exist_ok=True)
    # Seed prior crashes list for prepare context
    prior_dir = work / "crashes"
    prior_dir.mkdir(exist_ok=True)
    first = set(job.get("first_crash_names") or [])
    src_crash = Path(job.get("artifact_dir") or "") / "crashes"
    if src_crash.is_dir():
        for p in src_crash.iterdir():
            if p.is_file():
                shutil.copy2(p, prior_dir / p.name)

    source_paths = [p for p in (job.get("source_paths") or []) if p]
    if not source_paths:
        # Demo default
        demo = deploy / "demo-target" / "demo_fuzz.cc"
        if demo.is_file():
            source_paths = [str(demo)]

    bg_seconds = env("LLM_FUZZ_BG_SECONDS") or "600"
    child_env = os.environ.copy()
    child_env["ARTIFACT_DIR"] = str(work)
    child_env["TARGET_ID"] = str(job.get("target_id") or "unknown")
    child_env["LLM_FUZZ_ENABLED"] = "1"
    child_env["LLM_FUZZ_MAX_SEEDS"] = env("LLM_FUZZ_BG_MAX_SEEDS") or "128"
    child_env["FUZZ_ENGINE"] = str(job.get("engine") or "libfuzzer")
    child_env["CLUSTERFUZZ_UI_URL"] = str(job.get("ui_url") or "http://127.0.0.1:9300")
    child_env["GITHUB_REPOSITORY"] = repo
    child_env["PR_NUMBER"] = str(pr)
    if token:
        child_env["GITHUB_TOKEN"] = token

    log(f"prepare seeds for {repo}#{pr}")
    subprocess.run(
        [sys.executable, str(prepare), *source_paths],
        env=child_env,
        check=False,
    )

    # Load CORPUS_DIR / EXTRA_FUZZ_ARGS
    prep_env = work / "llm_prepare.env"
    if prep_env.is_file():
        for line in prep_env.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                child_env[k.strip()] = v.strip()

    log(f"timed fuzz {bg_seconds}s on {binary}")
    subprocess.run(
        ["bash", str(timed), binary, bg_seconds, str(work)],
        env=child_env,
        check=False,
    )

    zip_path = job.get("zip") or ""
    if zip_path and Path(zip_path).is_file() and ingest.is_file():
        gcs = env("LOCAL_GCS_ROOT") or "/data/local_gcs"
        log(f"ingest {zip_path} → {gcs}")
        subprocess.run(
            ["bash", str(ingest), zip_path],
            env={**child_env, "LOCAL_GCS_ROOT": gcs},
            check=False,
        )

    new_crashes = collect_crash_names(work) - first
    if not new_crashes:
        log("no new crash hashes — skip follow-up comment")
        return True

    if not token:
        log("GITHUB_TOKEN unset — cannot post follow-up")
        return True

    # Isolate only new crashes for the commenter
    comment_crashes = work / "crashes_new"
    if comment_crashes.exists():
        shutil.rmtree(comment_crashes)
    comment_crashes.mkdir()
    for name in new_crashes:
        src = work / "crashes" / name
        if src.is_file():
            shutil.copy2(src, comment_crashes / name)

    # Temporarily point crashes/ at new-only set
    full_crashes = work / "crashes"
    backup = work / "crashes_all"
    if backup.exists():
        shutil.rmtree(backup)
    full_crashes.rename(backup)
    comment_crashes.rename(full_crashes)

    # Marker for post_pr_comments title
    (work / "triage.md").write_text(
        "## Background LLM-assisted pass\n\n"
        "Additional unique crash(es) found after the PR timed budget "
        f"({bg_seconds}s deep fuzz).\n",
        encoding="utf-8",
    )

    log(f"posting follow-up for {len(new_crashes)} new crash(es)")
    rc = subprocess.run(
        [sys.executable, str(post)],
        env={
            **child_env,
            "ARTIFACT_DIR": str(work),
            "REVIEW_TITLE": "ClusterFuzz local — background LLM-assisted findings",
        },
        check=False,
    )
    return rc.returncode == 0


def main() -> int:
    qdir = queue_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    poll = float(env("LLM_FUZZ_POLL_S") or "15")
    log(f"polling {qdir} every {poll}s")

    while True:
        jobs = sorted(qdir.glob("job-*.json"))
        # Ignore already claimed suffixes
        jobs = [j for j in jobs if j.name.endswith(".json") and ".running" not in j.name]
        if not jobs:
            time.sleep(poll)
            continue
        claimed = claim_job(jobs[0])
        if not claimed:
            time.sleep(1)
            continue
        log(f"claimed {claimed.name}")
        try:
            ok = process_job(claimed)
        except Exception as exc:  # noqa: BLE001 — worker must not die
            log(f"job error: {exc!r}")
            ok = False
        finish_job(claimed, ok)
        # One deep job at a time; brief pause before next
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
