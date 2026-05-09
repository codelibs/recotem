# Kubernetes Deployment

## Overview

Two Kubernetes objects cover the Recotem lifecycle:

- **CronJob** — runs `recotem train` on a schedule.
- **Deployment** — runs `recotem serve` continuously, reading artifacts from a shared store.

Recipes can be delivered to both objects via ConfigMap (small, static recipes), PVC (read-write volume), or object storage (S3/GCS — recipes and artifacts both live remotely).

## CronJob (train)

```yaml
# examples/k8s/cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: recotem-train
spec:
  schedule: "0 3 * * *"
  concurrencyPolicy: Forbid          # skip if a previous run is still active
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: train
              image: ghcr.io/codelibs/recotem:2.0.0
              command: ["recotem", "train", "/recipes/my_recipe.yaml"]
              volumeMounts:
                - name: recipes
                  mountPath: /recipes
                  readOnly: true
                - name: artifacts
                  mountPath: /artifacts
              env:
                - name: RECOTEM_SIGNING_KEYS
                  valueFrom:
                    secretKeyRef:
                      name: recotem-auth
                      key: RECOTEM_SIGNING_KEYS
          volumes:
            - name: recipes
              configMap:
                name: recotem-recipes
            - name: artifacts
              persistentVolumeClaim:
                claimName: recotem-artifacts
```

Set `concurrencyPolicy: Forbid` so overlapping runs skip rather than corrupt the artifact. Recotem's own file lock provides a secondary guard, but the K8s policy is cheaper.

Exit code mapping for `restartPolicy: OnFailure`:

| Code | Meaning | K8s action |
|------|---------|-----------|
| 0 | Success or skip | Job completes |
| 2 | RecipeError | Retry (likely a config bug; fix the ConfigMap) |
| 3 | DataSourceError | Retry (transient network/auth) |
| 4 | TrainingError | Retry up to `backoffLimit` |
| 5 | ArtifactError | No retry (signing key config issue; fix Secret) |
| 1 | Unexpected | Retry |

Set `backoffLimit: 2` for production CronJobs to avoid runaway retry loops on persistent data issues. The bundled Helm CronJob also sets `activeDeadlineSeconds: 3600` (1 h hard kill); raise it for slow Optuna budgets or data sources.

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
  labels:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: serve
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
      # terminationGracePeriodSeconds >= RECOTEM_DRAIN_SECONDS + 5 (default 30+5=35)
      terminationGracePeriodSeconds: 35
      containers:
        - name: serve
          image: ghcr.io/codelibs/recotem:2.0.0
          command: ["recotem", "serve", "--recipes", "/recipes/"]
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: recipes
              mountPath: /recipes
              readOnly: true
            - name: artifacts
              mountPath: /artifacts
              readOnly: true
          env:
            - name: RECOTEM_HOST
              value: "0.0.0.0"
            - name: RECOTEM_PORT
              value: "8080"
            - name: RECOTEM_LOG_FORMAT
              value: "json"
            - name: RECOTEM_WATCH_INTERVAL
              value: "30"
            - name: RECOTEM_DRAIN_SECONDS
              value: "30"
            - name: RECOTEM_SIGNING_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-auth
                  key: RECOTEM_SIGNING_KEYS
            - name: RECOTEM_API_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-auth
                  key: RECOTEM_API_KEYS
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
              httpHeaders:
                - name: Host
                  value: localhost
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
              httpHeaders:
                - name: Host
                  value: localhost
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 10
            failureThreshold: 3
      volumes:
        - name: recipes
          configMap:
            name: recotem-recipes
        - name: artifacts
          persistentVolumeClaim:
            claimName: recotem-artifacts
```

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
spec:
  selector:
    app.kubernetes.io/name: recotem
    app.kubernetes.io/component: serve
  ports:
    - port: 80
      targetPort: 8080
  type: ClusterIP
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
> `ingress.enabled=true`. If you bypass the chart, expose the service
> under additional hostnames (internal Service DNS, custom LoadBalancer),
> or run `helm template` and inject the env yourself, set the env var
> explicitly:
>
> ```yaml
> - name: RECOTEM_ALLOWED_HOSTS
>   value: "api.example.com,api-internal.svc.cluster.local"
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

> ⚠️ **Per-recipe lock is host-local.** Recotem's `<output.path>.lock` uses POSIX `flock` and only coordinates writers on the same host. With an `s3://` or `gs://` `output.path` the lock file is created at a local path derived from the URI (e.g. `./s3:/my-bucket/...lock`) and **does not prevent concurrent writes from a second pod**. Rely on the scheduler for single-writer guarantees:
>
> - The bundled CronJob sets `concurrencyPolicy: Forbid` (default in `values.yaml`); keep it.
> - When triggering training from outside Kubernetes (Argo Workflows, Airflow, custom controllers), enforce parallelism = 1 there (Argo `synchronization.mutex`, Airflow `max_active_runs=1`, etc.).
> - `recotem train --fail-on-busy` only helps for *same-host* contention; do not depend on it for cross-pod safety with object storage outputs.
>
> Recotem logs `recipe_lock_local_only` on every remote-scheme run so this is visible at runtime.

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
  ingressFromPodSelector: {}   # restrict by pod-label selector

hpa:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
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
