# Multi-repo PR fuzzing (minimal enable)

One local ClusterFuzz farm serves many repos.

**Preferred for “all repos by default”:** the Sourcery-style **GitHub App** ([github-app.md](github-app.md)) — install once on WiredMind2 with **All repositories**, webhook via host `fuzzing.tetrazero.com`, no thin workflow per target. Disable via App install settings, `github-app/deny-repos.txt`, or `.clusterfuzz.yml`.

**Actions fallback:** **one** self-hosted runner on `WiredMind2/clusterfuzz` + thin callers (`cf_enable_repo.sh --with-actions`). Personal accounts have no org runners, so that path uses **central `repository_dispatch`**. Do not leave App + thin workflow both enabled (duplicate jobs).

---

## GitHub App (default-on)

See [github-app.md](github-app.md). After the App + host tunnel are up, new PRs on installed repos are fuzzed without `cf_enable_repo.sh`. Use `cf_enable_repo.sh` to scaffold harnesses and register `deploy/targets/<slug>.yaml`.

Pilots (`AnimeManager`, `OpenRouterClient`) are **App-only** (thin `fuzz-pr.yml` removed).

---

## Enable script (App-first)

### One-command enable

On this farm host:

```bash
cd /home/william/Documents/clusterfuzz/deploy

# Default: harness scaffold + farm targets YAML + App notes (no thin workflow)
./scripts/cf_enable_repo.sh WiredMind2/MyProject

# Also open a farm PR for deploy/targets/<slug>.yaml
./scripts/cf_enable_repo.sh WiredMind2/MyProject --farm-pr

# Actions fallback only (sets vars; secrets require CLUSTERFUZZ_DISPATCH_TOKEN env)
CLUSTERFUZZ_DISPATCH_TOKEN=ghp_… \
  ./scripts/cf_enable_repo.sh WiredMind2/MyProject --with-actions

# options: --engine auto|atheris|libfuzzer   --no-scaffold   --dry-run
```

The script will (default):

1. Detect Python → Atheris / C++ → libFuzzer (or honor `--engine`)
2. Scaffold a harness under `fuzz/` if missing (LLM via Ollama/OpenRouter, fail-open to a static template)
3. Open an enablement PR on the target (**harness only** — no workflow)
4. Write `deploy/targets/<slug>.yaml` on this checkout
5. With `--farm-pr`, commit/push and open a PR on `WiredMind2/clusterfuzz` for that YAML

With `--with-actions` it additionally adds a thin dispatch workflow and Actions variables. Secrets are written **only** when `CLUSTERFUZZ_DISPATCH_TOKEN` is set in the environment (fine-grained PAT / installation token). The operator’s `gh auth token` is **never** copied automatically.

Merge the enablement PR, then open any PR on that project to exercise App fuzzing.

## Architecture

```
Preferred:
  Target repo PR
    → GitHub App webhook (fuzzing.tetrazero.com)
    → cf-github-app worker
    → run_pr_fuzz_job.sh → post_pr_comments.py

Actions fallback:
  Target repo PR
    → thin workflow (GitHub-hosted ubuntu-latest)
    → repository_dispatch → WiredMind2/clusterfuzz
    → self-hosted clusterfuzz-local runner
    → reusable fuzz job
```

| Piece | Path |
| --- | --- |
| Enable script | [`scripts/cf_enable_repo.sh`](../scripts/cf_enable_repo.sh) |
| Harness scaffold | [`scripts/llm_scaffold_harness.py`](../scripts/llm_scaffold_harness.py) |
| Thin caller template | [`workflows/fuzz-pr-caller.yml.template`](../workflows/fuzz-pr-caller.yml.template) |
| Reusable job | `WiredMind2/clusterfuzz/.github/workflows/fuzz-pr-reusable.yml` |
| Dispatch entry | `.github/workflows/fuzz-dispatch.yml` |
| Target registry | [`targets/`](../targets/) |

## Runner (farm — Actions fallback)

Keep the runner registered to **this** repo only if you still use `--with-actions`:

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
4. Switch targets to [`workflows/fuzz-pr-org-caller.yml.template`](../workflows/fuzz-pr-org-caller.yml.template).
