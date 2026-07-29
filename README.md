# Local Docker ClusterFuzz

Dockerized **local-mode** (no GCP) ClusterFuzz on a single host, with a self-hosted GitHub Actions runner that can post PR review comments when timed fuzzing finds crashes.

## Layout

| Path | Role |
| --- | --- |
| `upstream/` | Official `google/clusterfuzz` @ **v2.35.0** (local checkout; see [`UPSTREAM.md`](UPSTREAM.md)) |
| `deploy/` | Compose, Dockerfiles, scripts, demo target, workflow template, docs |
| `data/` | Local runtime storage (`local_gcs/`, bot, runner) — **not in git** |

## Quick start

```bash
# First time (or wipe): build images, bootstrap storage, start server+bot
./deploy/scripts/bootstrap.sh

# Later starts
cd deploy && docker compose up -d

# UI (remapped off :9000)
curl -fsS http://127.0.0.1:9300/
```

Self-hosted runner + PR fuzz: see [`deploy/GITHUB_RUNNER.md`](deploy/GITHUB_RUNNER.md) and [`deploy/docs/`](deploy/docs/).

## Docs

Full documentation lives under [`deploy/docs/README.md`](deploy/docs/README.md).
