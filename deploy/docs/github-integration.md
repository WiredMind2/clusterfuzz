# GitHub integration

Official ClusterFuzz does **not** integrate with GitHub Actions or post PR review comments. This deployment adds custom glue so PRs can trigger fuzzing on this server and receive comments when crashes are found.

**Sourcery-style (recommended for all repos):** private **GitHub App** + **host** `cloudflared` at `fuzzing.tetrazero.com` — see [github-app.md](github-app.md). No per-repo workflow required.

**Actions path (opt-in fallback):** **self-hosted runner** + thin callers (`cf_enable_repo.sh --with-actions`) + `post_pr_comments.py` (below).

## Why self-hosted (not GitHub-hosted)

| Requirement | GitHub-hosted | Self-hosted on this server |
| --- | --- | --- |
| Same toolchain / bot image family as ClusterFuzz | Hard to mirror | Native |
| Write builds/crashes into local `local_gcs` | No shared volume | Shared Docker volumes |
| Heavy ASan fuzz CPU/disk | Expensive / capped | Uses this machine |
| Predictable timed fuzz for PR comments | Possible but isolated | Same host as the farm |

Workflows **must** use:

```yaml
runs-on: [self-hosted, clusterfuzz-local]
```

If the runner is offline, PR fuzz jobs queue indefinitely — there is no fallback to GitHub-hosted compute for this design.

## Self-hosted runner model

1. Service **`gha-runner`** lives in the **override** file `deploy/docker-compose.runner.yml` (not started by main `docker compose up` alone).
2. Configure `deploy/runner.env` from `runner.env.example`; see `deploy/GITHUB_RUNNER.md`.
3. Start with:
   `docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d gha-runner`
4. Labels default: `self-hosted`, `linux`, **`clusterfuzz-local`**.
5. Volumes: Docker socket; `./data` (symlink → repo-root `data/`) → `/data`; `deploy/` → `/opt/clusterfuzz/deploy:ro`.
6. After registration, GitHub shows the runner as **Idle** when ready.

Re-registration is needed if `data/runner` registration files are deleted or the registration is revoked.

## Actions fallback walkthrough

Preferred path is the GitHub App ([github-app.md](github-app.md)) — no thin workflow. For the Actions fallback only:

| Piece | Path |
| --- | --- |
| Thin caller template | `deploy/workflows/fuzz-pr-caller.yml.template` → copied as `.github/workflows/fuzz-pr.yml` by `cf_enable_repo.sh --with-actions` |
| Farm dispatch | `.github/workflows/fuzz-dispatch.yml` (`repository_dispatch`) |
| Reusable job | `.github/workflows/fuzz-pr-reusable.yml` (runs on `gha-runner`) |

**Thin caller:** GitHub-hosted `ubuntu-latest` job that `repository_dispatch`es into `WiredMind2/clusterfuzz` with PR metadata (needs `CLUSTERFUZZ_DISPATCH_TOKEN`).

**Reusable job stages** (on self-hosted `clusterfuzz-local`):

1. Checkout PR head from the target repo.
2. Load `deploy/targets/<id>.yaml` (`load_target_config.py`).
3. Optional LLM prepare (`llm_fuzz_prepare.py`) — seeds + `.dict` (fail-open).
4. Timed fuzz via `run_pr_fuzz_job.sh` / `run_timed_fuzz.sh` (`FUZZ_SECONDS`).
5. Ingest into `LOCAL_GCS_ROOT` (`ingest_build.sh`).
6. Optional LLM triage → `triage.md` (fail-open).
7. Comment via `post_pr_comments.py`.
8. Optional enqueue deep job (`LLM_FUZZ_BG_ENABLED=1`) for `llm-fuzz-worker`.

Do not enable App + thin workflow on the same repo (duplicate jobs/comments). Continuous background fuzzing still happens via `cf-server` / `cf-bot` after ingest.

## LLM-assisted seeds, triage, and background worker

LLM assist is **glue only** (not upstream ClusterFuzz). Shared client: `scripts/llm_chat.py`. Switch providers with **`LLM_FUZZ_PROVIDER`**:

| Provider | Default base | Default model | Auth |
| --- | --- | --- | --- |
| `ollama` (default) | `http://127.0.0.1:11434` | `Qwen2.5-Coder:1.5b` | none |
| `openrouter` | `https://openrouter.ai/api/v1` | `qwen/qwen-2.5-coder-7b-instruct` | `OPENROUTER_API_KEY` |

