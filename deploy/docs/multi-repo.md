# Multi-repo PR fuzzing (minimal enable)

One local ClusterFuzz farm + **one** self-hosted runner on `WiredMind2/clusterfuzz` serves many repos.

> **Note:** `WiredMind2` is a personal GitHub account (not an Organization), so GitHub org-level runners are unavailable. Multi-repo works via **central `repository_dispatch`**: target repos send an event here; the farm runner checks out the PR and fuzzes it. If you later create a GitHub Organization, use [`workflows/fuzz-pr-org-caller.yml.template`](../workflows/fuzz-pr-org-caller.yml.template) + `RUNNER_SCOPE=org` instead.

## One-command enable

On this farm host:

```bash
cd /home/william/Documents/clusterfuzz/deploy
./scripts/cf_enable_repo.sh WiredMind2/MyProject
# options: --engine auto|atheris|libfuzzer   --no-scaffold   --dry-run
```

The script will:

1. Detect Python → Atheris / C++ → libFuzzer (or honor `--engine`)
2. Scaffold a harness under `fuzz/` if missing (Ollama LLM, fail-open to a static template)
3. Open an enablement PR on the target with a **thin** `.github/workflows/fuzz-pr.yml` (dispatch caller)
4. Set Actions variables + `CLUSTERFUZZ_DISPATCH_TOKEN` on the target
5. Ensure `CLUSTERFUZZ_CROSS_REPO_TOKEN` on `WiredMind2/clusterfuzz` (cross-repo checkout + PR comments)
6. Write `deploy/targets/<slug>.yaml` on this checkout (commit/push the farm repo when ready)

Merge the enablement PR, then open any PR on that project to exercise fuzzing.

## Architecture

```
Target repo PR
  → thin workflow (GitHub-hosted ubuntu-latest)
  → repository_dispatch → WiredMind2/clusterfuzz
  → self-hosted clusterfuzz-local runner
  → reusable fuzz job (build / LLM prepare / timed fuzz / comment / enqueue)
```

| Piece | Path |
| --- | --- |
| Enable script | [`scripts/cf_enable_repo.sh`](../scripts/cf_enable_repo.sh) |
| Harness scaffold | [`scripts/llm_scaffold_harness.py`](../scripts/llm_scaffold_harness.py) |
| Thin caller template | [`workflows/fuzz-pr-caller.yml.template`](../workflows/fuzz-pr-caller.yml.template) |
| Reusable job | `WiredMind2/clusterfuzz/.github/workflows/fuzz-pr-reusable.yml` |
| Dispatch entry | `.github/workflows/fuzz-dispatch.yml` |
| Target registry | [`targets/`](../targets/) |

## Runner (farm)

Keep the runner registered to **this** repo (personal-account mode):

```bash
# deploy/runner.env
REPO_URL=https://github.com/WiredMind2/clusterfuzz
RUNNER_SCOPE=repo
# ACCESS_TOKEN=… or RUNNER_TOKEN=…
```

```bash
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d gha-runner
```

### Optional: GitHub Organization later

1. Create an org and move repos (or mirror).
2. Set `RUNNER_SCOPE=org`, `ORG_NAME=…`, PAT with org runner admin; re-register.
3. In org Settings → Actions → Runners, allow the runner group for member repos.
4. Use [`fuzz-pr-org-caller.yml.template`](../workflows/fuzz-pr-org-caller.yml.template) so jobs run directly on the org runner (`uses: …/fuzz-pr-reusable.yml@main`).

## Engines

- **atheris** — `fuzz/**/*_fuzzer.py`; usually `SKIP_INGEST=1`
- **libfuzzer** — prefer `fuzz/build.sh` producing a binary + `name-([0-9]+).zip` under `fuzz/out/`

Both write `fuzz-artifacts/crashes/` for `post_pr_comments.py`. LLM prepare/triage/background worker behave as documented in [github-integration.md](github-integration.md).

## Appendix: manual enable (no script)

1. Copy `workflows/fuzz-pr-caller.yml.template` → target `.github/workflows/fuzz-pr.yml`
2. `gh secret set CLUSTERFUZZ_DISPATCH_TOKEN` on the target (PAT with `repo` + `workflow`)
3. `gh secret set CLUSTERFUZZ_CROSS_REPO_TOKEN` on `WiredMind2/clusterfuzz`
4. Set variables: `FUZZ_ENGINE`, `TARGET_ID`, `CLUSTERFUZZ_UI_URL`, …
5. Add `deploy/targets/<id>.yaml` on the farm
