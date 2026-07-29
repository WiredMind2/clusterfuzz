# Official ClusterFuzz pin

Local builds expect a checkout at `upstream/` (gitignored).

**Pinned tag:** `v2.35.0`

```bash
git clone --branch v2.35.0 --depth 1 \
  https://github.com/google/clusterfuzz.git upstream
```

Custom Docker Compose, scripts, and GitHub glue live under `deploy/` — do not put project glue inside `upstream/`.
