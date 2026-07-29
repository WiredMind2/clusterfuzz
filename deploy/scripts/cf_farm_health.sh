#!/usr/bin/env bash
# Light health / queue probe for the local ClusterFuzz farm.
# Exit 0 when critical endpoints are up; non-zero if UI or App is down.
#
# Usage: cf_farm_health.sh [--quiet] [--alert-age-seconds N]
set -euo pipefail

QUIET=0
ALERT_AGE="${CF_QUEUE_ALERT_AGE_S:-1800}"
UI_URL="${CLUSTERFUZZ_UI_URL:-http://127.0.0.1:9300/}"
APP_URL="${GITHUB_APP_HEALTH_URL:-http://127.0.0.1:9310/healthz}"
TUNNEL_URL="${GITHUB_APP_PUBLIC_HEALTH_URL:-https://fuzzing.tetrazero.com/healthz}"
DATA_ROOT="${CLUSTERFUZZ_DATA_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)/data}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) QUIET=1; shift ;;
    --alert-age-seconds) ALERT_AGE="${2:-1800}"; shift 2 ;;
    -h|--help)
      sed -n '2,6p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { [[ "${QUIET}" -eq 1 ]] || echo "$*"; }
fail=0
warn=0

check_http() {
  local name="$1" url="$2" required="$3"
  local code body=""
  local tmp
  tmp="$(mktemp)"
  # Cap download — CF UI HTML is ~1MB+
  code="$(curl -sS -o "${tmp}" -w '%{http_code}' --connect-timeout 3 --max-time 5 \
    -H 'Range: bytes=0-4095' "${url}" 2>/dev/null || echo 000)"
  # Some servers ignore Range and still 200; truncate locally either way.
  body="$(head -c 200 "${tmp}" 2>/dev/null | tr '\n' ' ')"
  rm -f "${tmp}"
  if [[ "${code}" =~ ^2[0-9][0-9]$ ]]; then
    log "OK   ${name}: ${url} → HTTP ${code}"
    if [[ "${name}" == "app" || "${name}" == "tunnel" ]]; then
      log "     ${body}"
      if [[ "${body}" =~ oldest_job_age_s=([0-9]+) ]]; then
        age="${BASH_REMATCH[1]}"
        if [[ "${age}" -gt "${ALERT_AGE}" ]]; then
          log "WARN ${name}: oldest job age ${age}s > ${ALERT_AGE}s"
          warn=1
        fi
      fi
    fi
  else
    if [[ "${required}" == "1" ]]; then
      log "FAIL ${name}: ${url} (HTTP ${code})"
      fail=1
    else
      log "WARN ${name}: ${url} (HTTP ${code}; optional)"
      warn=1
    fi
  fi
}

check_http "ui" "${UI_URL}" 1
check_http "app" "${APP_URL}" 1
check_http "tunnel" "${TUNNEL_URL}" 0

# Queue depth on disk (App) — name-only, maxdepth 1
APP_Q="${DATA_ROOT}/github_app_queue"
if [[ -d "${APP_Q}" ]]; then
  pending=0
  claimed=0
  oldest_mtime=0
  shopt -s nullglob
  for f in "${APP_Q}"/job-*.json; do
    pending=$((pending + 1))
    mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    if [[ "${oldest_mtime}" -eq 0 || "${mt}" -lt "${oldest_mtime}" ]]; then
      oldest_mtime="${mt}"
    fi
  done
  for f in "${APP_Q}"/job-*.claimed; do
    claimed=$((claimed + 1))
    mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    if [[ "${oldest_mtime}" -eq 0 || "${mt}" -lt "${oldest_mtime}" ]]; then
      oldest_mtime="${mt}"
    fi
  done
  shopt -u nullglob
  log "OK   app_queue: pending=${pending} claimed=${claimed} (${APP_Q})"
  if [[ "${oldest_mtime}" -gt 0 ]]; then
    now=$(date +%s)
    age=$((now - oldest_mtime))
    if [[ "${age}" -gt "${ALERT_AGE}" ]]; then
      log "WARN app_queue: oldest file age ${age}s > ${ALERT_AGE}s"
      warn=1
    fi
  fi
else
  log "WARN app_queue: missing ${APP_Q}"
  warn=1
fi

LLM_Q="${DATA_ROOT}/llm_fuzz_queue"
if [[ -d "${LLM_Q}" ]]; then
  llm_pending=0
  shopt -s nullglob
  for f in "${LLM_Q}"/job-*.json; do
    llm_pending=$((llm_pending + 1))
  done
  shopt -u nullglob
  log "OK   llm_queue: pending=${llm_pending} (${LLM_Q})"
fi

# Runner / LLM containers (optional; docker may be slow — timeout)
if timeout 5 docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'clusterfuzz-gha-runner'; then
  log "OK   gha-runner: container up"
else
  log "WARN gha-runner: container not running (Actions fallback only)"
  warn=1
fi

if timeout 5 docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'clusterfuzz-llm-fuzz-worker'; then
  log "OK   llm-fuzz-worker: container up"
else
  log "WARN llm-fuzz-worker: container not running"
  warn=1
fi

if [[ "${fail}" -ne 0 ]]; then
  log "RESULT: FAIL"
  exit 1
fi
if [[ "${warn}" -ne 0 ]]; then
  log "RESULT: OK (with warnings)"
  exit 0
fi
log "RESULT: OK"
exit 0
