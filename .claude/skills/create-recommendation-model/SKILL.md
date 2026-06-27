---
name: create-recommendation-model
description: >-
  Create, train, and verify a recotem recommendation model from BigQuery, CSV,
  Parquet, or SQL data.
---

# Create a recommendation model (recotem)

Produce a working recotem model from a data source and prove it serves
recommendations. The pipeline is source-agnostic:

**model interactions → choose source → write recipe → validate → estimate
volume → train → inspect → (optional) serve + curl**

`train` and `serve` communicate only through the signed artifact file, so each
half can be run and verified independently.

The data-source-specific parts (what to ask the user, the `source:` block, how
to shape `item_id`, and cost characteristics) live in `references/`. Read the
one matching the user's source in Step 1 — do not load all of them.

## What counts as an interaction

recotem (via irspack) trains **implicit-feedback** recommenders from a table of
interactions, each row a single positive signal:

| Column (`schema`) | Meaning | Required |
|---|---|---|
| `user_column` | who acted (e.g. `user_pseudo_id`, `customer_id`) | yes |
| `item_column` | what they acted on (page, product, video, article) | yes |
| `time_column` | when (used by time-based splits) | optional but recommended |

The *domain* varies — page views in access logs, line items in a purchase log,
song plays, clicks — but they all reduce to (user, item, [time]). Decide with
the user **what counts as a positive interaction**: e.g. `event_name =
'purchase'`, a rating `>= 4`, or any `page_view`. Each retained row is treated
as one positive; aggregate or dedup upstream/in-query if the raw data has many
repeats and you do not want them weighted by frequency (the `cleansing.dedup`
policy can collapse exact (user, item) repeats).

## Non-negotiables

- **Pick the recotem runtime: installed first, source fallback.** Prefer an
  installed `recotem` (the production-representative path). Fall back to running
  from a source checkout via `uv` when there is no installed package — and force
  the source path when you are intentionally verifying changes to recotem's own
  code:

  ```bash
  if command -v recotem >/dev/null 2>&1; then R="recotem"; else R="uv run recotem"; fi
  # when iterating on recotem itself: R="uv run recotem"
  ```

  Use `$R ...` for every command below. Never invoke bare `pip`/`python` in a
  `uv`-managed checkout — use `uv run python` for ad-hoc scripts.

- **Keep secrets and private identifiers out of anything committed.** GCP
  project IDs, dataset/DB names, DSNs, service-account keys, signing/API keys,
  internal URLs, and concrete user/item IDs are private. Put working recipes in
  a scratch directory (not the repo), keep credentials in env vars (the SQL
  source reads its DSN from an env var by design; a BigQuery `project` can use
  `${RECOTEM_RECIPE_GCP_PROJECT}`), and never paste these values into commit
  messages, PR descriptions, or version-controlled files.

- **Validate before you spend, and estimate volume before the real run.** For
  query sources (BigQuery/SQL), `validate` is a free dry run — always run it
  first. Keep the date window / row count small while iterating.

## Step 0 — Environment and working directory

**Ask the user where the recipe and model artifact should live** — the working
directory. Everything this skill writes (the `recipe.yaml`, the `.recotem`
artifact, and the serve `recipes/` dir) goes there. Default to a scratch
location outside the repo so private values never land in version control; let
the user override it.

```bash
WORKDIR="<the directory the user chose>"   # e.g. ~/recotem-work
mkdir -p "$WORKDIR"

# choose $R as above, then:
$R keygen --type signing
export RECOTEM_SIGNING_KEYS="<kid>:<hex64>"   # from the keygen env_entry line
```

Both `train` and `serve` require a signing key and fail closed without one. The
keys you generate here are throwaway dev keys — generate fresh keys for any real
deployment. Source-specific auth (BigQuery ADC, SQL DSN) is covered in the
reference for that source.

## Step 1 — Choose the data source and read its reference

Pick the source, then read the matching reference and gather the inputs it lists
**before** writing the recipe. The questions genuinely differ per source.

| Source | Reference | You will gather |
|---|---|---|
| BigQuery — GA4 export **or** a custom dataset | `references/bigquery.md` | project, table, date range, how `item_id` is encoded, ADC/IAM |
| CSV or Parquet file (local / S3 / GCS / Azure / HTTP) | `references/files.md` | path + scheme, column names, delimiter/sha256 |
| SQL database (Postgres / MySQL / SQLite) | `references/sql.md` | DSN env var, query, row volume |
| Custom source plugin | see `docs/plugin-authoring.md` | the plugin's own config fields |

