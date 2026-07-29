#!/usr/bin/env bash
# Enable ClusterFuzz PR fuzzing on a WiredMind2 repo with minimal setup.
#
# Preferred path is the GitHub App (no thin workflow). Actions is opt-in.
#
# Usage:
#   ./cf_enable_repo.sh OWNER/REPO [options]
#
# Options:
#   --engine auto|atheris|libfuzzer
#   --no-scaffold / --scaffold
#   --with-actions     Also add thin fuzz-pr.yml + Actions vars/secrets (fallback)
#   --farm-pr          Open a PR on the farm repo for deploy/targets/<slug>.yaml
#   --dry-run
#
# Default (App-first):
#   1. Detect engine / existing harnesses (or scaffold via llm_scaffold_harness.py)
#   2. Open an enablement PR on the target (harness only; no workflow unless --with-actions)
#   3. Write deploy/targets/<slug>.yaml on this farm checkout
#   4. Print App install / deny-list notes
#
# --with-actions additionally:
#   - Adds thin dispatch workflow
#   - Sets Actions variables on the target
#   - Sets CLUSTERFUZZ_DISPATCH_TOKEN / CLUSTERFUZZ_CROSS_REPO_TOKEN only when
#     CLUSTERFUZZ_DISPATCH_TOKEN is already set in the environment (fine-grained
#     PAT or installation token). Never copies `gh auth token` automatically.
#
# Requires: gh auth with repo scopes; farm host with this deploy tree.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"
CALLER_TEMPLATE="${DEPLOY_DIR}/workflows/fuzz-pr-caller.yml.template"
FARM_REPO="${FARM_REPO:-WiredMind2/clusterfuzz}"
UI_URL="${CLUSTERFUZZ_UI_URL:-http://127.0.0.1:9300}"

ENGINE="auto"
SCAFFOLD=1
DRY_RUN=0
WITH_ACTIONS=0
FARM_PR=0
REPO=""

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \?//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine) ENGINE="${2:-}"; shift 2 ;;
    --no-scaffold) SCAFFOLD=0; shift ;;
    --scaffold) SCAFFOLD=1; shift ;;
    --with-actions) WITH_ACTIONS=1; shift ;;
    --farm-pr) FARM_PR=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    -*)
      echo "error: unknown flag $1" >&2
      usage
      ;;
    *)
      if [[ -n "${REPO}" ]]; then
        echo "error: unexpected arg $1" >&2
        usage
      fi
      REPO="$1"
      shift
      ;;
  esac
done

