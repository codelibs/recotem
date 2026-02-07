# Kubernetes Deployment Guide

## Prerequisites

- Kubernetes cluster (1.26+)
- Helm 3.12+
- kubectl configured
- Container images pushed to a registry

## Quick Start

1. Build and push images:

```bash
docker build -t your-registry/recotem-backend:latest -f backend/Dockerfile backend/
docker build -t your-registry/recotem-proxy:latest -f proxy.dockerfile .
docker push your-registry/recotem-backend:latest
docker push your-registry/recotem-proxy:latest
```

2. Install with Helm:

```bash
helm install recotem ./helm/recotem \
  --set image.backend.repository=your-registry/recotem-backend \
  --set image.proxy.repository=your-registry/recotem-proxy \
  --set secrets.secretKey="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
  --set secrets.databaseUrl="postgresql://user:pass@host:5432/recotem" \
  --set secrets.defaultAdminPassword="your-admin-password" \
  --set config.allowedHosts="recotem.example.com"
```

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
```

Deploy:

```bash
helm install recotem ./helm/recotem \
  -f values-myenv.yaml \
  --set secrets.secretKey="$SECRET_KEY" \
  --set secrets.databaseUrl="$DATABASE_URL" \
  --set secrets.defaultAdminPassword="$ADMIN_PASSWORD"
```

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

## Helm Chart Validation

```bash
helm lint helm/recotem/
helm template recotem helm/recotem/ --debug
```