## Step 2 — Write the recipe

Write the recipe to `$WORKDIR/my_reco.yaml`. The `source:` block comes from the
reference; the rest is common:

```yaml
name: my_reco                      # becomes the /v1/recipes/{name}:* verb
source:
  # << from references/<source>.md >>
schema:
  user_column: user_id
  item_column: item_id
  time_column: ts                  # omit if the source has no timestamp
cleansing:
  drop_null_ids: true              # drop rows with a null user/item id
  dedup: keep_last                 # collapse exact (user,item) repeats
  min_rows: 10
  min_users: 2
  min_items: 2
training:
  algorithms: [TopPop, IALS]       # Optuna picks the best; see project CLAUDE.md for the full list
  metric: ndcg
  cutoff: 10
  n_trials: 6
  split:
    scheme: time_user
    heldout_ratio: 0.3
    test_user_ratio: 1.0
    seed: 42
output:
  path: <WORKDIR>/my_reco.recotem  # a concrete absolute path; recipe files are NOT shell-expanded
  versioning: always_overwrite     # only always_overwrite | append_sha are valid
```

Recipe gotchas that bite the first time:

- `output.versioning` accepts **only** `always_overwrite` or `append_sha`
  (there is no `none`).
- **There is no recipe-level regex or derived-column feature.** `schema.*` are
  plain column names. Any extraction/derivation of `item_id` (e.g. pulling an ID
  out of a URL) must happen **in the source query** (BigQuery/SQL) or **upstream**
  when producing a CSV/Parquet file. The reference shows the idiom for the
  source.
- For query sources, `${...}` expansion is restricted to `RECOTEM_RECIPE_*` and
  never applied inside the query — use the source's native bind parameters, not
  string interpolation.

## Step 3 — Validate

```bash
$R validate $WORKDIR/my_reco.yaml
```

What `validate` checks depends on the source: BigQuery runs a free dry run (ADC
auth + query syntax + parameter types, no billing); SQL probes connectivity and
the statement; CSV/Parquet check that the path exists and is readable. Fix any
error here before training.

## Step 4 — Estimate volume before the real run

Right-size cost/memory before `train` actually pulls data. The mechanics are
source-specific (BigQuery scan bytes via a manual dry run; SQL/file row volume
and the `RECOTEM_MAX_SQL_ROWS` / download caps) — see the **Cost / volume**
section of the source's reference. Keep the window small while iterating.

## Step 5 — Train

```bash
$R train $WORKDIR/my_reco.yaml
```

**Watch the data shape, not just exit 0.** Collaborative filtering needs users
with **more than one interaction**. If almost every user has a single
interaction (common in raw web logs — one visit, one item), every trial scores 0
and training aborts with `zero_score` (exit 4). This is correct behavior, not a
bug. To get a trainable model, restrict to users with multiple distinct items
(in the query for BigQuery/SQL, or upstream for files):

```sql
WITH ev AS ( /* user_id, item_id, ts ... */ ),
     multi AS (
       SELECT user_id FROM ev WHERE item_id IS NOT NULL
       GROUP BY user_id HAVING COUNT(DISTINCT item_id) >= 2
     )
SELECT ev.* FROM ev JOIN multi USING (user_id) WHERE ev.item_id IS NOT NULL
```

On success the log reports `train_done` with `best_class`, `best_score`, and
`n_users`/`n_items` — sanity-check those counts.

## Step 6 — Inspect the artifact

```bash
$R inspect $WORKDIR/my_reco.recotem
```

Verifies the HMAC (`HMAC: OK`) and prints the header (`best_class`,
`best_params`, `best_score`, `data_stats`, versions) without deserializing the
payload.

## Step 7 (optional) — Serve and confirm with curl

Do this when the user wants to see the recommend API return results.

```bash
$R keygen --type api                      # note the plaintext AND the env_entry
export RECOTEM_API_KEYS="<kid>:sha256:<hex64>"
mkdir -p $WORKDIR/recipes
cp $WORKDIR/my_reco.yaml $WORKDIR/recipes/   # serve scans *.yaml
export RECOTEM_HOST=127.0.0.1 RECOTEM_PORT=8099
$R serve --recipes $WORKDIR/recipes &
```

