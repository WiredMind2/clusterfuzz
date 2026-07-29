# Local Docker ClusterFuzz

Dockerized **local-mode** (no GCP) ClusterFuzz on a single host. PR fuzz comments are posted by a **GitHub App** (preferred) at `fuzzing.tetrazero.com`; a self-hosted Actions runner remains as a fallback for pilots.

## Layout

| Path | Role |
| --- | --- |
| `upstream/` | Official `google/clusterfuzz` @ **v2.35.0** (local checkout; see [`UPSTREAM.md`](UPSTREAM.md)) |
| `deploy/` | Compose, Dockerfiles, scripts, demo target, workflow templates, docs |
| `data/` | Local runtime storage (`local_gcs/`, bot, runner, App queue) — **not in git** |

## Quick start

```bash
# First time (or wipe): build images, bootstrap storage, start server+bot
./deploy/scripts/bootstrap.sh

# Later starts
cd deploy && docker compose up -d

# UI (remapped off :9000)
curl -fsS http://127.0.0.1:9300/
```

### GitHub App (preferred PR path)

```bash
cd deploy
# Fill github-app.env; place private-key.pem under github-app/
docker compose -f docker-compose.yml -f docker-compose.github-app.yml \
  --env-file github-app.env up -d --build cf-github-app
# Public URL uses the host cloudflared tunnel (TetrazeroDashboard):
#   https://fuzzing.tetrazero.com/github/webhooks → 127.0.0.1:9310
```

See [`deploy/docs/github-app.md`](deploy/docs/github-app.md). Actions fallback: [`deploy/GITHUB_RUNNER.md`](deploy/GITHUB_RUNNER.md).

## Docs

Full documentation lives under [`deploy/docs/README.md`](deploy/docs/README.md).
