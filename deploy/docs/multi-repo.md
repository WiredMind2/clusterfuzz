# Multi-repo PR fuzzing

One local ClusterFuzz farm can serve many GitHub repos. AnimeManager is the pilot entry under `targets/`.

## Add a repo

1. Copy `targets/_example.yaml` → `targets/<id>.yaml` and fill `github`, `engine`, harnesses, `ui_url`.
2. In the target repo, add `.github/workflows/fuzz-pr.yml` from `workflows/fuzz-pr.yml` (set `FUZZ_ENGINE`, harness paths / build step, UI URL).
3. Ensure a runner with labels `self-hosted`, `linux`, `clusterfuzz-local` can pick up jobs for that repo (see below).

## Runner: repo vs org

| Phase | Scope | When |
|-------|--------|------|
| Pilot (1 repo) | `RUNNER_SCOPE=repo`, `REPO_URL=https://github.com/OWNER/REPO` | AnimeManager now |
| Many repos (same org) | `RUNNER_SCOPE=org`, `ORG_NAME=…`, PAT with org runner admin | Prefer this as you grow |
| Personal repos / no shared org | One registration per repo (separate `runner.env` / runner name), or create a GitHub org for fuzzed projects | Documented; prefer org when possible |

Credentials: copy `runner.env.example` → `runner.env` (gitignored). Use `ACCESS_TOKEN` (PAT) or a short-lived `RUNNER_TOKEN` from the Add Runner UI / `gh api …/registration-token`.

```bash
cd deploy
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d --build gha-runner
```

## Engines

- **libfuzzer** — build a binary, run `scripts/run_timed_fuzz.sh`, optionally `ingest_build.sh`.
- **atheris** — install Python deps + harnesses, run `scripts/run_timed_atheris.sh` per harness; skip ingest unless you produce a bot-compatible zip.

Both engines write `fuzz-artifacts/crashes/` for `post_pr_comments.py`. Optional OTLP: set `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` and `OTEL_SERVICE_NAME=clusterfuzz-pr-fuzz` (see `scripts/report_fuzz_otel.py`).
