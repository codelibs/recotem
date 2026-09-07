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

### A degraded recipe now recovers on its own

Before this release `last_load_error` was cleared by exactly one thing: a
successful load of a **different** artifact. (The two short-circuits below are
present unchanged in the 2.0.0 source, and neither clears it.) Anything else
that set it stayed set for the life of the process, so `/v1/health/details`
answered `503 degraded` long after the fault had passed, while
`/v1/recipes/{name}:recommend` served normally throughout. Two ordinary
situations ended that way:

- **A transient stat failure.** An S3 throttle, an IAM propagation window, an
  NFS stale handle or a remount makes one poll fail. The artifact is untouched,
  so the next poll sees an unchanged marker and takes the fast path — which at
  2.0.0 never reached the code that could clear the annotation.
- **A rollback.** Putting the *previous* artifact back — the first response to
  a bad deploy, and the remedy this page gives for a skewed IALS artifact —
  restores bytes that hash to the model already in memory, which at 2.0.0
  short-circuited before the annotation was cleared.

In both cases the only ways out were writing a *different* artifact or
restarting the process. 2.1.0 retracts the annotation once it can show it no
longer holds and logs `artifact_load_error_cleared` (INFO) when it does; see
[Watcher and registry semantics](operations.md#watcher-and-registry-semantics).

**What this changes for your alerting.** If you have a rule that treats a
`/v1/health/details` `degraded` as sticky — a runbook step that says "restart
the pod to clear it", or an alert with a long `for:` chosen because the signal
never cleared by itself — drop it. A `degraded` at 2.1.0 means the fault is
current. A recipe YAML that stopped parsing, and a load failure against the
artifact still on disk, are still reported until they are actually fixed.

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

**`az://` was not the only scheme with this gap** — see
[`gs://` and `file://` disclosed the same way](#gs-and-file-disclosed-the-same-way)
below before you conclude you have nothing to rotate.

The other two changed rows are a fix, and need no action: the canonical
`container@account.dfs.core.windows.net` form that Azure's own documentation
uses was being refused on `abfs://` / `abfss://` as if it were a credential.
If you worked around that by rewriting those paths, you can now write them the
documented way. The rule 2.1.0 applies to all three Azure aliases is: a bare
`container@account` is addressing and is accepted; a real `user:pass@` pair is
refused.

### `gs://` and `file://` disclosed the same way

The section above is written as an Azure story, but `az` was not the only
scheme missing from 2.0.0's credentials check. `gs` was exempt from it
**entirely** — rather than password-gated the way `az`/`abfs`/`abfss` were —
because `gs://project@bucket/key` is a legitimate gcsfs billing-project
override. A `gs://` URI whose userinfo also carried a *password* inherited that
exemption and loaded. `file://` had no check at all.

Measured at `v2.0.0` and at 2.1.0, same recipe, only `source.path` changed, with
a marked secret in place of the password:

| scheme | form | 2.0.0 | 2.1.0 | times the secret appeared in one `train` |
|---|---|---|---|---|
| `gs://` | `project@bucket…` (addressing) | accepted | accepted | — (no secret) |
| `gs://` | `user:pass@…` (credentials) | **accepted** | **rejected** | **5** at 2.0.0 → **0** at 2.1.0 |
| `file://` | `user:pass@…` (credentials) | **accepted** | **rejected** | **3** at 2.0.0 → **0** at 2.1.0 |
| `s3://` | `anything@…` | rejected | rejected | 0 (control — `s3` has no addressing use of `@`) |

The five `gs://` occurrences are not five copies of one line. The secret reaches
the log through four independent paths plus stderr: the INFO
`csv_source_fetch_start` event, an ERROR record raised by `gcsfs` itself
(complete with traceback), the WARN `size_cap_probe_failed` event, the
`train_error` event, and the `Training failed: …` line on stderr. Redacting any
one of them would not have been enough, which is why the fix is at recipe load.

**So the two-step remedy above applies to `gs://` as well**, and the "nothing to
rotate" sentence is about `az://` only. If any recipe carried a `gs://` path
with a colon before the `@`:

1. Move the secret into the environment — gcsfs reads credentials from
   `GOOGLE_APPLICATION_CREDENTIALS` or the ambient service account. `gs://`
   paths whose userinfo is a bare project name (`gs://my-project@my-bucket/…`,
   no colon) are the addressing form, still accepted, and were never logged as
   a secret; leave them alone.
2. **Rotate that key and purge or rotate the log archives**, for the same reason
   and with the same urgency as the Azure case.

`file://` is the narrow one. The refusal is scoped to an `@` in the URI's
*authority* — the `file://` scheme followed directly by a `user:pass@` pair and
only then the path. A local path that merely contains
`@` in a directory or file name is unaffected in both the bare form
(`/data/user@example.com/x.csv`) and the three-slash form
(`file:///data/user@example.com/x.csv`); both load and train under 2.0.0 and
2.1.0 alike. Only the authority form changed, and it is not a shape anyone
writes by accident.

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

### Exit codes moved, and one of them moved off zero

Nothing in this section requires a recipe change. It is here because two things
branch on these numbers — your supervisor or CronJob, and any CI step that runs
`recotem validate` — and both can change behaviour on upgrade with no file
touched.

**The one that matters most: an artifact directory the process cannot write to
used to be a silent success.** Under 2.0.0 the run exited **0** and logged
`recipe_lock_contended_skipping`, the same structured event a genuine
concurrent run produces — so a nightly job whose artifact volume lost write
permission (an `fsGroup` change, a remount, a re-provisioned PVC) reported
success every night and wrote nothing, while serve kept the model it already
had. Measured at v2.0.0, two different causes, identical outcome:

| cause | exit | structured event | artifact written |
|---|---|---|---|
| another process holds the lock | 0 | `recipe_lock_contended_skipping` | no |
| artifact directory not writable | 0 | `recipe_lock_contended_skipping` | **no** |

2.1.0 separates them: the second case now exits **8** with
`lock_permission_denied`, naming the lock path, the uid/gid, and the fact that
retrying will not help. The first case is unchanged — a real contended lock
still exits 0 with `recipe_lock_contended_skipping` under both versions, which
is what makes the split safe to rely on.

**So expect a green job to turn red.** If a training CronJob starts failing with
exit 8 immediately after the upgrade, 2.1.0 did not break it — 2.1.0 is telling
you it had already stopped producing artifacts. Check how old the served model
is before changing anything.

The rest of the moves, measured by running both versions over the same recipes:

| recipe fault | command | 2.0.0 | 2.1.0 |
|---|---|---|---|
| `output.path` directory not writable | `train` | **0** | 8 |
| `output.path` names an existing directory | `train` | 1 | 8 |
| `source.sha256` mismatch on a bare local or `file://` path | `train` | 7 | 3 |
| `training.storage_path` naming an unsupported dialect | `train` | 1 | 8 |
| `training.storage_path` carrying userinfo | `train` | 4 | 8 |
| `training.cutoff` above the distinct item count | `train` | 1 | 4 |
| `schema.user_column` absent from the data | `train` | 1 | 3 |
| `item_metadata.sha256` mismatch | `validate` | 0 | 3 |
| `item_metadata.path` that does not exist | `validate` | 0 | 3 |
| an `item_metadata.fields` entry absent from the file | `validate` | 0 | 3 |
| `schema.user_column` absent from the data | `validate` | 0 | 3 |
| an algorithm name that does not exist | `validate` | 0 | 4 |
| `training.storage_path` naming an unsupported dialect | `validate` | 0 | 8 |
| `training.storage_path` carrying userinfo | `validate` | 0 | 8 |

Two consequences:

- **Exit 1 was never a category.** Four `train` rows moved off it. Exit 1 is
  `_EXIT_UNKNOWN` — an unmapped exception — so alerting that treated it as
  "recotem crashed, page someone" now gets the specific class instead
  (8 configuration, 4 training, 3 data source). That is the improvement; the
  point is that the *number* your rule matches on changed.
- **`recotem validate` got materially stricter.** Seven recipe faults that
  `validate` reported as passing under 2.0.0 now fail it. If you run
  `recotem validate` as a CI gate, that gate can go red on recipes that have
  been merged for months. The recipes were already broken — `train` failed on
  most of them under 2.0.0 too — but the gate is where you will see it first.

The `source.sha256` row is the one to check your retry logic against.
[deployment/k8s.md](deployment/k8s.md) classifies exit 7 as *retry* (a
transient network fault) and exit 3 as a data-source error. A permanently
wrong `sha256` pin on a local path was reported as retryable under 2.0.0, so a
CronJob would retry bytes that can never match; 2.1.0 reports 3.

A malformed `RECOTEM_SIGNING_KEYS` also moved, from 5 to 8 — see
[Unchanged by this upgrade](#unchanged-by-this-upgrade) below, which covers it
in the context of the artifact format it does *not* affect.

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

**Four recipes' worth of exceptions, and they need different things from you.**
Three `path` forms that loaded under 2.0.0 are now refused with **exit 2** and
must be fixed *before* you upgrade. A fourth recipe shape keeps working but
changes what it measures — nothing to fix, but see
[`split.scheme: random` with a `time_column`](#splitscheme-random-with-a-time_column-now-splits-differently)
so the moved number does not read as a regression.

Fix these three before upgrading:

- **`az://` carrying a `user:pass@` pair** — see
  [Azure URIs](#azure-uris-changed-in-both-directions) above. Move the secret
  into the environment **and rotate it**: 2.0.0 logged that URI in the clear on
  every run, so the key must be treated as disclosed.
- **`gs://` carrying a `user:pass@` pair, and `file://` carrying an `@` in its
  authority** — see
  [`gs://` and `file://` disclosed the same way](#gs-and-file-disclosed-the-same-way).
  The `gs://` form needs the same rotation as the `az://` one: 2.0.0 wrote that
  secret to the log five times per run. A `gs://project@bucket` path with no
  colon is addressing, is still accepted, and needs nothing.
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
firing for an environment-variable typo and must learn exit 8. It is not the
only exit code this release moves — see
[Exit codes moved](#exit-codes-moved-and-one-of-them-moved-off-zero) above,
where one of them moves off **0**.

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
