#!/usr/bin/env bash
# Run an Atheris (libFuzzer-backed) Python harness for N seconds and collect crashes.
#
# Usage:
#   ./run_timed_atheris.sh <harness.py> [seconds] [artifact_dir]
# Env:
#   FUZZ_SECONDS    — default 60 (overridden by arg 2)
#   ARTIFACT_DIR    — default ./fuzz-artifacts (overridden by arg 3)
#   CORPUS_DIR      — optional seed/corpus directory
#   PYTHON          — python interpreter (default: python3)
#   EXTRA_FUZZ_ARGS — extra args passed to the harness
#   HARNESS_NAME    — optional subdir name under artifact_dir (default: harness basename)
set -euo pipefail

HARNESS="${1:?usage: run_timed_atheris.sh <harness.py> [seconds] [artifact_dir]}"
SECONDS_LIMIT="${2:-${FUZZ_SECONDS:-60}}"
ARTIFACT_DIR="${3:-${ARTIFACT_DIR:-./fuzz-artifacts}}"
CORPUS_DIR="${CORPUS_DIR:-}"
PYTHON="${PYTHON:-python3}"
HARNESS_NAME="${HARNESS_NAME:-$(basename "${HARNESS}" .py)}"

if [[ ! -f "${HARNESS}" ]]; then
  echo "error: harness not found: ${HARNESS}" >&2
  exit 1
fi

mkdir -p "${ARTIFACT_DIR}"
# Per-harness subdir keeps multi-target PR runs from clobbering each other,
# while still feeding a shared crashes/ tree for post_pr_comments.py.
HARNESS_ART="${ARTIFACT_DIR}/${HARNESS_NAME}"
mkdir -p "${HARNESS_ART}"
WORK_CORPUS="${HARNESS_ART}/corpus"
mkdir -p "${WORK_CORPUS}"

if [[ -n "${CORPUS_DIR}" && -d "${CORPUS_DIR}" ]]; then
  cp -a "${CORPUS_DIR}/." "${WORK_CORPUS}/" 2>/dev/null || true
fi

# Convention: fuzz/seed_corpus/<harness_stem>/ next to the harness.
HARNESS_DIR="$(cd "$(dirname "${HARNESS}")" && pwd)"
SEED_DIR="${HARNESS_DIR}/seed_corpus/${HARNESS_NAME}"
# Also accept stem without _fuzzer suffix
SEED_STEM="${HARNESS_NAME%_fuzzer}"
if [[ ! -d "${SEED_DIR}" && -d "${HARNESS_DIR}/seed_corpus/${SEED_STEM}" ]]; then
  SEED_DIR="${HARNESS_DIR}/seed_corpus/${SEED_STEM}"
fi
if [[ -d "${SEED_DIR}" ]]; then
  cp -a "${SEED_DIR}/." "${WORK_CORPUS}/" 2>/dev/null || true
fi

echo "==> Timed Atheris: ${HARNESS} for ${SECONDS_LIMIT}s → ${HARNESS_ART}"

set +e
"${PYTHON}" "${HARNESS}" \
  "${WORK_CORPUS}" \
  -max_total_time="${SECONDS_LIMIT}" \
  -artifact_prefix="${HARNESS_ART}/" \
  -print_final_stats=1 \
  ${EXTRA_FUZZ_ARGS:-} \
  > "${HARNESS_ART}/fuzz.log" 2>&1
RC=$?
set -e

# Merge into shared crashes/ + fuzz.log (append) for the PR commenter.
CRASH_DIR="${ARTIFACT_DIR}/crashes"
mkdir -p "${CRASH_DIR}"
shopt -s nullglob
for f in "${HARNESS_ART}"/crash-* "${HARNESS_ART}"/oom-* \
         "${HARNESS_ART}"/timeout-* "${HARNESS_ART}"/leak-*; do
  base="$(basename "$f")"
  cp -a "$f" "${CRASH_DIR}/${HARNESS_NAME}-${base}"
done
shopt -u nullglob

{
  echo "===== ${HARNESS_NAME} (exit ${RC}) ====="
  cat "${HARNESS_ART}/fuzz.log" 2>/dev/null || true
  echo
} >> "${ARTIFACT_DIR}/fuzz.log"

CRASH_COUNT="$(find "${CRASH_DIR}" -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "==> Atheris exit code: ${RC}"
echo "==> Crashes collected (all harnesses): ${CRASH_COUNT} (in ${CRASH_DIR})"
echo "${CRASH_COUNT}" > "${ARTIFACT_DIR}/crash_count.txt"

# Non-zero exit on crash is expected; treat as success for CI.
exit 0
