#!/usr/bin/env python3
"""GitHub App JWT and installation access tokens."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import jwt  # PyJWT
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "error: PyJWT required — pip install 'PyJWT[crypto]'"
    ) from exc


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def require(name: str) -> str:
    val = env(name)
    if not val:
        raise RuntimeError(f"missing required env {name}")
    return val


def load_private_key() -> str:
    path = require("GITHUB_APP_PRIVATE_KEY_PATH")
    pem = Path(path).read_text(encoding="utf-8")
    if "BEGIN" not in pem:
        raise RuntimeError(f"invalid private key at {path}")
    return pem


def app_jwt(app_id: str | None = None, private_key: str | None = None) -> str:
    app_id = app_id or require("GITHUB_APP_ID")
    private_key = private_key or load_private_key()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def github_request(
    method: str,
    url: str,
    token: str,
    *,
    token_type: str = "Bearer",
    body: dict[str, Any] | None = None,
    accept: str = "application/vnd.github+json",
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": accept,
            "Authorization": f"{token_type} {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clusterfuzz-local-github-app",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code}\n{detail}") from exc


def installation_token(
    installation_id: int | str,
    *,
    api: str | None = None,
) -> str:
    """Mint a short-lived installation access token."""
    api = (api or env("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    token = app_jwt()
    url = f"{api}/app/installations/{installation_id}/access_tokens"
    # GitHub Apps authenticate JWT as "Bearer"
    result = github_request("POST", url, token, body={})
    access = result.get("token")
    if not access:
        raise RuntimeError(f"no token in installation response: {result}")
    return str(access)


def post_issue_comment(
    repo: str,
    issue_number: int | str,
    body: str,
    token: str,
    *,
    api: str | None = None,
) -> dict[str, Any]:
    api = (api or env("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    url = f"{api}/repos/{repo}/issues/{issue_number}/comments"
    return github_request("POST", url, token, body={"body": body})
