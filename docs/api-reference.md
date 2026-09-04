# recotem v1 API Reference

Authoritative reference for the v1 HTTP surface mounted under `/v1`.

## Authentication

All endpoints except `/v1/health` require the `X-API-Key` header.  See
`docs/security.md` for key rotation procedures.

## Endpoints

### `POST /v1/recipes/{name}:recommend`
Single-user recommendation.

**Path parameters:** `name` matches `^[A-Za-z0-9_-]{1,64}$` (same as the
recipe-name constraint enforced by the recipe loader).

> **Brace the variable in a shell.** In zsh, `$NAME:recommend` is read as the
> `:r` history modifier — "strip the extension" — and silently becomes
> `NAMEecommend`. Quoting does **not** help; `"$NAME:recommend"` is mangled
> the same way. Only `"${NAME}:recommend"` is safe. The mangled path matches
> `GET /v1/recipes/{name}`, so a `POST` to it returns **405 Method Not
> Allowed**, which reads exactly like a missing route. If a verb 405s,
> suspect the shell before the server: a 422 carrying Pydantic field errors
> proves the POST route was reached. Literal recipe names in a URL are
> unaffected, which is why every example on this page works as printed.

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `user_id` | string | yes | – | 1-256 chars |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null | ≤1000 items. Post-filtered off the ranked list, so the response can be **shorter than `limit`**. See [Item exclusion](#item-exclusion). |
| `user_features` | object \| null | no | null | Raw feature values, keyed by the recipe's `features.user` column names. See [Feature-aware cold start](#feature-aware-cold-start) below. ≤64 keys. |

**Response body:** see `RecommendResponse` in `src/recotem/serving/schemas.py`.

**Status codes:** 200, 400 (`FEATURES_NOT_SUPPORTED` | `FEATURE_VALUE_UNUSABLE`), 401, 404 (`UNKNOWN_USER` | `RECIPE_NOT_FOUND`), 413 (`PAYLOAD_TOO_LARGE`), 422 (`VALIDATION_ERROR`), 503 (`RECIPE_UNAVAILABLE`).

### `POST /v1/recipes/{name}:recommend-related`
Seed-item → items.

**Request body:**

| field | type | required | default | notes |
|---|---|---|---|---|
| `seed_items` | string[] | yes | – | 1-100 items |
| `limit` | int | no | 10 | 1..1000 |
| `exclude_items` | string[] \| null | no | null | ≤1000 items. Post-filtered off the ranked list, so the response can be **shorter than `limit`**. See [Item exclusion](#item-exclusion). |
| `user_features` | object \| null | no | null | Raw feature values, keyed by the recipe's `features.user` column names. Adds a profile prior to the seed-history solve. See [Feature-aware cold start](#feature-aware-cold-start). ≤64 keys. |
| `item_features` | object[string, object] \| null | no | null | Raw feature values for seed items absent from training, keyed by seed item id. ≤100 keys; each value ≤64 keys. See [Feature-aware cold start](#feature-aware-cold-start). |

**Status codes:** 200, 400 (`FEATURES_NOT_SUPPORTED` | `FEATURE_VALUE_UNUSABLE`), 401, 404 (`UNKNOWN_SEED_ITEMS` | `NO_CANDIDATES` | `RECIPE_NOT_FOUND`), 413 (`PAYLOAD_TOO_LARGE`), 422 (`VALIDATION_ERROR`), 503 (`RECIPE_UNAVAILABLE`).

`UNKNOWN_SEED_ITEMS` means none of the supplied `seed_items` were known
to the model id-map (typically a client-side data issue).
`NO_CANDIDATES` means at least one seed was known but the ranker did not
produce any survivors after its internal filtering — typically a data
distribution issue rather than a client mistake. Every branch of this verb
raises it the same way — the pre-existing all-seeds-known path and both
feature-aware cold-start branches (cases B and C) — so an empty result is
reported identically regardless of which path served the request.

### `POST /v1/recipes/{name}:batch-recommend`
Multi-user batch.  Body: `{ "requests": RecommendRequest[], "include_metadata": bool }` (1..256).
Response: `BatchRecommendResponse`.  Per-element `status` ∈ {ok, error}.
HTTP 200 on partial failure; HTTP 503 only when the recipe itself is
unavailable.

Each element accepts `user_features` exactly as the single `:recommend`
endpoint does (see [Feature-aware cold start](#feature-aware-cold-start));
an element whose model has no matching feature state surfaces as
`status=error, code=FEATURES_NOT_SUPPORTED` rather than failing the whole
batch.

`include_metadata` (default `false`): when `true`, each `ok` result
includes per-item metadata fields (same join as the single-recommend
endpoint).  Default `false` preserves the performance-first default for
bulk callers.

The aggregate `sum(requests[].limit)` must not exceed **5000**.  When a
sub-request would push the running aggregate over the cap, that element
surfaces as `status=error, code=VALIDATION_ERROR` and processing of
subsequent elements continues — earlier elements are unaffected.  The
list size cap (1..256) is enforced at the schema level (whole-request
422 if violated); per-element schema failures are surfaced per-element
so a single bad entry never 422s the whole batch.

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 413 (`PAYLOAD_TOO_LARGE`), 422 (`VALIDATION_ERROR` — only for whole-request shape, e.g. missing `requests` key, list too large), 503 (`RECIPE_UNAVAILABLE`).

> **Note:** batch endpoints return `{item_id, score}` only by default
> (`include_metadata=false`).  Set `include_metadata: true` to include
> per-item metadata fields (same join as single-recommend endpoints).
> Be aware that metadata enrichment increases response size; for bulk callers
> that do not need metadata the default `false` is recommended.

### `POST /v1/recipes/{name}:batch-recommend-related`
Multi-seed batch.  Body: `{ "requests": RecommendRelatedRequest[], "include_metadata": bool }` (1..256).
Same aggregate-limit, per-element validation rules, and `include_metadata`
semantics as `:batch-recommend`.

**Cold-seed solve cap.** This verb carries a *second* aggregate cap that
`:batch-recommend` does not need. Case C runs one solve per cold seed, so
the aggregate count of cold seeds — `sum` over elements of the seeds named
in that element's `item_features` — must not exceed **512**. An element
that would push the running total over the cap surfaces as `status=error,
code=VALIDATION_ERROR`, exactly like the aggregate-`limit` cap, and later
elements continue to be processed.

The two caps guard different dimensions and neither subsumes the other:
`sum(limit)` bounds response volume, while this bounds solver work. A batch
of `limit: 1` elements sits at 2% of the aggregate-`limit` cap while
demanding 25,600 solves. The count is taken from the request alone — a seed
named in `item_features` counts even if it turns out to be a known item
whose learned embedding is used instead — so the same body is always
accepted or rejected identically, regardless of which model is loaded.

A single `:recommend-related` call cannot reach this cap: `seed_items` is
capped at 100, so a maximal single request is 100 solves.

Each element accepts `user_features` / `item_features` exactly as the
single `:recommend-related` endpoint does, including the case A/B/C
precedence rules described in
[Feature-aware cold start](#feature-aware-cold-start); an element that
produces no survivors surfaces as `status=error, code=NO_CANDIDATES`, on
every branch.

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 413 (`PAYLOAD_TOO_LARGE`), 422 (`VALIDATION_ERROR` — only for whole-request shape), 503 (`RECIPE_UNAVAILABLE`).

### `GET /v1/recipes`
Authenticated.  Returns `RecipesListResponse` with one entry per loaded
recipe.

### `GET /v1/recipes/{name}`
Authenticated.  Returns `RecipeDetailResponse` or 404 (`RECIPE_NOT_FOUND`).

**Status codes:** 200, 401, 404 (`RECIPE_NOT_FOUND`), 422 (`VALIDATION_ERROR`), 503 (`RECIPE_UNAVAILABLE`).

The 422 is what a mistyped verb lands on: `GET /v1/recipes/demo:recommend`
puts `demo:recommend` in the `{name}` slot, which fails the
`^[A-Za-z0-9_-]{1,64}$` pattern. A `POST` to the same path routes to the verb
and behaves normally — see the note on shell quoting under
[`:recommend`](#post-v1recipesnamerecommend).

### `GET /v1/health`
Unauthenticated.  Returns `{status, total, loaded}`.  Body status is
`"ok"` when every registered recipe is loaded, `"degraded"` otherwise.
The HTTP response code mirrors body status: **200 OK** when ok, **503
Service Unavailable** when degraded — so K8s readiness probes pointing
at this endpoint mark the pod NotReady whenever any recipe is
unloaded.

### `GET /v1/health/live`
Unauthenticated. Always `200 {"status": "alive"}` while the process can answer.

**Liveness only.** It never reads artifact state, because a restart cannot fix a
missing or unloadable artifact: the replacement pod reads the same recipes
directory and the same artifact store and fails identically, while dropping the
models that *had* loaded. Point `livenessProbe` here, never at `/v1/health`.

### `GET /v1/health/ready`
Unauthenticated. `200` when at least one recipe is loaded, `503` when none is.
Body is `{status, total, loaded}` (plus `skipped` when non-zero).

**Readiness.** "Should the Service send traffic here?" is a different question
from `/v1/health`'s "is every recipe present?". A replica holding 13 of 14
models can serve 13 of them, and every replica reads the same recipes
directory — so failing readiness on one untrained recipe takes the whole
fleet out of the Service at once. A cold fleet (nothing loaded) still fails,
which preserves the first-install guarantee.

Use the three together as the shipped chart does: `startupProbe` on
`/v1/health` (a **new** pod waits for every recipe), `readinessProbe` on
`/v1/health/ready`, `livenessProbe` on `/v1/health/live`.

### `GET /v1/health/details`
Authenticated.  Returns `{status, recipes: {name: health}}`.  Same 200
/ 503 status-code rule as `/v1/health`.

### `GET /v1/metrics`
Prometheus exposition.  Excluded from OpenAPI.  Requires
`RECOTEM_METRICS_ENABLED` to be truthy at startup.

**Requires `X-API-Key`** — configure your Prometheus scraper with an
`authorization` block or `http_headers` accordingly.

## Item exclusion

`exclude_items` is a **post-filter, not a constraint on the ranker.** The
model is asked for exactly `limit` items, and any of those that appear in
`exclude_items` are then dropped. Excluded items are not backfilled, so a
response can be **shorter than `limit`** — by however many of the excluded ids
happened to rank inside the top `limit`:

```
limit=5, no exclude          -> 5 items  ['40', '3', 'beta', 'mmm', 'aaa']
limit=5, excluding the top 2 -> 3 items  ['beta', 'mmm', 'aaa']
```

This is the behaviour on all four inference verbs, including each element of
the two batch verbs. Ids in `exclude_items` that the model never ranked are
simply no-ops.

**Plan for it client-side.** The common case — "don't recommend what this user
already bought" — is exactly the case where exclusions are *likely* to rank
highly, so the shortfall is not a rare edge. If you need a full page of `n`
items, request `limit = n + len(exclude_items)` and trim the response yourself.
There is no server-side option to backfill.

**Why it is not a ranker constraint.** Pushing `exclude_items` down into the
ranker (as `forbidden_item_ids`) would make it backfill to a full `limit`.
That option is deliberately not taken, because it is only available on some
code paths: the feature-aware cold-start paths could honour it, while the
pre-existing paths post-filter. The same request would then return a different
number of items depending on whether feature values were supplied — one
parameter quietly meaning two different things. Uniformly post-filtering
everywhere is the trade the implementation makes; see the reasoning in
`src/recotem/_idmap.py`.

Note the contrast with `:recommend-related`'s own seed handling: the seed
items *are* removed via `forbidden_item_ids`, so the ranker **does** backfill
around them and you still get `limit` items. A client that asked "what goes
with `i0`" should not spend a slot on `i0` itself. Seed removal is the
server's own business and is invisible in the count; client-requested
`exclude_items` is not.

## Feature-aware cold start

`user_features` and `item_features` are only meaningful against a model
trained with a [`features:`](recipe-reference.md#features) block. They are
accepted (and validated) on every model, but a model with no matching
feature state (or whose search winner is not feature-capable — see
`docs/recipe-reference.md#features`) responds `400 FEATURES_NOT_SUPPORTED`
rather than silently ignoring the field or guessing.

Whether a given artifact can serve these cases is readable up front, without
sending a request: `recotem inspect` prints `features.active`, which is `true`
only when the search winner can actually consume the encoder state. An artifact
with no `features` key at all, or with `"active": false`, will answer
`FEATURES_NOT_SUPPORTED` — see
[recipe-reference.md — What the artifact header records](recipe-reference.md#what-the-artifact-header-records).

Three cold-start cases, spread across two verbs:

| Case | Verb | Trigger | What it does |
|---|---|---|---|
| A — unknown user, features only | `:recommend` | `user_id` unknown, `user_features` present | Scores every known item against the profile alone (no interaction history exists yet for this user). |
| B — unknown user, features + ad-hoc history | `:recommend-related` | `user_features` present | Runs the same seed-history solve as the pre-existing path, with the profile added as a joint prior. This is a genuine joint solve, not either/or: it correlates with neither a features-only nor a history-only score alone. |
| C — unknown seed item(s) | `:recommend-related` | one or more `seed_items` absent from training, and a matching entry in `item_features` | Computes each cold seed's embedding from its features, averages it with the known seeds' learned embeddings, and scores as item-item similarity. |

If a request supplies both a cold seed's `item_features` **and**
`user_features` on `:recommend-related`, case C wins: a cold seed has no row
in the seed-history matrix that case B's solve uses, so running case B alone
would silently drop that seed's contribution. Case C is the only path that
can actually use a cold seed's features.

### Cold-start `score` is uncalibrated — rank it, do not threshold it

A cold-start `score` is an uncalibrated similarity. **Its magnitude is not
comparable with a warm `:recommend` score from the same model, it is not
stable across training runs of the same recipe, and it is not stable across
requests.** Rankings are unaffected — the ordering within one response is
meaningful, the absolute numbers are not.

The last of those is the one that surprises people. Cold-start scoring is an
iterative solve, and it does not reproduce bit-for-bit even for one loaded
artifact answering an identical request twice: measured over ten identical
`:recommend-related` calls with inline `item_features` against a single
running server, nine returned a different score vector, with the top score
varying by ~2e-4 relative. The item order was identical every time. This is
not thread-related — it persists with `IRSPACK_NUM_THREADS_DEFAULT=1` and in
single-process repetition — so **do not cache cold-start scores, diff them
between deploys, or assert on them in tests.** Warm `:recommend` and
`:recommend-related` from a known seed *are* bit-stable; only the cold paths
move.

The reason is the feature ridge `lambda_item_feature` /
`lambda_user_feature`, which Optuna samples log-uniformly over `[1, 1e6]`
(see [recipe-reference.md — features](recipe-reference.md#features)). Holding
every other iALS hyperparameter fixed on `examples/feature-aware/` and varying
only that ridge:

| `lambda_item_feature` | ndcg@10 (the search objective) | warm top score | cold top score |
|---|---|---|---|
| `1` | 0.7733973654 | 5.81e-01 | 1.70e-01 |
| `10` | 0.7796865566 | 5.65e-01 | 3.83e-02 |
| `100` | 0.7808524871 | 5.54e-01 | 4.50e-03 |
| `1000` | 0.7981623067 | 5.53e-01 | 4.57e-04 |
| `10000` | 0.7981623067 | 5.53e-01 | 4.57e-05 |
| `100000` | 0.7981623067 | 5.53e-01 | 4.58e-06 |
| `1000000` | 0.7981623067 | 5.53e-01 | 4.58e-07 |

Two things follow. The cold-start score falls roughly as `1/lambda` — four
orders of magnitude across the table, six across the full sampled range —
while the warm score moves by about 5%. And above `lambda ≈ 1e3` the search
objective is **identical to ten decimal places**, so Optuna has nothing to
choose on across the top three decades of its own sampling range and settles
somewhere in that band arbitrarily. Two runs of one unchanged recipe can
therefore land three orders of magnitude apart in cold-start score while
scoring identically on the metric you asked it to optimise.

So: sort by `score`, take the top *k*, and show them. Do not compare a
cold-start `score` against a warm one, do not set a numeric relevance
threshold on it, and do not persist it as a feature for a downstream model
that will see a differently-tuned artifact after the next retrain.

**A known `user_id` with `user_features` supplied is not an error.** The
learned embedding from that user's real interaction history strictly
dominates a profile prior, so the server always prefers it and simply
**ignores** the supplied `user_features` — it does not reject the request.
This lets a client always send the user's profile on every request without
needing to know in advance whether the user is new or returning.

**A feature key that names no declared column is silently ignored — it is
not an error.** `_row_values` (`_features.py`) drives the encode from the
model's *declared* `features:` columns and does `values.get(name)`, so a key
in `user_features` / `item_features` that matches no declared column on that
side is never read. The request returns `200` with no error field and nothing
in the body marking the key as rejected. The only server-side signal is the
`recotem_v1_feature_unknown_column_total` metric (see
[operations.md](operations.md#feature-aware-ials-sizing)), labelled by recipe
and **side only — never by the key name** — and incremented once per side per
request that carried at least one such key. This is distinct from an unknown
*value* in a *declared* column (next section), which also returns `200` but is
counted separately, by `recotem_v1_feature_unknown_value_total`. A mapping in
which *every* key is mistyped (or is aimed at the wrong side) therefore
encodes to the bias column alone and comes back with **population-prior
results** — the same output an empty `user_features` would produce, and
indistinguishable from it in the response. **This is current behavior: clients
must not rely on the API to validate feature keys.** A silently-ignored key is
byte-for-byte identical, in the response, to a correct request that happened
to add no signal.

**Unknown feature values degrade, they do not fail the request.** What
"degrade" means, and whether `recotem_v1_feature_unknown_value_total` (see
[operations.md](operations.md#feature-aware-ials-sizing)) actually catches
it, differs by encoding:

- `categorical` — a value absent from the training vocabulary encodes to an
  all-zero segment for that column, and the counter increments.
- `multi_label` — each token is looked up independently: known tokens are
  retained (each contributing exactly one `1.0` to its dimension, even if
  the token is repeated in the input — see the multi-hot note below),
  unknown tokens are dropped. The counter increments whenever **any**
  supplied token misses the vocabulary, even if other tokens in the same
  value are known. A mixed value such as `"Action|Thrller"` sets the bit
  for the known token, drops `Thrller`, and still increments the counter —
  a partial typo is caught, not silently absorbed.
- `numerical` — a **missing** value (absent, `null`, or `NaN`) or a value
  that fails to parse as a number at all contributes nothing to the row,
  equivalent to encoding the standardized mean (`0`), and does **not**
  increment the counter. A value that DOES parse as a number but is
  **non-finite** (`Infinity` / `-Infinity` — valid in JSON per Python's
  parser extension — or a `NaN` reached via a string like `"nan"`) also
  contributes nothing to the row, but this case **does** increment the
  counter: it is a real, present value the server could not use, not an
  absent one.

Do not rely on this counter as a general typo detector for `numerical`
columns: a **missing or unparseable** value still degrades the
recommendation with no signal at all — only the non-finite case above is
covered. `categorical` and `multi_label` are both reliably covered.

`multi_label` is multi-**hot**, not a count vector: `"rock|pop|rock"`
contributes `1.0` to the `rock` dimension, not `2.0` — duplicate tokens in
one value are deduplicated before encoding, both at training time and for a
cold-start request's `item_features` / `user_features`.

**A large `numerical` value degrades silently across a wide range; only the
extreme tail is a hard 400.** Unlike the missing/unparseable case above, a
`numerical` value is standardized at serve time by dividing the raw request
value by the column's *training* mean/std — a fit the request's own value
was never part of (see the "Training is unaffected" note below for why that
matters). Nothing clamps how large the resulting magnitude may get, so
behavior is NOT a clean two-way split ("normal" vs. "hard 400"). An actual
sweep against a column with training std ≈ 0.425 found:

| value | result |
|---|---|
| `0.3` | `200`, small, normal-looking score |
| `100` | `200`, but the score is already visibly degenerate (order alone, no longer proportional to the profile) |
| `1e6` – `1e18` | `200`, score grows without bound (into the hundreds of millions and beyond) as the value grows |
| ~`1e19`+ | `400 FEATURE_VALUE_UNUSABLE` — only here does irspack's per-request cold-start solver itself give up |

So roughly `1e2` through `1e18` in this measurement is a **silent degrade**:
`200`, an unbounded and effectively meaningless score, a fixed/degenerate
ranking — and none of these finite values touch
`recotem_v1_feature_unknown_value_total` (per the counter note above, that
counter fires for a `numerical` value only when it is non-finite), so
nothing server-side signals that this happened either. The 400 only fires
once the standardized magnitude is large enough to make the underlying
conjugate-gradient solve singular; **the exact crossover is not a fixed
constant** — it depends on the column's training std and the BLAS
implementation solving the system, so do not hard-code a boundary value
(e.g. `1e22`) as a contract.

**The 400's `detail` message describes the standardized value, not the
client's raw one — because the raw value need not be extreme.** A column
whose training std is small enough (see the near-constant-column note
below) can make an ordinary raw value like `10000` standardize to a
magnitude that breaks the solver, exactly like `1e22` does against a
normal-sized std. The `detail` string therefore never claims the supplied
value itself was extreme; it says the resulting *standardized* value was
numerically unusable for this model's cold-start scoring, which is true
regardless of which side (raw magnitude vs. tiny std) produced it.

**A near-constant column is a special case of a small std, not a separate
bug — and training floors the most common cause of it.** A column whose
values are "the same number" up to floating-point rounding noise (e.g.
`std ≈ 1.36e-15`, not exactly `0.0`) would otherwise divide serve-time
standardization by a near-zero denominator, turning a routine value like
`10000` into an astronomically large standardized one — an ordinary client
value producing a 400 for a reason the client cannot see. `build_encoder_state`
(`_features.py`) floors a numerical column's training-time std to zero
whenever it is no larger than a relative tolerance of the column's own
scale (`1e-8 × max(abs(mean), 1.0)`) — tight enough to preserve real,
intentional small variance while absorbing realistic floating-point
rounding noise. A column caught by this floor never reaches the
standardization divide at all: it degrades exactly like a missing value
(logged once as `feature_zero_variance_column`), never a 400. This is a
**training-time behavior change**: a column that previously stood a chance
of triggering `FEATURE_VALUE_UNUSABLE` for a near-constant reason now never
does. It does not eliminate the phenomenon in general — a column with
genuine (not rounding-noise) small variance just above the floor still
standardizes an ordinary value to an unusable magnitude by the same
mechanism as the sweep above, which is exactly why the `detail` message
above is worded the way it is rather than promising the raw value was at
fault.

**Clamping the standardized magnitude before it reaches the solver — which
would close the silent-degrade band above — was deliberately deferred, not
overlooked.** Picking a clamp bound (how many training standard deviations
is "too many") is a modelling decision that changes what every downstream
consumer of the same encoding sees, including training, not a bugfix to the
400 path added here; it was intentionally scoped out of this fix. This
deferral was previously disclosed nowhere — this paragraph is that
disclosure.

Training is unaffected either way: the same value flowing through
training-time encoding is untouched by this guard, which only wraps the
serve-time cold-start solve. (Training has its own, much stronger bound: a
numerical column's training-time mean/std are computed from the same values
being standardized, so an outlier inflates the very std it is divided by —
this caps the worst-case training-time standardized magnitude at roughly
`(n_rows - 1) / sqrt(n_rows)` no matter how extreme the raw value is, which
is nowhere near the magnitude needed to break the solver. Serve-time has no
such self-bound, because the request's value is standardized against a
std fit without it.)

**Every `:recommend-related` branch reports an empty result the same way.**
The all-seeds-known branch, the `user_features` profile-prior branch (case
B), and the cold-seed branch (case C) all return `404 NO_CANDIDATES` when the
ranker produces zero survivors after its own filtering. An earlier revision
of this feature returned `200 {"items": []}` from the two cold-start
branches; that asymmetry was fixed before release, so a client may branch on
HTTP status for this verb. `:recommend` has no `NO_CANDIDATES` code at all
and returns `200` with an empty `items` list in every case.

**Length and size bounds on cold-start fields.** A cold-start feature mapping
is bounded on three axes, each rejected before the model is consulted:

- **Key count** — each `user_features` / `item_features` mapping accepts at
  most **64 keys** (`item_features` additionally caps its outer seed-id keys at
  **100**). Over the cap is `422 VALIDATION_ERROR`.
- **Key length** — each feature-dict key (a `user_features` column name, an
  `item_features` outer seed id, or a nested per-seed feature key) must be
  **1..256 characters**. Over the cap is `422`; the error reports only the
  offending length, never the key text.
- **Value type** — each feature value must be a JSON **scalar** (string,
  number, boolean, or `null`). An array or object is rejected with `422`.
  This is not merely a size guard: values are encoded via `str(value)`, so an
  array would be matched against the training vocabulary as its Python repr
  and could never match anything — it was already a no-op, just an expensive
  one.
- **Value length** — each *string* feature value must be **≤ 8192 characters**
  (this bounds `multi_label` tokenization work). Over the cap is `422`; the
  error names the offending column but never echoes the value. Non-string
  scalars are unaffected — their encoded form is bounded by their own type.

On the batch verbs a key-length, value-type, or value-length violation
surfaces as a per-element `VALIDATION_ERROR` inside the `200` batch response
rather than failing the whole batch.

Independently of these per-field caps, the **entire request body** is bounded
by `RECOTEM_MAX_BODY_BYTES` (default **128 MiB**, clamped to
`[1 MiB, 2 GiB]`). A body over that limit is rejected with `413
PAYLOAD_TOO_LARGE` **before** the JSON is parsed, so it applies to every POST
endpoint regardless of which fields the body carries.

## Headers

- `X-Request-ID` — accepted (regex `^[A-Za-z0-9_-]{1,128}$`) or generated;
  always echoed in the response.  When missing or invalid the server
  substitutes a 12-char hex string.  Handlers read the validated value
  from `request.state.request_id`, so the body field and response header
  always agree.
- `X-Recotem-Model-Version` — present on every successful recommend
  response; mirrors `model_version` in the body.
- `X-Recotem-Items-Degraded` — present on `:recommend` and
  `:recommend-related` responses only when one or more items could not be
  fully serialized with metadata.  The value is the total count of items
  that fell back to bare `{item_id, score}` (fallback) or were omitted
  entirely (dropped) due to metadata serialization failures.  Absent when
  all items serialize cleanly.  **Not sent** on `:batch-recommend` or
  `:batch-recommend-related` endpoints.

## Error body shape

All v1 error responses share a flat envelope at the top of the body:

```json
{"detail": "<human-readable message>", "code": "<MACHINE_CODE>"}
```

There is no nested `{"detail": {"detail": ..., "code": ...}}` form —
clients parse `body["detail"]` and `body["code"]` directly.

**`code` is present on every error a `/v1` route returns, but not on the ones
that never reach a route.** Three responses come from Starlette's default
handlers before routing or after the middleware, and carry `detail` alone —
or no JSON at all:

| response | body |
|---|---|
| `GET /v1/metrics` when metrics are disabled | `{"detail": "Not Found"}` |
| an unknown verb, e.g. `POST /v1/recipes/{name}:frobnicate` | `{"detail": "Method Not Allowed"}` |
| a `Host:` header outside `RECOTEM_ALLOWED_HOSTS` | `Invalid host header` (plain text, 400) |

Read `body.get("code")` rather than `body["code"]`.

**422 validation errors** add a per-field breakdown from FastAPI /
Pydantic and include the request ID so the body is correlatable with the
`X-Request-ID` response header:

```json
{
  "request_id": "<id matching X-Request-ID>",
  "detail": "Request validation failed",
  "code": "VALIDATION_ERROR",
  "errors": [{"loc": ["body", "limit"], "msg": "...", "type": "..."}]
}
```

**500 unhandled errors** flatten to:

```json
{"detail": "internal error", "code": "INTERNAL_ERROR"}
```

Each endpoint above lists the status codes it can emit; the body shape
in every error case is one of the three forms above.

## Error Code Table

| code | HTTP | when |
|---|---|---|
| `RECIPE_UNAVAILABLE` | 503 | recipe not loaded |
| `RECIPE_NOT_FOUND`   | 404 | no such recipe in registry |
| `UNKNOWN_USER`       | 404 | user not in idmap |
| `UNKNOWN_SEED_ITEMS` | 404 | none of seed_items known to model |
| `NO_CANDIDATES`      | 404 | `:recommend-related` produced no survivors after ranker filtering — reachable on **every** branch of the verb, including both feature-aware cold-start branches (also surfaced per-element inside a `200` batch response) |
| `VALIDATION_ERROR`   | 422 | Pydantic schema rejected the request (also used per-element inside batch responses) |
| `FEATURES_NOT_SUPPORTED` | 400 | `user_features` / `item_features` supplied but the model has no matching feature state, or its search winner is not feature-capable (also used per-element inside batch responses) |
| `FEATURE_VALUE_UNUSABLE` | 400 | a supplied `numerical` feature value, once standardized against the column's training mean/std, is large enough to make irspack's cold-start solver itself fail (the exact threshold is std/BLAS-dependent, not a fixed constant, and depends on the column's std as much as the raw value — see [Feature-aware cold start](#feature-aware-cold-start)) — the model and feature side both support cold start, but this particular value does not. Values large enough to be meaningless but not large enough to break the solver degrade silently as `200` instead (also used per-element inside batch responses) |
| `PAYLOAD_TOO_LARGE`  | 413 | request body exceeds `RECOTEM_MAX_BODY_BYTES` (default 128 MiB, clamped `[1 MiB, 2 GiB]`); rejected before the body is parsed, so it applies to every POST endpoint |
| `MISSING_API_KEY`    | 401 | `X-API-Key` header missing |
| `INVALID_API_KEY`    | 401 | `X-API-Key` header present but did not match any configured digest (also covers short-key / oversize-key rejections so callers cannot fingerprint the guard) |
| `INTERNAL_ERROR`     | 500 / batch | unhandled server-side exception, or unexpected recommender internal layout (`recommender_layout_unexpected`) — status=500 on single endpoints; per-element `status=error` inside batch responses |

All v1 codes use `UPPER_SNAKE_CASE`.
