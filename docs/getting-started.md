# Getting Started

Train a recommender from a small public CSV and serve it as a REST API in
under 10 minutes. Two paths: Docker Compose (no Python install needed) and
pip (everything in your venv).

## Prerequisites

- Either Docker (with the Compose plugin) **or** Python 3.12+
- ~50 MB of disk
- Network access to fetch a small CSV from `raw.githubusercontent.com`

## Path A — Docker Compose (recommended)

The repo ships a `compose.yaml` and an `examples/tutorial-purchase-log/`
recipe. From the repo root:

### 1. Generate keys

```bash
docker run --rm ghcr.io/codelibs/recotem:latest keygen --type signing --kid dev
# kid=dev
# plaintext=<64-char hex>
# fingerprint=<8-char hex>  # matches /security.posture log; NOT for config
# env_entry=RECOTEM_SIGNING_KEYS=dev:<plaintext>

docker run --rm ghcr.io/codelibs/recotem:latest keygen --type api --kid dev
# kid=dev
# plaintext=<43-char base64url>          ← keep this; clients pass it as X-API-Key
# hash=sha256:<64-char hex>
# env_entry=RECOTEM_API_KEYS=dev:sha256:<hex>
```

Export both into your shell. Compose passes these straight through to the
container; the names match what the app actually reads.

```bash
export RECOTEM_SIGNING_KEYS="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS="dev:sha256:<hash-hex-from-api>"
export RECOTEM_API_PLAINTEXT="<plaintext-from-api>"      # used in step 4 (curl)
```

### 2. Train

```bash
docker compose run --rm train
```

What happens: the train container fetches `purchase_log.csv` over HTTPS,
verifies its sha256, runs Optuna with IALS + TopPop, and writes a signed
artifact to the `artifacts` volume.

Expected last log line (JSON):

```json
{"event":"train_done","name":"purchase_log","exit_code":0,
 "artifact":"./artifacts/purchase_log....recotem","best_class":"IALSRecommender",...}
```

### 3. Serve

```bash
docker compose up -d serve
docker compose logs --no-color -n 20 serve
```

Health check:

```bash
curl http://localhost:8080/v1/health
# {"status":"ok","total":1,"loaded":1}
```

### 4. Recommend

```bash
curl -sX POST http://localhost:8080/v1/recipes/purchase_log:recommend \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "1", "limit": 5}' | jq .
```

Expected (the exact items / scores depend on training):

```json
{
  "request_id": "req_01HZX...",
  "recipe": "purchase_log",
  "model_version": "sha256:abc...",
  "items": [
    {"item_id": "...", "score": 0.91},
    ...
  ]
}
```

### 4b. Recommend related items

`:recommend-related` returns items similar to one or more seed items —
useful for "related products" widgets or content carousels:

```bash
curl -sX POST http://localhost:8080/v1/recipes/purchase_log:recommend-related \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"seed_items": ["<item_id>"], "limit": 5}' | jq .
```

### 5. Tear down

```bash
docker compose down -v
```

## Path B — pip install

```bash
pip install recotem            # or: uv pip install recotem
```

Verify the install resolves the entry-point:

```bash
recotem --help                 # should list train, serve, inspect, validate, schema, keygen
recotem validate examples/tutorial-purchase-log/recipe.yaml
```

`validate` parses the recipe, instantiates the data source, and runs its
optional `probe()` (HTTP HEAD for the tutorial CSV) — a fast way to catch
network or recipe problems before launching `train`.

### 1. Generate keys

```bash
recotem keygen --type signing --kid dev
recotem keygen --type api     --kid dev
```

Export into your shell (mirrors Path A):

```bash
export RECOTEM_SIGNING_KEYS="dev:<plaintext-hex-from-signing>"
export RECOTEM_API_KEYS="dev:sha256:<hash-hex-from-api>"
export RECOTEM_API_PLAINTEXT="<plaintext-from-api>"
```

### 2. Train

The tutorial recipe writes to `./artifacts/...` (CWD-relative). Run from
the repo root:

```bash
mkdir -p artifacts
recotem train examples/tutorial-purchase-log/recipe.yaml
```

### 3. Serve

```bash
recotem serve --recipes examples/tutorial-purchase-log/
```

### 4. Recommend

```bash
curl -sX POST http://127.0.0.1:8080/v1/recipes/purchase_log:recommend \
  -H "X-API-Key: $RECOTEM_API_PLAINTEXT" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "1", "limit": 5}' | jq .
```

## What just happened

- `recotem train` parsed the recipe, fetched the CSV over HTTPS, compared its
  sha256 against the recipe pin, ran an Optuna hyperparameter search with
  IALS and TopPop, and wrote a binary artifact signed with your signing key.
- `recotem serve` watched the artifact directory, picked up the new file,
  HMAC-verified it against the same signing key, and registered the
  `/v1/recipes/purchase_log:recommend` endpoint.
- The recommend request was authenticated by the API key allow-list and
  scored using the trained model.

## Train from SQLite (zero cloud, zero Docker)

The `sql` source needs only a database URL. SQLite is the smallest example:

```bash
# Seed a tiny SQLite DB.
uv run python examples/sql-sqlite/seed.py

# Point Recotem at it.
export RECOTEM_RECIPE_DB_DSN="sqlite:///$(pwd)/examples/sql-sqlite/events.db"
export $(uv run recotem keygen --type signing | grep '^env_entry=' | sed 's/^env_entry=//')

# Train. Artifact lands in examples/sql-sqlite/artifacts/.
mkdir -p artifacts
uv run recotem train examples/sql-sqlite/recipe.yaml
```

See `docs/data-sources/sql.md` for PostgreSQL / MySQL recipes.

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `RecipeError: 'source.path' uses a network scheme … requires a 'sha256' integrity pin` | Recipe edited; sha256 removed | Re-add the `sha256:` line in the recipe |
| `DataSourceError: sha256 mismatch` | Upstream rotated the file | Re-compute with `curl -sL <url> \| shasum -a 256` and update the recipe |
| `DataSourceError: HTTP 404 fetching …` | Upstream `source.path` URL moved or was removed | Verify the URL resolves in a browser; update the recipe `source.path` (and its `sha256:` pin) to the current location or a stable mirror |
| `ArtifactError: RECOTEM_SIGNING_KEYS not set` | Step 1 not exported | Re-run the export and try again |
| `401 Unauthorized` on `:recommend` | Wrong API key plaintext | Use the `plaintext` line from `keygen --type api`, not the `hash` |
| `503 recipe_unavailable` on `:recommend` immediately after train | Watcher has not polled yet | Wait up to `RECOTEM_WATCH_INTERVAL` seconds (default 5; tutorial sets 10). Check `/v1/health`. |
| Path B: artifact written to wrong directory | Recipe `output.path` is CWD-relative | Run `recotem train` from the repo root (or edit `output.path` to an absolute path). |
| `recotem: command not found` after pip install | `pip` installed to a venv not on `PATH` | Use `python -m recotem ...`, or activate the venv (`uv run recotem ...`). |

## Next steps

- [docs/recipe-reference.md](recipe-reference.md) — every recipe field
- [docs/data-sources/csv.md](data-sources/csv.md) — full CSV/Parquet documentation including schemes
- [docs/deployment/docker.md](deployment/docker.md) — production Docker patterns
- [docs/deployment/k8s.md](deployment/k8s.md) — Helm chart and CronJob
- [docs/security.md](security.md) — threat model and operator responsibilities
- [docs/operations.md](operations.md) — key rotation, recovery, sizing, troubleshooting
