# GitHub integration

Official ClusterFuzz does **not** integrate with GitHub Actions or post PR review comments. This deployment adds a **self-hosted runner** and a **small reporter** so PRs can trigger fuzzing on this server and receive comments when crashes are found.

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

## Workflow template walkthrough

Template path: `deploy/workflows/fuzz-pr.yml`  
Copy into target repos as `.github/workflows/fuzz-pr.yml` and adapt build steps.

Shipped stages in `fuzz-pr.yml`:

1. **Trigger** — `pull_request` (`opened`, `synchronize`, `reopened`).
2. **Checkout** — PR head.
3. **Locate glue** — finds `deploy/scripts/run_timed_fuzz.sh` under the workspace, `/opt/clusterfuzz/deploy`, or `/clusterfuzz/deploy`.
4. **Build** — default uses bundled `demo-target/build.sh`; replace for a real project.
5. **LLM prepare** (optional, `LLM_FUZZ_ENABLED=1`) — `llm_fuzz_prepare.py` calls local Ollama for seeds + `.dict`, sets `CORPUS_DIR` / `EXTRA_FUZZ_ARGS` (fail-open).
6. **Timed fuzz** — `run_timed_fuzz.sh` / Atheris (`FUZZ_SECONDS`, default 60).
7. **Ingest** — `ingest_build.sh` into `LOCAL_GCS_ROOT` (default `/data/local_gcs`).
8. **LLM triage** — `llm_crash_triage.py` → `triage.md` appended by the commenter (fail-open).
9. **Comment** — `post_pr_comments.py` with `pull-requests: write` and optional `CLUSTERFUZZ_UI_URL`.
10. **Enqueue deep job** (`LLM_FUZZ_BG_ENABLED=1`) — JSON under `/data/llm_fuzz_queue` for the background worker.

Continuous background fuzzing still happens in ClusterFuzz once `cf-server` / `cf-bot` are up; the workflow’s timed session exists so comments have a **synchronous, PR-scoped** result set.

## LLM-assisted seeds, triage, and background worker

LLM assist is **glue only** (not upstream ClusterFuzz). Default LLM endpoint is **local Ollama** (`http://127.0.0.1:11434`, model `Qwen2.5-Coder:1.5b`). The `gha-runner` service uses `network_mode: host` so PR jobs can reach loopback Ollama (and OTLP).

| Piece | Role |
| --- | --- |
| `scripts/llm_fuzz_prepare.py` | Pre-fuzz seeds + libFuzzer `-dict=` |
| `scripts/llm_crash_triage.py` | Short root-cause markdown for the PR review |
| `scripts/llm_fuzz_enqueue.py` | After the PR job, queue a deep job |
| `scripts/llm_fuzz_worker.py` | Longer seed batch + timed fuzz; follow-up comment on **new** crash hashes |
| `docker-compose.llm-worker.yml` | Optional Compose service for the worker |

From the **gha-runner** / **llm-fuzz-worker** (both host-networked), Ollama is `http://127.0.0.1:11434`.

```bash
cp llm-worker.env.example llm-worker.env   # optional GITHUB_TOKEN for follow-ups
docker compose -f docker-compose.yml -f docker-compose.llm-worker.yml up -d llm-fuzz-worker
```

Repo vars: `LLM_FUZZ_ENABLED`, `LLM_FUZZ_MODEL`, `LLM_FUZZ_BASE_URL`, `LLM_FUZZ_BG_ENABLED`. Prep/triage **fail open** if Ollama is down so classic mutation still runs.

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

- Keep **`gha-runner` up** whenever you expect PR fuzzing.
- Label drift breaks scheduling: workflow `runs-on` and runner labels must stay aligned.
- Do not point these workflows at GitHub-hosted runners and expect `local_gcs` ingest or the same sanitizer environment.
- For org-wide use, prefer GitHub **runner groups** and explicit repository allow-lists (future / optional hardening).

See also: [architecture.md](architecture.md) (data flow), [setup.md](setup.md) (registration steps), [multi-repo.md](multi-repo.md) (`cf_enable_repo.sh`), [limitations.md](limitations.md) (GitHub gaps).
