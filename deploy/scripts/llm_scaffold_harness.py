#!/usr/bin/env python3
"""Scaffold a minimal fuzz harness (LLM via Ollama, fail-open to static templates).

Usage:
  llm_scaffold_harness.py --engine atheris|libfuzzer --out-dir DIR [--repo-root DIR]

Writes files under out-dir (typically a temp tree that becomes the enablement PR).
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


ATHERIS_TEMPLATE = '''\
"""Atheris harness scaffolded by ClusterFuzz cf_enable_repo.sh."""
import sys
import atheris

# Optional: import a pure function from your package and fuzz that instead.
# from mypkg import parse


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    s = fdp.ConsumeUnicodeNoSurrogates(min(len(data), 4096))
    # Exercise string handling; replace with real entrypoints as you harden the harness.
    _ = s.lower().strip()
    try:
        _ = s.encode("utf-8")
    except Exception:
        pass


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
'''

ATHERIS_REQUIREMENTS = "atheris>=2.3.0\n"

LIBFUZZER_CC = '''\
// libFuzzer harness scaffolded by ClusterFuzz cf_enable_repo.sh.
#include <cstdint>
#include <cstddef>
#include <cstring>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size == 0) {
    return 0;
  }
  // Touch the buffer so ASan can catch obvious overflows in callees you add later.
  volatile uint8_t sink = data[size - 1];
  (void)sink;
  return 0;
}
'''

LIBFUZZER_BUILD = '''\
#!/usr/bin/env bash
# Build scaffolded libFuzzer target into OUT_DIR (default: fuzz/out).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT}/out}"
REV="${1:-1}"
CXX="${CXX:-clang++}"
mkdir -p "${OUT_DIR}"
NAME="${FUZZ_NAME:-target}"
"${CXX}" -O1 -g -fsanitize=fuzzer,address -o "${OUT_DIR}/${NAME}" "${ROOT}/target.cc"
(
  cd "${OUT_DIR}"
  zip -q "${NAME}-${REV}.zip" "${NAME}"
)
echo "==> Built ${OUT_DIR}/${NAME} and ${OUT_DIR}/${NAME}-${REV}.zip"
'''


def env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in (None, "") else default


def log(msg: str) -> None:
    print(f"==> llm_scaffold: {msg}", flush=True)


def ollama_chat(base_url: str, model: str, prompt: str, timeout_s: float) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.2},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write minimal fuzz harnesses. Reply with a single fenced code block only, "
                    "no explanation. Prefer safe, side-effect-free entrypoints."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "clusterfuzz-llm-scaffold"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("message") or {}).get("content") or ""
    if not content.strip():
        raise ValueError("empty scaffold response")
    return content


def extract_code(raw: str) -> str:
    fence = re.search(r"```(?:python|py|c\+\+|cpp|cc)?\s*([\s\S]*?)```", raw)
    if fence:
        return fence.group(1).strip() + "\n"
    return raw.strip() + "\n"


def collect_hints(repo_root: Path, limit: int = 6000) -> str:
    hints: list[str] = []
    for pattern in ("README.md", "pyproject.toml", "setup.py", "shared/**/*.py", "src/**/*.py"):
        for path in sorted(repo_root.glob(pattern))[:20]:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunk = text[:1500]
            hints.append(f"// file: {path.relative_to(repo_root)}\n{chunk}")
            if sum(len(h) for h in hints) >= limit:
                return "\n\n".join(hints)[:limit]
    return "\n\n".join(hints)[:limit]


def write_atheris(out_dir: Path, body: str) -> list[Path]:
    fuzz = out_dir / "fuzz"
    fuzz.mkdir(parents=True, exist_ok=True)
    harness = fuzz / "scaffold_fuzzer.py"
    req = fuzz / "requirements.txt"
    harness.write_text(body, encoding="utf-8")
    if not req.exists():
        req.write_text(ATHERIS_REQUIREMENTS, encoding="utf-8")
    return [harness, req]


def write_libfuzzer(out_dir: Path, cc_body: str) -> list[Path]:
    fuzz = out_dir / "fuzz"
    fuzz.mkdir(parents=True, exist_ok=True)
    cc = fuzz / "target.cc"
    build = fuzz / "build.sh"
    cc.write_text(cc_body, encoding="utf-8")
    build.write_text(LIBFUZZER_BUILD, encoding="utf-8")
    build.chmod(0o755)
    return [cc, build]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("atheris", "libfuzzer"), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = args.repo_root.resolve()

    base_url = env("LLM_FUZZ_BASE_URL") or "http://127.0.0.1:11434"
    model = env("LLM_FUZZ_MODEL") or "Qwen2.5-Coder:1.5b"
    timeout_s = float(env("LLM_FUZZ_TIMEOUT_S") or "45")
    use_llm = env("LLM_FUZZ_ENABLED", "1") in ("1", "true", "TRUE", "yes", "YES")

    body: str | None = None
    if use_llm:
        hints = collect_hints(repo_root)
        if args.engine == "atheris":
            prompt = (
                "Write a complete Atheris Python harness named TestOneInput that fuzzes "
                "safe string/bytes helpers. Include atheris.Setup/Fuzz main. "
                f"Repo hints:\n{hints}"
            )
        else:
            prompt = (
                "Write a complete LLVMFuzzerTestOneInput C++ file (no main) that is safe "
                "and compiles with -fsanitize=fuzzer,address. "
                f"Repo hints:\n{hints}"
            )
        try:
            raw = ollama_chat(base_url, model, prompt, timeout_s)
            body = extract_code(raw)
            if args.engine == "atheris" and "atheris" not in body:
                raise ValueError("LLM output missing atheris")
            if args.engine == "libfuzzer" and "LLVMFuzzerTestOneInput" not in body:
                raise ValueError("LLM output missing LLVMFuzzerTestOneInput")
            log("using LLM scaffold")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
            log(f"LLM failed ({exc!r}) — using static template")
            body = None

    if body is None:
        body = ATHERIS_TEMPLATE if args.engine == "atheris" else LIBFUZZER_CC
        log("using static template")

    if args.engine == "atheris":
        paths = write_atheris(out_dir, body)
    else:
        paths = write_libfuzzer(out_dir, body)

    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
