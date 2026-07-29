#!/usr/bin/env bash
# Remap ClusterFuzz local-mode ports from the upstream 900x defaults to 930x
# so host :9000 (e.g. Tetrazero) stays free. Intended to run against a
# ClusterFuzz source tree (Docker image build or a checkout).
#
# Usage: apply_local_ports.sh [CLUSTERFUZZ_ROOT]
# Default root: current directory (Docker WORKDIR /clusterfuzz).
set -euo pipefail

ROOT="${1:-.}"
CONSTANTS="${ROOT}/src/local/butler/constants.py"
UPLOAD_FUZZER="${ROOT}/src/local/butler/scripts/upload_fuzzer.py"
GCS_GO="${ROOT}/local/emulators/gcs.go"
RUN_SERVER="${ROOT}/src/local/butler/run_server.py"

if [[ ! -f "${CONSTANTS}" ]]; then
  echo "error: constants.py not found at ${CONSTANTS}" >&2
  exit 1
fi

# Idempotent: only rewrite known upstream defaults (or already-patched 930x).
sed -i \
  -e 's/^DEV_APPSERVER_PORT = 9000$/DEV_APPSERVER_PORT = 9300/' \
  -e 's/^DEV_APPSERVER_PORT = 9300$/DEV_APPSERVER_PORT = 9300/' \
  -e 's/^CRON_SERVICE_PORT = 9009$/CRON_SERVICE_PORT = 9309/' \
  -e 's/^CRON_SERVICE_PORT = 9309$/CRON_SERVICE_PORT = 9309/' \
  -e 's/^DEV_APPSERVER_ADMIN_PORT = 9002$/DEV_APPSERVER_ADMIN_PORT = 9302/' \
  -e 's/^DEV_APPSERVER_ADMIN_PORT = 9302$/DEV_APPSERVER_ADMIN_PORT = 9302/' \
  -e 's/^DATASTORE_EMULATOR_PORT = 9004$/DATASTORE_EMULATOR_PORT = 9304/' \
  -e 's/^DATASTORE_EMULATOR_PORT = 9304$/DATASTORE_EMULATOR_PORT = 9304/' \
  -e 's/^PUBSUB_EMULATOR_PORT = 9006$/PUBSUB_EMULATOR_PORT = 9306/' \
  -e 's/^PUBSUB_EMULATOR_PORT = 9306$/PUBSUB_EMULATOR_PORT = 9306/' \
  -e 's/^LOCAL_GCS_SERVER_PORT = 9008$/LOCAL_GCS_SERVER_PORT = 9308/' \
  -e 's/^LOCAL_GCS_SERVER_PORT = 9308$/LOCAL_GCS_SERVER_PORT = 9308/' \
  "${CONSTANTS}"

if [[ -f "${UPLOAD_FUZZER}" ]]; then
  sed -i \
    -e "s|http://localhost:9000/fuzzers|http://localhost:9300/fuzzers|" \
    -e "s|http://localhost:9300/fuzzers|http://localhost:9300/fuzzers|" \
    "${UPLOAD_FUZZER}"
fi

# Go GCS emulator defaults to -port 9008 unless overridden.
if [[ -f "${GCS_GO}" ]]; then
  sed -i \
    -e 's/flag\.IntVar(&port, "port", 9008,/flag.IntVar(\&port, "port", 9308,/' \
    -e 's/flag\.IntVar(&port, "port", 9308,/flag.IntVar(\&port, "port", 9308,/' \
    "${GCS_GO}"
fi

# Ensure run_server passes -port from constants (idempotent Python rewrite).
if [[ -f "${RUN_SERVER}" ]]; then
  python3 - "${RUN_SERVER}" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
# Collapse any existing go-run GCS line(s) to the port-aware form.
text2 = re.sub(
    r"'go run emulators/gcs\.go(?: -port='\s*\+\s*str\(constants\.LOCAL_GCS_SERVER_PORT\)\s*\+\s*')?(?: -port=\d+)? -storage-path='",
    "'go run emulators/gcs.go -port=' + str(constants.LOCAL_GCS_SERVER_PORT) + ' -storage-path='",
    text,
)
if "'go run emulators/gcs.go -storage-path='" in text2:
    text2 = text2.replace(
        "'go run emulators/gcs.go -storage-path='",
        "'go run emulators/gcs.go -port=' + str(constants.LOCAL_GCS_SERVER_PORT) + ' -storage-path='",
    )
path.write_text(text2)
print(f"Patched run_server GCS invoke in {path}")
PY
fi

# Sanity check
grep -q '^DEV_APPSERVER_PORT = 9300$' "${CONSTANTS}"
grep -q '^LOCAL_GCS_SERVER_PORT = 9308$' "${CONSTANTS}"
if [[ -f "${GCS_GO}" ]]; then
  grep -q 'flag.IntVar(&port, "port", 9308,' "${GCS_GO}"
fi
echo "Applied local port remap (UI :9300, GCS :9308, …) in ${ROOT}"
