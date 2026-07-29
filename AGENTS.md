## Learned User Preferences

- Prefers full official ClusterFuzz (not ClusterFuzzLite), fully Dockerized, with no GCP dependency—local butler mode only.
- Wants GitHub CI to trigger fuzzing on this server and post crash findings as PR review comments.
- Keep custom glue outside `upstream/`; do not modify official ClusterFuzz sources for deploy/docs/demo ownership splits unless required for local patches.
- Wants detailed project documentation of what was implemented plus known gaps and limitations (including no GCP).
- Do not bind the ClusterFuzz UI to host port 9000; that port is already in use on this machine—use the remapped 930x range instead.
- Single-developer local-only ops: keep compute and primary storage on this server; no real GCS or offsite backup needed (losing local fuzz data is acceptable).
- Design deploy glue to onboard many repositories over time; AnimeManager is the current pilot, not a one-off.
- Prefer light integration with TetrazeroDashboard and local OTLP/observability when useful (no fight over host `:9000`).

## Learned Workspace Facts

- Workspace layout: `upstream/` is official google/clusterfuzz (pinned release tag, e.g. v2.35.0); `deploy/` holds Docker Compose, Dockerfiles, scripts, docs, and GitHub glue; `data/` holds persistent local storage (`local_gcs/`, bot state, runner state); glue must not live inside `upstream/`.
- Local stack runs `cf-server` and `cf-bot` with `network_mode: host` so Datastore/Pub/Sub/GCS emulators on localhost work; filesystem under `data/local_gcs` replaces cloud GCS.
- Upstream local mode hardcodes ports in `constants.py`; this deploy remaps 900x → 930x via `deploy/scripts/apply_local_ports.sh` and `deploy/patches/local_butler_ports.patch` (UI `:9300`, local GCS `:9308`).
- Host port conflicts are fatal under host networking; TetrazeroDashboard previously occupied CF UI ports—keep CF on 930x so other services can keep `:9000`.
- Official ClusterFuzz has no native GitHub Actions or PR-comment integration; this repo adds multi-repo targets under `deploy/targets/`, workflow template `deploy/workflows/fuzz-pr.yml` (libFuzzer or Atheris), self-hosted `gha-runner` (`docker-compose.runner.yml`, label `clusterfuzz-local`), and `deploy/scripts/post_pr_comments.py`.
- Pilot target is `WiredMind2/AnimeManager` (Python/Atheris harnesses); runner secrets live in gitignored `deploy/runner.env` (never commit tokens).
- Optional PR-job OTLP export targets `http://127.0.0.1:4318` when configured and no-ops if the collector is down; Tetrazero can register ClusterFuzz on `:9300` via `services.yaml`.
- Ubuntu 24.04 server images need deadsnakes (or equivalent) for Python 3.11; bot image needs a `copytree`/`dirs_exist_ok` fix so bot restarts do not fail on `update_dir`.
- Docs live under `deploy/docs/` (including `multi-repo.md`); bootstrap outcome and port notes are in `deploy/BOOTSTRAP_STATUS.md`.
