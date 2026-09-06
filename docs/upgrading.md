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
`loaded < total`. That covers the version-skew case this page is about — a
recipe whose *artifact* will not load is counted in `total`, so the endpoint
fires.

It is **not** the same as "any recipe failed". A recipe file that cannot be
parsed at all is *skipped* rather than counted: it is excluded from both
`total` and `loaded` and reported under a separate `skipped` count, so
`/v1/health` stays **200 `ok`** and `/v1/health/details` stays `ok` too. Both
failures leave that recipe's verbs unavailable, but only the artifact one is
visible in the counts. Measured, one loaded recipe alongside one broken one:

```
artifact missing   /v1/health 503 degraded   {"total":2,"loaded":1}
YAML unparseable   /v1/health 200 ok         {"total":1,"loaded":1,"skipped":1}
```

The `skipped` field is the signal in the second case — see
[Unparseable recipe files](operations.md#unparseable-recipe-files), which also
explains why the obvious alert (`recotem_model_loaded == 0`) fires on both.

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

### Azure URIs changed in both directions

2.1.0 rewrote how `source.path` and `item_metadata.path` treat an `@` in an
Azure URI. Both halves are user-visible against 2.0.0, and one of them will
stop a recipe that used to load. Measured through `load_recipe` at `v2.0.0`
and at 2.1.0:

| scheme | form | 2.0.0 | 2.1.0 |
|---|---|---|---|
| `abfss://` | `container@account…` (addressing) | rejected | **accepted** |
| `abfs://` | `container@account…` (addressing) | rejected | **accepted** |
| `az://` | `container@account…` (addressing) | accepted | accepted |
| `abfss://` | `user:pass@…` (credentials) | rejected | rejected |
| `abfs://` | `user:pass@…` (credentials) | rejected | rejected |
| `az://` | `user:pass@…` (credentials) | **accepted** | **rejected** |
| `s3://` | `user:pass@…` (credentials) | rejected | rejected |

**The breaking row is `az://` with a real `user:pass@` pair.** `az` was absent
from the credentials check at 2.0.0, so such a URI loaded silently. It now
exits **2** (`RecipeError`, category `security`) with `'source.path' contains
embedded credentials in the URI. Use environment-based authentication
instead.`

Grep your recipes for an `az://` path with a colon before the `@`. **If you
find one, it needs two separate things, and the second is easy to skip.**

1. **Fix the recipe.** Move the secret into the environment — the Azure fsspec
   backends read credentials from `AZURE_STORAGE_*` or a connection string.
   That is what stops the exit 2.
2. **Treat that credential as disclosed: rotate the storage account key, and
   purge or rotate the log archives that may hold it.** Under 2.0.0 the URI was
   written to the logs *in the clear*, on every run. Measured on 2.0.0 with a
   marked secret, one `recotem train` emitted it **four times** — once at INFO
   in the source-fetch event, and again inside the error text. The redaction
   helper that strips `user:pass@` from logged URLs only covered
   `http`/`https`/`ftp`/`ftps`, and the structlog DSN scrubber behind it did not
   list `az` either. The generic high-entropy scrubbers are shape-based, so
   whether a given key was caught depended on the key: across ten secrets that
   were not chosen to be catchable, seven went through — including every
   human-chosen password, and about half of the genuine random account keys,
   because standard base64's `+` and `/` break the 43-character run the pattern
   looks for.

   Moving the secret fixes the recipe going forward. It does nothing about logs
   already shipped to an aggregator, so do not stop at step 1.

Under 2.1.0 the same recipe never reaches a log line — it is refused at load,
and the marked secret appears **zero** times in the output.

This applies only if you actually had such a recipe. If your `az://` paths carry
no colon before the `@`, they are the addressing form, nothing was logged, and
there is nothing to rotate.

The other two changed rows are a fix, and need no action: the canonical
`container@account.dfs.core.windows.net` form that Azure's own documentation
uses was being refused on `abfs://` / `abfss://` as if it were a credential.
If you worked around that by rewriting those paths, you can now write them the
documented way. The rule 2.1.0 applies to all three Azure aliases is: a bare
`container@account` is addressing and is accepted; a real `user:pass@` pair is
refused.

### `split.scheme: random` with a `time_column` now splits differently

This one needs no action, but it will move a number you may be watching.

Under 2.0.0 the pipeline forwarded `schema.time_column` to the splitter for
*any* recipe that declared one, and irspack switches to a per-user **recency**
holdout the moment it receives a time column. So a recipe asking for `random`
while also declaring a `time_column` silently got a `time_user` split. 2.1.0
forces `time_column` to `None` under `random`, which is what the field is
documented to do.

Measured, with irspack held constant so the difference is recotem's and not
irspack's — the within-user time rank of the held-out interactions, where 1.0
is the user's most recent:

| what the version passes to irspack | mean time rank | share in the user's last 20% |
|---|---|---|
| `time_column="ts"` — 2.0.0 | 0.925 | **100%** |
| `time_column=None` — 2.1.0 | 0.51 – 0.54 | 18–21% (i.e. uniform) |

Identical under irspack 0.4.2 and 0.5.2. Instrumenting the call site confirms
the argument itself changed: for one recipe with `scheme: random` and
`time_column: ts`, 2.0.0 passes `"ts"` and 2.1.0 passes `None`.

**Nothing errors and no exit code changes.** The recipe stays valid, training
succeeds, and the only visible effect is that the validation set the Optuna
search scores against is a different set of interactions — so `best_score` can
move on the first retrain after the upgrade with nothing in the recipe touched.
**That is not a regression and not something to chase.** It is also not a
like-for-like comparison: a `best_score` from before the upgrade and one from
after were computed against different holdouts, so do not diff them. See
[operations.md](operations.md#what-best_score-is-and-is-not).

If you actually wanted the recency holdout, say so explicitly — set
`split.scheme: time_user`, which is what 2.0.0 was giving you by accident.

**One of the six shipped example recipes is affected:**
`examples/sql-sqlite/recipe.yaml` has carried this exact pairing
(`scheme: random` with `time_column: event_at`) unchanged since 2.0.0. Of the
other five, two use `time_user`, one sets no scheme, and two use `random` with
no time column at all — so they are genuinely unaffected.

That matters because `sql-sqlite` is the example the SQL data-source
documentation points at. If you started from it, you have a recipe whose
`best_score` moves on the first retrain after upgrading, for a reason nothing
in the recipe explains. Grep your own recipes for the same combination.

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

Signing keys and the key-rotation procedure; and the artifact container itself
(magic bytes, `FORMAT_VERSION` 1, and the header layout). A 2.0.0-signed
artifact verifies under 2.1.0 and a 2.1.0-signed one verifies under 2.0.0, with
the same key. Every recipe's `recipe_hash` does change, but nothing gates on it.

**Three recipes' worth of exceptions, and they need different things from you.**
Two `path` forms that loaded under 2.0.0 are now refused with **exit 2** and
must be fixed *before* you upgrade. A third recipe shape keeps working but
changes what it measures — nothing to fix, but see
[`split.scheme: random` with a `time_column`](#splitscheme-random-with-a-time_column-now-splits-differently)
so the moved number does not read as a regression.

Fix these two before upgrading:

- **`az://` carrying a `user:pass@` pair** — see
  [Azure URIs](#azure-uris-changed-in-both-directions) above. Move the secret
  into the environment **and rotate it**: 2.0.0 logged that URI in the clear on
  every run, so the key must be treated as disclosed.
- **`arrow_hdfs://` and `async_wrapper://`** — the only two protocols fsspec
  registers whose names contain an underscore. RFC 3986 forbids `_` in a
  scheme, so `urlparse` reported no scheme at all and 2.0.0's allow-list read
  these as bare local paths and let them through, while `fsspec.open` routed
  them to a real remote backend. 2.1.0 derives the scheme the way fsspec does
  and refuses them, as it always did for the equivalent `hdfs://` form. This
  was an allow-list bypass, so the refusal is the point — but a recipe that
  relied on it stops loading. Use a supported scheme.

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
