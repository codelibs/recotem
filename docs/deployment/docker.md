# Docker Deployment

Recotem ships as a single Docker image. `recotem train` and `recotem serve` are separate commands in the same image.

## Image tags

Pushed by `.github/workflows/docker.yml` to `ghcr.io/codelibs/recotem`:

| Tag pattern | Mutability | Use for |
|---|---|---|
| `2.0.0`, `2.0.1`, ... (semver `{{version}}`) | immutable | production — pin here |
| `2.0`, `2.1`, ... (semver `{{major}}.{{minor}}`) | mutable within a minor | rolling minor pin |
| `latest` | mutable, tracks `main` | quick evaluation; do not use in production |
| `main` (branch ref) | mutable, head of `main` | smoke-tests only |
| `sha-<short>` | immutable | reproducing a specific commit |

`:latest` is updated on every push to `main`. The tutorial `compose.yaml` references `:latest`; in production always pin to a semver tag (e.g. `2.0.0a0`). The Helm chart and `examples/k8s/` already pin `2.0.0a0`.

The image is multi-arch (`linux/amd64`, `linux/arm64`). SBOM and SLSA
provenance attestations are attached at push time (`provenance: mode=max`,
`sbom: true`); verify with `cosign verify-attestation` if your supply-chain
policy requires it.

## compose.yaml walkthrough

The repository includes `compose.yaml` (the Docker Compose v2 default
filename — `docker compose` picks it up automatically without `-f`). Here
is an annotated version:

```yaml
services:

  # ------------------------------------------------------------------
  # train: runs recotem train once on container startup.
  # In production, replace with a CronJob (K8s) or cron entry on the host.
  # ------------------------------------------------------------------
  train:
    image: ghcr.io/codelibs/recotem:latest    # pin to a semver tag in production
    command: recotem train /recipes/my_recipe.yaml
    working_dir: /workspace
    volumes:
      - ./examples/tutorial-purchase-log:/recipes:ro  # bind-mount recipe dir read-only
      - artifacts:/workspace/artifacts                # shared artifact volume
    environment:
      RECOTEM_SIGNING_KEYS: "${RECOTEM_SIGNING_KEYS}"
    restart: "no"                      # run once; use a cron wrapper to repeat

  # ------------------------------------------------------------------
  # serve: long-running FastAPI server. Watches the artifacts volume
  # and hot-swaps models when train writes a new artifact.
  # ------------------------------------------------------------------
  serve:
    image: ghcr.io/codelibs/recotem:latest    # pin to a semver tag in production
    command: recotem serve --recipes /recipes/
    working_dir: /workspace
    ports:
      - "8080:8080"
    volumes:
      - ./examples/tutorial-purchase-log:/recipes:ro
      - artifacts:/workspace/artifacts:ro  # serve only reads; ro is safe
    environment:
      RECOTEM_SIGNING_KEYS:      "${RECOTEM_SIGNING_KEYS}"
      RECOTEM_API_KEYS:          "${RECOTEM_API_KEYS}"
      RECOTEM_HOST:              "0.0.0.0"
      RECOTEM_PORT:              "8080"
      RECOTEM_WATCH_INTERVAL:    "10"    # poll every 10 s
      RECOTEM_LOG_FORMAT:        "json"
      RECOTEM_ALLOWED_HOSTS:     "localhost,myapp.example.com"
      RECOTEM_ALLOWED_ORIGINS:   "https://myapp.example.com"
    healthcheck:
      test:
        - "CMD-SHELL"
        - "python -c \"import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).status == 200 else 1)\""
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    restart: unless-stopped

volumes:
  artifacts:
```

## Key points

**Shared artifact volume.** Both `train` and `serve` mount the same `artifacts` volume. The server polls for changes via `RECOTEM_WATCH_INTERVAL` and hot-swaps when a new artifact appears. No restart is needed.

**Secrets via env.** Never hard-code `RECOTEM_SIGNING_KEYS` or `RECOTEM_API_KEYS` in the Compose file. Pass them from a `.env` file or a secrets manager:

```bash
# .env (never commit this file)
RECOTEM_SIGNING_KEYS=prod-2026-q2:aabbcc...
RECOTEM_API_KEYS=client-a:sha256:dd0eeff...,client-b:sha256:1122334...
```

```bash
docker compose --env-file .env up -d serve
```

**RECOTEM_HOST must be 0.0.0.0 inside Docker.** The default `127.0.0.1` only binds loopback and is unreachable from outside the container. Note also that when `RECOTEM_API_KEYS` is empty, the server forces `127.0.0.1` regardless of `RECOTEM_HOST` — pass `--insecure-no-auth` with `RECOTEM_ENV` in `{development, dev, test}` to override (development environments only).

**Volume permissions (UID 1000).** The image runs as `appuser` (UID/GID 1000). Bind-mounted host directories that the container writes to (e.g. `./artifacts/`) must be writable by UID 1000:

```bash
mkdir -p ./artifacts && chown 1000:1000 ./artifacts
```

Named Docker volumes (as in `compose.yaml`) are pre-created with the right ownership and need no chown. The container also has `readOnlyRootFilesystem` semantics in mind — `/tmp` is the only writable location outside mounted volumes.

