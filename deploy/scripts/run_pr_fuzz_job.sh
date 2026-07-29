#!/usr/bin/env bash
# Run a timed PR fuzz job for the GitHub App worker (mirrors fuzz-pr-reusable.yml).
#
# Required env:
#   REPO_ROOT       — checked-out PR head
#   DEPLOY_DIR      — path to deploy/ (scripts)
#   GITHUB_TOKEN    — installation access token
#   GITHUB_REPOSITORY — owner/repo
#   PR_NUMBER
#   GITHUB_SHA      — commit sha (for enqueue / logs)
#
# Optional: FUZZ_ENGINE (auto|atheris|libfuzzer), FUZZ_SECONDS, ARTIFACT_DIR,
#           CLUSTERFUZZ_UI_URL, LOCAL_GCS_ROOT, SKIP_INGEST, LLM_*, etc.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:?}"
DEPLOY_DIR="${DEPLOY_DIR:?}"
ARTIFACT_DIR="${ARTIFACT_DIR:-${REPO_ROOT}/fuzz-artifacts}"
FUZZ_SECONDS="${FUZZ_SECONDS:-60}"
FUZZ_ENGINE="${FUZZ_ENGINE:-auto}"
CLUSTERFUZZ_UI_URL="${CLUSTERFUZZ_UI_URL:-http://127.0.0.1:9300}"
LOCAL_GCS_ROOT="${LOCAL_GCS_ROOT:-/data/local_gcs}"
LLM_FUZZ_ENABLED="${LLM_FUZZ_ENABLED:-1}"
SKIP_INGEST="${SKIP_INGEST:-}"
TARGET_ID="${TARGET_ID:-${GITHUB_REPOSITORY##*/}}"

cd "${REPO_ROOT}"
mkdir -p "${ARTIFACT_DIR}"

log() { echo "==> $*"; }

# --- .clusterfuzz.yml opt-out (tertiary disable) ---
if [[ -f .clusterfuzz.yml ]] || [[ -f .clusterfuzz.yaml ]]; then
  CFG="$([[ -f .clusterfuzz.yml ]] && echo .clusterfuzz.yml || echo .clusterfuzz.yaml)"
  if python3 - <<'PY' "${CFG}"
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
# Minimal parse: enabled: false
for line in text.splitlines():
    s = line.split("#", 1)[0].strip().lower()
    if s in ("enabled: false", "enabled:false", "enabled: 0", "enabled: no"):
        sys.exit(0)
sys.exit(1)
PY
  then
    log "skip: ${CFG} has enabled: false"
    export REVIEW_TITLE="ClusterFuzz local — skipped"
    mkdir -p "${ARTIFACT_DIR}"
    echo "Skipped: repository has enabled: false in ${CFG}" > "${ARTIFACT_DIR}/fuzz.log"
    GITHUB_TOKEN="${GITHUB_TOKEN}" GITHUB_REPOSITORY="${GITHUB_REPOSITORY}" \
      PR_NUMBER="${PR_NUMBER}" ARTIFACT_DIR="${ARTIFACT_DIR}" \
      CLUSTERFUZZ_UI_URL="${CLUSTERFUZZ_UI_URL}" \
      REVIEW_TITLE="ClusterFuzz local — skipped (repo config)" \
      python3 "${DEPLOY_DIR}/scripts/post_pr_comments.py" || true
    exit 0
  fi
fi

# --- detect engine ---
ENGINE="${FUZZ_ENGINE}"
if [[ "${ENGINE}" == "auto" || -z "${ENGINE}" ]]; then
  if find . -type f \( -path './fuzz/*_fuzzer.py' -o -path './fuzz/**/*_fuzzer.py' \) 2>/dev/null | grep -q .; then
    ENGINE=atheris
  elif [[ -x fuzz/build.sh ]] || grep -Rsl 'LLVMFuzzerTestOneInput' \
      --include='*.c' --include='*.cc' --include='*.cpp' . 2>/dev/null | head -1 | grep -q .; then
    ENGINE=libfuzzer
  else
    ENGINE=auto_scaffold
  fi
fi
log "FUZZ_ENGINE=${ENGINE}"