if [[ -z "${REPO}" || "${REPO}" != */* ]]; then
  echo "error: pass OWNER/REPO" >&2
  usage
fi

if [[ "${WITH_ACTIONS}" -eq 1 && ! -f "${CALLER_TEMPLATE}" ]]; then
  echo "error: missing ${CALLER_TEMPLATE}" >&2
  exit 1
fi

command -v gh >/dev/null || { echo "error: gh CLI required" >&2; exit 1; }
command -v git >/dev/null || { echo "error: git required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "error: python3 required" >&2; exit 1; }

SLUG="$(echo "${REPO#*/}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')"
TARGET_YAML="${DEPLOY_DIR}/targets/${SLUG}.yaml"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cf-enable.XXXXXX")"
cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

log() { echo "==> $*"; }

log "Inspecting ${REPO} (App-first${WITH_ACTIONS:+; Actions fallback})"
DEFAULT_BRANCH="$(gh repo view "${REPO}" --json defaultBranchRef --jq .defaultBranchRef.name)"
gh repo clone "${REPO}" "${WORK}/repo" -- --depth 1 --branch "${DEFAULT_BRANCH}" >/dev/null
REPO_ROOT="${WORK}/repo"

detect_engine() {
  local eng="$1"
  if [[ "${eng}" != "auto" ]]; then
    echo "${eng}"
    return
  fi
  if find "${REPO_ROOT}/fuzz" -type f -name '*_fuzzer.py' 2>/dev/null | grep -q .; then
    echo atheris
    return
  fi
  if [[ -x "${REPO_ROOT}/fuzz/build.sh" ]] \
    || grep -Rsl 'LLVMFuzzerTestOneInput' "${REPO_ROOT}" --include='*.c' --include='*.cc' --include='*.cpp' 2>/dev/null | head -1 | grep -q .; then
    echo libfuzzer
    return
  fi
  if [[ -f "${REPO_ROOT}/pyproject.toml" || -f "${REPO_ROOT}/setup.py" ]] \
    || find "${REPO_ROOT}" -maxdepth 2 -name '*.py' | head -1 | grep -q .; then
    echo atheris
  else
    echo libfuzzer
  fi
}

ENGINE="$(detect_engine "${ENGINE}")"
log "Engine: ${ENGINE}"

NEED_SCAFFOLD=0
if [[ "${ENGINE}" == "atheris" ]]; then
  if ! find "${REPO_ROOT}/fuzz" -type f -name '*_fuzzer.py' 2>/dev/null | grep -q .; then
    NEED_SCAFFOLD=1
  fi
else
  if [[ ! -x "${REPO_ROOT}/fuzz/build.sh" ]]; then
    NEED_SCAFFOLD=1
  fi
fi

if [[ "${NEED_SCAFFOLD}" -eq 1 && "${SCAFFOLD}" -eq 0 ]]; then
  echo "error: no harness found and --no-scaffold set" >&2
  exit 1
fi

BRANCH="chore/clusterfuzz-enable"
PUSH_DIR="${WORK}/push"
gh repo clone "${REPO}" "${PUSH_DIR}" -- --depth 1 --branch "${DEFAULT_BRANCH}" >/dev/null
cd "${PUSH_DIR}"
git checkout -B "${BRANCH}"

if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  mkdir -p .github/workflows
  cp "${CALLER_TEMPLATE}" .github/workflows/fuzz-pr.yml
fi

if [[ "${NEED_SCAFFOLD}" -eq 1 ]]; then
  log "Scaffolding ${ENGINE} harness"
  LLM_FUZZ_ENABLED="${LLM_FUZZ_ENABLED:-1}" \
    python3 "${SCRIPT_DIR}/llm_scaffold_harness.py" \
      --engine "${ENGINE}" \
      --out-dir "${PUSH_DIR}" \
      --repo-root "${REPO_ROOT}" >/dev/null
fi

HARNESS_LINES=""
if [[ "${ENGINE}" == "atheris" ]]; then
  while IFS= read -r h; do
    HARNESS_LINES+="  - ${h}"$'\n'
  done < <(find fuzz -type f -name '*_fuzzer.py' 2>/dev/null | sort)
else
  HARNESS_LINES="  - fuzz/build.sh"$'\n'
fi

SKIP_INGEST="0"
if [[ "${ENGINE}" == "atheris" ]]; then
  SKIP_INGEST="1"
fi

NOTES="App-only PR path (GitHub App at fuzzing.tetrazero.com)"
if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  NOTES="Actions fallback via repository_dispatch to ${FARM_REPO} (prefer App; avoid dual-path)"
fi

LLM_PROVIDER="${LLM_FUZZ_PROVIDER:-ollama}"
if [[ "${LLM_PROVIDER}" == "openrouter" ]]; then
  LLM_MODEL="${LLM_FUZZ_MODEL:-qwen/qwen-2.5-coder-7b-instruct}"
  LLM_BASE="${LLM_FUZZ_BASE_URL:-https://openrouter.ai/api/v1}"
else
  LLM_MODEL="${LLM_FUZZ_MODEL:-Qwen2.5-Coder:1.5b}"
  LLM_BASE="${LLM_FUZZ_BASE_URL:-http://127.0.0.1:11434}"
fi

cat > "${WORK}/target.yaml" <<EOF
# Generated by cf_enable_repo.sh
id: ${SLUG}
github: ${REPO}
engine: ${ENGINE}
fuzz_seconds: 60
harnesses:
${HARNESS_LINES}ui_url: ${UI_URL}
notes: ${NOTES}
llm:
  enabled: true
  provider: ${LLM_PROVIDER}
  model: ${LLM_MODEL}
  base_url: ${LLM_BASE}
  background: true
EOF

if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  git add .github/workflows/fuzz-pr.yml
fi
if [[ -d fuzz ]]; then
  git add fuzz
fi

COMMIT_BODY="Enable ClusterFuzz PR fuzzing (GitHub App).

Scaffolds a harness when none existed. PR comments are posted by the
ClusterFuzz GitHub App (fuzzing.tetrazero.com) — no thin workflow required.
"
if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  COMMIT_BODY="Enable ClusterFuzz PR fuzzing (Actions fallback).

Adds a thin workflow that dispatches to ${FARM_REPO} for self-hosted timed fuzz,
plus a scaffolded harness when none existed.

Prefer the GitHub App path; do not leave both App and this workflow enabled.
"
fi

if git diff --cached --quiet; then
  log "Nothing to commit in target repo (already enabled?)"
else
  git commit -m "${COMMIT_BODY}"
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "DRY RUN — would push branch ${BRANCH}, open PR, write ${TARGET_YAML}"
  if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
    if [[ -n "${CLUSTERFUZZ_DISPATCH_TOKEN:-}" ]]; then
      log "DRY RUN — would set Actions secrets from CLUSTERFUZZ_DISPATCH_TOKEN env"
    else
      log "DRY RUN — would skip secret set (CLUSTERFUZZ_DISPATCH_TOKEN unset)"
    fi
  fi
  if [[ "${FARM_PR}" -eq 1 ]]; then
    log "DRY RUN — would open farm PR for targets/${SLUG}.yaml"
  fi
  cat "${WORK}/target.yaml"
  exit 0
fi

if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  log "Configuring Actions vars on ${REPO}"
  gh variable set FUZZ_ENGINE --repo "${REPO}" --body "${ENGINE}"
  gh variable set FUZZ_SECONDS --repo "${REPO}" --body "60"
  gh variable set CLUSTERFUZZ_UI_URL --repo "${REPO}" --body "${UI_URL}"
  gh variable set TARGET_ID --repo "${REPO}" --body "${SLUG}"
  gh variable set LLM_FUZZ_ENABLED --repo "${REPO}" --body "1"
  gh variable set LLM_FUZZ_BG_ENABLED --repo "${REPO}" --body "1"
  gh variable set SKIP_INGEST --repo "${REPO}" --body "${SKIP_INGEST}"

  if [[ -n "${CLUSTERFUZZ_DISPATCH_TOKEN:-}" ]]; then
    log "Setting dispatch secrets from CLUSTERFUZZ_DISPATCH_TOKEN env (not gh auth token)"
    gh secret set CLUSTERFUZZ_DISPATCH_TOKEN --repo "${REPO}" --body "${CLUSTERFUZZ_DISPATCH_TOKEN}"
    gh secret set CLUSTERFUZZ_CROSS_REPO_TOKEN --repo "${FARM_REPO}" --body "${CLUSTERFUZZ_DISPATCH_TOKEN}"
  else
    echo "warn: CLUSTERFUZZ_DISPATCH_TOKEN unset — skipping secret writes." >&2
    echo "warn: Export a fine-grained PAT (or App installation token) as" >&2
    echo "warn: CLUSTERFUZZ_DISPATCH_TOKEN before re-running with --with-actions," >&2
    echo "warn: or set secrets manually. Operator 'gh auth token' is never copied." >&2
  fi
fi

log "Pushing enablement branch"
git push -u origin "HEAD:${BRANCH}" --force-with-lease

PR_TITLE="Enable ClusterFuzz PR fuzzing"
PR_BODY="$(cat <<EOF
## Summary
- Engine: \`${ENGINE}\`${NEED_SCAFFOLD:+
- Scaffolds an initial fuzz harness under \`fuzz/\`}
- Preferred path: **GitHub App** at \`fuzzing.tetrazero.com\` (no thin workflow)

## After merge
Ensure the ClusterFuzz GitHub App is installed on this account/repo. PRs will
get timed fuzz + review comments from the App worker on the farm host.

Farm target id: \`${SLUG}\`

Docs: https://github.com/${FARM_REPO}/blob/main/deploy/docs/github-app.md
EOF
)"
if [[ "${WITH_ACTIONS}" -eq 1 ]]; then
  PR_TITLE="Enable ClusterFuzz PR fuzzing (Actions fallback)"
  PR_BODY="$(cat <<EOF
## Summary
- Adds \`.github/workflows/fuzz-pr.yml\` (dispatches to \`${FARM_REPO}\`)
- Engine: \`${ENGINE}\`${NEED_SCAFFOLD:+
- Scaffolds an initial fuzz harness under \`fuzz/\`}

## Important
Prefer the **GitHub App** path. If the App is already installed, remove this
workflow after merge to avoid duplicate jobs/comments.

Farm target id: \`${SLUG}\`
EOF
)"
fi

PR_URL="$(gh pr list --repo "${REPO}" --head "${BRANCH}" --json url --jq '.[0].url // empty')"
if [[ -z "${PR_URL}" ]]; then
  # Only open a PR when there is something on the branch vs default.
  if git rev-list --count "origin/${DEFAULT_BRANCH}..HEAD" 2>/dev/null | grep -vq '^0$'; then
    PR_URL="$(gh pr create --repo "${REPO}" --base "${DEFAULT_BRANCH}" --head "${BRANCH}" \
      --title "${PR_TITLE}" --body "${PR_BODY}")"
  else
    log "No commits vs ${DEFAULT_BRANCH}; skipping target PR"
    PR_URL="(none — nothing to merge on target)"
  fi
fi

log "Writing farm target ${TARGET_YAML}"
cp "${WORK}/target.yaml" "${TARGET_YAML}"

FARM_PR_URL=""
if [[ "${FARM_PR}" -eq 1 ]]; then
  log "Opening farm PR for targets/${SLUG}.yaml"
  FARM_WORK="${WORK}/farm"
  # Use local checkout when this is already the farm tree.
  if git -C "${ROOT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    && [[ "$(git -C "${ROOT_DIR}" remote get-url origin 2>/dev/null || true)" == *"${FARM_REPO}"* ]]; then
    (
      cd "${ROOT_DIR}"
      FARM_BRANCH="chore/cf-target-${SLUG}"
      git checkout -B "${FARM_BRANCH}"
      git add "deploy/targets/${SLUG}.yaml"
      if git diff --cached --quiet; then
        log "Farm target YAML already committed"
      else
        git commit -m "Register ClusterFuzz target ${SLUG}

Adds deploy/targets/${SLUG}.yaml for ${REPO} (App-first enable)."
        git push -u origin "HEAD:${FARM_BRANCH}" --force-with-lease
        FARM_DEFAULT="$(gh repo view "${FARM_REPO}" --json defaultBranchRef --jq .defaultBranchRef.name)"
        EXISTING="$(gh pr list --repo "${FARM_REPO}" --head "${FARM_BRANCH}" --json url --jq '.[0].url // empty')"
        if [[ -z "${EXISTING}" ]]; then
          FARM_PR_URL="$(gh pr create --repo "${FARM_REPO}" --base "${FARM_DEFAULT}" --head "${FARM_BRANCH}" \
            --title "Register fuzz target ${SLUG}" \
            --body "$(cat <<EOF
## Summary
- Registers \`${REPO}\` as farm target \`${SLUG}\`
- Engine: \`${ENGINE}\`

EOF
)")"
        else
          FARM_PR_URL="${EXISTING}"
        fi
      fi
    )
  else
    echo "warn: --farm-pr requires running inside the ${FARM_REPO} checkout; wrote YAML only." >&2
  fi
fi

log "Done"
echo "  Enablement PR: ${PR_URL}"
echo "  Farm target:   ${TARGET_YAML}"
if [[ -n "${FARM_PR_URL}" ]]; then
  echo "  Farm PR:       ${FARM_PR_URL}"
fi
echo "  App notes: ensure GitHub App is installed on ${REPO}; mute via deny-repos.txt or .clusterfuzz.yml"
if [[ "${WITH_ACTIONS}" -eq 0 ]]; then
  echo "  Tip: pass --with-actions only if you need the Actions fallback (avoid dual-path)."
fi
echo "  Tip: pass --farm-pr to open a PR on ${FARM_REPO} for the new targets YAML."
