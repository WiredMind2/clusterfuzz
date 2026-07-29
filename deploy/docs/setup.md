# Setup

End-to-end setup for the local Docker ClusterFuzz stack on this host. Host software requirement is **Docker (+ Compose)**; language runtimes and ClusterFuzz deps live inside images.

Live status after bootstrap: [`../BOOTSTRAP_STATUS.md`](../BOOTSTRAP_STATUS.md) and [implemented.md](implemented.md).

## Host prerequisites

| Requirement | Notes |
| --- | --- |
| Docker Engine | Recent Docker with BuildKit recommended |
| Docker Compose | `docker compose` v2 plugin |
| Disk | Enough for upstream checkout, images, corpora, and builds (plan tens of GB for serious use; demo needs far less) |
| CPU / RAM | Continuous bots + runner builds compete; size for your target |
| **Host ports free** | **`9300`** (UI) and **`9308`** (local GCS emulator), plus `9302/9304/9306/9309` — remapped from upstream `900x` by `apply_local_ports.sh` at image build; stack uses **`network_mode: host`** |
| Git | To clone / update `upstream/` |

No host-level Python 3.11, Java, or ClusterFuzz `install_deps` is required on the bare metal for the compose path.

### Ports (this host)

Upstream local mode defaults to `:9000` (UI) / `:9008` (GCS). This deploy patches those to **`:9300` / `:9308`** (full block `9300–9309`) in the Docker image so Tetrazero can use `:9000`. If something else already listens on `:9300`, gunicorn will not start — free that port or change the remap script. Details: `deploy/BOOTSTRAP_STATUS.md`.

### Secrets you will need later

| Secret | Used for |
| --- | --- |
| GitHub PAT or registration token | Register `gha-runner` to a repo or org |
| Token with `pull-requests: write` (workflow `GITHUB_TOKEN` or PAT) | Post PR review comments from `post_pr_comments.py` |

Do **not** commit tokens. Use `deploy/runner.env` (gitignored) from `runner.env.example`.

## Repository layout

Workspace root: `/home/william/Documents/clusterfuzz`

```
clusterfuzz/
  upstream/                         # google/clusterfuzz @ v2.35.0
  data/                             # shared storage (compose: ../data:/data)
    local_gcs/
    bot/
    runner/
  deploy/
    docker-compose.yml              # cf-server + cf-bot (network_mode: host)
    docker-compose.runner.yml       # gha-runner override
    Dockerfile.server|.bot|.runner
    runner.env.example
    GITHUB_RUNNER.md
    BOOTSTRAP_STATUS.md
    scripts/
      bootstrap.sh
      build_upstream_images.sh      # optional official docker/ bases
      run_timed_fuzz.sh
      ingest_build.sh
      post_pr_comments.py
    demo-target/
    workflows/fuzz-pr.yml
    data -> ../data                 # symlink for runner/ingest
    docs/
```

## 1. Clone official upstream

```bash
cd /home/william/Documents/clusterfuzz
git clone https://github.com/google/clusterfuzz.git upstream
cd upstream
git checkout v2.35.0
```

Already present at **v2.35.0** in this workspace. Keep glue under `deploy/`.

## 2. Build images

### Preferred: bootstrap (builds + boots)

```bash
./deploy/scripts/bootstrap.sh
# optional wipe of bot workdir: FORCE_BOT_RESET=1 ./deploy/scripts/bootstrap.sh
```

### Manual compose build

```bash
cd /home/william/Documents/clusterfuzz/deploy
docker compose build          # cf-server (and bot via Dockerfile.bot dependency path)
```

| Dockerfile | Image |
| --- | --- |
| `deploy/Dockerfile.server` | `clusterfuzz-server:local` |
| `deploy/Dockerfile.bot` | `clusterfuzz-bot:local` |
| `deploy/Dockerfile.runner` | `clusterfuzz-gha-runner:local` |

### Optional official base images

