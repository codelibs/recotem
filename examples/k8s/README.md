# Kubernetes manifests

Standalone Kubernetes manifests for running Recotem. Use these as a
starting point if you don't want to use the Helm chart in
[`helm/recotem/`](../../helm/recotem/).

## Files

| File | Purpose |
|---|---|
| `00-serviceaccount.yaml` | ServiceAccount `recotem`, referenced by all three pod specs |
| `bootstrap-job.yaml` | One-shot `recotem train` Job that seeds the first artifact |
| `serve-deployment.yaml` | Long-running `recotem serve` Deployment with health probes |
| `serve-service.yaml` | ClusterIP Service in front of the Deployment |
| `cronjob.yaml` | Daily `recotem train` CronJob writing into the shared PVC |

Together these form a minimal production-style topology: a bootstrap Job
seeds the artifact store, train then runs on a schedule, writes artifacts to
a shared PVC, and the long-running serve process hot-swaps when it detects
new files.

`00-serviceaccount.yaml` is not optional. `serve-deployment.yaml`,
`cronjob.yaml` and `bootstrap-job.yaml` all set
`serviceAccountName: recotem`, and Kubernetes refuses to create a pod whose
ServiceAccount does not exist (`error looking up service account
recotem/recotem`) — the Deployment would sit at zero ready replicas forever.
The `00-` prefix keeps it first in the lexical order `kubectl apply -f <dir>`
uses, so the workloads never race ahead of it.

## Prerequisites

- A namespace called `recotem` (or edit `metadata.namespace` in each file).
- A `PersistentVolumeClaim` named `recotem-artifacts` accessible from both
  the train and serve pods. With `ReadWriteMany` access mode if they will
  run on different nodes; otherwise `ReadWriteOnce` works as long as both
  schedule on the same node.
- A `ConfigMap` named `recotem-recipes` supplying recipe YAML files at
  `/recipes/`. Both `serve-deployment.yaml` and `cronjob.yaml` mount this
  ConfigMap, so it must exist before applying the manifests. Create it with:

  ```bash
  # Single recipe
  kubectl -n recotem create configmap recotem-recipes \
    --from-file=recipe.yaml=path/to/recipe.yaml

  # Multiple recipes (repeat --from-file for each)
  kubectl -n recotem create configmap recotem-recipes \
    --from-file=news_articles.yaml=path/to/news_articles.yaml \
    --from-file=purchase_log.yaml=path/to/purchase_log.yaml
  ```

  To update the ConfigMap after adding or changing a recipe, delete and
  recreate it (or use `kubectl create configmap ... --dry-run=client -o yaml | kubectl apply -f -`).
  The running serve pod will pick up new artifacts written by the next train
  CronJob run, but recipe YAML changes require a pod restart (or a rolling
  update).
- A `Secret` named `recotem-auth` containing `RECOTEM_SIGNING_KEYS` and
  `RECOTEM_API_KEYS`, mounted as env vars on both pods.

The `ServiceAccount` is **not** in this list — `00-serviceaccount.yaml` ships
with these manifests and is applied along with them.

## Apply

```bash
kubectl apply -f examples/k8s/
```

Or file by file, in dependency order:

```bash
kubectl apply -f examples/k8s/00-serviceaccount.yaml
kubectl apply -f examples/k8s/bootstrap-job.yaml     # seeds the first artifact
kubectl apply -f examples/k8s/serve-deployment.yaml
kubectl apply -f examples/k8s/serve-service.yaml
kubectl apply -f examples/k8s/cronjob.yaml
```

Verify:

```bash
kubectl -n recotem wait --for=condition=complete job/recotem-bootstrap --timeout=30m
kubectl -n recotem get pods,svc,cronjob,job
kubectl -n recotem logs -l app.kubernetes.io/component=serve --tail=20
```

## First install: seed an artifact before serve can go Ready

`recotem serve` loads one artifact per recipe at startup and answers
`/v1/health` with **503** and `{"status":"degraded","total":N,"loaded":0}`
while any recipe has no artifact. On a brand-new install the artifacts PVC is
empty, so:

- every serve pod fails its probes and is restarted, and
- `cronjob.yaml` will not produce the first artifact until its next scheduled
  run (`0 2 * * *` — up to a day away).

`bootstrap-job.yaml` exists to close that gap: it trains every recipe once,
immediately, so the store is seeded within minutes of the first apply.

Because `kubectl apply -f examples/k8s/` creates the bootstrap Job and the
Deployment at the same time, the serve pods stay NotReady while training runs
— you will see `Readiness probe failed: HTTP probe failed with statuscode:
503` — and if training outlasts the liveness threshold (~2 min) they will also
restart. **Both are expected and self-healing** — no manual step is needed.
Once the artifact lands, the next probe succeeds and the pods go Ready:

```bash
# Watch the bootstrap Job produce the first artifact.
kubectl -n recotem logs -f job/recotem-bootstrap

# Serve becomes Ready shortly after the Job completes.
kubectl -n recotem rollout status deployment/recotem-serve --timeout=10m
```

If you would rather never see a restarting pod, apply the bootstrap Job
first, wait for it, and only then create the Deployment:

```bash
kubectl apply -f examples/k8s/00-serviceaccount.yaml
kubectl apply -f examples/k8s/bootstrap-job.yaml
kubectl -n recotem wait --for=condition=complete job/recotem-bootstrap --timeout=30m
kubectl apply -f examples/k8s/serve-deployment.yaml
kubectl apply -f examples/k8s/serve-service.yaml
kubectl apply -f examples/k8s/cronjob.yaml
```

The Job is left in place after it completes, so re-running
`kubectl apply -f examples/k8s/` is a no-op rather than a surprise retrain.
To seed again — after adding a recipe, say — delete and re-apply it:

```bash
kubectl -n recotem delete job recotem-bootstrap
kubectl apply -f examples/k8s/bootstrap-job.yaml
```

Serve pods still reporting `degraded` after the Job succeeds usually means the
recipe's `output.path` does not resolve to the shared PVC mount
(`/artifacts`), or the serve pod's `RECOTEM_SIGNING_KEYS` has no kid matching
the one the artifact was signed with. Check with:

```bash
kubectl -n recotem exec deploy/recotem-serve -- ls -la /artifacts
kubectl -n recotem logs -l app.kubernetes.io/component=serve --tail=50
```

## Helm alternative

For a single-command install with templated values (replicas, resources,
PDB, NetworkPolicy, ServiceAccount), prefer the Helm chart:

```bash
helm install recotem ./helm/recotem -f my-values.yaml
```

Set `train.enabled=true` in your values file to enable the chart-managed
CronJob equivalent of `cronjob.yaml`.

The chart has no bootstrap Job — the first-artifact problem described above
applies to it too. See
[docs/deployment/k8s.md](../../docs/deployment/k8s.md#first-install-seed-an-artifact-before-serve-starts)
for the Helm bootstrap sequence.

## Production checklist

- [ ] Replace `latest` image tag with a pinned version (e.g. `2.0.0`).
- [ ] Configure resource requests / limits sized for your dataset.
- [ ] Add a NetworkPolicy restricting egress to only the data sources and
      object-store endpoints you need.
- [ ] Pipe pod logs to a log aggregator that respects structlog JSON fields.
- [ ] Wire the Service behind an Ingress / LoadBalancer with TLS.
- [ ] Read [docs/operations.md](../../docs/operations.md) for the signing-key
      rotation runbook before going live.
