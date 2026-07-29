#!/usr/bin/env bash
# Build local butler images, bootstrap the server once, start server+bot,
# and verify the UI on :9300.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="${ROOT}/deploy"
DATA="${ROOT}/data"
COMPOSE=(docker compose -f "${DEPLOY}/docker-compose.yml")
STATUS_FILE="${DEPLOY}/BOOTSTRAP_STATUS.md"
BOOTSTRAP_WAIT_SECS="${BOOTSTRAP_WAIT_SECS:-900}"

log() { printf '%s\n' "$*"; }
fail_status() {
  local msg="$1"
  cat > "${STATUS_FILE}" <<EOF
# Bootstrap status

**Result:** blocked / incomplete

**When:** $(date -Is)

**Blocker:** ${msg}

**Upstream tag:** $(git -C "${ROOT}/upstream" describe --tags --always 2>/dev/null || echo unknown)

## What was attempted
- Build \`clusterfuzz-server:local\` / \`clusterfuzz-bot:local\`
- \`run_server --bootstrap --storage-path /data\`
- Start \`cf-bot\` with \`--server-storage-path /data\`
- Probe \`http://127.0.0.1:9300/\`
EOF
  log "error: ${msg}"
  exit 1
}

mkdir -p "${DATA}/local_gcs" "${DATA}/runner"
# Do not pre-create ${DATA}/bot — butler run_bot creates it on first start.
# A leftover /data/bot from a failed start can confuse setup; wipe only when
# FORCE_BOT_RESET=1.
if [[ "${FORCE_BOT_RESET:-0}" == "1" ]]; then
  rm -rf "${DATA}/bot"
fi

if [[ ! -e "${DEPLOY}/data" ]]; then
  ln -sfn ../data "${DEPLOY}/data"
fi

cd "${DEPLOY}"

log "==> Building cf-server image (install_deps; can take 20–60+ minutes)..."
"${COMPOSE[@]}" build cf-server \
  || fail_status "docker compose build cf-server failed"

log "==> Building cf-bot image (FROM clusterfuzz-server:local)..."
docker build -t clusterfuzz-bot:local -f "${DEPLOY}/Dockerfile.bot" "${ROOT}" \
  || fail_status "docker build cf-bot failed"

log "==> Stopping any previous stack / leftover bootstrap container..."
"${COMPOSE[@]}" down --remove-orphans 2>/dev/null || true
docker rm -f clusterfuzz-cf-bootstrap >/dev/null 2>&1 || true

log "==> Bootstrapping server (run_server --bootstrap; clears/recreates /data)..."
docker run -d --network host \
  --name clusterfuzz-cf-bootstrap \
  -v "${DATA}:/data" \
  -e LOCAL_DEVELOPMENT=True \
  -e PIPENV_VENV_IN_PROJECT=1 \
  clusterfuzz-server:local \
  pipenv run python butler.py run_server --bootstrap --skip-install-deps --storage-path /data \
  || fail_status "failed to start bootstrap server container"

cleanup_bootstrap() {
  docker rm -f clusterfuzz-cf-bootstrap >/dev/null 2>&1 || true
}
trap cleanup_bootstrap EXIT

log "==> Waiting up to ${BOOTSTRAP_WAIT_SECS}s for UI on :9300 ..."
deadline=$((SECONDS + BOOTSTRAP_WAIT_SECS))
ui_ok=0
while (( SECONDS < deadline )); do
  if ! docker inspect clusterfuzz-cf-bootstrap >/dev/null 2>&1; then
    docker logs clusterfuzz-cf-bootstrap 2>&1 | tail -n 100 || true
    fail_status "bootstrap container exited unexpectedly"
  fi
  state="$(docker inspect -f '{{.State.Status}}' clusterfuzz-cf-bootstrap 2>/dev/null || echo missing)"
  if [[ "${state}" != "running" ]]; then
    docker logs clusterfuzz-cf-bootstrap 2>&1 | tail -n 100 || true
    fail_status "bootstrap container state=${state}"
  fi
  if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:9300/" 2>/dev/null; then
    ui_ok=1
    break
  fi
  if (( SECONDS % 30 < 5 )); then
    log "    ... still waiting ($((deadline - SECONDS))s left); state=${state}"
  fi
  sleep 5
done

if (( ui_ok != 1 )); then
  docker logs clusterfuzz-cf-bootstrap 2>&1 | tail -n 120 || true
  fail_status "UI did not respond on :9300 within ${BOOTSTRAP_WAIT_SECS}s during bootstrap"
fi

log "==> UI responded during bootstrap. Waiting for DB setup thread (~45s)..."
sleep 45

log "==> Stopping bootstrap container; starting normal compose stack..."
cleanup_bootstrap
trap - EXIT

"${COMPOSE[@]}" up -d cf-server \
  || fail_status "failed to start cf-server"

log "==> Waiting for cf-server UI..."
deadline=$((SECONDS + BOOTSTRAP_WAIT_SECS))
while (( SECONDS < deadline )); do
  if curl -fsS -o /tmp/cf-ui-check.html --max-time 5 "http://127.0.0.1:9300/" 2>/dev/null; then
    break
  fi
  sleep 5
done

if [[ ! -f /tmp/cf-ui-check.html ]] || ! curl -fsS -o /tmp/cf-ui-check.html --max-time 5 "http://127.0.0.1:9300/" 2>/dev/null; then
  "${COMPOSE[@]}" logs --tail=100 cf-server || true
  fail_status "cf-server UI not reachable on :9300 after compose up"
fi

log "==> Starting cf-bot..."
"${COMPOSE[@]}" up -d cf-bot \
  || fail_status "failed to start cf-bot"

sleep 20
bot_state="$(docker inspect -f '{{.State.Status}}' clusterfuzz-cf-bot 2>/dev/null || echo missing)"
if [[ "${bot_state}" != "running" ]]; then
  "${COMPOSE[@]}" logs --tail=100 cf-bot || true
  fail_status "cf-bot container not running (state=${bot_state})"
fi

ui_snippet="$(head -c 200 /tmp/cf-ui-check.html | tr '\n' ' ')"
tag="$(git -C "${ROOT}/upstream" describe --tags --always 2>/dev/null || echo unknown)"

cat > "${STATUS_FILE}" <<EOF
# Bootstrap status

**Result:** success

**When:** $(date -Is)

**Upstream tag:** ${tag}

**UI:** http://127.0.0.1:9300/ responded (snippet: \`${ui_snippet}\`)

**Bot:** container \`clusterfuzz-cf-bot\` state=\`${bot_state}\`

**Storage:** \`${DATA}\` → container \`/data\` (GCS buckets under \`/data/local_gcs\`)

## Compose how-to
\`\`\`bash
cd ${DEPLOY}
docker compose up -d          # start server + bot
docker compose logs -f        # follow logs
docker compose down           # stop
\`\`\`

Re-bootstrap (wipes local datastore under data/):
\`\`\`bash
${DEPLOY}/scripts/bootstrap.sh
\`\`\`
EOF

log "==> Bootstrap complete."
log "    UI:  http://127.0.0.1:9300/"
log "    Bot: ${bot_state}"
log "    Status written to ${STATUS_FILE}"
