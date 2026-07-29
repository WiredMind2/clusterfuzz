# Sourcery-style GitHub App (all-repo PR fuzz)

Official ClusterFuzz has no GitHub App. This deploy adds a **private GitHub App** that receives `pull_request` webhooks for every installed repo, runs timed fuzz on this server, and posts review comments as the **ClusterFuzz** bot — similar to how [Sourcery](https://github.com/marketplace/sourcery-ai) reviews PRs.

| Piece | Detail |
| --- | --- |
| Public URL | `https://fuzzing.tetrazero.com` |
| Webhook path | `https://fuzzing.tetrazero.com/github/webhooks` |
| Local listen | `127.0.0.1:9310` (`cf-github-app`) |
| Tunnel | `cloudflared` (Compose) |
| Disable | App install repos, `github-app/deny-repos.txt`, or `.clusterfuzz.yml` |

The Actions + `gha-runner` path remains for already-enabled pilots until you cut over; **new targets do not need** a thin `.github/workflows/fuzz-pr.yml` once the App is installed on the account.

---

## 1. Register the GitHub App

1. Open [GitHub → Settings → Developer settings → GitHub Apps → New GitHub App](https://github.com/settings/apps/new).
2. Use values from [`github-app/app-manifest.yml`](../github-app/app-manifest.yml) (or paste that manifest if “Create from manifest” is available):
   - **Name:** `ClusterFuzz Local` (or similar)
   - **Homepage:** `https://github.com/WiredMind2/clusterfuzz`
   - **Webhook URL:** `https://fuzzing.tetrazero.com/github/webhooks`
   - **Webhook secret:** generate a long random string (save it)
   - **Permissions:** Repository → Contents **Read-only**, Pull requests **Read & write**, Metadata **Read-only**
   - **Events:** Pull request
   - **Where can this GitHub App be installed?** Only on this account
3. Create the App → note the **App ID**.
4. **Generate a private key** → download the `.pem` → store as `deploy/github-app/private-key.pem` (gitignored).
5. **Install App** → WiredMind2 → **All repositories** (Sourcery-style default-on).

You can later switch to **Only select repositories** or remove individual repos to disable (primary disable path).

---

## 2. Public URL: existing cloudflared (not Compose)

Do **not** start the Compose `cloudflared` service. This host already runs `cloudflared.service` with config managed from TetrazeroDashboard.

| Piece | Value |
| --- | --- |
| Hostname | `fuzzing.tetrazero.com` |
| Origin | `http://127.0.0.1:9310` (`cf-github-app`) |
| Path | bypasses Caddy — **explicit** tunnel ingress (not `*.tetrazero.com`) |

Ingress is declared in [`TetrazeroDashboard/cloudflared/config.yml`](/home/william/TetrazeroDashboard/cloudflared/config.yml). Deploy (needs sudo):

```bash
cd /home/william/TetrazeroDashboard
./scripts/deploy-cloudflared.sh
```

Also ensure Cloudflare DNS has a proxied CNAME/record for `fuzzing.tetrazero.com` pointing at this tunnel (same as your other `*.tetrazero.com` names).

Checks:

```bash
curl -fsS http://127.0.0.1:9310/healthz
curl -fsS https://fuzzing.tetrazero.com/healthz
```

GitHub App webhook URL remains `https://fuzzing.tetrazero.com/github/webhooks`.

---

## 3. Start the farm services

```bash
cd /home/william/Documents/clusterfuzz/deploy
cp github-app.env.example github-app.env   # if not already filled
# Fill GITHUB_APP_ID, GITHUB_APP_WEBHOOK_SECRET; place private-key.pem

# App only — do not start Compose cloudflared
docker compose -f docker-compose.yml -f docker-compose.github-app.yml \
  --env-file github-app.env up -d --build cf-github-app
```

Then deploy the host tunnel ingress (see §2) if you have not already.

---

## 4. Disable a repo

| Method | How |
| --- | --- |
| **Primary (Sourcery-like)** | GitHub → Settings → Applications → ClusterFuzz Local → Configure → remove repo or use “Only select repositories” |
| **Farm deny list** | Add `WiredMind2/RepoName` to [`github-app/deny-repos.txt`](../github-app/deny-repos.txt); restart not required (re-read each webhook/job) |
| **In-repo** | Commit `.clusterfuzz.yml` with `enabled: false` |

---

## 5. What runs on each PR

1. Webhook `pull_request` (`opened` / `synchronize` / `reopened`) → ack fast → queue under `/data/github_app_queue`.
2. Worker mints an **installation token**, shallow-checkouts the PR head.
3. [`scripts/run_pr_fuzz_job.sh`](../scripts/run_pr_fuzz_job.sh): detect engine, optional LLM scaffold, timed Atheris/libFuzzer, triage, [`post_pr_comments.py`](../scripts/post_pr_comments.py).
4. Comments appear as the **GitHub App** identity.

Repos without harnesses get a fail-open scaffold or a short skip review — quality varies; mute noisy repos via deny list.

---

## 6. Secrets checklist (never commit)

| File | Contents |
| --- | --- |
| `github-app.env` | App ID, webhook secret, paths |
| `github-app/private-key.pem` | App private key |
| `cloudflared.env` | `TUNNEL_TOKEN` |

---

## Cutover vs Actions runner

- Keep `gha-runner` + thin callers for repos that already use `repository_dispatch` until you are happy with App delivery.
- Once the App covers a repo, you can remove that repo’s thin `fuzz-pr.yml` to avoid **double** fuzz jobs (or deny-list one path).
- New repos: **install App (all repos)** is enough; `cf_enable_repo.sh` remains useful for hand harnesses / Continuous CF targets, not required for PR comments.

See also: [github-integration.md](github-integration.md), [multi-repo.md](multi-repo.md), [limitations.md](limitations.md).
