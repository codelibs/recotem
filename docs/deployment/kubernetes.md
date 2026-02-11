# Kubernetes Deployment Guide

## Prerequisites

- Kubernetes cluster (1.26+)
- Helm 3.12+
- kubectl configured
- Container images pushed to a registry
- External PostgreSQL and Redis endpoints (managed service or self-managed)

## Quick Start

1. Build and push images:

```bash
docker build -t your-registry/recotem-backend:latest -f backend/Dockerfile backend/
docker build -t your-registry/recotem-inference:latest -f inference/Dockerfile inference/
docker build -t your-registry/recotem-proxy:latest -f proxy.dockerfile .
docker push your-registry/recotem-backend:latest
docker push your-registry/recotem-inference:latest
docker push your-registry/recotem-proxy:latest
```

2. Install with Helm:

```bash
helm install recotem ./helm/recotem \
  --set image.backend.repository=your-registry/recotem-backend \
  --set image.inference.repository=your-registry/recotem-inference \
  --set image.proxy.repository=your-registry/recotem-proxy \
  --set postgresql.external=true \
  --set redis.external=true \
  --set redis.host=your-redis-host \
  --set secrets.secretKey="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
  --set secrets.databaseUrl="postgresql://user:pass@host:5432/recotem" \
  --set secrets.defaultAdminPassword="your-admin-password" \
  --set config.allowedHosts="recotem.example.com"
```

> The Helm chart does not provision PostgreSQL/Redis StatefulSets; provide external endpoints via `secrets.databaseUrl` and `redis.host`.

## External Dependencies

The Helm chart expects external PostgreSQL and Redis. Use managed services:

### AWS (EKS)

- **Database**: Amazon RDS for PostgreSQL
- **Cache**: Amazon ElastiCache for Redis
- **Storage**: EBS volumes (default StorageClass) or EFS for shared access
- **Ingress**: AWS Load Balancer Controller

### GCP (GKE)

- **Database**: Cloud SQL for PostgreSQL
- **Cache**: Memorystore for Redis
- **Storage**: Persistent Disk (default StorageClass)
- **Ingress**: GKE Ingress or nginx-ingress

## Configuration

### values.yaml overrides

Create a `values-myenv.yaml`:

```yaml
image:
  backend:
    repository: your-registry/recotem-backend
    tag: "v1.0.0"
  inference:
    repository: your-registry/recotem-inference
    tag: "v1.0.0"
  proxy:
    repository: your-registry/recotem-proxy
    tag: "v1.0.0"

postgresql:
  external: true
  host: "your-rds-endpoint.amazonaws.com"
  port: 5432
  database: recotem
  username: recotem_user

redis:
  external: true
  host: "your-elasticache-endpoint.amazonaws.com"
  port: 6379

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: recotem.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: recotem-tls
      hosts:
        - recotem.example.com

backend:
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 5

worker:
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10

inference:
  replicaCount: 2
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilization: 60
    targetMemoryUtilization: 70
```

Deploy:

```bash
helm install recotem ./helm/recotem \
  -f values-myenv.yaml \
  --set secrets.secretKey="$SECRET_KEY" \
  --set secrets.databaseUrl="$DATABASE_URL" \
  --set secrets.defaultAdminPassword="$ADMIN_PASSWORD"
```

## TLS with cert-manager

Install cert-manager and create a ClusterIssuer:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
```

```yaml
# cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
```

Then in your values file:

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: recotem.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: recotem-tls
      hosts:
        - recotem.example.com
```

## S3 Storage Configuration

For storing training data and trained models in S3 (or S3-compatible storage like MinIO):

```yaml
# values-s3.yaml
config:
  storageType: "S3"

secrets:
  awsAccessKeyId: "AKIAEXAMPLE"
  awsSecretAccessKey: "your-secret-key"

extraEnv:
  AWS_STORAGE_BUCKET_NAME: "recotem-data"
  AWS_S3_ENDPOINT_URL: ""  # Leave empty for AWS S3, set for MinIO/Ceph
  AWS_LOCATION: ""  # Optional prefix within the bucket
```

For MinIO deployed in-cluster:

```yaml
extraEnv:
  AWS_STORAGE_BUCKET_NAME: "recotem-data"
  AWS_S3_ENDPOINT_URL: "http://minio.storage.svc.cluster.local:9000"
```

## HPA Tuning Guide

### Backend HPA

The backend handles HTTP/WebSocket requests. Scale based on CPU:

