# Operations

Day-2 operations for the local Docker ClusterFuzz stack.

Bootstrap / verification snapshot: [`../BOOTSTRAP_STATUS.md`](../BOOTSTRAP_STATUS.md).

## Start / stop

Work from `deploy/`. Main stack is `docker-compose.yml` (`cf-server`, `cf-bot`). GitHub App and runner are **override** files. Prefer the App for PR fuzz; runner is Actions fallback only.

```bash
cd /home/william/Documents/clusterfuzz/deploy

# Start continuous farm (host networking)
docker compose up -d

# Preferred PR path: GitHub App (host cloudflared already routes fuzzing.tetrazero.com)
docker compose -f docker-compose.yml -f docker-compose.github-app.yml \
  --env-file github-app.env up -d cf-github-app

# Optional LLM background worker
docker compose -f docker-compose.yml -f docker-compose.llm-worker.yml up -d llm-fuzz-worker

# Actions fallback runner (needs runner.env)
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d gha-runner

# Stop farm (data/ retained)
docker compose stop
docker compose down
```

### First boot vs later boots

- **First time / wipe:** `./scripts/bootstrap.sh` (or `FORCE_BOT_RESET=1 ./scripts/bootstrap.sh`), then normal `docker compose up -d`.
- **Normal restarts:** do **not** re-bootstrap — `--bootstrap` resets local data.

### Upgrade

1. `cd upstream && git fetch && git checkout <new-tag>`
2. Optional: `./deploy/scripts/build_upstream_images.sh`
3. `cd deploy && docker compose build && docker compose up -d`
4. Smoke-test `http://127.0.0.1:9300/` and `bot.log`

Pin the upstream tag in [implemented.md](implemented.md) when you change it.

## Networking and ports

`cf-server` and `cf-bot` use **`network_mode: host`**. They bind host loopback:

| Port | Role |
| --- | --- |
| `127.0.0.1:9300` | ClusterFuzz UI (gunicorn) |
| `127.0.0.1:9308` | Local GCS emulator / uploads |

If another process already owns `:9300` (CF UI after remap), ClusterFuzz will fail to listen. Free that port or adjust `apply_local_ports.sh`, then restart:

```bash
docker stop tetrazerodashboard-dashboard-1   # example from this host
cd deploy && docker compose up -d
```

## Logs

### Compose

```bash
cd /home/william/Documents/clusterfuzz/deploy
docker compose logs -f
docker compose logs -f cf-server
docker compose logs -f cf-bot
docker compose -f docker-compose.yml -f docker-compose.runner.yml logs -f gha-runner
```

### Bot log file

```bash
tail -f /home/william/Documents/clusterfuzz/data/bot/clusterfuzz/bot/logs/bot.log
```

Harmless until jobs exist: `Failed to get any fuzzing tasks`.

### GitHub Actions

Workflow logs appear in the GitHub Actions UI. Runner registration issues show in `gha-runner` compose logs.

## Jobs and builds

### Continuous farm

1. Define / enable a fuzzing **job** in the ClusterFuzz UI (or via local bootstrap defaults).
2. Produce a **revisioned build zip** (sanitizer fuzzers).
3. Ingest into filesystem GCS:

```bash
cd /home/william/Documents/clusterfuzz/deploy
./scripts/ingest_build.sh   # defaults resolve via deploy/data → ../data/local_gcs
```

4. Confirm the bot picks up a fuzz task; crashes appear as testcases in the UI.

### Demo path

```bash
cd /home/william/Documents/clusterfuzz/deploy/demo-target
./build.sh 1
../scripts/ingest_build.sh
```

Prebuilt zips may already be under `demo-target/out/`.

### PR path (preferred: GitHub App)

1. Webhook → `cf-github-app` (`127.0.0.1:9310` via `fuzzing.tetrazero.com`).
2. Worker runs `scripts/run_pr_fuzz_job.sh` (timed fuzz).
3. `post_pr_comments.py` posts review comments as the App.
4. Optional ingest into `local_gcs` for continuous-farm triage.

**Actions fallback:** thin `fuzz-pr.yml` → `repository_dispatch` → `gha-runner` → reusable workflow. Do not leave both paths enabled on the same repo (duplicate comments).

Health probe: `./scripts/cf_farm_health.sh`.

## Backup

Durable state lives under repo-root **`data/`** (also reachable as `deploy/data`):

| Path | Contents |
| --- | --- |
| `data/local_gcs/` | Builds, corpora, crashes |
| `data/bot/` | Bot workdir + logs |
| `data/runner/` | Runner registration |
| `data/env.yaml`, `data/WEB-INF/` | Local server config artifacts |

```bash
cd /home/william/Documents/clusterfuzz/deploy
docker compose stop

sudo tar -C /home/william/Documents/clusterfuzz -czf \
  ~/clusterfuzz-data-$(date +%Y%m%d).tar.gz data/

docker compose start
```

Restore by extracting over `data/` with the stack stopped, then start **without** bootstrap.

## Troubleshooting

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| UI not on `:9300` | Port taken / server still starting | `ss -tlnp \| grep 9300`; free conflicting process; `docker compose logs -f cf-server` |
| Healthcheck failing | Bootstrap incomplete or deps install | Wait through `start_period`; check `BOOTSTRAP_STATUS.md`; re-run `./scripts/bootstrap.sh` if needed |
| Bot: `Failed to get any fuzzing tasks` | No jobs/builds yet | Expected until ingest; otherwise check `data/local_gcs` |
| Bot cannot see builds | Wrong storage root | Confirm both services mount `../data:/data` and use `/data` |
| Bootstrap wiped data | `--bootstrap` / `bootstrap.sh` on non-first run | Restore from backup; avoid bootstrap on upgrades |
| Workflow waiting for runner | Runner not started / labels | Start override compose with `runner.env`; labels must include `clusterfuzz-local` |
| No PR comments | Permissions / no findings | `pull-requests: write`; check timed fuzz artifacts |
| OOM / thrashing | Bot + runner + builds on one host | Reduce parallelism / fuzz timeout |

### Resetting local data

```bash
cd deploy
docker compose down
# ONLY if you intend to wipe:
# sudo rm -rf ../data/local_gcs ../data/bot   # keep runner/ if desired
./scripts/bootstrap.sh
```

## Health checklist

```bash
./scripts/cf_farm_health.sh
```

1. `docker compose ps` → `cf-server` **healthy**, `cf-bot` **Up**
2. `curl -fsS http://127.0.0.1:9300/` → 200
3. `curl -fsS http://127.0.0.1:9310/healthz` and `https://fuzzing.tetrazero.com/healthz` → 200
4. `data/local_gcs` populated; bot log past platform init
5. (If using Actions fallback) runner **Idle** in GitHub with correct labels
6. Demo or real job produces a testcase in the UI / PR comments