# --- scaffold if needed ---
if [[ "${ENGINE}" == "auto_scaffold" ]]; then
  SCAFFOLD_ENGINE=atheris
  if find . -type f \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.h' \) 2>/dev/null | head -1 | grep -q .; then
    SCAFFOLD_ENGINE=libfuzzer
  elif find . -type f -name '*.py' 2>/dev/null | head -1 | grep -q .; then
    SCAFFOLD_ENGINE=atheris
  else
    log "no harness and no obvious language — posting skip comment"
    echo "No fuzz harness and no scaffoldable sources detected." > "${ARTIFACT_DIR}/fuzz.log"
    REVIEW_TITLE="ClusterFuzz local — skipped (no harness)" \
      GITHUB_TOKEN="${GITHUB_TOKEN}" GITHUB_REPOSITORY="${GITHUB_REPOSITORY}" \
      PR_NUMBER="${PR_NUMBER}" ARTIFACT_DIR="${ARTIFACT_DIR}" \
      CLUSTERFUZZ_UI_URL="${CLUSTERFUZZ_UI_URL}" \
      python3 "${DEPLOY_DIR}/scripts/post_pr_comments.py" || true
    exit 0
  fi
  log "scaffolding ${SCAFFOLD_ENGINE} harness (fail-open)"
  LLM_FUZZ_ENABLED="${LLM_FUZZ_ENABLED}" \
    python3 "${DEPLOY_DIR}/scripts/llm_scaffold_harness.py" \
      --engine "${SCAFFOLD_ENGINE}" --out-dir fuzz --repo-root "${REPO_ROOT}" || true
  ENGINE="${SCAFFOLD_ENGINE}"
fi

export FUZZ_ENGINE="${ENGINE}"
export ARTIFACT_DIR LOCAL_GCS_ROOT CLUSTERFUZZ_UI_URL TARGET_ID

SOURCE_PATHS=""

if [[ "${ENGINE}" == "libfuzzer" ]]; then
  if ! command -v clang++ >/dev/null 2>&1; then
    echo "error: clang++ required for libFuzzer jobs" >&2
    exit 1
  fi
  if [[ -x fuzz/build.sh ]]; then
    OUT_DIR="${REPO_ROOT}/fuzz/out" fuzz/build.sh "${PR_NUMBER:-0}"
    BINARY="$(find "${REPO_ROOT}/fuzz/out" -type f -executable ! -name '*.sh' | head -1 || true)"
    ZIP="$(ls -1 "${REPO_ROOT}/fuzz/out"/*-[0-9]*.zip 2>/dev/null | tail -1 || true)"
    mapfile -t SRC < <(find fuzz -type f \( -name '*.cc' -o -name '*.cpp' -o -name '*.c' \) | head -5)
    SOURCE_PATHS="${SRC[*]:-}"
  elif [[ -x "${DEPLOY_DIR}/demo-target/build.sh" && "${TARGET_ID}" == "clusterfuzz" ]]; then
    OUT_DIR="${REPO_ROOT}/out" "${DEPLOY_DIR}/demo-target/build.sh" "${PR_NUMBER:-0}"
    BINARY="${REPO_ROOT}/out/demo-fuzz"
    ZIP="$(ls -1 "${REPO_ROOT}/out"/demo-fuzz-*.zip 2>/dev/null | tail -1 || true)"
    SOURCE_PATHS="${DEPLOY_DIR}/demo-target/demo_fuzz.cc"
  else
    log "no fuzz/build.sh — skip with comment"
    echo "No fuzz/build.sh after scaffold attempt." > "${ARTIFACT_DIR}/fuzz.log"
    REVIEW_TITLE="ClusterFuzz local — skipped (no build)" \
      GITHUB_TOKEN="${GITHUB_TOKEN}" GITHUB_REPOSITORY="${GITHUB_REPOSITORY}" \
      PR_NUMBER="${PR_NUMBER}" ARTIFACT_DIR="${ARTIFACT_DIR}" \
      CLUSTERFUZZ_UI_URL="${CLUSTERFUZZ_UI_URL}" \
      python3 "${DEPLOY_DIR}/scripts/post_pr_comments.py" || true
    exit 0
  fi
  if [[ -z "${BINARY:-}" || ! -x "${BINARY}" ]]; then
    echo "error: libFuzzer binary missing" >&2
    exit 1
  fi

  if [[ "${LLM_FUZZ_ENABLED}" == "1" ]]; then
    # shellcheck disable=SC2086
    python3 "${DEPLOY_DIR}/scripts/llm_fuzz_prepare.py" --github-env ${SOURCE_PATHS:-} || true
    # shellcheck disable=SC1091
    [[ -f "${ARTIFACT_DIR}/llm_prepare.env" ]] && source "${ARTIFACT_DIR}/llm_prepare.env" || true
  fi

  "${DEPLOY_DIR}/scripts/run_timed_fuzz.sh" "${BINARY}" "${FUZZ_SECONDS}" "${ARTIFACT_DIR}"

  if [[ "${SKIP_INGEST}" != "1" && -n "${ZIP:-}" && -f "${ZIP}" ]]; then
    LOCAL_GCS_ROOT="${LOCAL_GCS_ROOT}" "${DEPLOY_DIR}/scripts/ingest_build.sh" "${ZIP}" || true
  fi