The `--recipes` directory holds **recipe YAMLs**, not artifacts; serve reads each
recipe's `output.path` to locate its artifact. `:recommend` needs a `user_id`
seen during training; `:recommend-related` only needs a known `item_id`.

```bash
KEY="<plaintext from keygen --type api>"
BASE="http://127.0.0.1:8099"

curl -s "$BASE/v1/health"                  # expect {"status":"ok","total":1,"loaded":1}

curl -s -X POST "$BASE/v1/recipes/my_reco:recommend" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"user_id": "<known user>", "limit": 5}'

curl -s -X POST "$BASE/v1/recipes/my_reco:recommend-related" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"seed_items": ["<known item>"], "limit": 5}'
```

Behaviors that confirm correct wiring: a scored `items` array on `200`; unknown
user → `404 UNKNOWN_USER`; missing `X-API-Key` → `401 MISSING_API_KEY`. With
`RECOTEM_API_KEYS` unset, serve binds to loopback and skips auth. Stop with
`pkill -f "recotem serve"`.

## Step 8 — Hand off the recommend API usage

After the model is built, **always finish by printing a complete, copy-pasteable
recommend API reference for the user**, with the placeholders filled in from
what you actually used — recipe `name`, `$WORKDIR`, host/port, and a couple of
**real** `user_id` / `item_id` values from the training data so the user can
call it immediately. (Pull a few known IDs from the source if you do not already
have them.) This is the deliverable; do not end with just "training done".

Emit a block like this:

```markdown
### Recommend API — my_reco

Start the server:
    export RECOTEM_SIGNING_KEYS="<kid>:<hex64>"
    export RECOTEM_API_KEYS="<kid>:sha256:<hex64>"   # omit to bind loopback with no auth
    export RECOTEM_HOST=127.0.0.1 RECOTEM_PORT=8099
    <recotem> serve --recipes <WORKDIR>/recipes

Auth: send the API key plaintext in the `X-API-Key` header (none needed in
loopback no-auth mode). Base URL: http://127.0.0.1:8099

Health:
    curl -s http://127.0.0.1:8099/v1/health

User recommendations  (user_id must be one seen in training):
    curl -s -X POST http://127.0.0.1:8099/v1/recipes/<name>:recommend \
      -H "X-API-Key: <key>" -H "Content-Type: application/json" \
      -d '{"user_id": "<real known user>", "limit": 10, "exclude_items": []}'

Related items  (no user; seed with known item_id values):
    curl -s -X POST http://127.0.0.1:8099/v1/recipes/<name>:recommend-related \
      -H "X-API-Key: <key>" -H "Content-Type: application/json" \
      -d '{"seed_items": ["<real known item>"], "limit": 10}'

Response: {"request_id", "recipe", "model_version": "sha256:…",
           "items": [{"item_id", "score"}, …]}

Errors: 404 UNKNOWN_USER (user not in training) · 404 UNKNOWN_SEED_ITEMS ·
        401 MISSING_API_KEY / INVALID_API_KEY · 503 RECIPE_UNAVAILABLE
        (artifact not loaded). Stop the server: pkill -f "recotem serve".

Known IDs to try — users: <id>, <id>  ·  items: <id>, <id>
```

Substitute the real recipe name, paths, port, and IDs; keep private identifiers
in this on-screen handoff only (never commit them).

## Troubleshooting (exit codes and common failures)

| Symptom | Likely cause / fix |
|---|---|
| Exit 4 `zero_score` | Too few multi-interaction users — filter to users with ≥2 distinct items (Step 5). |
| Exit 2, `output.versioning ... pattern` | Use `always_overwrite` or `append_sha`. |
| Exit 3 `DataSourceError` on auth | Source credentials missing — see the source reference (BigQuery ADC / SQL DSN env var). |
| `Query parameter '@x' has type STRING ... differs from INT64` | YAML quoting — `30` is INT64, `"30"` is STRING. |
| Exit 3 `...is required for <Source>` | Install the extra: `uv sync --all-extras` (dev) or `pip install "recotem[<extra>]"`. |
| Serve health `degraded`, `:recommend` → 503 `RECIPE_UNAVAILABLE` | Artifact failed to load — check the serve log; ensure the signing key matches and the artifact deserializes. |

See the CLI exit-code table and algorithm list in the project `CLAUDE.md`, and
`docs/data-sources/` for the full per-source reference.
