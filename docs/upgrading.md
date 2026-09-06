# Upgrading

Version-to-version upgrade paths, what breaks, and what to do about it. Per-release
change lists live in the [GitHub Releases](https://github.com/codelibs/recotem/releases)
page; this page carries only the parts that require action.

## 2.0.0 → 2.1.0

### IALS artifacts must be retrained

2.1.0 moves irspack from 0.4.2 to 0.5.2. irspack 0.5.0 changed
`IALSModelConfig`'s pickled state from a 7-tuple to a 10-tuple, so **IALS
artifacts trained on 2.0.0 cannot be loaded by 2.1.0.** Every other algorithm
carries over unchanged: of the six algorithms trained under 2.0.0 and loaded
under 2.1.0, five (`CosineKNN`, `TopPop`, `RP3beta`, `DenseSLIM`,
`TruncatedSVD`) load and serve bit-identical scores; only IALS is refused. The
refusal is correct rather than over-cautious — bypassing the guard and
deserializing anyway reproduces the real `TypeError: __setstate__():
incompatible function arguments`. See
[irspack 0.4 → 0.5](#irspack-04--05) below for the mechanism and the full
verified-pair table.

This affects more deployments than it might appear to. The shipped tutorial
recipe searches `algorithms: [IALS, TopPop]` and normally settles on IALS, so a
deployment that started from the tutorial holds an IALS artifact without anyone
having chosen IALS explicitly. The winning algorithm is a search outcome, not a
recipe setting — check each artifact with `recotem inspect` rather than reading
it off the recipe.

**"Bit-identical" is a claim about loading an existing artifact, not about
retraining.** Those five algorithms load a 2.0.0-trained artifact under 2.1.0
and serve the same scores. Retraining the same recipe on 2.1.0 and diffing
against the 2.0.0 model — the obvious way to convince yourself the upgrade is
safe — is a different comparison, and it will show small differences: measured
at roughly 8.5e-09 on `DenseSLIM` and 3e-15 on `TruncatedSVD`, with the item
ordering unchanged. That is float drift from a dependency range that admits
more than one build, not a break. Validate the upgrade by loading and serving
the artifacts you already have.

### The 2.0.0 container image never started

The published `ghcr.io/codelibs/recotem:2.0.0` cannot start on either
architecture: its console script carries the build stage's shebang,
`#!/build/.venv/bin/python`, a path that does not exist in the final image, so
the entrypoint fails with `exec /opt/venv/bin/recotem: no such file or
directory`. Verified by digest on both `linux/amd64` and `linux/arm64`. If you
are on the 2.0.0 image, you are not running it. The image published for 2.1.0
starts normally.

### What a skewed artifact looks like

`serve` starts normally — it does not crash. The IALS recipe is registered with
`"loaded": false` and an error naming the recipe, both irspack versions, and the
remedy. Requests to that recipe return `503` (`RECIPE_UNAVAILABLE`); every other
recipe keeps serving.
`recotem_artifact_load_failures_total{reason="version_skew"}` increments, and
`/v1/health/details` reports `"status": "degraded"`.

### On Kubernetes, the blast radius depends on your probes

`/v1/health` is count-based: it returns `degraded` with HTTP **503** whenever
`loaded < total` — that is, whenever *any* recipe failed to load.

**No probe in the 2.1.0 chart reads it.** Startup and readiness both read
`/v1/health/ready`, which is `200` while at least one recipe is loaded, so a
refused IALS artifact alongside healthy recipes lets the pod start, join the
Service, and serve everything else; only the skewed recipe returns `503`. If the
skewed recipe is the *only* recipe, nothing loads, `/v1/health/ready` stays
`503`, and the startupProbe restarts the container (12 failures × 10 s = a 120 s
window) until you retrain.

**If your own manifests point any probe at `/v1/health`, the blast radius is the
whole pod instead.** A failing startupProbe restarts the container rather than
withholding traffic, so on the count-based endpoint one refused artifact keeps
every new pod from ever starting, while the recipes that would have served fine
never receive traffic.

**If your own manifests were copied from the 2.0.0 chart, all three of your
probes are still on `/v1/health`.** Move all three before you upgrade — startup
and readiness onto `/v1/health/ready`, liveness onto `/v1/health/live`, as the
chart, `examples/k8s/` and [deployment/k8s.md](deployment/k8s.md) all do — or
one refused artifact will take every replica out of the Service and then
CrashLoop them.

**That is the picture at startup only. A hot-swap fails silently instead.** When
a skewed artifact lands in an *already-running* server, the previously loaded
model stays in memory — the watcher annotates the load error onto the registry
entry without clearing its `loaded` flag — so the count-based `/v1/health` stays
**200** and no probe fails. Nothing restarts the pod. Only
`/v1/health/details`, which reads the error strings rather than the count,
reports `degraded`, and
`recotem_artifact_load_failures_total{reason="version_skew"}` increments. The
fleet quietly keeps serving the *old* model until the next involuntary restart —
a node drain, an eviction, a scale-up — turns it into the startup case above,
potentially long after the deploy that caused it. Alert on that counter and
scrape `/v1/health/details`; a green `/v1/health` is not evidence the swap
worked. [operations.md](operations.md) calls this "degraded now, down later".

### An `az://` path that embeds `user:pass@` now fails to load

2.1.0 changed the embedded-credential rule for the three Azure protocol
aliases, in **both** directions. Measured through the real loader on a recipe
whose path is the URI under test — "addressing" is the bare
`container@account` form, "userinfo" is a real `user:pass@` pair:

| scheme  | form       | 2.0.0    | 2.1.0    |
|---------|------------|----------|----------|
| `az`    | addressing | accepted | accepted |
| `az`    | userinfo   | **accepted** | **rejected** |
| `abfs`  | addressing | **rejected** | **accepted** |
| `abfs`  | userinfo   | rejected | rejected |
| `abfss` | addressing | **rejected** | **accepted** |
| `abfss` | userinfo   | rejected | rejected |

`s3://`, `http(s)://`, `ftp(s)://` and `gs://project@bucket/key` are unchanged.

Only the second row requires action. At 2.0.0 `az` was simply missing from the
userinfo reject-list, so the check was skipped for that alias entirely: an
`az://` URI that really did carry a secret loaded without complaint. It is now
refused, on `source.path`, `item_metadata.path`, `features.*.source.path` and
`output.path` alike, with:

```
'source.path' contains embedded credentials in the URI. Use environment-based
authentication instead.
```

**Before upgrading, grep your recipes for `az://` paths containing a `:` before
the `@`.** Move the secret out of the URI: `AZURE_STORAGE_ACCOUNT_NAME` /
`AZURE_STORAGE_ACCOUNT_KEY`, a connection string, or a managed identity.

The other two changed rows are a fix and need nothing done. All three aliases
resolve to one adlfs filesystem and all three accept
`<container>@<account>.dfs.core.windows.net` (`.blob.` for Blob storage) — the
form Azure's own documentation uses, where the `@` separates the container from
the storage account rather than carrying a secret. 2.0.0 refused that form on
`abfs` and `abfss`, blaming the operator for a credential they had not written.

### Upgrade procedure

1. `recotem inspect` every artifact and note which report
   `"best_class": "IALSRecommender"` — only those need work.
2. Upgrade the **train** side first and retrain every IALS recipe on 2.1.0.
3. Wait for the new artifacts to land in the artifact store.
4. Upgrade the **serve** side.
5. Confirm `/v1/health/details` reports `"status": "ok"`.

The full runbook, including the zero-downtime caveat that this upgrade breaks,
is [operations.md](operations.md#irspack-version-skew).

If you upgrade serve first — the default rolling-deploy order — the old IALS
artifact is still on disk, so that recipe comes up `loaded: false` and returns
503 until a 2.1.0-trained artifact replaces it. Non-IALS recipes are unaffected
in themselves. This is a clean, visible outage rather than corruption, but it is
what a rolling deploy does by default, so plan around it.

**Do not reach for `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1` here.** It only
downgrades the refusal to a warning and lets the payload reach the
deserializer; the load then fails anyway with the bare `TypeError` the guard
exists to replace. It converts an actionable error into an unattributable one
and buys nothing. The flag is for algorithms that are merely *unverified*, not
for the known IALS break.

### Rollback

Roll serve and artifacts back together. Once a recipe has been retrained on
2.1.0, a 2.0.0 serve cannot load its IALS artifact either — the break is
bidirectional. Keep the pre-upgrade artifacts until the upgrade is confirmed;
the default `versioning: append_sha` plus its pointer file makes this natural —
repoint, do not delete. **A recipe using the new `features:` block cannot be
rolled back at all** and must be retrained without the block to run on 2.0.0.

### Unchanged by this upgrade

Signing keys and the key-rotation procedure; the artifact container itself
(magic bytes, `FORMAT_VERSION` 1, and the header layout); and every existing
recipe except one shape — an `az://` path that embeds `user:pass@`, covered
above. Every recipe's `recipe_hash` does change, but nothing gates on it.

One thing in that area *did* change: a malformed `RECOTEM_SIGNING_KEYS` now
exits **8** (`_EXIT_CONFIG`) where 2.0.0 exited **5** (`_EXIT_ARTIFACT`), on
`train`, `serve` and `inspect` alike. The container format is untouched and no
artifact needs anything done to it — but supervisor, CronJob or alerting logic
that branches on exit 5 to mean "the artifact is corrupt, retrain it" will stop
firing for an environment-variable typo and must learn exit 8.

## irspack 0.4 → 0.5

irspack 0.5.0 changed `IALSModelConfig`'s pickled state from a 7-tuple to a
10-tuple (the three new fields back feature-aware iALS). Its `__setstate__` is
a strict-arity binding, so **IALS artifacts trained with irspack 0.4.x cannot be
loaded under 0.5.x**. This is an upstream format change that irspack's own
changelog does not mention; Recotem cannot migrate such artifacts in place,
because the missing fields are internal C++ state that only a retrain produces
correctly.

- **Action required:** retrain and redeploy every recipe whose `best_class` is
  `IALSRecommender` (the known break). `BPRFMRecommender` is refused too, for a
  different reason: it is **unproven**, not known-broken. Its absence from the
  verified table is what the guard acts on — it refuses the unproven rather
  than risk loading a model that serves subtly wrong scores. Verifying it needs
  a second version axis the header does not record: a BPRFM payload embeds a
  LightFM object, so `(best_class, irspack_mm, running_mm)` does not describe
  the pair completely.
- The break is **bidirectional**: 0.5.x-trained IALS artifacts also fail to load
  on 0.4.x. Upgrade `train` and `serve` together — the upgrade cannot be staged
  serve-first, and serve cannot be rolled back to 0.4.x once artifacts have been
  retrained on 0.5.x.
- **Verified compatible across 0.4 ↔ 0.5, in both directions:** `CosineKNN`,
  `TopPop`, `RP3beta`, `DenseSLIM`, and `TruncatedSVD`. These artifacts load
  unchanged and need no retrain. "Verified" here means an artifact trained under
  one version was loaded under the other, with irspack as the only variable, and
  the recommendation scores compared bit-exact.
- **Every future irspack minor starts out refused.** The guard consults a table
  of verified pairs, so a later 0.5 → 0.6 upgrade will refuse artifacts for
  *all* algorithms — including the five above — until that transition is
  verified and its rows are added. Patch upgrades within a minor (e.g.
  0.5.0 → 0.5.3) are unaffected: matching major.minor short-circuits before the
  table is consulted.
- Artifacts that skew are refused with an actionable error rather than a raw
  `TypeError`. Runbook: [operations.md](operations.md#irspack-version-skew).
- **Escape hatch.** `RECOTEM_ALLOW_IRSPACK_VERSION_SKEW=1` downgrades the
  refusal to an `irspack_version_skew_allowed` warning and lets the payload
  reach the deserializer. It does not make an incompatible payload loadable — a
  genuinely broken artifact then fails with the bare `TypeError` the guard
  exists to replace. It is for operators who know their artifact is unaffected
  (e.g. an algorithm we simply have not verified yet), not a way to skip a
  needed retrain.

## 1.x → 2.x

There is no automated migration. Recotem 2.x shares the name and the
recommendation domain with 1.x but is an entirely new system:

1. **Re-train, don't migrate models.** 1.x model state is incompatible with the
   2.x signed-artifact format. Define recipes and run `recotem train`.
2. **Drop the database and message broker.** 2.x is stateless; the only durable
   state is the signed artifact file.
3. **Update API clients** from `/predict/{name}` to
   `POST /v1/recipes/{name}:recommend`.
4. **Generate keys.** Run `recotem keygen --type signing` (and `--type api` for
   serve auth) and set `RECOTEM_SIGNING_KEYS` / `RECOTEM_API_KEYS`.

See [getting-started.md](getting-started.md) for the full walkthrough.