elif [[ "${ENGINE}" == "atheris" ]]; then
  python3 -m venv "${REPO_ROOT}/.venv-fuzz"
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv-fuzz/bin/activate"
  python -m pip install --upgrade pip >/dev/null
  if [[ -f fuzz/requirements.txt ]]; then
    python -m pip install -r fuzz/requirements.txt
  else
    python -m pip install atheris
  fi
  if [[ -f pyproject.toml ]] || [[ -f setup.py ]]; then
    python -m pip install -e . || true
  fi
  export PYTHON="${REPO_ROOT}/.venv-fuzz/bin/python"
  mapfile -t HARNESSES < <(find fuzz -type f -name '*_fuzzer.py' | sort)
  if [[ ${#HARNESSES[@]} -eq 0 ]]; then
    log "no Atheris harnesses — skip"
    echo "No fuzz/*_fuzzer.py found." > "${ARTIFACT_DIR}/fuzz.log"
    REVIEW_TITLE="ClusterFuzz local — skipped (no harness)" \
      GITHUB_TOKEN="${GITHUB_TOKEN}" GITHUB_REPOSITORY="${GITHUB_REPOSITORY}" \
      PR_NUMBER="${PR_NUMBER}" ARTIFACT_DIR="${ARTIFACT_DIR}" \
      CLUSTERFUZZ_UI_URL="${CLUSTERFUZZ_UI_URL}" \
      python3 "${DEPLOY_DIR}/scripts/post_pr_comments.py" || true
    exit 0
  fi
  SOURCE_PATHS="${HARNESSES[*]}"
  if [[ "${LLM_FUZZ_ENABLED}" == "1" ]]; then
    # shellcheck disable=SC2086
    python3 "${DEPLOY_DIR}/scripts/llm_fuzz_prepare.py" --github-env ${SOURCE_PATHS:-} || true
    # shellcheck disable=SC1091
    [[ -f "${ARTIFACT_DIR}/llm_prepare.env" ]] && source "${ARTIFACT_DIR}/llm_prepare.env" || true
  fi
  PER=$(( FUZZ_SECONDS / ${#HARNESSES[@]} ))
  if [[ "${PER}" -lt 5 ]]; then PER=5; fi
  for h in "${HARNESSES[@]}"; do
    log "Harness ${h} (${PER}s)"
    "${DEPLOY_DIR}/scripts/run_timed_atheris.sh" "${h}" "${PER}" "${ARTIFACT_DIR}"
  done
else
  echo "error: unknown FUZZ_ENGINE=${ENGINE}" >&2
  exit 1
fi

if [[ "${LLM_FUZZ_ENABLED}" == "1" ]]; then
  python3 "${DEPLOY_DIR}/scripts/llm_crash_triage.py" || true
fi

export GITHUB_TOKEN GITHUB_REPOSITORY PR_NUMBER ARTIFACT_DIR CLUSTERFUZZ_UI_URL FUZZ_ENGINE
python3 "${DEPLOY_DIR}/scripts/post_pr_comments.py"

if [[ "${LLM_FUZZ_BG_ENABLED:-0}" == "1" ]]; then
  export GITHUB_SHA="${GITHUB_SHA:-}"
  python3 "${DEPLOY_DIR}/scripts/llm_fuzz_enqueue.py" || true
fi

if [[ -x "${DEPLOY_DIR}/scripts/report_fuzz_otel.py" ]] || [[ -f "${DEPLOY_DIR}/scripts/report_fuzz_otel.py" ]]; then
  python3 "${DEPLOY_DIR}/scripts/report_fuzz_otel.py" || true
fi

log "done ${GITHUB_REPOSITORY}#${PR_NUMBER}"
