# Kubernetes Deployment

## Overview

Two Kubernetes objects cover the Recotem lifecycle:

- **CronJob** — runs `recotem train` on a schedule.
- **Deployment** — runs `recotem serve` continuously, reading artifacts from a shared store.

Recipes can be delivered to both objects via ConfigMap (small, static recipes), PVC (read-write volume), or object storage (S3/GCS — recipes and artifacts both live remotely).

## First install: seed an artifact before serve starts

**Read this before your first `helm install` or `kubectl apply`.** Train and
serve are ordered: serve cannot start healthy until train has produced at
least one artifact, and nothing in the chart or the example manifests runs
train for you at install time.

`recotem serve` loads one artifact per recipe at startup. Until at least one
recipe has one, `/v1/health/ready` answers **503** with
`{"status":"unready","total":1,"loaded":0}`. On an empty artifact store that
means:

```
Warning  Unhealthy  kubelet  Startup probe failed: HTTP probe failed with statuscode: 503
```

...repeatedly, until the startup probe's `failureThreshold` is reached and the
container is restarted — a crash loop that looks like a bug but is only a
missing artifact. `helm install --wait` fails with a rollout timeout, and the
CronJob's default `schedule: "0 2 * * *"` means nothing produces the artifact
for up to a day.

**Always train before serving.** Pick whichever fits your setup:

**A. Helm — install with training enabled, seed, then verify.** Install
without `--wait` (the serve pods will not be Ready yet), kick off the CronJob
immediately as a one-off Job, then wait:

```bash
helm upgrade --install recotem ./helm/recotem -n recotem \
  -f values-prod.yaml --set train.enabled=true      # no --wait

kubectl -n recotem create job bootstrap-0 --from=cronjob/recotem-train
kubectl -n recotem wait --for=condition=complete job/bootstrap-0 --timeout=30m

kubectl -n recotem rollout status deployment/recotem --timeout=10m
```

**B. Raw manifests — apply the bundled bootstrap Job.**
`examples/k8s/bootstrap-job.yaml` is a one-shot `recotem train` Job with the
same container spec as the CronJob; `kubectl apply -f examples/k8s/` creates
it alongside the Deployment. See
[`examples/k8s/README.md`](../../examples/k8s/README.md).

**C. Train out-of-cluster.** Run `recotem train` anywhere that can write the
recipe's `output.path` (an `s3://` / `gs://` URI, or the PVC mounted on a
workstation) before creating the Deployment at all. Signing keys must match
the ones serve is configured with.

In every case the serve pods recover on their own once an artifact appears —
the watcher picks it up within `RECOTEM_WATCH_INTERVAL` seconds and the next
probe succeeds. No rollout restart is needed.

**Adding a recipe later is not a first install.** All three probes read the
same "at least one recipe loaded" state, so a valid recipe whose artifact has
not been trained yet leaves the running fleet in the Service *and* lets new
pods start. `/v1/recipes/<name>:recommend` for that one recipe answers 503
`RECIPE_UNAVAILABLE` until the next train run; every other recipe keeps
serving. Point a probe at `/v1/health` instead and you get the opposite:
`/v1/health` is the strict "is *every* recipe present?" endpoint, and a
startup probe that reads it restarts the container, so one untrained recipe
stops every new pod — a rolling update or an HPA scale-out never converges.
Use `/v1/health` for alerting, not for probes.

> **Why no post-install hook in the chart?** Training is an unbounded
> operation — the CronJob allows it an hour (`activeDeadlineSeconds: 3600`).
> Wiring it into `helm install` would make every first install block on it and
> fail against Helm's `--timeout` (5 minutes by default), trading a legible
> "no artifact yet" crash loop for an opaque failed release. Seeding is kept
> as an explicit step for that reason.

**Recovering an install that is already crash-looping** is the same fix — the
pods need no manual intervention beyond producing the artifact:

```bash
kubectl -n recotem create job recover-0 --from=cronjob/recotem-train
kubectl -n recotem logs -f job/recover-0
```

If `train.enabled=false` (the chart default) there is no CronJob to copy from;
either enable it, or apply `examples/k8s/bootstrap-job.yaml` with the image,
Secret and volume names adjusted to your release.

## Verify the install with one request

Nothing above proves the API answers. Two of this page's warnings only show
up here, so make the request before you call the install done.

```bash
NS=recotem
POD=$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=recotem \
        -o jsonpath='{.items[0].metadata.name}')

# 1. probes, from inside the pod (Host: localhost always passes)
kubectl -n "$NS" exec "$POD" -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/v1/health/ready').read())"

# 2. a real recommendation, through the Service
kubectl -n "$NS" port-forward svc/recotem 8080:8080 &
PF=$!
RECIPE=news_articles          # your recipe's `name:`, case-sensitive
curl -sS -X POST \
  -H 'Host: localhost' \
  -H "X-API-Key: <the plaintext key, not the sha256 hash>" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","limit":3}' \
  "http://127.0.0.1:8080/v1/recipes/${RECIPE}:recommend"
kill "$PF"
```

