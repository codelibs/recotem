# Operations

## Signing key rotation

Signing keys are configured in `RECOTEM_SIGNING_KEYS` as a comma-separated list of `<kid>:<hex32>` entries. The server verifies against any entry; `recotem train` always signs with the **first** entry (the active key).

This multi-kid pattern enables zero-downtime rotation:

### Step-by-step rotation

1. **Generate a new key.**

   ```bash
   recotem keygen
   # kid:        prod-2026-q3
   # plaintext:  <not used for signing>
   # hash:       sha256:<hex>   <-- this is the signing key hash, shown for reference only
   # signing hex: <64-hex>      <-- use this in RECOTEM_SIGNING_KEYS
   ```

   `recotem keygen` prints a `(kid, plaintext, hash)` triple. For signing keys, use the raw hex output, not the `sha256:` prefixed form (that prefix is for API keys). See your key generation output carefully.

2. **Add the new kid as the first entry, keeping the old one.**

   ```bash
   # Before:
   RECOTEM_SIGNING_KEYS="prod-2026-q2:aabbcc..."

   # After (new key first):
   RECOTEM_SIGNING_KEYS="prod-2026-q3:ddeeff...,prod-2026-q2:aabbcc..."
   ```

   Restart (or reload) `recotem serve` with the updated env. The server now accepts artifacts signed by either kid.

3. **Retrain all models.**

   Run `recotem train` for each recipe. Each new artifact is signed with `prod-2026-q3` (the first entry). The server hot-swaps each model as the new artifact appears. Old artifacts signed with `prod-2026-q2` continue to serve until each recipe is retrained.

4. **Remove the old kid.**

   Once all recipes have been retrained and hot-swapped, remove the old entry:

   ```bash
   RECOTEM_SIGNING_KEYS="prod-2026-q3:ddeeff..."
   ```

   Restart `recotem serve`. Any artifact still signed with the old kid will fail to load and appear as `loaded: false` in `/health`. Retrain those recipes.

5. **Verify.**

   ```bash
   curl http://localhost:8080/health | jq '.recipes | to_entries[] | select(.value.loaded == false)'
   ```

   Empty output means all recipes loaded successfully under the new key.

### Key fingerprint

At startup, `recotem serve` logs a `security.posture` event that includes `sha256(key)[:8]` per kid. You can confirm the correct key is active without ever exposing the key itself:

```json
{"event": "security.posture", "signing_keys": [{"kid": "prod-2026-q3", "fingerprint": "ddeeff00"}], ...}
```

---

## API key rotation

API keys live in `RECOTEM_API_KEYS` as `<kid>:sha256:<hex64>` entries. Rotation is additive: add the new entry, update clients, then remove the old entry.

1. **Generate a new key.**

   ```bash
   recotem keygen --type api --kid client-a-v2
   # kid=client-a-v2
   # plaintext=<43-char base64url — share with the client>
   # hash=sha256:<64-hex — put this in RECOTEM_API_KEYS>
   # env_entry=RECOTEM_API_KEYS=client-a-v2:sha256:<64-hex>
   ```

   `--type api` is required — without it `recotem keygen` defaults to
   `--type signing` and would emit the wrong key format.

2. **Add the new entry alongside the old one.**

   ```bash
   # Before:
   RECOTEM_API_KEYS="client-a:sha256:oldhhh..."

   # After:
   RECOTEM_API_KEYS="client-a:sha256:oldhhh...,client-a-v2:sha256:newhhh..."
   ```

   Restart `recotem serve`. Both keys are valid simultaneously. Share the new plaintext with the client.

3. **Client switches to the new key.**

4. **Remove the old entry.**

   ```bash
   RECOTEM_API_KEYS="client-a-v2:sha256:newhhh..."
   ```

   Restart `recotem serve`.

The plaintext is shown only once at generation time. If lost, generate a new key — there is no recovery.

---

## Recovery from a corrupt artifact

If an artifact is corrupt (truncated write, disk error, storage-side corruption), `recotem serve` logs an error and marks the recipe as `loaded: false`:

```json
{"event": "artifact_load_error", "recipe": "my_recipe", "error": "magic bytes mismatch", "kid": null}
```

The server continues running and returns 503 for that recipe's `/predict/{name}` endpoint.

**Recovery steps:**

1. **Inspect the artifact** (safe even on corrupt files — HMAC and size checks reject before deserialization):

   ```bash
   recotem inspect ./artifacts/my_recipe.recotem
   # exit 5: ArtifactError: magic bytes mismatch
   ```

2. **Retrain.**

   ```bash
   recotem train ./recipes/my_recipe.yaml
   ```

   This writes a fresh, signed artifact. The server detects the new file at the next poll and hot-swaps.

3. **Verify.**

   ```bash
   curl http://localhost:8080/health | jq '.recipes.my_recipe'
   # {"loaded": true, "best_class": "IALSRecommender", ...}
   ```

If the artifact was written with `versioning: append_sha`, the old corrupt file is still present with its sha-suffix name. You can delete it after confirming the new artifact loaded:

```bash
ls ./artifacts/
# my_recipe.recotem           <- pointer file (points to current)
# my_recipe.abc12345.recotem  <- old corrupt file (safe to delete)
# my_recipe.def67890.recotem  <- new good file (current)
rm ./artifacts/my_recipe.abc12345.recotem
```

---

## Sizing `recotem serve` memory

Each model replica holds every loaded model in RAM. Plan accordingly.