```yaml
backend:
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 5
    targetCPUUtilizationPercentage: 70
```

### Worker HPA

Workers handle CPU-intensive tuning/training. Scale based on Celery queue length or CPU:

```yaml
worker:
  autoscaling:
    enabled: true
    minReplicas: 1
    maxReplicas: 10
    targetCPUUtilizationPercentage: 80
```

For queue-based scaling with KEDA:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: recotem-worker
spec:
  scaleTargetRef:
    name: recotem-worker
  minReplicaCount: 1
  maxReplicaCount: 10
  triggers:
    - type: redis
      metadata:
        address: your-redis-host:6379
        listName: celery
        listLength: "5"
```

### Inference HPA

The inference service handles prediction requests. Scale based on CPU and memory (model loading is memory-intensive):

```yaml
inference:
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilization: 60
    targetMemoryUtilization: 70
```

Each inference replica independently loads models into memory and subscribes to Redis Pub/Sub for hot-swap events. Replicas are distributed across nodes via `podAntiAffinity`.

### Resource Recommendations

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-----------|----------|---------------|-------------|
| Backend | 250m | 1000m | 512Mi | 2Gi |
| Worker | 500m | 2000m | 1Gi | 4Gi |
| Inference | 500m | 2000m | 1Gi | 4Gi |
| Beat | 100m | 500m | 128Mi | 512Mi |
| Proxy | 100m | 500m | 64Mi | 256Mi |

## Upgrade

```bash
helm upgrade recotem ./helm/recotem -f values-myenv.yaml
```

## Monitoring

Check pod status:
```bash
kubectl get pods -l app.kubernetes.io/name=recotem
```

View logs:
```bash
kubectl logs -l app.kubernetes.io/component=backend -f
kubectl logs -l app.kubernetes.io/component=worker -f
```

## Troubleshooting

### Pods stuck in CrashLoopBackOff

**Backend pod fails to start:**
```bash
# Check logs for migration or configuration errors
kubectl logs -l app.kubernetes.io/component=backend -c migrate --previous
kubectl logs -l app.kubernetes.io/component=backend
```

Common causes:
- `DATABASE_URL` incorrect or database unreachable — verify the Secret and network connectivity
- `SECRET_KEY` missing or too short — must be at least 50 characters when `DEBUG=false`
- `ALLOWED_HOSTS` not set — must match your domain/ingress hostname

**Worker pod fails to start:**
```bash
kubectl logs -l app.kubernetes.io/component=worker
```

Common causes:
- Redis unreachable — check `CELERY_BROKER_URL` in the ConfigMap
- `REDIS_PASSWORD` set but not reflected in Redis configuration

### initContainer failures

```bash
# Check migrate initContainer
kubectl logs <pod-name> -c migrate

# Check collectstatic initContainer
kubectl logs <pod-name> -c collectstatic
```

If migrate fails, ensure the database exists and the user has permission to create/alter tables.

### Readiness/Liveness probe failures

**Backend:** Probes hit `/api/ping/` — if it returns non-200, check:
```bash
kubectl exec -it <backend-pod> -- curl -f http://localhost:80/api/ping/
```

**Worker:** Probes run `celery inspect ping` — if it times out:
```bash
kubectl exec -it <worker-pod> -- celery -A recotem inspect ping --timeout 10
```
Long-running tasks can prevent the worker from responding to inspect commands. The livenessProbe `periodSeconds` is set to 120s to accommodate this.

### PVC issues

If pods are stuck in `Pending` with PVC-related events:
```bash
kubectl get pvc -l app.kubernetes.io/name=recotem
kubectl describe pvc <pvc-name>
```

Common causes:
- No default StorageClass configured — set `persistence.data.storageClass` explicitly
- Insufficient capacity in the storage backend

### Ingress not routing traffic

```bash
kubectl get ingress -l app.kubernetes.io/name=recotem
kubectl describe ingress <ingress-name>
```

Verify:
- The ingress controller is installed and running
- TLS secret exists if `tls` is configured
- `config.allowedHosts` includes the ingress hostname

### Resource pressure

If pods are OOMKilled:
```bash
kubectl describe pod <pod-name> | grep -A5 "Last State"
```

Increase memory limits in `values.yaml`:
```yaml
worker:
  resources:
    limits:
      memory: 8Gi  # Increase for large datasets
```

## Helm Chart Validation

```bash
helm lint helm/recotem/
helm template recotem helm/recotem/ --debug
```
