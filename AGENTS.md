## Learned User Preferences

- Prefers full official ClusterFuzz (not ClusterFuzzLite), fully Dockerized, with no GCP dependency—local butler mode only.
- Wants Sourcery-style account-wide PR fuzzing (all WiredMind2 repos by default) with easy per-repo opt-out; prefer GitHub App comments (`clusterfuzz[bot]`) over Actions/`github-actions[bot]`.
- Keep custom glue outside `upstream/`; do not modify official ClusterFuzz sources for deploy/docs/demo ownership splits unless required for local patches.
- Wants detailed project documentation of what was implemented plus known gaps and limitations (including no GCP).
- Do not bind the ClusterFuzz UI to host port 9000; that port is already in use on this machine—use the remapped 930x range instead.
- Single-developer local-only ops: keep compute and primary storage on this server; no real GCS or offsite backup needed (losing local fuzz data is acceptable).
- Design deploy glue for minimal auto-onboarding of many repositories (App-first; `cf_enable_repo.sh` for optional Actions pilots); AnimeManager and other WiredMind2 repos are pilots, not one-offs.
- Prefer light integration with TetrazeroDashboard and local OTLP/observability when useful (no fight over host `:9000`).
- Prefer optional local Ollama or OpenRouter LLM assist (`LLM_FUZZ_PROVIDER`); fail-open if the backend is down or the OpenRouter key is missing.
- Prefer the existing host cloudflared tunnel with an explicit `fuzzing.tetrazero.com` hostname (not Caddy wildcard routing).

## Learned Workspace Facts

- Workspace layout: `upstream/` is official google/clusterfuzz (pinned release tag, e.g. v2.35.0); `deploy/` holds Docker Compose, Dockerfiles, scripts, docs, and GitHub glue; `data/` holds persistent local storage (`local_gcs/`, bot state, runner state); glue must not live inside `upstream/`.
- Local stack runs `cf-server` and `cf-bot` with `network_mode: host` so Datastore/Pub/Sub/GCS emulators on localhost work; filesystem under `data/local_gcs` replaces cloud GCS.
- Upstream local mode hardcodes ports in `constants.py`; this deploy remaps 900x → 930x via `deploy/scripts/apply_local_ports.sh` and `deploy/patches/local_butler_ports.patch` (UI `:9300`, local GCS `:9308`).
- Host port conflicts are fatal under host networking; TetrazeroDashboard previously occupied CF UI ports—keep CF on 930x so other services can keep `:9000`.
- Farm GitHub repo is public `WiredMind2/clusterfuzz` (push `deploy/` + docs; ignore `data/`, `runner.env`, `github-app.env`, nested `upstream/`). Because `WiredMind2` is a personal account (no org runners), multi-repo Actions pilots use central `repository_dispatch` into this farm (`fuzz-dispatch.yml` → reusable `fuzz-pr-reusable.yml`).
- Preferred PR path is a private GitHub App (`cf-github-app` on `127.0.0.1:9310`) exposed by host cloudflared at `fuzzing.tetrazero.com` → webhook `/github/webhooks`, with deny-list / `.clusterfuzz.yml` opt-out; Actions + self-hosted `gha-runner` remain a fallback for pilots (`post_pr_comments.py` / `cf_enable_repo.sh`).
- Targets include self-test `clusterfuzz` (libFuzzer demo), `openrouterclient`, and `animemanager` (Python/Atheris); runner and App secrets live in gitignored `deploy/runner.env` / `deploy/github-app.env` and `deploy/github-app/private-key.pem` (never commit tokens or PEMs).
- Optional LLM path: `LLM_FUZZ_PROVIDER=ollama|openrouter` via `llm_chat.py` / `llm_fuzz_prepare.py` / `llm_crash_triage.py` / `llm_fuzz_worker.py` and `docker-compose.llm-worker.yml` (OpenRouter needs `OPENROUTER_API_KEY`); PR-job OTLP export targets `http://127.0.0.1:4318` when configured and no-ops if the collector is down; Tetrazero can register ClusterFuzz on `:9300` via `services.yaml`.
- Ubuntu 24.04 server images need deadsnakes (or equivalent) for Python 3.11; bot image needs a `copytree`/`dirs_exist_ok` fix so bot restarts do not fail on `update_dir`.
- Docs live under `deploy/docs/` (including `multi-repo.md`, `github-integration.md`, `github-app.md`); bootstrap outcome and port notes are in `deploy/BOOTSTRAP_STATUS.md`.
