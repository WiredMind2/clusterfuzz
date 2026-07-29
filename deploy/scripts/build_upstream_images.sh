#!/usr/bin/env bash
# Build official ClusterFuzz docker/ images locally (ubuntu-24-04, no push).
# Safe to skip if you only need the local butler server/bot images from
# Dockerfile.server / Dockerfile.bot — those bake install_deps themselves.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPSTREAM="${ROOT}/upstream"
DOCKER_DIR="${UPSTREAM}/docker"
UBUNTU_VERSION="${UBUNTU_VERSION:-ubuntu-24-04}"

if [[ ! -d "${DOCKER_DIR}" ]]; then
  echo "error: upstream docker/ not found at ${DOCKER_DIR}" >&2
  echo "       clone google/clusterfuzz into upstream/ first" >&2
  exit 1
fi

echo "==> Building upstream images (${UBUNTU_VERSION}, --no-push)"
echo "    cwd: ${DOCKER_DIR}"
cd "${DOCKER_DIR}"
./build.sh "${UBUNTU_VERSION}" --no-push

echo "==> Done. Also build local butler images with:"
echo "    cd ${ROOT}/deploy && docker compose build"
