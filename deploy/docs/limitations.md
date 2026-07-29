# Limitations, gaps, and missing pieces

This deployment is **useful for local fuzzing and GitHub-triggered PR feedback**, but it is **not** production ClusterFuzz on Google Cloud. This document lists intentional omissions, local-mode constraints, GitHub gaps we paper over, and future work.

---

## GCP / production ClusterFuzz (intentionally not used)

Production ClusterFuzz is built around Google Cloud. We do **not** deploy or emulate that control plane.

| Production piece | What it normally provides | Our stance |
| --- | --- | --- |
| **App Engine** | Hosted ClusterFuzz web/control plane | Replaced by local `butler.py run_server` in Docker |
| **Firebase Auth** | Google-managed sign-in for the UI | No Firebase; local UI access model only (treat as trusted-network) |
| **Cloud Storage (GCS)** | Durable multi-region object storage for builds/corpora/crashes | Filesystem tree under `data/local_gcs` |
| **Datastore** | Primary metadata / entity store in production | Local-mode persistence only (no cloud Datastore) |
| **Pub/Sub** | Async messaging between components | Not used |
| **BigQuery** | Analytics warehouse for fuzzer/crash stats | Not used — **stats dashboards unavailable** |
| **Stackdriver / Cloud Logging & Monitoring** | Centralized ops telemetry | Compose / file logs on this host only |
| **`butler.py create_config`** | Generates production project config / deploy artifacts | **Not run**; no production deploy path in this stack |
| **GCE bots / autoscaling** | Managed bot VMs that scale with load | Fixed `cf-bot` containers on **one** Docker host |

### Implications

- **No cloud HA** — disk or host failure loses the farm unless you backup `data/`.
- **No Google-managed auth** — do not expose `:9300` / `:9308` to the public internet without your own access control.
- **No BigQuery-backed crash/fuzzer statistics** — the UI features that depend on those pipelines stay empty or disabled (also called out in upstream local-instance docs).
- **No GCE bot pool** — you cannot “add capacity in another zone” without standing up more hosts yourself (or later migrating to GCP).

---

## Local-mode ClusterFuzz limitations

