# Bootstrap status

**Result:** success — local ports remapped to **9300–9309**

**Upstream tag:** v2.35.0 (`/home/william/Documents/clusterfuzz/upstream`)

## Port remap

Upstream local mode hardcodes `9000`/`9008`/… in `src/local/butler/constants.py` and the Go GCS emulator (`local/emulators/gcs.go` defaults to `-port 9008`).
This deploy applies `deploy/scripts/apply_local_ports.sh` at **image build** time:

| Role | Upstream | This deploy |
| --- | --- | --- |
| UI (gunicorn) | 9000 | **9300** |
| Admin | 9002 | 9302 |
| Datastore emulator | 9004 | 9304 |
| Pub/Sub emulator | 9006 | 9306 |
| Local GCS | 9008 | **9308** |
| Cron gunicorn | 9009 | 9309 |

**Why:** host `:9000` is reserved for Tetrazero. ClusterFuzz uses host networking, so it must not bind `9000`.

## Verified

| Check | Status |
| --- | --- |
| Images rebuilt with port patch | yes |
| UI `http://127.0.0.1:9300/` | HTTP 200 |
| GCS on `127.0.0.1:9308` | listening (`-port=9308`) |
| Datastore/PubSub/cron on 9304/9306/9309 | listening |
| `:9000` / `:9008` free of ClusterFuzz | yes |

## Compose how-to

```bash
cd /home/william/Documents/clusterfuzz/deploy
docker compose up -d
curl -fsS http://127.0.0.1:9300/
```

First-time / wipe:

```bash
./scripts/bootstrap.sh
```