| Factor | Impact |
|--------|--------|
| `RECOTEM_MAX_ARTIFACT_BYTES` | Hard cap per artifact (default 2 GiB). Reduce this if you have many small models. |
| Number of recipes | Each recipe loads one model. 10 recipes × 500 MiB = 5 GiB baseline. |
| Number of replicas | Each replica is independent. 2 replicas = 2× memory. |
| Item metadata | DataFrame in-memory per recipe. Size ≈ rows × columns × 8 bytes. |

Rough formula:

```
RAM per pod ≈ (avg_artifact_size_GiB × n_recipes) + (avg_metadata_size_GiB × n_recipes) + 1 GiB OS overhead
```

For large models (IALS with many components, large item sets), use `recotem inspect` to read `data_stats` and `best_params` from the header before committing to a host size.

`recotem serve` is sized for ≤ 100 recipes per process. Beyond that, shard recipes across multiple `serve` processes (separate `--recipes` directories, separate ports, load-balance at the proxy layer).

---

## SLOs

Recotem does not enforce SLOs internally. Recommended baseline targets for production:

| Metric | Target |
|--------|--------|
| `/predict/{name}` p99 latency | < 50 ms (pure recommender, no metadata join) |
| `/health` p99 latency | < 5 ms |
| Availability (per recipe) | Measure via `recotem_model_loaded{recipe}` Prometheus gauge |
| Artifact hot-swap time | ≤ `RECOTEM_WATCH_INTERVAL` + model load time |
| Train-to-serve lag | Schedule train; serve detects in ≤ `RECOTEM_WATCH_INTERVAL` seconds |

Enable Prometheus metrics:

```bash
pip install "recotem[metrics]"
```

The `/metrics` endpoint is opt-in and off by default. Set `RECOTEM_METRICS_ENABLED=true` to activate.

Available metrics:

| Metric | Type | Labels |
|--------|------|--------|
| `recotem_predict_total` | Counter | `recipe`, `status` |
| `recotem_predict_latency_seconds` | Histogram | `recipe` |
| `recotem_model_loaded` | Gauge | `recipe` |
| `recotem_artifact_load_failures_total` | Counter | `recipe` |
| `recotem_active_recipes` | Gauge | — |
| `recotem_swap_total` | Counter | `recipe`, `result` |

---

## Troubleshooting

### `recotem serve` starts but recipe is `loaded: false`

```bash
curl http://localhost:8080/health | jq '.recipes'
```

```json
{"my_recipe": {"loaded": false, "error": "signature mismatch"}}
```

Causes and fixes:

| Error | Cause | Fix |
|-------|-------|-----|
| `signature mismatch` | Artifact signed with a key not in `RECOTEM_SIGNING_KEYS` | Add the signing kid used at train time |
| `unknown kid: prod-old` | The kid in the artifact is not in the server's key list | Add that kid or retrain with a known kid |
| `magic bytes mismatch` | Corrupt or truncated artifact | Retrain |
| `payload exceeds max bytes` | Artifact larger than `RECOTEM_MAX_ARTIFACT_BYTES` | Increase the limit or reduce model size |
| `header JSON too large` | Malformed artifact | Retrain |

### `recotem train` exits 3 (DataSourceError)

For BigQuery: run `gcloud auth application-default print-access-token` to confirm ADC is working. Check the exact error in the JSON stderr line:

```bash
recotem train recipe.yaml 2>&1 | grep '"event":"train_error"' | jq .
```

### `recotem train` exits 4 with `min_data_violation`

The cleaned dataset fell below a threshold. The JSON error line includes observed counts:

```json
{"event": "train_error", "code": "min_data_violation", "n_rows": 842, "min_rows": 1000, ...}
```

Lower `cleansing.min_rows` in the recipe or investigate why fewer rows arrived from the source.

### `recotem train` exits 4 with `all_scores_zero`

All Optuna trials scored 0.0. Common causes:

- The split produced an empty test set (too few users or interactions). Try `split.scheme: random` or lower `split.heldout_ratio`.
- The data after cleansing has too few items for the cutoff. Lower `training.cutoff`.

### 401 on `/predict`

- Trailing or leading whitespace in the `X-API-Key` header is treated as part of the key and will not match. Trim client-side.
- Confirm the hash in `RECOTEM_API_KEYS` was produced by `recotem keygen --type api` for the plaintext you are sending. The wire prefix is `sha256:` but the digest is **scrypt** (`hashlib.scrypt(plaintext, salt=b"recotem.api-key.v1", n=2, r=8, p=1, dklen=32)`). A plain `sha256(plaintext)` will not match.

### 503 on `/predict/{name}`

The recipe is unhealthy (`loaded: false`). See `/health` for the error. Usually a signing mismatch or corrupt artifact.

### 404 on `/predict/{name}`

The `user_id` in the request was not present in training data. This is expected for new users. Handle it in your application layer (fall back to popularity-based recommendations, for example).

### Watcher does not pick up new artifact

- Check `RECOTEM_WATCH_INTERVAL`. Default is 5 s.
- For object stores, check that the IAM role on the serve process has `GetObject` (S3) or `storage.objects.get` (GCS) on the artifact bucket.
- Run `recotem inspect` on the artifact path to confirm it is valid and signed with a kid the server knows.

### Log redaction

All log events are processed by the redaction processor before output. If you see `[REDACTED]` in a log line where you expected a value, the field name matched the redaction pattern (see [security.md](security.md#log-redaction)). This is intentional.
