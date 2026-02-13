# AWS Deployment Guide

Deploy Recotem on AWS using ECS (Fargate) or EKS with managed services.

## Architecture

```
[Route 53] → [ALB (HTTPS)] → [ECS/EKS]
                                 ├── proxy (nginx + SPA)
                                 ├── backend (daphne)
                                 ├── inference (FastAPI)
                                 ├── worker (celery)
                                 └── beat (celery beat)
                                      ├── [RDS PostgreSQL]
                                      ├── [ElastiCache Redis]
                                      └── [S3 bucket]
```

## Managed Services Setup

### RDS PostgreSQL

1. Create a PostgreSQL 17 instance:
   - Instance class: `db.t4g.medium` or larger
   - Multi-AZ for production
   - Enable automated backups (7+ day retention)
   - Enable encryption at rest

2. Create database and user:
```sql
CREATE DATABASE recotem;
CREATE USER recotem_user WITH PASSWORD 'your-secure-password';
GRANT ALL PRIVILEGES ON DATABASE recotem TO recotem_user;
```

3. Note the endpoint for `DATABASE_URL`:
```
postgresql://recotem_user:password@your-rds-endpoint.rds.amazonaws.com:5432/recotem
```

### ElastiCache Redis

1. Create a Redis 7.x cluster:
   - Node type: `cache.t4g.medium` or larger
   - Enable encryption in-transit and at-rest
   - Enable AUTH token

2. Note the endpoint:
```
redis://:auth-token@your-cache-endpoint.cache.amazonaws.com:6379/0
```

Redis is used for four purposes (different databases):
- db 0: Celery broker
- db 1: Django Channels
- db 2: Django cache
- db 3: Model event Pub/Sub (inference hot-swap)

### S3 Storage (Optional)

For storing training data and trained models in S3:

1. Create an S3 bucket (e.g., `recotem-data-prod`)
2. Create an IAM policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::recotem-data-prod",
        "arn:aws:s3:::recotem-data-prod/*"
      ]
    }
  ]
}
```

## Option A: ECS (Fargate)

### Task Definitions

Create three task definitions:

**Backend Task:**
```json
{
  "family": "recotem-backend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [
    {
      "name": "backend",
      "image": "ghcr.io/codelibs/recotem-backend:latest",
      "portMappings": [{ "containerPort": 80 }],
      "command": ["daphne", "-b", "0.0.0.0", "-p", "80", "recotem.asgi:application"],
      "healthCheck": {
        "command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:80/api/ping/')\""],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/recotem-backend",
          "awslogs-region": "your-region",
          "awslogs-stream-prefix": "backend"
        }
      }
    }
  ]
}
```

**Worker Task:**
```json
{
  "family": "recotem-worker",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "containerDefinitions": [
    {
      "name": "worker",
      "image": "ghcr.io/codelibs/recotem-backend:latest",
      "command": ["celery", "-A", "recotem", "worker", "--loglevel=INFO", "--concurrency=2"],
      "stopTimeout": 120,
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/recotem-worker",
          "awslogs-region": "your-region",
          "awslogs-stream-prefix": "worker"
        }
      }
    }
  ]
}
```

**Proxy Task:**
```json
{
  "family": "recotem-proxy",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "containerDefinitions": [
    {
      "name": "proxy",
      "image": "ghcr.io/codelibs/recotem-proxy:latest",
      "portMappings": [{ "containerPort": 8000 }],
      "healthCheck": {
        "command": ["CMD-SHELL", "wget -qO- http://localhost:8000/api/ping/ || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      }
    }
  ]
}
```

### Services

Create ECS services with:
- **Backend**: Desired count 2, ALB target group on port 80
- **Worker**: Desired count 1-5 (auto-scaling based on CPU)
- **Proxy**: Desired count 2, ALB target group on port 8000

### ALB Configuration

- Listener: HTTPS (443) with ACM certificate
- Target groups routing:
  - `/api/*`, `/ws/*`, `/admin/*` → backend target group
  - `/*` → proxy target group
- Enable sticky sessions for WebSocket connections
- WebSocket idle timeout: 300s

### Secrets Manager

Store sensitive values in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name recotem/production \
  --secret-string '{
    "SECRET_KEY": "your-django-secret-key",
    "DATABASE_URL": "postgresql://user:pass@rds-endpoint:5432/recotem",
    "REDIS_PASSWORD": "your-redis-auth-token",
    "DEFAULT_ADMIN_PASSWORD": "your-admin-password"
  }'
```

Reference in task definitions using `secrets` in container definitions.

### Database Migration

Run migrations as a one-off ECS task before deploying:

```bash
aws ecs run-task \
  --cluster recotem \
  --task-definition recotem-backend \
  --overrides '{"containerOverrides":[{"name":"backend","command":["python","manage.py","migrate"]}]}' \
  --network-configuration '...'
```

## Option B: EKS

See the [Kubernetes Deployment Guide](kubernetes.md) with these AWS-specific additions:

### IAM Roles for Service Accounts (IRSA)

For S3 access without static credentials:

```bash
eksctl create iamserviceaccount \
  --name recotem-sa \
  --namespace default \
  --cluster your-cluster \
  --attach-policy-arn arn:aws:iam::123456789012:policy/RecotemS3Policy \
  --approve
```

Then in Helm values:
```yaml
serviceAccount:
  create: false
  name: recotem-sa

config:
  storageType: "S3"

extraEnv:
  AWS_STORAGE_BUCKET_NAME: "recotem-data-prod"
```

### ALB Ingress

```yaml
ingress:
  enabled: true
  className: alb
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/certificate-arn: arn:aws:acm:region:account:certificate/id
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: "443"
  hosts:
    - host: recotem.example.com
      paths:
        - path: /
          pathType: Prefix
```

## Secrets Management

### EKS: Encryption at Rest for Kubernetes Secrets

By default, etcd stores Kubernetes Secrets base64-encoded but not encrypted. Enable envelope encryption with AWS KMS:

1. Create a KMS key for EKS secret encryption:

```bash
aws kms create-key \
  --description "EKS Secret Encryption for recotem" \
  --key-usage ENCRYPT_DECRYPT
```

2. Enable secret encryption on the EKS cluster:

```bash
aws eks associate-encryption-config \
  --cluster-name your-cluster \
  --encryption-config '[{
    "resources": ["secrets"],
    "provider": {
      "keyArn": "arn:aws:kms:region:account:key/key-id"
    }
  }]'
```

3. Re-encrypt existing secrets after enabling:

```bash
kubectl get secrets --all-namespaces -o json | kubectl replace -f -
```

See the [AWS documentation on EKS envelope encryption](https://docs.aws.amazon.com/eks/latest/userguide/enable-kms.html) for details.

### External Secrets Operator (Recommended)

For production, use the [External Secrets Operator (ESO)](https://external-secrets.io) to sync secrets from AWS Secrets Manager into Kubernetes:

1. Install ESO:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace
```

2. Create a `SecretStore` pointing to AWS Secrets Manager:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: aws-secrets
spec:
  provider:
    aws:
      service: SecretsManager
      region: your-region
      auth:
        jwt:
          serviceAccountRef:
            name: recotem
```

3. Create an `ExternalSecret` to sync Recotem secrets:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: recotem-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets
    kind: SecretStore
  target:
    name: recotem-secrets
  data:
    - secretKey: SECRET_KEY
      remoteRef:
        key: recotem/production
        property: SECRET_KEY
    - secretKey: DATABASE_URL
      remoteRef:
        key: recotem/production
        property: DATABASE_URL
    - secretKey: REDIS_PASSWORD
      remoteRef:
        key: recotem/production
        property: REDIS_PASSWORD
```

This approach avoids storing secrets in Helm values or environment files, and supports automatic rotation.

## Cost Estimation

Approximate monthly costs (us-east-1, minimal production setup):

| Service | Spec | ~Cost/month |
|---------|------|-------------|
| RDS PostgreSQL | db.t4g.medium, Multi-AZ | $140 |
| ElastiCache Redis | cache.t4g.medium | $95 |
| ECS/Fargate (backend x2) | 1 vCPU, 2GB | $60 |
| ECS/Fargate (worker x1) | 2 vCPU, 4GB | $60 |
| ECS/Fargate (proxy x2) | 0.25 vCPU, 0.5GB | $15 |
| ALB | — | $20 |
| S3 | 10GB | $1 |
| **Total** | | **~$390** |

Costs vary by region and usage. Use the [AWS Pricing Calculator](https://calculator.aws/) for accurate estimates.