```bash
./deploy/scripts/build_upstream_images.sh
# wraps: upstream/docker/build.sh ubuntu-24-04 --no-push
```

Not required for the butler server/bot images (they run `install_deps` themselves).

## 3. Bootstrap local server

First run (or full reset):

```bash
./deploy/scripts/bootstrap.sh
```

This builds images, runs `butler.py run_server --bootstrap --storage-path /data` once, starts `cf-server` + `cf-bot`, probes `http://127.0.0.1:9300/`, and writes `deploy/BOOTSTRAP_STATUS.md`.

**After bootstrap, do not pass `--bootstrap` on normal starts** — it resets data.

Verify:

```bash
cd deploy && docker compose ps
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9300/
# expect 200
```

## 4. Volumes and networking

| Host path | Container | Purpose |
| --- | --- | --- |
| `data/` (repo root) | `/data` | Shared root: `local_gcs`, bot workdir, datastore files, `env.yaml` |
| `data/local_gcs` | `/data/local_gcs` | Filesystem GCS |
| `data/bot` | `/data/bot` | Bot checkout + `…/bot/logs/bot.log` |
| `data/runner` | (via symlink) `/data/runner` or runner mount | Actions runner registration |
| `deploy/data` | — | Symlink → `../data` |

Main compose mounts:

```yaml
volumes:
  - ../data:/data
network_mode: host   # required for local emulators on localhost
```

Runner override (`docker-compose.runner.yml`) mounts `./data` (the symlink) → `/data` and `.:/opt/clusterfuzz/deploy:ro`.

Butler storage path is **`/data`** (GCS objects under `/data/local_gcs`). Server and bot must share it — they do via the same host mount + host network.

## 5. Bring up server and bot

```bash
cd /home/william/Documents/clusterfuzz/deploy
docker compose up -d
# or individually:
docker compose up -d cf-server
docker compose up -d cf-bot     # waits for cf-server healthy
```

Until jobs/builds exist, bot logs may show a harmless `Failed to get any fuzzing tasks`.

```bash
docker compose logs -f cf-bot
tail -f ../data/bot/clusterfuzz/bot/logs/bot.log
```

## 6. Demo target

```bash
cd deploy/demo-target
./build.sh 1    # → out/demo-fuzz and out/demo-fuzz-*.zip
../scripts/ingest_build.sh   # into data/local_gcs (via deploy/data symlink)
```

Built artifacts already exist under `deploy/demo-target/out/` from prior runs.

## 7. Register the self-hosted runner

See `deploy/GITHUB_RUNNER.md`.

```bash
cd deploy
cp runner.env.example runner.env
# edit REPO_URL + RUNNER_TOKEN or ACCESS_TOKEN

# Prefer override alongside main stack:
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d --build gha-runner

# Or runner-only:
docker compose -f docker-compose.runner.yml --env-file runner.env up -d --build
```

Labels default to `self-hosted`, `linux`, **`clusterfuzz-local`**.

```yaml
runs-on: [self-hosted, clusterfuzz-local]
```

## 8. Wiring a real repository

1. Copy `deploy/workflows/fuzz-pr.yml` into the target repo as `.github/workflows/fuzz-pr.yml` (replace the demo build step).
2. Match `runs-on` labels to the registered runner.
3. Ensure `permissions.pull-requests: write`.
4. Open a test PR; confirm the job lands here, fuzz runs, and comments appear.

## Config defaults (live)

| Setting | Value |
| --- | --- |
| UI | `http://127.0.0.1:9300` |
| Networking | `network_mode: host` |
| Storage | Repo-root `data/` → `/data` |
| Symlink | `deploy/data` → `../data` |
| Upstream | **v2.35.0** |
| Runner labels | `self-hosted`, `linux`, `clusterfuzz-local` |
| Runner compose | Override: `-f docker-compose.yml -f docker-compose.runner.yml` |
| GCP / Firebase / SA JSON | None |
