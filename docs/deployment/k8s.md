# Kubernetes Deployment

## Overview

Two Kubernetes objects cover the Recotem lifecycle:

- **CronJob** — runs `recotem train` on a schedule.
- **Deployment** — runs `recotem serve` continuously, reading artifacts from a shared store.

Recipes can be delivered to both objects via ConfigMap (small, static recipes), PVC (read-write volume), or object storage (S3/GCS — recipes and artifacts both live remotely).

## First install: seed an artifact before serve starts

**Read this before your first `helm install` or `kubectl apply`.** Train and
serve are ordered: serve cannot start healthy until train has produced an
artifact for every recipe, and nothing in the chart or the example manifests
runs train for you at install time.

`recotem serve` loads one artifact per recipe at startup. Until every recipe
has one, `/v1/health` answers **503** with
`{"status":"degraded","total":1,"loaded":0}`. On an empty artifact store that
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

When `failOnBusy: false` (the chart default), a lock collision from
`concurrencyPolicy: Forbid` is impossible at the K8s layer, but if you set
`concurrencyPolicy: Allow` the in-process file lock will exit 0 on the
second invocation. The CronJob will be marked Succeeded — set
`failOnBusy: true` (which appends `--fail-on-busy`) if your alerting needs
to see overlapping runs.

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
          # Startup keeps the strict, count-based gate: a NEW pod does not
          # enter the Service until every recipe has an artifact.  Readiness
          # and liveness below deliberately do not -- adding an untrained
          # recipe to a running fleet must not take the loaded models offline,
          # and a restart cannot fix a missing artifact anyway.
          startupProbe:
            httpGet:
              path: /v1/health
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

Note on multiple replicas: each pod holds its own in-memory copy of every model and runs its own watcher thread. This is intentional — there is no shared cache. With 2 GiB max artifact size and 10 recipes, plan for up to 20 GiB per pod before allocating replicas.

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
`podSelector` rule can match them. Set it to `false` and readiness/liveness
probes are silently dropped, pods never become Ready, and the Deployment
never converges.

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

Only set `allowKubeletProbes: false` when a separate NetworkPolicy or CNI
mechanism already admits node-originating traffic; with it false and
`ingressFromPodSelector` empty, the chart does render a true `ingress: []`
deny-all.

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
