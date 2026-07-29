#!/usr/bin/env python3
"""Decide whether a PR diff touches fuzzable sources.

Exit codes:
  0 — fuzzable delta (or FORCE / no base → fuzz)
  2 — no fuzzable delta (caller should skip timed fuzz)
  1 — usage / error

Env / args:
  --repo-root DIR (default .)
  --base SHA
  --head SHA (default HEAD)
  FUZZ_DELTA_FORCE=1 — always fuzzable
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

FUZZABLE_RE = re.compile(
    r"(^|/)"
    r"("
    r"fuzz/"
    r"|deploy/demo-target/"
    r"|demo-target/"
    r"|.*_fuzzer\.py$"
    r"|.*_fuzzer\.(c|cc|cpp)$"
    r"|fuzz/build\.sh$"
    r")",
    re.IGNORECASE,
)


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    # Merge-base aware when possible
    try:
        mb = subprocess.check_output(
            ["git", "-C", str(repo), "merge-base", base, head],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        mb = base
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--name-only", f"{mb}...{head}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--name-only", base, head],
            text=True,
        )
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def is_fuzzable(path: str) -> bool:
    p = path.replace("\\", "/")
    return bool(FUZZABLE_RE.search(p))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base", default=env("BASE_SHA") or "")
    parser.add_argument("--head", default=env("GITHUB_SHA") or "HEAD")
    args = parser.parse_args()

    if env("FUZZ_DELTA_FORCE") in ("1", "true", "TRUE", "yes"):
        print("fuzzable: force")
        return 0

    if not args.base:
        print("fuzzable: no-base (full run)")
        return 0

    repo = args.repo_root.resolve()
    try:
        files = changed_files(repo, args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"error: git diff failed: {exc}", file=sys.stderr)
        return 1

    hits = [f for f in files if is_fuzzable(f)]
    if hits:
        print("fuzzable:")
        for f in hits[:30]:
            print(f"  {f}")
        return 0

    print("skip: no fuzzable delta")
    for f in files[:40]:
        print(f"  {f}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
