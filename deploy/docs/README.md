# Local Docker ClusterFuzz

Dockerized **local-mode** (no GCP) ClusterFuzz on this host, triggered by GitHub via a self-hosted Actions runner, with a small custom bridge that posts PR review comments when fuzzing finds crashes.

## What this is

| Piece | Role |
| --- | --- |
| Official `google/clusterfuzz` **v2.35.0** (local mode) | UI, task queue, bots, corpus, minimize, filesystem “GCS” |
| Docker Compose (`cf-server`, `cf-bot`; optional `gha-runner`) | Always-on fuzz farm + CI worker on this machine |
| Custom glue | Self-hosted runner workflow + `post_pr_comments.py` for GitHub PR reviews |

## What this is not

- **Not** production ClusterFuzz on GCP (no App Engine, Firebase, real GCS, Datastore, Pub/Sub, BigQuery, or Stackdriver).
- **Not** native ClusterFuzz ↔ GitHub integration — PR comments are our reporter, not an upstream feature.
- **Not** a multi-region HA farm — single host, capacity limited by this server.

See [limitations.md](limitations.md) for the full gap list. Live inventory: [implemented.md](implemented.md). Bootstrap result: [`../BOOTSTRAP_STATUS.md`](../BOOTSTRAP_STATUS.md).

## Quick start

Host needs **Docker** and **Docker Compose** only. Python and bot deps live inside images.

`cf-server` / `cf-bot` use **`network_mode: host`** so local Datastore/Pub/Sub/GCS emulators on localhost work. The UI listens on **`127.0.0.1:9300`** (and **`9308`** for the local GCS emulator). Nothing else on the host may bind those ports.

```bash
# Workspace: /home/william/Documents/clusterfuzz
# Upstream pin: v2.35.0 under upstream/

# First time (or wipe): build images, bootstrap storage, start server+bot
./deploy/scripts/bootstrap.sh

# Later starts
cd deploy
docker compose up -d

# UI
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9300/
# → open http://127.0.0.1:9300/

# Optional self-hosted runner (override compose + secrets)
cp runner.env.example runner.env   # set REPO_URL + token
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d gha-runner
```

**Ports:** this deploy remaps local-mode ports from upstream `900x` to **`9300–9309`** via `deploy/scripts/apply_local_ports.sh` at image build time, so Tetrazero can keep **`:9000`**. Ensure **`9300`/`9308`** (etc.) are free — see `BOOTSTRAP_STATUS.md`.

Data lives at repo-root **`data/`** (mounted `../data:/data`). `deploy/data` is a symlink to `../data` for runner/ingest paths.

Demo target: `deploy/demo-target/` + `scripts/ingest_build.sh`. PR template: `deploy/workflows/fuzz-pr.yml` — see [github-integration.md](github-integration.md) and `deploy/GITHUB_RUNNER.md`.

## Documentation index

| Doc | Contents |
| --- | --- |
| [architecture.md](architecture.md) | Server / bot / local_gcs / runner / GitHub diagram and data flows; upstream vs glue |
| [setup.md](setup.md) | Host prereqs, image builds, bootstrap, volumes, runner registration, wiring repos |
| [operations.md](operations.md) | Start/stop, logs, jobs/builds, backup, troubleshooting |
| [github-integration.md](github-integration.md) | Self-hosted runner, workflow template, comment format, permissions |
| [implemented.md](implemented.md) | Inventory of what shipped / is running |
| [limitations.md](limitations.md) | GCP gaps, local-mode limits, GitHub gaps, future work |
| [`../BOOTSTRAP_STATUS.md`](../BOOTSTRAP_STATUS.md) | Last bootstrap verification + port notes |

## Layout (workspace)

```
clusterfuzz/
  upstream/                      # google/clusterfuzz @ v2.35.0
  data/                          # local_gcs, bot, runner (host mount → /data)
  deploy/
    docker-compose.yml           # cf-server + cf-bot (host network)
    docker-compose.runner.yml    # gha-runner override
    Dockerfile.server|.bot|.runner
    scripts/                     # bootstrap, build_upstream_images, timed fuzz, ingest, PR comments
    demo-target/
    workflows/fuzz-pr.yml
    data -> ../data              # symlink
    BOOTSTRAP_STATUS.md
    GITHUB_RUNNER.md
    docs/                        # ← you are here
```
