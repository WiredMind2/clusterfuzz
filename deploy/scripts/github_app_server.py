#!/usr/bin/env python3
"""Sourcery-style ClusterFuzz GitHub App: webhooks + local fuzz worker.

Endpoints:
  GET  /healthz
  POST /github/webhooks

Env: see deploy/github-app.env.example
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Allow importing sibling modules when run as a script
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from github_app_auth import installation_token  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("cf-github-app")


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def require(name: str) -> str:
    val = env(name)
    if not val:
        raise RuntimeError(f"missing required env {name}")
    return val


def deploy_dir() -> Path:
    return Path(env("DEPLOY_DIR") or str(_SCRIPTS.parent)).resolve()


def queue_dir() -> Path:
    p = Path(env("GITHUB_APP_QUEUE_DIR") or "/data/github_app_queue")
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir() -> Path:
    p = Path(env("GITHUB_APP_WORK_DIR") or "/data/github_app_work")
    p.mkdir(parents=True, exist_ok=True)
    return p


def deny_list_path() -> Path:
    return Path(
        env("GITHUB_APP_DENY_LIST")
        or str(deploy_dir() / "github-app" / "deny-repos.txt")
    )


def load_deny_set() -> set[str]:
    path = deny_list_path()
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.add(s.lower())
    return out


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def enqueue_job(job: dict[str, Any]) -> Path:
    q = queue_dir()
    job_id = job.get("id") or str(uuid.uuid4())
    job["id"] = job_id
    job["enqueued_at"] = time.time()
    path = q / f"job-{job_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.rename(path)
    log.info("enqueued %s for %s#%s", job_id, job.get("repository"), job.get("pr_number"))
    return path


def claim_next_job() -> Path | None:
    q = queue_dir()
    jobs = sorted(q.glob("job-*.json"), key=lambda p: p.stat().st_mtime)
    for path in jobs:
        claimed = path.with_suffix(".claimed")
        try:
            path.rename(claimed)
            return claimed
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def clone_pr_head(repo: str, sha: str, dest: Path, token: str) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "--depth", "50", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "fetch", "--depth", "50", "origin", sha],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "checkout", "--force", sha],
        check=True,
        capture_output=True,
        text=True,
    )


def run_job(job_path: Path) -> None:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    repo = job["repository"]
    pr_number = str(job["pr_number"])
    sha = job["sha"]
    installation_id = job["installation_id"]
    job_id = job.get("id", job_path.stem)

    deny = load_deny_set()
    if repo.lower() in deny:
        log.info("deny-list skip %s", repo)
        job_path.unlink(missing_ok=True)
        return

    log.info("start job %s %s#%s @ %s", job_id, repo, pr_number, sha[:12])
    token = installation_token(installation_id)
    dest = work_dir() / job_id
    try:
        clone_pr_head(repo, sha, dest, token)
        env_vars = os.environ.copy()
        env_vars.update(
            {
                "REPO_ROOT": str(dest),
                "DEPLOY_DIR": str(deploy_dir()),
                "GITHUB_TOKEN": token,
                "GITHUB_REPOSITORY": repo,
                "PR_NUMBER": pr_number,
                "GITHUB_SHA": sha,
                "ARTIFACT_DIR": str(dest / "fuzz-artifacts"),
                "TARGET_ID": repo.split("/")[-1].lower(),
            }
        )
        script = deploy_dir() / "scripts" / "run_pr_fuzz_job.sh"
        result = subprocess.run(
            ["bash", str(script)],
            env=env_vars,
            cwd=str(dest),
            capture_output=False,
        )
        if result.returncode != 0:
            log.error("job %s failed with exit %s", job_id, result.returncode)
        else:
            log.info("job %s finished ok", job_id)
    except Exception:
        log.exception("job %s crashed", job_id)
    finally:
        keep = env("GITHUB_APP_KEEP_WORK") == "1"
        if not keep and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        done = job_path.with_suffix(".done")
        try:
            job_path.rename(done)
            # Retain a few done markers; prune old
            _prune_done()
        except OSError:
            job_path.unlink(missing_ok=True)


def _prune_done(keep: int = 50) -> None:
    q = queue_dir()
    done = sorted(q.glob("job-*.done"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in done[keep:]:
        path.unlink(missing_ok=True)


class WorkerPool:
    def __init__(self, max_workers: int) -> None:
        self.max_workers = max(1, max_workers)
        self._active = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        t = threading.Thread(target=self._loop, name="cf-github-app-dispatcher", daemon=True)
        t.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                can = self._active < self.max_workers
            if not can:
                time.sleep(1)
                continue
            claimed = claim_next_job()
            if not claimed:
                time.sleep(1)
                continue
            with self._lock:
                self._active += 1

            def _run(path: Path = claimed) -> None:
                try:
                    run_job(path)
                finally:
                    with self._lock:
                        self._active -= 1

            threading.Thread(target=_run, name=f"fuzz-{claimed.stem}", daemon=True).start()


def handle_pull_request(payload: dict[str, Any]) -> tuple[int, str]:
    action = payload.get("action")
    if action not in ("opened", "synchronize", "reopened"):
        return 200, f"ignored action={action}"

    pr = payload.get("pull_request") or {}
    repo_obj = payload.get("repository") or {}
    installation = payload.get("installation") or {}
    repo = repo_obj.get("full_name")
    if not repo:
        return 400, "missing repository"
    if repo.lower() in load_deny_set():
        log.info("deny-list drop webhook for %s", repo)
        return 200, "denied"

    # Draft PRs: still fuzz (Sourcery-like); no special skip
    sha = (pr.get("head") or {}).get("sha")
    number = pr.get("number")
    installation_id = installation.get("id")
    if not sha or not number or not installation_id:
        return 400, "missing sha/number/installation"

    enqueue_job(
        {
            "repository": repo,
            "pr_number": number,
            "sha": sha,
            "installation_id": installation_id,
            "action": action,
        }
    )
    return 202, "queued"


class Handler(BaseHTTPRequestHandler):
    server_version = "ClusterFuzzGitHubApp/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - " + fmt, self.address_string(), *args)

    def _send(self, code: int, body: str, content_type: str = "text/plain") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/healthz", "/health", "/"):
            self._send(200, "ok\n")
            return
        self._send(404, "not found\n")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/github/webhooks":
            self._send(404, "not found\n")
            return
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else b""
        try:
            secret = require("GITHUB_APP_WEBHOOK_SECRET")
        except RuntimeError as exc:
            log.error("%s", exc)
            self._send(500, "server misconfigured\n")
            return
        sig = self.headers.get("X-Hub-Signature-256")
        if not verify_signature(secret, body, sig):
            self._send(401, "invalid signature\n")
            return
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            self._send(200, "pong\n")
            return
        if event != "pull_request":
            self._send(200, f"ignored event={event}\n")
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, "bad json\n")
            return
        try:
            code, msg = handle_pull_request(payload)
        except Exception:
            log.exception("webhook handler failed")
            self._send(500, "handler error\n")
            return
        self._send(code, msg + "\n")


def main() -> int:
    bind = env("GITHUB_APP_BIND") or "127.0.0.1"
    port = int(env("GITHUB_APP_PORT") or "9310")
    # Validate critical env early
    require("GITHUB_APP_ID")
    require("GITHUB_APP_WEBHOOK_SECRET")
    require("GITHUB_APP_PRIVATE_KEY_PATH")
    if not Path(require("GITHUB_APP_PRIVATE_KEY_PATH")).is_file():
        log.error("private key not found at %s", env("GITHUB_APP_PRIVATE_KEY_PATH"))
        return 2

    max_workers = int(env("GITHUB_APP_MAX_WORKERS") or "1")
    pool = WorkerPool(max_workers)
    pool.start()

    httpd = ThreadingHTTPServer((bind, port), Handler)
    log.info(
        "listening on http://%s:%s/github/webhooks (public: https://fuzzing.tetrazero.com/github/webhooks)",
        bind,
        port,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        pool.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
