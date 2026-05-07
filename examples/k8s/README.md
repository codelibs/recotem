# Kubernetes manifests

Standalone Kubernetes manifests for running Recotem 2.0. Use these as a
starting point if you don't want to use the Helm chart in
[`helm/recotem/`](../../helm/recotem/).

## Files

| File | Purpose |
|---|---|
| `serve-deployment.yaml` | Long-running `recotem serve` Deployment with health probes |
| `serve-service.yaml` | ClusterIP Service in front of the Deployment |
| `cronjob.yaml` | Daily `recotem train` CronJob writing into the shared PVC |

These three together form a minimal production-style topology: train runs
on a schedule, writes artifacts to a shared PVC, and the long-running serve
process hot-swaps when it detects new files.

## Prerequisites

- A namespace called `recotem` (or edit `metadata.namespace` in each file).
- A `PersistentVolumeClaim` named `recotem-artifacts` accessible from both
  the train and serve pods. With `ReadWriteMany` access mode if they will
  run on different nodes; otherwise `ReadWriteOnce` works as long as both
  schedule on the same node.
- A `ConfigMap` (or another mechanism) supplying recipe YAML files at
  `/recipes/`.
- A `Secret` containing `RECOTEM_SIGNING_KEYS` and `RECOTEM_API_KEYS`,
  mounted as env vars on both pods.

## Apply

```bash
kubectl apply -f examples/k8s/serve-deployment.yaml
kubectl apply -f examples/k8s/serve-service.yaml
kubectl apply -f examples/k8s/cronjob.yaml
```

Verify:

```bash
kubectl -n recotem get pods,svc,cronjob
kubectl -n recotem logs -l app.kubernetes.io/component=serve --tail=20
```

## Helm alternative

For a single-command install with templated values (replicas, resources,
PDB, NetworkPolicy, ServiceAccount), prefer the Helm chart:

```bash
helm install recotem ./helm/recotem -f my-values.yaml
```

Set `train.enabled=true` in your values file to enable the chart-managed
CronJob equivalent of `cronjob.yaml`.

## Production checklist

- [ ] Replace `latest` image tag with a pinned version (e.g. `2.0.0`).
- [ ] Configure resource requests / limits sized for your dataset.
- [ ] Add a NetworkPolicy restricting egress to only the data sources and
      object-store endpoints you need.
- [ ] Pipe pod logs to a log aggregator that respects structlog JSON fields.
- [ ] Wire the Service behind an Ingress / LoadBalancer with TLS.
- [ ] Read [docs/operations.md](../../docs/operations.md) for the signing-key
      rotation runbook before going live.