Two traps this catches:

- **`-H 'Host: localhost'` is not optional.** Without it the request carries
  `Host: 127.0.0.1:8080` (or the Service DNS name) and
  `TrustedHostMiddleware` answers **400** unless `RECOTEM_ALLOWED_HOSTS`
  lists that name. With chart defaults and no Ingress, a request to
  `http://recotem:8080/v1/health` from inside the cluster returns 400 — the
  chart only widens the list when `ingress.enabled=true`. See the
  `RECOTEM_ALLOWED_HOSTS` warning under [Service](#service).
- **`${RECIPE}` must be brace-quoted.** In zsh, `$RECIPE:recommend` is the
  `:r` history modifier, not a variable followed by a literal colon — the URL
  silently becomes `/v1/recipes/RECIPEecommend` and the POST lands on the GET
  route as a **405**, which reads exactly like a missing endpoint.

A 200 with an `items` array means train, signing keys, the artifact store,
the probes and the allow-list are all wired correctly. A 401 means the API
key is wrong (the Secret holds `<kid>:sha256:<hex>`; clients send the
plaintext). A 503 `RECIPE_UNAVAILABLE` means that one recipe has no artifact
yet.

## CronJob (train)

```yaml
# examples/k8s/cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: recotem-train
  namespace: recotem
  labels:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: train
spec:
  # Daily at 02:00 UTC.
  schedule: "0 2 * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      # Hard deadline: 2 hours per training run.
      activeDeadlineSeconds: 7200
      template:
        metadata:
          labels:
            app.kubernetes.io/name: recotem
            app.kubernetes.io/component: train
        spec:
          serviceAccountName: recotem
          restartPolicy: OnFailure
          # Pod-level security context — only fields valid at this scope.
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            runAsGroup: 1000
            # fsGroup ensures mounted PVC files are owned by GID 1000 (appuser),
            # matching the Dockerfile USER.  Without this, PVC data may be
            # inaccessible when the volume's on-disk owner differs from runAsUser.
            fsGroup: 1000

          containers:
            - name: train
              image: ghcr.io/codelibs/recotem:2.0.0
              imagePullPolicy: IfNotPresent
              # Container-level security context — fields like
              # allowPrivilegeEscalation / readOnlyRootFilesystem are only
              # valid here, not under spec.securityContext.
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop:
                    - ALL
              # Train all recipes in /recipes.  Fail loudly when the directory
              # is empty, so a wrong ConfigMap name surfaces here instead of as
              # an unexplained 503 from serve.
              command:
                - /bin/sh
                - -c
                - |
                  set -eu
                  count=0
                  for recipe in /recipes/*.yaml; do
                    [ -f "$recipe" ] || continue
                    count=$((count + 1))
                    recotem train "$recipe"
                  done
                  if [ "$count" -eq 0 ]; then
                    echo "no recipe files found under /recipes" >&2
                    exit 1
                  fi
              env:
                - name: RECOTEM_LOG_FORMAT
                  value: "json"
                - name: RECOTEM_SIGNING_KEYS
                  valueFrom:
                    secretKeyRef:
                      name: recotem-auth
                      key: RECOTEM_SIGNING_KEYS
              resources:
                requests:
                  cpu: "1"
                  memory: 2Gi
                limits:
                  cpu: "4"
                  memory: 8Gi
              volumeMounts:
                - name: recipes
                  mountPath: /recipes
                  readOnly: true
                - name: artifacts
                  mountPath: /artifacts
                  readOnly: false
                - name: tmp
                  mountPath: /tmp
                # /workspace is the Dockerfile WORKDIR and the process cwd.
                # readOnlyRootFilesystem=true forbids writes to the container root
                # filesystem, so this emptyDir provides a writable cwd for lock
                # files and any tooling that creates temp files relative to cwd.
                - name: workspace
                  mountPath: /workspace

          volumes:
            - name: recipes
              configMap:
                name: recotem-recipes
            - name: artifacts
              persistentVolumeClaim:
                claimName: recotem-artifacts
            - name: tmp
              emptyDir: {}
            - name: workspace
              emptyDir: {}
```

Set `concurrencyPolicy: Forbid` so overlapping runs skip rather than corrupt the artifact. Recotem's own file lock provides a secondary guard, but the K8s policy is cheaper.

Exit code mapping for `restartPolicy: OnFailure`:

| Code | Meaning | K8s action |
|------|---------|-----------|
| 0 | Success or skip (lock contended without `--fail-on-busy`) | Job completes |
| 2 | RecipeError | No retry (config bug; fix the ConfigMap) |
| 3 | DataSourceError | No retry typically (CSV/Parquet format error, missing required column, local-FS path not found, `sha256` mismatch on a non-HTTP path, SQL DSN refused by the SSRF guard — persistent) |
| 4 | TrainingError | Retry up to `backoffLimit` |
| 5 | ArtifactError | No retry (artifact corrupt / unverifiable; retrain). A malformed signing key exits 8, not 5 |
| 6 | LockContestedError (`--fail-on-busy` set) | Retry or let orchestrator route |
| 7 | HttpFetchError | Retry (transient HTTP/SSRF/timeout/sha256 mismatch/body cap on network fetch — `http://` / `https://` sources only) |
| 8 | Configuration error | No retry (missing or malformed signing keys, an unwritable or unauthenticated `output.path`, bad env; fix the Secret / ConfigMap) |
| 1 | Unexpected error | Retry |

Set `backoffLimit: 2` for production CronJobs to avoid runaway retry loops on persistent data issues — the bundled Helm CronJob template does *not* set `backoffLimit`, so add it via your values overlay (or on plain manifests). The bundled Helm CronJob does set `activeDeadlineSeconds: 3600` (1 h hard kill); raise it for slow Optuna budgets or data sources.

`concurrencyPolicy: Forbid` stops the CronJob overlapping *itself*, and only
that. It says nothing about any other process holding the same recipe's lock,
and the chart's own first-install procedure creates one — the bootstrap Job in
`helm/recotem/values.yaml` is `kubectl create job bootstrap-0 --from=cronjob/…`,
a second trainer on the same recipe and the same lock. An out-of-cluster cron,
a manual `recotem train`, or a second cluster sharing the artifact store are the
same shape.

When that happens with `failOnBusy: false` (the chart default), the losing run
does **not** fail. It logs `recipe_lock_contended_skipping` at INFO, exits 0,
and the Job is marked `Complete` with `succeeded: 1` — while the artifact it
was scheduled to produce is not written. Measured on a live cluster, with an
out-of-band trainer holding `/artifacts/<recipe>.recotem.lock`:

```console
$ kubectl -n recotem create job scheduled-run --from=cronjob/recotem-train
$ kubectl -n recotem get job scheduled-run \
    -o custom-columns='COND:.status.conditions[*].type,SUCCEEDED:.status.succeeded'
COND                          SUCCEEDED
SuccessCriteriaMet,Complete   1
$ kubectl -n recotem logs job/scheduled-run | tail -1
{"recipe": "slow_recipe", "event": "recipe_lock_contended_skipping", "level": "info", …}
# the artifact pointer is byte-for-byte what it was before the run
```

Alerting on Job success therefore cannot see a model going stale. Set
`failOnBusy: true` (which appends `--fail-on-busy`) so the losing run exits 6
and the Job fails, or alert on artifact `trained_at` rather than on Job status.
Setting `concurrencyPolicy: Allow` adds the CronJob's own overlapping runs to
the same silent-skip path.

## Deployment (serve)

```yaml
# examples/k8s/serve-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: recotem-serve
  namespace: recotem
  labels:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: serve
    app.kubernetes.io/version: "2.0.0"
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: recotem
      app.kubernetes.io/component: serve
  template:
    metadata:
      labels:
        app.kubernetes.io/name: recotem
        app.kubernetes.io/component: serve
    spec:
      serviceAccountName: recotem
      # terminationGracePeriodSeconds must be >= RECOTEM_DRAIN_SECONDS + 5.
      # Default RECOTEM_DRAIN_SECONDS=30, so 35 is the minimum recommended value.
      terminationGracePeriodSeconds: 35
      # Spread replicas across nodes for availability.
      # ScheduleAnyway, not DoNotSchedule: a hard hostname spread and a
      # ReadWriteOnce artifacts PVC are mutually exclusive and would leave the
      # second replica Pending forever.  Use ReadWriteMany and DoNotSchedule
      # when you need the spread to be a guarantee.
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: recotem
              app.kubernetes.io/component: serve

      # Pod-level security context — only fields valid at this scope.
      # See https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.31/#podsecuritycontext-v1-core
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        # Required by the Pod Security Standards "restricted" profile.
        seccompProfile:
          type: RuntimeDefault
        # fsGroup ensures mounted PVC files are owned by GID 1000 (appuser),
        # matching the Dockerfile USER.  Without this, PVC data may be
        # inaccessible when the volume's on-disk owner differs from runAsUser.
        fsGroup: 1000

      containers:
        - name: serve
          image: ghcr.io/codelibs/recotem:2.0.0
          imagePullPolicy: IfNotPresent
          command: ["recotem", "serve", "--recipes", "/recipes"]

          # Container-level security context — fields like
          # allowPrivilegeEscalation / readOnlyRootFilesystem / capabilities
          # are only valid here, not under spec.securityContext.
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL

          env:
            - name: RECOTEM_HOST
              value: "0.0.0.0"
            - name: RECOTEM_PORT
              value: "8080"
            - name: RECOTEM_WATCH_INTERVAL
              value: "10"
            - name: RECOTEM_LOG_FORMAT
              value: "json"
            - name: RECOTEM_ENV
              value: "production"
            - name: RECOTEM_DRAIN_SECONDS
              value: "30"
            # TrustedHostMiddleware defaults to "127.0.0.1,localhost".
            # External Service / Ingress traffic arrives with a different Host
            # header and will be rejected with HTTP 400. Set the hosts you
            # actually expose the API under (Service DNS, Ingress hostnames).
            # RECOTEM_ALLOWED_HOSTS REPLACES the default, it does not extend
            # it: `_split_csv_env` falls back to "127.0.0.1,localhost" only when
            # the value strips to empty. The probes below send Host: localhost,
            # so a list without `localhost` makes all three fail with HTTP 400 --
            # a correct TrustedHostMiddleware rejection, which is why the pod
            # logs nothing that points at the cause. Keep `localhost` first.
            # Example:
            #   - name: RECOTEM_ALLOWED_HOSTS
            #     value: "localhost,recotem.example.com,recotem-serve.recotem.svc.cluster.local"
            # API keys and signing keys from Secret.
            # The Secret data keys match the env var names so what the app
            # reads, what kubectl shows, and what the Secret stores are all
            # spelled identically.
            - name: RECOTEM_API_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-auth
                  key: RECOTEM_API_KEYS
            - name: RECOTEM_SIGNING_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-auth
                  key: RECOTEM_SIGNING_KEYS

          ports:
            - name: http
              containerPort: 8080
              protocol: TCP

          # Probes send Host: localhost, which passes TrustedHostMiddleware on
          # the DEFAULT allowlist (127.0.0.1,localhost) -- but not on a
          # RECOTEM_ALLOWED_HOSTS that omits it, because that variable replaces
          # the default rather than extending it. See the note on the env var
          # above; the Helm chart prepends `localhost` for you, a hand-written
          # env var does not.
          # All three probes read artifact state through the SAME question as
          # readiness: "is at least one recipe loaded?".  A startupProbe is not
          # a gate that withholds traffic -- a failing one RESTARTS the
          # container.  Pointed at the strict, count-based /v1/health it turned
          # one untrained recipe into a restart loop for every NEW pod, so a
          # rolling update or an HPA scale-out could not converge while the
          # running replicas served happily.  /v1/health/ready still answers
          # 503 on a cold store (nothing loaded), which is what keeps the
          # first-install guarantee: serve does not enter the Service before
          # train has produced something.
          startupProbe:
            httpGet:
              path: /v1/health/ready
              port: http
              httpHeaders:
                - name: Host
                  value: localhost
            periodSeconds: 5
            failureThreshold: 60

          readinessProbe:
            httpGet:
              path: /v1/health/ready
              port: http
              httpHeaders:
                - name: Host
                  value: localhost
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3

          livenessProbe:
            httpGet:
              path: /v1/health/live
              port: http
              httpHeaders:
                - name: Host
                  value: localhost
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 10
            failureThreshold: 3

          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "2"
              memory: 4Gi

          volumeMounts:
            - name: recipes
              mountPath: /recipes
              readOnly: true
            - name: artifacts
              mountPath: /artifacts
              readOnly: true
            - name: tmp
              mountPath: /tmp
            # /workspace is the Dockerfile WORKDIR and the process cwd.
            # readOnlyRootFilesystem=true forbids writes to the container root
            # filesystem, so this emptyDir provides a writable cwd.
            - name: workspace
              mountPath: /workspace

      volumes:
        - name: recipes
          configMap:
            name: recotem-recipes
        - name: artifacts
          persistentVolumeClaim:
            claimName: recotem-artifacts
        - name: tmp
          emptyDir: {}
        - name: workspace
          emptyDir: {}
```

The `terminationGracePeriodSeconds: 35` above is the minimum for this manifest,
which has no preStop hook: `RECOTEM_DRAIN_SECONDS` (30) plus a 5 s buffer. The
bundled Helm chart adds a 5 s `preStop` sleep (`preStopSleepSeconds`), so its
default is 5 + 30 + 5 = 40.

Note on multiple replicas: each pod holds its own in-memory copy of every model and runs its own watcher thread. This is intentional — there is no shared cache. Budget roughly **4.8× the artifact size** per recipe, not 1×: loading holds the file bytes and the payload slice of them at the same time, and the deserialized model on top. A 644.5 MiB artifact measured 3,292 MiB resident. So 10 recipes at the 512 MiB `RECOTEM_MAX_PAYLOAD_BYTES` default is on the order of 25 GiB per pod, and 10 recipes allowed to reach the 2 GiB `RECOTEM_MAX_ARTIFACT_BYTES` default is on the order of 96 GiB — before allocating replicas. See [Sizing `recotem serve` memory](../operations.md#sizing-recotem-serve-memory) for the measurements.

The chart's default `limits.memory: 4Gi` therefore covers **one** recipe whose artifact is up to roughly 800 MiB, or a handful of small ones — not ten models at the artifact caps. Raise the limit, lower `RECOTEM_MAX_PAYLOAD_BYTES`, or shard recipes across `serve` processes.

**If you lower a cap, lower it on `train` too.** The chart renders
`.Values.env` into the serve Deployment and `.Values.train.env` into the
train CronJob — two separate maps. `recotem train` compares the artifact it
has just written against the caps resolved in **its own** environment and
warns (`artifact_payload_exceeds_serve_cap`, naming the variable) when the
file is one that `serve` will refuse. Lower the cap on serve alone and the
train job is still on the default: it writes an over-cap artifact, exits 0
with no warning, and the next `serve` rollout refuses it with
`reason: size_cap` and never becomes ready. Set the same value in both
`env` and `train.env`.

### Pod security context

The Helm chart applies a hardened security context by default:

```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000
securityContext:                 # container-level
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
```

`readOnlyRootFilesystem: true` requires every writable path to be a tmpfs or
volume mount; the chart mounts an `emptyDir` at `/tmp`. Add similar mounts
if a plugin or fsspec backend writes elsewhere (e.g. GCS FUSE cache).

### Rolling updates and warm-up

Each new pod re-fetches and HMAC-verifies every artifact at startup before
the readinessProbe passes (default `initialDelaySeconds: 10`). With many
recipes or large artifacts, increase `initialDelaySeconds` and tune
`maxSurge` / `maxUnavailable` so the rollout does not run below the
desired-replica count. The watcher polls on a shared interval inside each
pod — when `train` writes a new artifact, **all** replicas pick it up
within `RECOTEM_WATCH_INTERVAL` seconds; no rollout is needed for hot-swap.

### Secret rotation

Changing data in the `recotem-auth` Secret does **not** trigger a pod
rollout — the env vars are evaluated once at process start. After rotating
either key, run:

```bash
kubectl rollout restart deployment/recotem-serve -n recotem
```

Use the multi-kid pattern from `docs/operations.md` to keep both old and
new keys active during the rollout window.

## Service

```yaml
# examples/k8s/serve-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: recotem-serve
  namespace: recotem
  labels:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: serve
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: serve
  ports:
    - name: http
      port: 8080
      targetPort: http
      protocol: TCP
```

Expose externally via an Ingress or a LoadBalancer. Do not expose the pod port directly without a TLS-terminating proxy in front.

> ⚠️ **`RECOTEM_ALLOWED_HOSTS` and Ingress.** TrustedHostMiddleware defaults
> to `127.0.0.1,localhost` when `RECOTEM_ALLOWED_HOSTS` is empty — that is
> just enough for the in-pod liveness/readiness probes (which use a
> `Host: localhost` header). Any request reaching the pod under a different
> hostname — typically the Ingress host — will return **400 Bad Request**.
>
> The bundled Helm chart (`helm/recotem/templates/deployment.yaml`)
> auto-derives `RECOTEM_ALLOWED_HOSTS` from `ingress.hosts[*].host` when
> `ingress.enabled=true`, and prepends `localhost` to whatever list it
> renders — including an explicit `env.RECOTEM_ALLOWED_HOSTS` override.
>
> **If you write the env var yourself, outside the chart, `localhost` is
> yours to include.** The three probes send `Host: localhost`, so a list
> without it makes every readiness and liveness check return 400 and the
> Deployment never becomes ready — it CrashLoops with no clue in the
> application log, because a 400 from `TrustedHostMiddleware` looks like a
> normal rejected request:
>
> ```yaml
> - name: RECOTEM_ALLOWED_HOSTS
>   value: "localhost,api.example.com,api-internal.svc.cluster.local"
> ```

## Recipe delivery patterns

### ConfigMap (static recipes)

Best for recipes that change infrequently. Update the ConfigMap and roll the Deployment.

```bash
kubectl create configmap recotem-recipes \
  --from-file=./recipes/my_recipe.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

After updating the ConfigMap, restart the Deployment to pick up new recipe files:

```bash
kubectl rollout restart deployment/recotem-serve
```

### PVC

Mount a `ReadWriteMany` PVC (e.g. NFS, EFS, GCS FUSE) to both the CronJob and the Deployment. New recipe files are picked up by the watcher at the next poll interval — no restart needed.

If the PVC does not support `ReadWriteMany`, use `ReadWriteOnce` for the Deployment and accept that you cannot mount it to the CronJob simultaneously. In that case, write artifacts to object storage instead (see below).

#### A network-filesystem outage stalls `train`, and says nothing

Serve and train do not degrade the same way when the file server behind an RWX
PVC stops answering. Measured on a live 3-node cluster with an NFS-backed RWX
PVC, by scaling the NFS server to zero replicas mid-run:

| | what happens | what the operator sees |
|---|---|---|
| `serve`, already running | keeps answering `:recommend` (10/10 `200`), stays `1/1` Ready, 0 restarts, 2–3 millicores | `artifact_stat_timeout` (WARN, per recipe, one scan every ~20 s), then `artifact_stat_failed` naming `OSError [Errno 116] Stale file handle` |
| `serve`, new pod | never starts | `FailedMount ... exit status 32` on the pod; the rollout stalls |
| `train`, mid-run | **blocks in the artifact write for as long as the outage lasts** — measured 23 min 19 s at 1 millicore — then fails | nothing at all while blocked: the last log line is `final_model_trained`, no error, no progress. On recovery, `exit 1` |

The asymmetry is deliberate on one side only. The watcher stats artifacts on a
worker thread with a wall-clock timeout and reports the ones that hang
(`src/recotem/serving/watcher.py`), so a wedged mount costs the scan loop a
timeout rather than the process. The artifact write
(`src/recotem/artifact/io.py`) is a plain `makedirs` → `mkstemp` → `write` →
`fsync` → `os.replace`; on a hard NFS mount whose server is gone every one of
those blocks in the kernel, uninterruptibly, for as long as the server stays
away.

**And the run does not simply resume when storage comes back.** With the file
server restored, the blocked `os.makedirs(dest_dir, exist_ok=True)` returned by
raising, and the run ended like this:

```console
Training failed: [Errno 17] File exists: '/artifacts'
RECOTEM_EXIT=1
```
```json
{"code": "internal_error", "exit_code": 1,
 "error": "[Errno 17] File exists: '/artifacts'", "event": "train_error"}
```

`exist_ok=True` suppresses `FileExistsError` only when the `isdir` check that
follows it succeeds; against a mount that has just come back that check does
not, so the error is re-raised. It is then unmapped, so it lands on **exit 1**,
which the table above calls "Unexpected error — Retry". Retrying is the right
action, but the message an operator is left holding is a `FileExistsError` on a
directory that plainly exists, with a traceback through `recotem/artifact/io.py`
and nothing naming the file server.

Consequences on the shipped chart:

* Nothing in the process ends the stall. The chart's
  `activeDeadlineSeconds: 3600` on the train Job is the only bound, so an
  outage longer than that costs the run its whole slot.
* It is then killed as `DeadlineExceeded` — `Job was active longer than
  specified deadline` — which names the deadline, not the storage. Nothing in
  the Job's status or events mentions the file server.
* With `concurrencyPolicy: Forbid` (the chart default) that one stalled run
  suppresses every scheduled run behind it for the same window, each skipped
  with `JobAlreadyActive`.
* The per-recipe lock is held for the whole stall, on a file the process can no
  longer reach.

If your artifact store is a network filesystem, either mount it `soft` with a
bounded `timeo`/`retrans` so the write fails instead of parking (accepting that
a soft mount can surface a short write as an error), lower
`activeDeadlineSeconds` to something you are willing to wait, or put artifacts
in object storage (next section), where a stalled request fails on the HTTP
timeout instead of in the kernel.

### Object storage (S3 / GCS)

Set `output.path` in the recipe to an `s3://` or `gs://` URI. The CronJob and Deployment need no shared volume; they access the artifact directly via fsspec.

```yaml
output:
  path: s3://my-bucket/artifacts/my_recipe.recotem
  versioning: append_sha
```

The Deployment needs IAM access to read from the bucket. Use IRSA (EKS) or Workload Identity (GKE):

```yaml
serviceAccountName: recotem-serve-sa   # annotated with IAM role ARN / GCP SA
```

Recipes themselves can also live in object storage; mount them via an init container or reference them by URL in a wrapper script.

> ⚠️ **Per-recipe lock is host-local.** Recotem's `<output.path>.lock` uses POSIX `flock` and only coordinates writers on the same host. With an `s3://` or `gs://` `output.path` the lock file is created at a stable host-local path under `$RECOTEM_LOCK_DIR` (or `<tempdir>/recotem-locks/<sha256-of-output-path>.lock`) and **does not prevent concurrent writes from a second pod**. Rely on the scheduler for single-writer guarantees:
>
> - The bundled CronJob sets `concurrencyPolicy: Forbid` (default in `values.yaml`); keep it.
> - When triggering training from outside Kubernetes (Argo Workflows, Airflow, custom controllers), enforce parallelism = 1 there (Argo `synchronization.mutex`, Airflow `max_active_runs=1`, etc.).
> - `recotem train --fail-on-busy` only helps for *same-host* contention; do not depend on it for cross-pod safety with object storage outputs.
>
> Recotem logs `recipe_lock_local_only` at WARNING on the first occurrence per lock path; subsequent occurrences for the same path are logged at DEBUG.

## Helm chart values

The Helm chart in `helm/recotem/` provides a `serve` Deployment, optional
`CronJob` template, `NetworkPolicy`, `PodDisruptionBudget`, `ServiceAccount`,
and optional `HorizontalPodAutoscaler`.

Key values (excerpt from `helm/recotem/values.yaml`):

```yaml
image:
  repository: ghcr.io/codelibs/recotem
  tag: "2.0.0"
  pullPolicy: IfNotPresent

# serve Deployment
replicaCount: 2

resources:
  requests:
    cpu: 250m
    memory: 512Mi
  limits:
    cpu: "2"
    memory: 4Gi

# train CronJob (disabled by default — set enabled: true to schedule it)
train:
  enabled: false
  schedule: "0 2 * * *"
  concurrencyPolicy: Forbid
  failOnBusy: false

# Reference an existing Kubernetes Secret containing both
#   RECOTEM_SIGNING_KEYS and RECOTEM_API_KEYS as data keys.
secrets:
  secretName: recotem-auth

recipes:
  mountPath: /recipes
  source: configMap   # configMap | pvc | objectStore
  configMap:
    name: recotem-recipes
    managed: false    # set true to let the chart manage the ConfigMap from .data
    data: {}
  pvc:
    claimName: recotem-recipes
    readOnly: true
  objectStore:
    initContainer: {} # provide a sync init container spec

networkPolicy:
  enabled: true
  # ingressFromPodSelector restricts which pods may reach recotem-serve.
  # Empty map ({}) renders no podSelector-based rule.  Set a label selector
  # to allow specific scrapers or ingress controllers:
  #   ingressFromPodSelector:
  #     app.kubernetes.io/name: ingress-nginx
  ingressFromPodSelector: {}

  # allowKubeletProbes adds a rule opening service.targetPort with no `from:`
  # — which in NetworkPolicy means ALL sources, not none.  Required because
  # kubelet probes originate from the node network and no podSelector can
  # match them.  See the warning below before turning this off.
  allowKubeletProbes: true

  # Restrict the probe rule to specific node CIDRs instead of all sources.
  kubeletCIDRs: []
  #   - "10.0.0.0/8"

  # extraEgress appends rules to the policy.  The built-in egress rules cover
  # 53, 443 and 8080 only, and the policy selects the train CronJob's pods as
  # well — add the ports your data sources use.  See the warning below.
  extraEgress: []

hpa:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

When `hpa.enabled: true` the chart omits `spec.replicas` from the Deployment
and `replicaCount` is ignored. That is deliberate: with the field rendered,
every `helm upgrade` re-applies `replicaCount` and snaps a fleet the HPA had
scaled out back down until the HPA reacts — an availability dip on each
release. Set the floor with `hpa.minReplicas` instead.

### PodDisruptionBudget covers serve only

The serve pods carry `app.kubernetes.io/component: serve`, and the PDB
selects on it. That matters because a PDB's allowed-disruption count is
`currentHealthy - minAvailable` computed over **the pods its selector
matches** — so an unscoped selector lets a train CronJob pod count as
healthy:

| Serve replicas | Training running? | currentHealthy | `minAvailable: 1` allows |
|---|---|---|---|
| 1 | no  | 1 | 0 disruptions — serve is protected |
| 1 | yes | 2 | 1 disruption — a drain may evict the only serve pod |

The protection would otherwise lapse exactly while a training job happened to
be running, which is schedule-dependent and easy to miss.

The Service selector is deliberately **not** scoped the same way. Train pods
stay out of its Endpoints because `targetPort` is the *name* `http`, which
the train container does not declare — narrowing the selector instead would
empty the Endpoints for the length of the rollout that adds the matching pod
label. If you add a port named `http` to the train container, revisit this.

### NetworkPolicy: the defaults are not deny-all inbound

> ⚠️ **With chart defaults, inbound to port 8080 is open to every source.**
> `ingressFromPodSelector: {}` on its own would render no ingress rule, but
> the default `allowKubeletProbes: true` renders a rule with **no `from:`
> field**, and in the Kubernetes NetworkPolicy API an ingress rule with no
> `from:` matches **all** sources — the opposite of deny-all. Verify what you
> actually got:
>
> ```console
> $ kubectl get networkpolicy recotem -o jsonpath='{.spec.ingress}'
> [{"ports":[{"port":8080,"protocol":"TCP"}]}]
> ```
>
> That single rule, with `ports` but no `from`, is "any source may reach TCP
> 8080". The canonical deny-all-inbound form is `ingress: []` with
> `policyTypes` including `Ingress`.

`allowKubeletProbes` defaults to `true` for a reason: kubelet health checks
originate from the **node** network rather than from a pod, so no
`podSelector` rule can match them.

**What setting it to `false` actually does is worse than a failed rollout.**
With `ingressFromPodSelector` also empty the chart renders `ingress: []`, a
true deny-all-inbound. Measured on a live 3-node cluster whose CNI enforces
NetworkPolicy, three minutes after applying it:

| | observed |
|---|---|
| pods | `1/1 Ready`, `restartCount 0` |
| Service endpoints | `ready=true` for every replica |
| in-cluster client -> Service | connection timeout (`000`) |
| external client -> Ingress -> Service | connection timeout (`000`) |

Many CNIs — including kind's default — exempt node-originating traffic from
pod NetworkPolicies, so the probes keep passing. Kubernetes therefore reports
a perfectly healthy fleet while **100% of client traffic is blackholed**, and
no pod restart, no endpoint change and no event points at the cause. Whether
probes survive is CNI-specific; the loss of client traffic is not.

To narrow inbound while keeping probes working, do **not** set
`allowKubeletProbes: false` — instead list your node CIDRs, which converts
the probe rule from "any source" to an `ipBlock` match:

```yaml
networkPolicy:
  enabled: true
  ingressFromPodSelector:
    app.kubernetes.io/name: ingress-nginx   # who may call the API
  allowKubeletProbes: true
  kubeletCIDRs:                             # where probes may come from
    - "10.0.0.0/8"
```

Only set `allowKubeletProbes: false` when a separate NetworkPolicy already
admits **both** node-originating probe traffic and your clients — `ingress: []`
denies everything, and additive policies are the only way back. `kubeletCIDRs`
does not help here: the template reads it only while `allowKubeletProbes` is
`true`. Verify with a request, not with `kubectl get pods`; the pods will look
healthy either way.

Note that `policyTypes` always includes `Egress`, and the egress rules are
port-based only (53, 443, 8080) with no destination restriction. Add your own
policy if you need to constrain which hosts the pod may reach.

### NetworkPolicy: egress also gates the train CronJob

> ⚠️ **The policy selects the train pods too, and its egress rules do not
> cover SQL or plain HTTP.** `podSelector` matches on
> `app.kubernetes.io/name` + `app.kubernetes.io/instance`, which the train
> CronJob's pods carry as well as the serve pods. The built-in egress rules
> are serve-shaped — DNS plus HTTPS for object storage — so with chart
> defaults a training run whose recipe uses `source.type: sql`, or a
> plain-`http://` `source.path`, is dropped by this policy. There is no
> NetworkPolicy-shaped error: the job just times out connecting, which sends
> you looking at the database instead of at the policy.

Ports the built-in rules **do not** open, and that you must add for the
matching data source:

| Port | Protocol | Needed by |
|------|----------|-----------|
| 5432 | TCP | `source.type: sql` against PostgreSQL |
| 3306 | TCP | `source.type: sql` against MySQL / MariaDB |
| 1433 | TCP | `source.type: sql` against SQL Server |
| 80   | TCP | `source.path` on plain `http://` |

BigQuery, and object-store paths (`s3://`, `gs://`, `az://`) and `https://`
URLs, already work: they go over 443.

Use `networkPolicy.extraEgress` to append rules; entries are the Kubernetes
`NetworkPolicyEgressRule` schema and are emitted verbatim:

```yaml
networkPolicy:
  enabled: true
  extraEgress:
    - to:
        - ipBlock:
            cidr: "10.0.0.0/8"     # the subnet your database lives on
      ports:
        - port: 5432
          protocol: TCP
```

Because serve and train share the one policy, anything opened here is
reachable from the serve pods as well — scope each rule with `to:` when that
matters. Confirm what you got:

```console
$ kubectl get networkpolicy recotem -o jsonpath='{.spec.egress}'
```

Create the auth Secret before installing the chart, e.g.:

```bash
kubectl create secret generic recotem-auth \
  --from-literal=RECOTEM_SIGNING_KEYS='prod-2026-q2:<hex64>' \
  --from-literal=RECOTEM_API_KEYS='client-a:sha256:<hex64>'
```

Render and inspect before applying:

```bash
helm template recotem ./helm/recotem -f values-prod.yaml | less
helm upgrade --install recotem ./helm/recotem -f values-prod.yaml -n recotem
```
