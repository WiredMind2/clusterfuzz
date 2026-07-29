#!/usr/bin/env bash
# Copy a revisioned build zip into the ClusterFuzz local_gcs filesystem layout.
#
# Local GCS layout (per ClusterFuzz docs):
#   <LOCAL_GCS_ROOT>/<bucket>/objects/<object path>
#   <LOCAL_GCS_ROOT>/<bucket>/metadata/<object path>
#
# Usage:
#   ./ingest_build.sh <zip-path> [bucket] [object-subdir]
# Env:
#   LOCAL_GCS_ROOT  — default: ../data/local_gcs (relative to this script's deploy/)
#   BUILD_BUCKET    — default: clusterfuzz-builds
#   BUILD_SUBDIR    — default: demo
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ZIP_PATH="${1:?usage: ingest_build.sh <zip-path> [bucket] [object-subdir]}"
BUCKET="${2:-${BUILD_BUCKET:-clusterfuzz-builds}}"
SUBDIR="${3:-${BUILD_SUBDIR:-demo}}"
LOCAL_GCS_ROOT="${LOCAL_GCS_ROOT:-${DEPLOY_DIR}/data/local_gcs}"

if [[ ! -f "${ZIP_PATH}" ]]; then
  echo "error: zip not found: ${ZIP_PATH}" >&2
  exit 1
fi

ZIP_NAME="$(basename "${ZIP_PATH}")"
# ClusterFuzz continuous builds: any-name-([0-9]+).zip
if [[ ! "${ZIP_NAME}" =~ ^.+-([0-9]+)\.zip$ ]]; then
  echo "error: zip name must match 'name-([0-9]+).zip', got: ${ZIP_NAME}" >&2
  exit 1
fi

OBJECT_REL="${SUBDIR}/${ZIP_NAME}"
OBJECTS_DIR="${LOCAL_GCS_ROOT}/${BUCKET}/objects/${SUBDIR}"
METADATA_DIR="${LOCAL_GCS_ROOT}/${BUCKET}/metadata/${SUBDIR}"
mkdir -p "${OBJECTS_DIR}" "${METADATA_DIR}"

cp -f "${ZIP_PATH}" "${OBJECTS_DIR}/${ZIP_NAME}"

# Minimal metadata sidecar (content-type) so local storage looks like GCS objects.
META_FILE="${METADATA_DIR}/${ZIP_NAME}"
cat > "${META_FILE}" <<EOF
{"contentType":"application/zip"}
EOF

# Optional .srcmap.json sibling if present next to the zip.
SRCMAP_SRC="${ZIP_PATH%.zip}.srcmap.json"
if [[ -f "${SRCMAP_SRC}" ]]; then
  SRCMAP_NAME="$(basename "${SRCMAP_SRC}")"
  cp -f "${SRCMAP_SRC}" "${OBJECTS_DIR}/${SRCMAP_NAME}"
  echo "{\"contentType\":\"application/json\"}" > "${METADATA_DIR}/${SRCMAP_NAME}"
fi

echo "==> Ingested gs://${BUCKET}/${OBJECT_REL}"
echo "    → ${OBJECTS_DIR}/${ZIP_NAME}"
echo "Job RELEASE_BUILD_BUCKET_PATH tip:"
echo "    gs://${BUCKET}/${SUBDIR}/$(echo "${ZIP_NAME}" | sed -E 's/-[0-9]+\.zip$/-([0-9]+).zip/')"
