# Register the self-hosted GitHub Actions runner

Labels used by `workflows/fuzz-pr.yml`: `self-hosted`, `linux`, `clusterfuzz-local`.

## 1. Create credentials

**Repo runner (typical)**

1. Open `https://github.com/OWNER/REPO/settings/actions/runners/new`
2. Either:
   - Copy the short-lived **registration token** → set `RUNNER_TOKEN` in `runner.env`, or
   - Create a **PAT** (`ACCESS_TOKEN`) with permission to manage self-hosted runners for that repo

**Org runner**

1. Org → Settings → Actions → Runners → New runner
2. Set `RUNNER_SCOPE=org`, `ORG_NAME=your-org`, and `ACCESS_TOKEN` (org admin / runner admin PAT)
3. `REPO_URL` is not required for org scope

## 2. Configure env

```bash
cd deploy
cp runner.env.example runner.env
# edit REPO_URL and ACCESS_TOKEN (or RUNNER_TOKEN)
```

## 3. Start the runner container

With the main ClusterFuzz compose (if present):

```bash
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d --build gha-runner
```

Runner only:

```bash
docker compose -f docker-compose.runner.yml --env-file runner.env up -d --build
```

Confirm in GitHub: Settings → Actions → Runners — status **Idle**, labels include `clusterfuzz-local`.

## 4. Wire a target repo

Copy `workflows/fuzz-pr.yml` to `.github/workflows/fuzz-pr.yml` in the repo the runner is registered to. Open a PR to exercise build → timed fuzz → ingest → PR review comment.

## Notes

- `docker.sock` is mounted so jobs can build sibling containers.
- `../data` is mounted at `/data` (local_gcs ingest path).
- This deploy tree is mounted read-only at `/opt/clusterfuzz/deploy` for the workflow glue scripts.
- You must supply a real token; registration cannot complete without it.
