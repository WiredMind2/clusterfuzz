#!/usr/bin/env bash
# Build the demo libFuzzer target and pack a revisioned zip for local_gcs ingest.
#
# Usage:
#   ./build.sh [revision]
# Env:
#   CXX          — compiler (default: clang++)
#   OUT_DIR      — output directory (default: ./out)
#   FUZZ_NAME    — binary / zip basename prefix (default: demo-fuzz)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CXX="${CXX:-clang++}"
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/out}"
FUZZ_NAME="${FUZZ_NAME:-demo-fuzz}"
REVISION="${1:-$(date +%Y%m%d%H%M%S)}"

mkdir -p "${OUT_DIR}"
BINARY="${OUT_DIR}/${FUZZ_NAME}"
ZIP="${OUT_DIR}/${FUZZ_NAME}-${REVISION}.zip"

echo "==> Compiling ${FUZZ_NAME} with ${CXX} (-fsanitize=fuzzer,address)"
"${CXX}" -O1 -g -std=c++17 \
  -fsanitize=fuzzer,address \
  -fno-omit-frame-pointer \
  "${SCRIPT_DIR}/demo_fuzz.cc" \
  -o "${BINARY}"

# Seed that triggers the crash quickly for smoke tests / timed fuzz.
SEED_DIR="${OUT_DIR}/seed"
mkdir -p "${SEED_DIR}"
printf 'BOOM' > "${SEED_DIR}/crash_seed"
( cd "${SEED_DIR}" && zip -q "${OUT_DIR}/${FUZZ_NAME}_seed_corpus.zip" crash_seed )

echo "==> Packing ${ZIP}"
( cd "${OUT_DIR}" && zip -q "$(basename "${ZIP}")" \
  "$(basename "${BINARY}")" \
  "${FUZZ_NAME}_seed_corpus.zip" )

# Verify naming matches ClusterFuzz continuous-build pattern: name-([0-9]+).zip
BASENAME="$(basename "${ZIP}")"
if [[ ! "${BASENAME}" =~ ^${FUZZ_NAME}-([0-9]+)\.zip$ ]]; then
  echo "error: zip name '${BASENAME}' does not match ${FUZZ_NAME}-([0-9]+).zip" >&2
  exit 1
fi

echo "Built: ${BINARY}"
echo "Zip:   ${ZIP}"
echo "Revision: ${REVISION}"
