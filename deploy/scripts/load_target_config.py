#!/usr/bin/env python3
"""Load deploy/targets/<id>.yaml for App / Actions PR fuzz jobs.

Minimal YAML subset (no PyYAML required): key: value and simple lists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _deploy_dir() -> Path:
    env = os.environ.get("DEPLOY_DIR")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def targets_dir() -> Path:
    override = os.environ.get("CLUSTERFUZZ_TARGETS_DIR")
    if override:
        return Path(override).resolve()
    return _deploy_dir() / "targets"


def slug_from_repo(repo: str) -> str:
    name = repo.split("/")[-1].lower()
    return re.sub(r"[^a-z0-9-]", "-", name)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small subset used by targets/*.yaml."""
    root: dict[str, Any] = {}
    # stack entries: (indent, dict)
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    # When we see `key:` with no value, wait for list items or nested keys.
    pending_key: str | None = None
    pending_indent = -1

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        if line.startswith("- "):
            if pending_key is None:
                continue
            # Parent dict is the one that owns pending_key at pending_indent.
            while len(stack) > 1 and stack[-1][0] >= pending_indent:
                stack.pop()
            parent = stack[-1][1]
            if not isinstance(parent.get(pending_key), list):
                parent[pending_key] = []
            parent[pending_key].append(line[2:].strip().strip("'\""))
            continue

        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if rest == "":
            pending_key = key
            pending_indent = indent
            # Placeholder; replaced by list or nested dict on following lines.
            parent[key] = {}
            stack.append((indent, parent[key]))
            continue

        pending_key = None
        if rest.lower() in ("true", "yes"):
            parent[key] = True
        elif rest.lower() in ("false", "no"):
            parent[key] = False
        elif re.fullmatch(r"-?\d+", rest):
            parent[key] = int(rest)
        else:
            parent[key] = rest.strip("'\"")

    return root


def load_target_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return _parse_simple_yaml(text)


def find_target(
    *,
    target_id: str | None = None,
    github_repo: str | None = None,
) -> dict[str, Any] | None:
    """Return the first matching target config, or None."""
    tdir = targets_dir()
    if not tdir.is_dir():
        return None

    paths = sorted(p for p in tdir.glob("*.yaml") if not p.name.startswith("_"))

    # Prefer exact filename match first
    if target_id:
        slug = target_id.lower()
        exact = tdir / f"{slug}.yaml"
        if exact.is_file():
            paths = [exact] + [p for p in paths if p != exact]

    for path in paths:
        cfg = load_target_file(path)
        if not cfg:
            continue
        cfg_id = str(cfg.get("id", "")).lower()
        if target_id and (cfg_id == target_id.lower() or path.stem.lower() == target_id.lower()):
            cfg["_path"] = str(path)
            return cfg
        if github_repo and str(cfg.get("github", "")).lower() == github_repo.lower():
            cfg["_path"] = str(path)
            return cfg
    return None


def apply_target_to_environ(cfg: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, str]:
    """Merge target YAML into env dict (does not overwrite existing non-empty env)."""
    env = environ if environ is not None else os.environ.copy()

    def set_if_empty(key: str, value: Any) -> None:
        if value is None or value == "":
            return
        cur = env.get(key)
        if cur not in (None, ""):
            return
        env[key] = str(value)

    engine = cfg.get("engine")
    if engine:
        set_if_empty("FUZZ_ENGINE", engine)
    set_if_empty("FUZZ_SECONDS", cfg.get("fuzz_seconds"))
    set_if_empty("CLUSTERFUZZ_UI_URL", cfg.get("ui_url"))
    set_if_empty("TARGET_ID", cfg.get("id"))

    llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    if llm.get("enabled") is True:
        set_if_empty("LLM_FUZZ_ENABLED", "1")
    elif llm.get("enabled") is False:
        set_if_empty("LLM_FUZZ_ENABLED", "0")
    set_if_empty("LLM_FUZZ_MODEL", llm.get("model"))
    set_if_empty("LLM_FUZZ_BASE_URL", llm.get("base_url"))
    set_if_empty("LLM_FUZZ_PROVIDER", llm.get("provider"))
    if llm.get("background") is True:
        set_if_empty("LLM_FUZZ_BG_ENABLED", "1")
    set_if_empty("LLM_FUZZ_BG_SECONDS", llm.get("bg_seconds"))

    harnesses = cfg.get("harnesses")
    if isinstance(harnesses, list) and harnesses:
        set_if_empty("FUZZ_HARNESSES", ",".join(str(h) for h in harnesses))

    return env


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-id", default=os.environ.get("TARGET_ID") or "")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or "")
    parser.add_argument("--export-env", action="store_true", help="Print KEY=value lines")
    parser.add_argument("--json", action="store_true", help="Print JSON config")
    args = parser.parse_args()

    cfg = find_target(target_id=args.target_id or None, github_repo=args.repo or None)
    if not cfg:
        print("no target config matched", flush=True)
        return 0
    if args.json:
        print(json.dumps(cfg, indent=2))
        return 0
    env = apply_target_to_environ(cfg, {})
    if args.export_env:
        for k, v in sorted(env.items()):
            # shell-safe single-line values
            print(f"{k}={v}")
    else:
        print(f"loaded {cfg.get('_path')} id={cfg.get('id')} engine={cfg.get('engine')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
