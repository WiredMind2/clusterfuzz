#!/usr/bin/env bash
# Run a libFuzzer binary for N seconds and collect crash artifacts.
#
# Usage:
#   ./run_timed_fuzz.sh <binary> [seconds] [artifact_dir]
# Env:
#   FUZZ_SECONDS   — default 60 (overridden by arg 2)
#   ARTIFACT_DIR   — default ./fuzz-artifacts (overridden by arg 3)
#   CORPUS_DIR     — optional seed/corpus directory
#   EXTRA_FUZZ_ARGS — extra args passed to the fuzzer
set -euo pipefail

BINARY="${1:?usage: run_timed_fuzz.sh <binary> [seconds] [artifact_dir]}"
SECONDS_LIMIT="${2:-${FUZZ_SECONDS:-60}}"
ARTIFACT_DIR="${3:-${ARTIFACT_DIR:-./fuzz-artifacts}}"
CORPUS_DIR="${CORPUS_DIR:-}"

if [[ ! -x "${BINARY}" && ! -f "${BINARY}" ]]; then
  echo "error: binary not found: ${BINARY}" >&2
  exit 1
fi
chmod +x "${BINARY}" 2>/dev/null || true

mkdir -p "${ARTIFACT_DIR}"
WORK_CORPUS="${ARTIFACT_DIR}/corpus"
mkdir -p "${WORK_CORPUS}"

if [[ -n "${CORPUS_DIR}" && -d "${CORPUS_DIR}" ]]; then
  cp -a "${CORPUS_DIR}/." "${WORK_CORPUS}/" 2>/dev/null || true
fi

# Prefer seed corpus zip next to the binary if present.
BIN_DIR="$(cd "$(dirname "${BINARY}")" && pwd)"
BIN_BASE="$(basename "${BINARY}")"
SEED_ZIP="${BIN_DIR}/${BIN_BASE}_seed_corpus.zip"
if [[ -f "${SEED_ZIP}" ]]; then
  unzip -qo "${SEED_ZIP}" -d "${WORK_CORPUS}" || true
fi

echo "==> Timed fuzz: ${BINARY} for ${SECONDS_LIMIT}s → ${ARTIFACT_DIR}"

# libFuzzer: -max_total_time stops cleanly; artifact_prefix captures crashes/OOMs.
set +e
"${BINARY}" \
  "${WORK_CORPUS}" \
  -max_total_time="${SECONDS_LIMIT}" \
  -artifact_prefix="${ARTIFACT_DIR}/" \
  -print_final_stats=1 \
  ${EXTRA_FUZZ_ARGS:-} \
  > "${ARTIFACT_DIR}/fuzz.log" 2>&1
RC=$?
set -e

# Collect crash/oom/timeout/leak inputs into a crashes/ subdir for the commenter.
CRASH_DIR="${ARTIFACT_DIR}/crashes"
mkdir -p "${CRASH_DIR}"
shopt -s nullglob
for f in "${ARTIFACT_DIR}"/crash-* "${ARTIFACT_DIR}"/oom-* \
         "${ARTIFACT_DIR}"/timeout-* "${ARTIFACT_DIR}"/leak-*; do
  cp -a "$f" "${CRASH_DIR}/"
done
shopt -u nullglob

CRASH_COUNT="$(find "${CRASH_DIR}" -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "==> Fuzzer exit code: ${RC}"
echo "==> Crashes collected: ${CRASH_COUNT} (in ${CRASH_DIR})"
echo "${CRASH_COUNT}" > "${ARTIFACT_DIR}/crash_count.txt"

# Non-zero exit from libFuzzer on crash is expected; treat as success for CI.
exit 0
