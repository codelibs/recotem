# Docker Deployment

Recotem ships as a single Docker image. `recotem train` and `recotem serve` are separate commands in the same image.

## docker-compose.example.yaml walkthrough

The repository includes `docker-compose.example.yaml`. Here is an annotated version:

```yaml
services:

  # ------------------------------------------------------------------
  # train: runs recotem train once on container startup.
  # In production, replace with a CronJob (K8s) or cron entry on the host.
  # ------------------------------------------------------------------
  train:
    image: ghcr.io/codelibs/recotem:2
    command: recotem train /recipes/my_recipe.yaml
    volumes:
      - ./recipes:/recipes:ro          # mount recipe YAMLs read-only
      - artifacts:/artifacts           # shared artifact volume
    environment:
      RECOTEM_SIGNING_KEYS: "${RECOTEM_SIGNING_KEYS}"
    restart: "no"                      # run once; use a cron wrapper to repeat

  # ------------------------------------------------------------------
  # serve: long-running FastAPI server. Watches the artifacts volume
  # and hot-swaps models when train writes a new artifact.
  # ------------------------------------------------------------------
  serve:
    image: ghcr.io/codelibs/recotem:2
    command: recotem serve --recipes /recipes/
    ports:
      - "8000:8000"
    volumes:
      - ./recipes:/recipes:ro
      - artifacts:/artifacts:ro        # serve only reads; ro is safe
    environment:
      RECOTEM_SIGNING_KEYS:      "${RECOTEM_SIGNING_KEYS}"
      RECOTEM_API_KEYS:          "${RECOTEM_API_KEYS}"
      RECOTEM_HOST:              "0.0.0.0"
      RECOTEM_PORT:              "8000"
      RECOTEM_WATCH_INTERVAL:    "30"    # poll every 30 s
      RECOTEM_LOG_FORMAT:        "json"
      RECOTEM_ALLOWED_HOSTS:     "localhost,myapp.example.com"
      RECOTEM_ALLOWED_ORIGINS:   "https://myapp.example.com"
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
    depends_on:
      train:
        condition: service_completed_successfully

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

**RECOTEM_HOST must be 0.0.0.0 inside Docker.** The default `127.0.0.1` only binds loopback and is unreachable from outside the container.

**Bind port only on localhost on the host if you put a reverse proxy in front:**

```yaml
ports:
  - "127.0.0.1:8000:8000"   # only accessible to host-local reverse proxy
```

## Running train on a schedule

Replace the one-shot `train` service with a cron wrapper or use the host cron to exec into the container:

```bash
# Host cron — runs inside existing serve container's shared volume
0 3 * * * docker compose -f /opt/recotem/docker-compose.yaml run --rm train
```

Or run the `train` image as a separate, throwaway container that shares the artifact volume:

```bash
docker run --rm \
  -v recotem_artifacts:/artifacts \
  -v /opt/recotem/recipes:/recipes:ro \
  -e RECOTEM_SIGNING_KEYS="${RECOTEM_SIGNING_KEYS}" \
  ghcr.io/codelibs/recotem:2 \
  recotem train /recipes/my_recipe.yaml
```

## Environment variables reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `RECOTEM_SIGNING_KEYS` | yes (train+serve) | — | `<kid>:<hex>,...` |
| `RECOTEM_API_KEYS` | yes (serve) | — | `<kid>:sha256:<hex>,...` |
| `RECOTEM_HOST` | no | `127.0.0.1` | Must be `0.0.0.0` inside Docker |
| `RECOTEM_PORT` | no | `8000` | |
| `RECOTEM_WATCH_INTERVAL` | no | `5` | Seconds between artifact polls |
| `RECOTEM_LOG_FORMAT` | no | `console`* | `json` recommended in containers |
| `RECOTEM_ALLOWED_HOSTS` | no | `127.0.0.1,localhost` | Comma-separated |
| `RECOTEM_ALLOWED_ORIGINS` | no | `""` (deny) | Comma-separated CORS origins |
| `RECOTEM_MAX_ARTIFACT_BYTES` | no | `2147483648` (2 GiB) | |
| `RECOTEM_DRAIN_SECONDS` | no | `30` | SIGTERM grace window |
| `RECOTEM_ENV` | no | `""` | Set to `development` to unlock `--insecure-no-auth` |

*Default switches to `json` automatically when stderr is not a TTY.

## Health check

```bash
curl http://localhost:8000/health
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
