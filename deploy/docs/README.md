# Local Docker ClusterFuzz

Dockerized **local-mode** (no GCP) ClusterFuzz on this host, with GitHub PR fuzz comments via a Sourcery-style **GitHub App** (preferred) or a self-hosted Actions runner (pilots).

## What this is

| Piece | Role |
| --- | --- |
| Official `google/clusterfuzz` **v2.35.0** (local mode) | UI, task queue, bots, corpus, minimize, filesystem “GCS” |
| Docker Compose (`cf-server`, `cf-bot`; optional App / runner) | Always-on fuzz farm + PR workers on this machine |
| GitHub App + **host** cloudflared | Account-wide PR webhooks at `fuzzing.tetrazero.com` — [github-app.md](github-app.md) |
| Custom glue | Timed fuzz + `post_pr_comments.py` for GitHub PR reviews |

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

# Preferred: GitHub App (all-repo PR fuzz via fuzzing.tetrazero.com)
cp github-app.env.example github-app.env       # App ID, webhook secret
# place private-key.pem under github-app/ — see docs/github-app.md
docker compose -f docker-compose.yml -f docker-compose.github-app.yml \
  --env-file github-app.env up -d --build cf-github-app
# Public URL uses the host cloudflared tunnel (TetrazeroDashboard):
#   fuzzing.tetrazero.com → http://127.0.0.1:9310

# Optional: self-hosted runner (Actions fallback only)
cp runner.env.example runner.env   # set REPO_URL + token
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d gha-runner
```

**Ports:** this deploy remaps local-mode ports from upstream `900x` to **`9300–9309`** via `deploy/scripts/apply_local_ports.sh` at image build time, so Tetrazero can keep **`:9000`**. App webhooks use **`127.0.0.1:9310`** behind the **host** cloudflared tunnel. Ensure **`9300`/`9308`** (etc.) are free — see `BOOTSTRAP_STATUS.md`.

Data lives at repo-root **`data/`** (mounted `../data:/data`). `deploy/data` is a symlink to `../data` for runner/ingest paths.

Demo target: `deploy/demo-target/` + `scripts/ingest_build.sh`. App docs: [github-app.md](github-app.md). Actions fallback: [github-integration.md](github-integration.md) and `deploy/GITHUB_RUNNER.md`.

## Documentation index

| Doc | Contents |
| --- | --- |
| [architecture.md](architecture.md) | Server / bot / local_gcs / App / runner / GitHub diagram and data flows |
| [setup.md](setup.md) | Host prereqs, image builds, bootstrap, volumes, wiring repos |
| [operations.md](operations.md) | Start/stop, logs, jobs/builds, backup, troubleshooting |
| [github-app.md](github-app.md) | Preferred: GitHub App + host cloudflared (`fuzzing.tetrazero.com`) |
| [github-integration.md](github-integration.md) | Actions fallback: runner, thin caller → reusable job, comments |
| [multi-repo.md](multi-repo.md) | App-first `cf_enable_repo.sh` + optional `--with-actions` |
| [implemented.md](implemented.md) | Inventory of what shipped / is running |
| [limitations.md](limitations.md) | GCP gaps, local-mode limits, GitHub gaps, future work |
| [`../BOOTSTRAP_STATUS.md`](../BOOTSTRAP_STATUS.md) | Last bootstrap verification + port notes |

## Layout (workspace)

```
clusterfuzz/
  upstream/                      # google/clusterfuzz @ v2.35.0
  data/                          # local_gcs, bot, runner, github_app_* (host mount → /data)
  deploy/
    docker-compose.yml           # cf-server + cf-bot (host network)
    docker-compose.github-app.yml # cf-github-app only (host tunnel)
    docker-compose.runner.yml    # gha-runner override (Actions fallback)
    docker-compose.llm-worker.yml
    Dockerfile.server|.bot|.runner|.github-app|.llm-worker
    patches/                     # ports + bot copytree
    targets/                     # farm registry YAML
    github-app/                  # deny list, app manifest, key (gitignored)
    scripts/                     # bootstrap, timed fuzz, App server, PR comments, LLM
    demo-target/
    workflows/                   # fuzz-pr-caller*.yml.template
    tests/
    data -> ../data              # symlink
    BOOTSTRAP_STATUS.md
    GITHUB_RUNNER.md
    docs/                        # ← you are here
```
