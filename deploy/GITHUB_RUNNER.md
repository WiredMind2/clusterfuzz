# Register the self-hosted GitHub Actions runner

Labels used by fuzz workflows: `self-hosted`, `linux`, `clusterfuzz-local`.

## Personal account (WiredMind2 today) — recommended

Register **one** runner to `WiredMind2/clusterfuzz`. Other repos are enabled with
`./scripts/cf_enable_repo.sh OWNER/REPO` and dispatch into this repo (see `docs/multi-repo.md`).

```bash
cd deploy
cp runner.env.example runner.env
# REPO_URL=https://github.com/WiredMind2/clusterfuzz
# RUNNER_SCOPE=repo
# ACCESS_TOKEN=… or RUNNER_TOKEN=…
docker compose -f docker-compose.yml -f docker-compose.runner.yml --env-file runner.env up -d --build gha-runner
```

Also set once (done automatically by `cf_enable_repo.sh`):

```bash
gh secret set CLUSTERFUZZ_CROSS_REPO_TOKEN --repo WiredMind2/clusterfuzz --body "$(gh auth token)"
```

Confirm in GitHub: repo Settings → Actions → Runners — **Idle**, labels include `clusterfuzz-local`.

## Org-scoped runner (when you have a GitHub Organization)

1. Org → Settings → Actions → Runners → New runner (or use PAT with org runner admin)
2. In `runner.env`: `RUNNER_SCOPE=org`, `ORG_NAME=your-org`, `ACCESS_TOKEN=…`
3. Allow the runner group for member repositories
4. Use `workflows/fuzz-pr-org-caller.yml.template` in member repos

## Notes

- `network_mode: host` on the runner so jobs reach loopback Ollama (`:11434`) and OTLP; OpenRouter uses outbound HTTPS when `LLM_FUZZ_PROVIDER=openrouter`.
- `docker.sock` is mounted so jobs can build sibling containers.
- `../data` → `/data`; deploy tree → `/opt/clusterfuzz/deploy:ro`.
- Never commit `runner.env` or tokens.