Upstream documents that a [local instance](https://google.github.io/clusterfuzz/getting-started/local-instance/) is for testing **core** functionality. Several features are disabled or degraded without Google Cloud emulators.

### Features disabled or degraded

- **Crash statistics** and **fuzzer statistics** that depend on BigQuery / Stackdriver-style backends.
- Any workflow that assumes production IAM, service accounts, or cloud project IDs.
- Production-only admin / deployment tooling (`create_config`, App Engine deploy).

What **does** still work in local mode (and is the point of this stack): running fuzz tasks, corpora on disk, crash testcases, minimize-style workflows as supported locally, and the web UI for triage.

### Filesystem “GCS” only

- Objects live under `…/<bucket>/objects/…` and metadata under `…/<bucket>/metadata/…`.
- This is **not** multi-region durable object storage.
- No object versioning / lifecycle policies comparable to real GCS unless you add them yourself on the host.
- Server and bot **must** share one path; misconfiguration looks like “no builds” or split-brain data.

### Single-host capacity

- CPU, RAM, and disk of **this server** bound both continuous bots and PR timed fuzz.
- Heavy ASan builds + long fuzz timeouts will contend with `cf-bot`.
- Scaling means more containers on the same machine (until resources exhaust) or more hosts (not automated here).

### Ports and remote UI access

- Upstream defaults: UI **9000**; GCS **9008**. This deploy remaps to UI **9300**; GCS **9308** (block `9300–9309`) via `apply_local_ports.sh`.
- This deploy uses **`network_mode: host`**; on this host the UI binds **`127.0.0.1:9300`** (loopback only).
- Host port conflicts on the remapped ports are fatal; `:9000` is left for Tetrazero (`deploy/BOOTSTRAP_STATUS.md`).
- If you access the UI from another machine, you need SSH tunneling or a reverse proxy — and must forward auxiliary ports too, or parts of the UI break.

---

## GitHub / product gaps we paper over

### No native PR comments or Actions integration

ClusterFuzz upstream has **no** first-class GitHub PR commenting and **no** Actions integration. Anything that looks like “ClusterFuzz commented on my PR” is **our custom bridge**:

- Self-hosted runner (`gha-runner`)
- Workflow template (`deploy/workflows/fuzz-pr.yml`)
- Reporter (`deploy/scripts/post_pr_comments.py`)

Do not document or demo this as an upstream feature.

### PR path vs async job queue

- **Source of truth for PR comments:** timed fuzz on the runner (synchronous, mapped to that PR).
- **Not** sufficient alone: enqueue a CF task and hope a bot finishes before the workflow ends — that race is why we chose timed runner fuzz.
- Background `cf-server` / `cf-bot` still matter for continuous coverage and UI triage after ingest.

### Bug tracking

- Upstream’s built-in tracker story centers on **Monorail**.
- We do **not** wire Monorail, GitHub Issues auto-filing, or another tracker beyond **PR review comments**.
- Persistent triage beyond the PR still happens in the local ClusterFuzz UI (and your own process).

### Runner availability

- Self-hosted runner **must stay online**.
- No runner ⇒ workflows wait; no PR fuzz; no automated comments.
- GitHub-hosted runners are **intentionally not** used for fuzz compute (no shared `local_gcs`, different environment).

### Auth and visibility of UI links in comments

- Comments may include links to `http://<host>:9300/…`. Those only help people who can reach this server’s network.
- There is no Firebase / IAP-style gate in this design.

---

## Not implemented / future work

| Item | Notes |
| --- | --- |
| Multi-repo **org-wide runner groups** | Possible in GitHub; not required for a single-repo pilot |
| Multi-sanitizer matrices beyond demo default | e.g. ASan + MSan + UBSan matrix jobs |
| Corpus pruning / coverage report cron | Optional follow-up; not part of the MVP glue |
| Windows / macOS bots | Linux Docker bots only |
| Horizontal multi-host farm | Manual compose on more machines or later GCP |
| Production **GCP migration path** | Outline only — see below |
| Native upstream GitHub support | Would require ClusterFuzz product work; out of scope |
| Full crash-stats dashboards in local mode | Blocked on BigQuery/Stackdriver (or a new local analytics design) |

### Outline: what a later GCP migration would add

If you outgrow local mode, you would typically need (high level — not implemented here):

1. A GCP project and `butler.py create_config` / production config generation.
2. App Engine (or current upstream-supported hosting) for the control plane.
3. Real GCS buckets, Datastore, Pub/Sub, and IAM service accounts.
4. Firebase (or current upstream auth) for UI access control.
5. BigQuery + monitoring for stats and ops.
6. GCE (or documented bot compute) with autoscaling instead of a single Docker host.
7. A decision on whether the custom GitHub comment bridge remains (likely yes — still not native) and how UI links point at the production deployment.

Until then, treat backups of `data/local_gcs` as mandatory operational practice.

---

## Known issues (living section)

Append issues discovered during implementation here (build failures, upstream quirks, workarounds).

| Date | Issue | Workaround / status |
| --- | --- | --- |
| 2026-07-21 | Host `:9000` conflict with Tetrazero | Remapped CF local ports to `9300–9309` at image build — see `BOOTSTRAP_STATUS.md` |
| 2026-07-21 | Host networking required for local emulators | `cf-server` / `cf-bot` use `network_mode: host`; do not switch to bridge without redesigning emulator addressing |
| 2026-07-21 | Storage path dual naming | Canonical host dir is repo-root `data/`; `deploy/data` is a symlink → `../data` for runner/ingest |

Refresh [implemented.md](implemented.md) after significant stack changes.
