# Implemented inventory

Inventory of deliverables for the **Local Docker ClusterFuzz + GitHub PR comments (no GCP)** plan. Paths are relative to the workspace root `/home/william/Documents/clusterfuzz`.

Status legend:

- **Present** — exists in the workspace / verified running as of the last refresh
- **Optional / not started** — present as files but not running (needs secrets or operator action)

> Last refresh: public repo `WiredMind2/clusterfuzz` live; continuous farm up; **gha-runner online** on this repo (`clusterfuzz-local`). UI HTTP 200 on `http://127.0.0.1:9300/`. See also `deploy/BOOTSTRAP_STATUS.md`.

## Top-level layout

| Path | Intent | Status |
| --- | --- | --- |
| `upstream/` | Official `google/clusterfuzz` checkout | **Present** — tag **v2.35.0** |
| `deploy/` | Compose, Dockerfiles, scripts, demo, workflows, docs | **Present** |
| `data/` | Shared host storage: `local_gcs/`, `bot/`, `runner/`, `env.yaml`, `WEB-INF/` | **Present** (populated by bootstrap) |
| `deploy/data` | Symlink → `../data` (so runner/ingest paths under `deploy/` resolve) | **Present** |

## Official upstream

| Item | Detail | Status |
| --- | --- | --- |
| Clone | `upstream/` = `google/clusterfuzz` | **Present** |
| Pin | Tag **v2.35.0** | **Present** |
| Helper | `deploy/scripts/build_upstream_images.sh` (`ubuntu-24-04 --no-push`) | **Present** (optional; butler images bake their own deps) |
| Local mode | `butler.py run_server` / `run_bot`; filesystem GCS under `/data` | **Present** (running) |

## Docker images and Compose

| Path | Role | Status |
| --- | --- | --- |
| `deploy/docker-compose.yml` | Main stack: `cf-server`, `cf-bot`; **`network_mode: host`**; volume `../data:/data` | **Present** |
| `deploy/Dockerfile.server` | Builds `clusterfuzz-server:local` | **Present** |
| `deploy/Dockerfile.bot` | Builds `clusterfuzz-bot:local` (FROM server image) | **Present** |
| `deploy/Dockerfile.runner` | Builds `clusterfuzz-gha-runner:local` | **Present** |
| `deploy/docker-compose.runner.yml` | **Override** for `gha-runner` (not in main compose by default) | **Present** |
| `deploy/runner.env.example` | Template for `REPO_URL` / tokens / labels | **Present** |
| `deploy/GITHUB_RUNNER.md` | Runner registration how-to | **Present** |
| `deploy/BOOTSTRAP_STATUS.md` | Bootstrap result, port conflict note, verified checks | **Present** |

**Compose services (runtime):**

| Service | Intent | Status |
| --- | --- | --- |
| `cf-server` | Local UI on host `127.0.0.1:9300` (+ `9308` GCS emulator); shared `/data` | **Running** (healthy) |
| `cf-bot` | `run_bot` with `--server-storage-path /data` (via image/entrypoint); host network | **Running** |
| `gha-runner` | Labels `self-hosted`, `linux`, `clusterfuzz-local`; start via runner override + `runner.env` | **Running** — registered to `WiredMind2/clusterfuzz` |

Images in use: `clusterfuzz-server:local`, `clusterfuzz-bot:local`.

No Firebase, App Engine, real GCS, or service-account JSON.

## Bootstrap and storage

| Path / item | Role | Status |
| --- | --- | --- |
| `deploy/scripts/bootstrap.sh` | Build images, `run_server --bootstrap`, start stack, write `BOOTSTRAP_STATUS.md` | **Present** (already run successfully) |
| `data/local_gcs/` | Filesystem GCS (`test-*-bucket`, …) | **Present** |
| `data/bot/clusterfuzz/bot/logs/bot.log` | Bot log | **Present** |
| `data/runner/` | Actions runner registration dir (created by bootstrap) | **Present** (registered to `WiredMind2/clusterfuzz`) |
| `deploy/data` → `../data` | Symlink for compose.runner / ingest defaults | **Present** |