Explicit `LLM_FUZZ_MODEL` / `LLM_FUZZ_BASE_URL` override the defaults. Target YAML may set `llm.provider`. The `gha-runner` service uses `network_mode: host` so PR jobs can reach loopback Ollama (and OTLP); OpenRouter is outbound HTTPS.

| Piece | Role |
| --- | --- |
| `scripts/llm_chat.py` | Shared Ollama / OpenRouter chat client |
| `scripts/llm_fuzz_prepare.py` | Pre-fuzz seeds + libFuzzer `-dict=` |
| `scripts/llm_crash_triage.py` | Short root-cause markdown for the PR review |
| `scripts/llm_fuzz_enqueue.py` | After the PR job, queue a deep job |
| `scripts/llm_fuzz_worker.py` | Longer seed batch + timed fuzz; follow-up comment on **new** crash hashes |
| `docker-compose.llm-worker.yml` | Optional Compose service for the worker |

```bash
# OpenRouter example (runner.env / github-app.env / llm-worker.env)
LLM_FUZZ_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...

cp llm-worker.env.example llm-worker.env   # optional GITHUB_TOKEN for follow-ups
docker compose -f docker-compose.yml -f docker-compose.llm-worker.yml up -d llm-fuzz-worker
```

Repo / env knobs: `LLM_FUZZ_ENABLED`, `LLM_FUZZ_PROVIDER`, `LLM_FUZZ_MODEL`, `LLM_FUZZ_BASE_URL`, `LLM_FUZZ_BG_ENABLED`, `OPENROUTER_API_KEY`. Prep/triage **fail open** if the provider is down or the OpenRouter key is missing so classic mutation still runs.

**Not** used: LLM inside the mutation hot loop (too slow for the 60s SLA).

## Review comment format and posting

Script: `deploy/scripts/post_pr_comments.py`

**Intended behavior:**

- Read crash / finding artifacts from the timed fuzz output (and optionally from ingested paths).
- For each distinct finding, create a **PR review comment** (or review with comments) via the GitHub API.
- Body typically includes:
  - Short summary (crash type / security severity if known)
  - Stack trace excerpt
  - Reproducer / artifact reference when available
  - Link toward the local ClusterFuzz UI testcase when one exists (`http://<host>:9300/…`) — note UI links are only useful to people who can reach this server

**Not** using Monorail or any upstream bug tracker for the PR path. Monorail is the only built-in tracker in upstream ClusterFuzz; we do not wire a replacement beyond PR comments.

## Permissions and secrets

### Workflow permissions

```yaml
permissions:
  contents: read
  pull-requests: write   # required to post review comments
```

Default `GITHUB_TOKEN` in Actions can post comments on PRs in the **same** repository when `pull-requests: write` is granted. Fork PRs from outside collaborators may need stricter review of token behavior (GitHub’s fork PR token rules apply).

### Runner registration

| Credential | Scope / use |
| --- | --- |
| Repo registration token | One-time (or short-lived) runner register for a single repo |
| Org registration token / PAT | Org-level runners; needs admin rights to manage runners |
| PAT for automation | If used instead of ephemeral tokens: least privilege for runner management |

Store registration secrets in the host environment or a gitignored `deploy/.env` — never in git.

### Comment posting

| Credential | Use |
| --- | --- |
| `secrets.GITHUB_TOKEN` | Preferred inside the workflow for same-repo PR comments |
| Fine-grained or classic PAT | Only if you must comment across repos or need APIs beyond the job token |

## Operational notes

- **App path:** keep **`cf-github-app` + host cloudflared** for account-wide PR fuzzing ([github-app.md](github-app.md)). Farm repo `WiredMind2/clusterfuzz` uses the App only (no same-repo `fuzz-pr.yml`) to avoid duplicate comments.
- **Actions path:** keep **`gha-runner` up** for target repos that still use thin `repository_dispatch` callers.
- Diff-aware App jobs skip when the PR does not touch harness/`demo-target` paths; demo `BOOM` crashes are labeled as expected smoke.

See also: [github-app.md](github-app.md) (Sourcery-style App), [architecture.md](architecture.md) (data flow), [setup.md](setup.md) (registration steps), [multi-repo.md](multi-repo.md) (`cf_enable_repo.sh`), [limitations.md](limitations.md) (GitHub gaps).