**Image-level HEALTHCHECK.** The Dockerfile declares its own `HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3` that probes the public `/health` endpoint with `urllib.request.urlopen(f'http://127.0.0.1:{RECOTEM_PORT}/health', timeout=3)` (so it picks up an overridden `RECOTEM_PORT`). For one-shot `train` containers this fires after the process has already exited and causes no spurious failures. The Compose-level healthcheck shown in the annotated example also targets `/health` (with a slightly looser `timeout=5` inside the Python probe) and overrides the image default for the `serve` service — orchestrators should rely on the HTTP 200 response from `/health`.

**Bind port only on localhost on the host if you put a reverse proxy in front:**

```yaml
ports:
  - "127.0.0.1:8080:8080"   # only accessible to host-local reverse proxy
```

## Running train on a schedule

Replace the one-shot `train` service with a cron wrapper or use the host cron to exec into the container:

```bash
# Host cron — runs inside existing serve container's shared volume
0 3 * * * docker compose -f /opt/recotem/compose.yaml run --rm train
```

Or run the `train` image as a separate, throwaway container that shares the artifact volume:

```bash
docker run --rm \
  -w /workspace \
  -v recotem_artifacts:/workspace/artifacts \
  -v /opt/recotem/recipes:/recipes:ro \
  -e RECOTEM_SIGNING_KEYS="${RECOTEM_SIGNING_KEYS}" \
  ghcr.io/codelibs/recotem:latest \
  recotem train /recipes/my_recipe.yaml
```

## Environment variables reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `RECOTEM_SIGNING_KEYS` | yes (train+serve) | — | `<kid>:<hex>,...` |
| `RECOTEM_API_KEYS` | yes (serve) | — | `<kid>:sha256:<hex>,...` |
| `RECOTEM_HOST` | no | `127.0.0.1` | Must be `0.0.0.0` inside Docker |
| `RECOTEM_PORT` | no | `8080` | |
| `RECOTEM_WATCH_INTERVAL` | no | `5` | Seconds between artifact polls (clamped 1–30) |
| `RECOTEM_LOG_FORMAT` | no | `auto`* | `json` recommended in containers |
| `RECOTEM_ALLOWED_HOSTS` | no | `127.0.0.1,localhost` | Comma-separated. Whitespace-only or empty input falls back to default. |
| `RECOTEM_ALLOWED_ORIGINS` | no | `""` (deny) | Comma-separated CORS origins |
| `RECOTEM_MAX_ARTIFACT_BYTES` | no | `2147483648` (2 GiB) | Per-artifact size cap (includes header + payload) |
| `RECOTEM_MAX_PAYLOAD_BYTES` | no | `536870912` (512 MiB) | Per-payload cap for serve-side deserialization (post-HMAC-verify). Clamped 1 MiB–16 GiB. Must be ≤ `RECOTEM_MAX_ARTIFACT_BYTES`; misconfiguration raises a `ConfigError` (exit 8) at startup. |
| `RECOTEM_MAX_DOWNLOAD_BYTES` | no | `268435456` (256 MiB) | Cap on source-path body for HTTP/HTTPS, local, and object-store reads (clamped 1 MiB–16 GiB) |
| `RECOTEM_HTTP_TIMEOUT_SECONDS` | no | `30` | Connect/read timeout for HTTP/HTTPS source fetch (clamped 1–600) |
| `RECOTEM_HTTP_ALLOW_PRIVATE` | no | `""` (blocked) | Set to `1`/`true`/`yes`/`on` to allow fetches to RFC1918/loopback/link-local destinations. Leave unset in production to block SSRF against cloud-metadata services. |
| `RECOTEM_DRAIN_SECONDS` | no | `30` | SIGTERM grace window (clamped 1–300) |
| `RECOTEM_ENV` | no | `""` | `--insecure-no-auth` permitted when set to `development`, `dev`, or `test`; `--dev-allow-unsigned` permitted only when set to `development`. |
| `RECOTEM_ARTIFACT_ROOT` | no | `""` | If set, local `output.path` must resolve under this directory (symlink-escape guard) |
| `RECOTEM_LOCK_DIR` | no | `""` | Override directory for per-recipe training lock files. Needed when `output.path` is a remote URI (lock files must be host-local). Falls back to a temp dir under the system temp directory. |
| `RECOTEM_METADATA_FIELD_DENY` | no | `""` | Comma-separated column names stripped from `/predict` responses after the metadata join |
| `RECOTEM_METRICS_ENABLED` | no | `""` | Set to `1`/`true`/`yes`/`on` to enable the Prometheus `/metrics` endpoint. Requires `recotem[metrics]` extra. |
| `RECOTEM_STARTUP_PARALLELISM` | no | `""` (auto) | Number of parallel threads used to load artifacts at startup. Default is `min(len(recipes), 8)`. Clamped 1–32. Set to `1` for sequential loading (useful for memory-constrained environments or debugging). |

*`auto` switches to `console` for an interactive TTY and `json` otherwise.

## Health check

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "recipes": {
    "my_recipe": {
      "loaded": true,
      "trained_at": "2026-05-07T01:23:45Z",
      "best_class": "IALSRecommender",
      "kid": "prod-2026-q2"
    }
  }
}
```

`status` is `degraded` if any recipe failed to load. A Kubernetes readiness probe should check HTTP 200 from `/health`.