## Demo fuzz target

| Path | Role | Status |
| --- | --- | --- |
| `deploy/demo-target/demo_fuzz.cc` | Tiny C++ buffer-overflow libFuzzer target | **Present** |
| `deploy/demo-target/build.sh` | Build → revisioned zip under `out/` | **Present** |
| `deploy/demo-target/Dockerfile` | Optional containerized demo build | **Present** |
| `deploy/demo-target/out/` | Built `demo-fuzz`, `demo-fuzz-*.zip`, seed corpus | **Present** |

## Scripts

| Path | Role | Status |
| --- | --- | --- |
| `deploy/scripts/bootstrap.sh` | First-time / wipe bootstrap + status file | **Present** |
| `deploy/scripts/build_upstream_images.sh` | Optional official `docker/` base builds | **Present** |
| `deploy/scripts/run_timed_fuzz.sh` | Time-boxed libFuzzer run for PR path | **Present** |
| `deploy/scripts/ingest_build.sh` | Drop builds/crashes into `local_gcs` | **Present** |
| `deploy/scripts/post_pr_comments.py` | Crash artifacts → GitHub PR review comments | **Present** |

## GitHub workflow and targets

| Path | Role | Status |
| --- | --- | --- |
| `deploy/workflows/fuzz-pr.yml` | Template for other repos | **Present** |
| `.github/workflows/fuzz-pr.yml` | In-repo PR fuzz workflow (libFuzzer default) | **Present** |
| `deploy/targets/clusterfuzz.yaml` | Self-test / demo libFuzzer on this repo | **Present** |
| `deploy/targets/animemanager.yaml` | Pilot: WiredMind2/AnimeManager (Atheris; deferred) | **Present** (not wired yet) |

## Documentation

| Path | Role | Status |
| --- | --- | --- |
| `deploy/docs/*.md` | Full docs set (README, architecture, setup, ops, GitHub, inventory, limitations) | **Present** |
| `deploy/GITHUB_RUNNER.md` | Runner registration companion | **Present** |
| `deploy/BOOTSTRAP_STATUS.md` | Bootstrap verification + port conflict | **Present** |

## Config defaults (live)

| Default | Value |
| --- | --- |
| Deployment mode | Local butler only (no GCP) |
| Host deps | Docker + Compose |
| Upstream pin | **v2.35.0** |
| Networking | **`network_mode: host`** for `cf-server` / `cf-bot` (local emulators on localhost) |
| UI | `http://127.0.0.1:9300/` (binds host loopback) |
| Aux port | `127.0.0.1:9308` (local GCS emulator) |
| Storage | Repo-root `data/` ↔ container `/data` (`/data/local_gcs`) |
| Symlink | `deploy/data` → `../data` |
| Runner | Override file `docker-compose.runner.yml` + `runner.env` |
| PR comment path | `run_timed_fuzz.sh` + `post_pr_comments.py` |
| Continuous farm | `cf-server` + `cf-bot` + shared `data/local_gcs` — **running** |

## Known host conflict (bootstrap)

Host **`:9000`** is reserved for Tetrazero. This deploy remaps ClusterFuzz local ports to **`9300–9309`** at image build (`deploy/scripts/apply_local_ports.sh`) so both can coexist. See `deploy/BOOTSTRAP_STATUS.md`.

## Explicitly not implemented (see limitations)

- GCP production stack (`create_config`, App Engine, Firebase, real GCS, Datastore, Pub/Sub, BigQuery, Stackdriver, GCE bots)
- Registered / running `gha-runner` on other repos (AnimeManager pilot deferred; this repo is the self-test target)
- Native ClusterFuzz GitHub / PR integration
- BigQuery / Stackdriver crash-stats dashboards
- Windows / macOS bots
