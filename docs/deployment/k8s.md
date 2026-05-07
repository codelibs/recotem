# Kubernetes Deployment

## Overview

Two Kubernetes objects cover the Recotem 2.0 lifecycle:

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
              image: ghcr.io/codelibs/recotem:2
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
                      name: recotem-secrets
                      key: signing-keys
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

Set `backoffLimit: 2` for production CronJobs to avoid runaway retry loops on persistent data issues.

## Deployment (serve)

```yaml
# examples/k8s/serve-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: recotem-serve
spec:
  replicas: 2
  selector:
    matchLabels:
      app: recotem-serve
  template:
    metadata:
      labels:
        app: recotem-serve
    spec:
      containers:
        - name: serve
          image: ghcr.io/codelibs/recotem:2
          command: ["recotem", "serve", "--recipes", "/recipes/"]
          ports:
            - containerPort: 8000
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
              value: "8000"
            - name: RECOTEM_LOG_FORMAT
              value: "json"
            - name: RECOTEM_WATCH_INTERVAL
              value: "30"
            - name: RECOTEM_SIGNING_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-secrets
                  key: signing-keys
            - name: RECOTEM_API_KEYS
              valueFrom:
                secretKeyRef:
                  name: recotem-secrets
                  key: api-keys
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 30
      volumes:
        - name: recipes
          configMap:
            name: recotem-recipes
        - name: artifacts
          persistentVolumeClaim:
            claimName: recotem-artifacts
```

Note on multiple replicas: each pod holds its own in-memory copy of every model and runs its own watcher thread. This is intentional — there is no shared cache. With 2 GiB max artifact size and 10 recipes, plan for up to 20 GiB per pod before allocating replicas.

## Service

```yaml
# examples/k8s/serve-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: recotem-serve
spec:
  selector:
    app: recotem-serve
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

Expose externally via an Ingress or a LoadBalancer. Do not expose the pod port directly without a TLS-terminating proxy in front.

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

## Helm chart values

The Helm chart in `helm/recotem/` provides a `serve` Deployment, `CronJob` template, `NetworkPolicy`, `PDB`, and optional `HPA`.

Key values:

```yaml
image:
  repository: ghcr.io/codelibs/recotem
  tag: "2"

serve:
  replicaCount: 2
  resources:
    requests:
      memory: "4Gi"
      cpu: "500m"
    limits:
      memory: "8Gi"

train:
  schedule: "0 3 * * *"
  concurrencyPolicy: Forbid

secrets:
  signingKeys: ""     # set via --set or external secrets operator
  apiKeys: ""

recipes:
  source: configmap   # configmap | pvc | objectStore
  configmap:
    name: recotem-recipes
  pvc:
    claimName: recotem-recipes
  objectStore:
    bucket: s3://my-bucket/recipes/

networkPolicy:
  enabled: true
  ingressFrom: []     # restrict by namespace/pod selector

autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

Render and inspect before applying:

```bash
helm template recotem ./helm/recotem -f values-prod.yaml | less
helm upgrade --install recotem ./helm/recotem -f values-prod.yaml -n recotem
```
