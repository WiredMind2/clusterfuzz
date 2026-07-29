#!/usr/bin/env bash
# Dry-run deploy patches against pristine upstream/ (v2.35.0 pin).
# Exit 0 if patches apply cleanly; non-zero otherwise.
#
# Usage: verify_patches.sh [UPSTREAM_ROOT]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"
UPSTREAM="${1:-${ROOT_DIR}/upstream}"
PATCH_DIR="${DEPLOY_DIR}/patches"

if [[ ! -d "${UPSTREAM}" ]]; then
  echo "error: upstream not found at ${UPSTREAM}" >&2
  exit 1
fi

fail=0
for patch in \
  "${PATCH_DIR}/local_butler_ports.patch" \
  "${PATCH_DIR}/bot_copytree_dirs_exist_ok.patch"
do
  name="$(basename "${patch}")"
  if [[ ! -f "${patch}" ]]; then
    echo "FAIL missing ${name}" >&2
    fail=1
    continue
  fi
  if patch --dry-run -p1 -d "${UPSTREAM}" < "${patch}" >/tmp/cf-patch-$$.out 2>&1; then
    echo "OK   ${name}"
  else
    echo "FAIL ${name}" >&2
    cat /tmp/cf-patch-$$.out >&2
    fail=1
  fi
  rm -f /tmp/cf-patch-$$.out
done

# Dockerfile.bot should still reference the same copytree fix (belt-and-suspenders).
if grep -q 'dirs_exist_ok=True' "${DEPLOY_DIR}/Dockerfile.bot"; then
  echo "OK   Dockerfile.bot references dirs_exist_ok"
else
  echo "FAIL Dockerfile.bot missing dirs_exist_ok" >&2
  fail=1
fi

# apply_local_ports.sh should agree with the ports patch targets.
if grep -q 'DEV_APPSERVER_PORT = 9300' "${PATCH_DIR}/local_butler_ports.patch" \
  && grep -q '9300' "${DEPLOY_DIR}/scripts/apply_local_ports.sh"; then
  echo "OK   ports patch aligns with apply_local_ports.sh (9300)"
else
  echo "FAIL ports remap mismatch" >&2
  fail=1
fi

exit "${fail}"
