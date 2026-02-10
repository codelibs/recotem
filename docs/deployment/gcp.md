# GCP Deployment Guide

Deploy Recotem on Google Cloud using GKE or Cloud Run with managed services.

## Architecture

```
[Cloud DNS] → [Cloud Load Balancer (HTTPS)] → [GKE/Cloud Run]
                                                  ├── proxy (nginx + SPA)
                                                  ├── backend (daphne)
                                                  └── worker (celery)
                                                       ├── [Cloud SQL PostgreSQL]
                                                       ├── [Memorystore Redis]
                                                       └── [Cloud Storage]
```

## Managed Services Setup

### Cloud SQL for PostgreSQL

1. Create a PostgreSQL 17 instance:

```bash
gcloud sql instances create recotem-db \
  --database-version=POSTGRES_17 \
  --tier=db-custom-2-4096 \
  --region=your-region \
  --storage-type=SSD \
  --storage-size=20GB \
  --availability-type=REGIONAL \
  --backup-start-time=03:00
```

2. Create database and user:

```bash
gcloud sql databases create recotem --instance=recotem-db
gcloud sql users create recotem_user --instance=recotem-db --password=your-secure-password
```

3. Note the connection name for the Cloud SQL Auth Proxy:
```
your-project:your-region:recotem-db
```

### Memorystore for Redis

```bash
gcloud redis instances create recotem-redis \
  --size=1 \
  --region=your-region \
  --redis-version=redis_7_2 \
  --tier=STANDARD_HA \
  --transit-encryption-mode=SERVER_AUTHENTICATION
```

Note the host IP for Redis configuration:
```
redis://host-ip:6379/0
```

Redis is used for three purposes (different databases):
- db 0: Celery broker
- db 1: Django Channels
- db 2: Django cache

### Cloud Storage (Optional)

For storing training data and trained models:

```bash
gsutil mb -l your-region gs://recotem-data-prod
```

Use S3-compatible access via interoperability keys, or use `django-storages[google]` (requires adding `google-cloud-storage` to backend dependencies).

With S3 interoperability:

```bash
# Generate HMAC keys
gsutil hmac create your-service-account@your-project.iam.gserviceaccount.com
```

Set environment variables:
```env
RECOTEM_STORAGE_TYPE=S3
AWS_ACCESS_KEY_ID=<HMAC-access-id>
AWS_SECRET_ACCESS_KEY=<HMAC-secret>
AWS_STORAGE_BUCKET_NAME=recotem-data-prod
AWS_S3_ENDPOINT_URL=https://storage.googleapis.com
```

## Option A: GKE

See the [Kubernetes Deployment Guide](kubernetes.md) with these GCP-specific additions.

### Workload Identity

Use Workload Identity instead of static credentials:

1. Create a GCP service account:
```bash
gcloud iam service-accounts create recotem-sa \
  --display-name="Recotem Service Account"
```

2. Grant permissions:
```bash
# Cloud SQL Client
gcloud projects add-iam-policy-binding your-project \
  --member="serviceAccount:recotem-sa@your-project.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client"

# Cloud Storage (if using GCS)
gcloud projects add-iam-policy-binding your-project \
  --member="serviceAccount:recotem-sa@your-project.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

3. Bind to Kubernetes service account:
```bash
gcloud iam service-accounts add-iam-policy-binding \
  recotem-sa@your-project.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:your-project.svc.id.goog[default/recotem]"
```

4. Annotate in Helm values:
```yaml
serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: recotem-sa@your-project.iam.gserviceaccount.com
```

### Cloud SQL Auth Proxy

Use the Cloud SQL Auth Proxy as a sidecar:

```yaml
# values-gke.yaml
extraContainers:
  - name: cloud-sql-proxy
    image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2
    args:
      - "--structured-logs"
      - "--auto-iam-authn"
      - "your-project:your-region:recotem-db"
    securityContext:
      runAsNonRoot: true
    resources:
      requests:
        memory: 128Mi
        cpu: 100m
```

With the proxy, use `localhost` in the database URL:
```
postgresql://recotem_user:password@localhost:5432/recotem
```

### GKE Ingress

```yaml
ingress:
  enabled: true
  className: gce
  annotations:
    kubernetes.io/ingress.global-static-ip-name: recotem-ip
    networking.gke.io/managed-certificates: recotem-cert
  hosts:
    - host: recotem.example.com
      paths:
        - path: /
          pathType: Prefix
```

With Google-managed certificate:
```yaml
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: recotem-cert
spec:
  domains:
    - recotem.example.com
```

## Option B: Cloud Run

For simpler deployments without Kubernetes:

### Deploy Backend

```bash
gcloud run deploy recotem-backend \
  --image=ghcr.io/codelibs/recotem-backend:latest \
  --platform=managed \
  --region=your-region \
  --port=80 \
  --memory=2Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=5 \
  --add-cloudsql-instances=your-project:your-region:recotem-db \
  --set-env-vars="DATABASE_URL=postgresql://user:pass@/recotem?host=/cloudsql/your-project:your-region:recotem-db" \
  --set-env-vars="CELERY_BROKER_URL=redis://memorystore-ip:6379/0" \
  --set-env-vars="ALLOWED_HOSTS=recotem-backend-xxxxx.run.app" \
  --set-env-vars="DEBUG=false"
```

> **Note**: Cloud Run does not support persistent WebSocket connections. For WebSocket-based job status updates, use polling as a fallback or deploy the backend on GKE.

### Deploy Worker

The Celery worker needs a long-running instance. Use Cloud Run Jobs or a Compute Engine VM:

```bash
# Using Compute Engine
gcloud compute instances create-with-container recotem-worker \
  --container-image=ghcr.io/codelibs/recotem-backend:latest \
  --container-command="celery" \
  --container-arg="-A" --container-arg="recotem" \
  --container-arg="worker" --container-arg="--loglevel=INFO" \
  --machine-type=e2-standard-2 \
  --zone=your-zone
```

### Deploy Proxy

```bash
gcloud run deploy recotem-proxy \
  --image=ghcr.io/codelibs/recotem-proxy:latest \
  --platform=managed \
  --region=your-region \
  --port=8000 \
  --memory=256Mi \
  --cpu=1 \
  --min-instances=1
```

### VPC Connector (for Memorystore)

Cloud Run requires a VPC connector to reach Memorystore:

```bash
gcloud compute networks vpc-access connectors create recotem-connector \
  --region=your-region \
  --range=10.8.0.0/28

# Add to Cloud Run service
gcloud run services update recotem-backend \
  --vpc-connector=recotem-connector
```

## Secrets Management

### GKE: Application-Layer Encryption for Kubernetes Secrets

GKE supports application-layer encryption of Secrets using Cloud KMS, providing an additional layer beyond the default GCP-managed encryption at rest.

1. Create a Cloud KMS key ring and key:

```bash
gcloud kms keyrings create recotem-ring \
  --location=your-region

gcloud kms keys create recotem-secrets-key \
  --location=your-region \
  --keyring=recotem-ring \
  --purpose=encryption
```

2. Grant the GKE service agent access to the key:

```bash
gcloud kms keys add-iam-policy-binding recotem-secrets-key \
  --location=your-region \
  --keyring=recotem-ring \
  --member="serviceAccount:service-PROJECT_NUMBER@container-engine-robot.iam.gserviceaccount.com" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter"
```

3. Enable application-layer encryption on the GKE cluster:

```bash
gcloud container clusters update your-cluster \
  --region=your-region \
  --database-encryption-key=projects/your-project/locations/your-region/keyRings/recotem-ring/cryptoKeys/recotem-secrets-key
```

See the [GCP documentation on application-layer secrets encryption](https://cloud.google.com/kubernetes-engine/docs/how-to/encrypting-secrets) for details.

### External Secrets Operator (Recommended)

For production, use the [External Secrets Operator (ESO)](https://external-secrets.io) to sync secrets from Google Secret Manager into Kubernetes:

1. Install ESO:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace
```

2. Create secrets in Google Secret Manager:

```bash
echo -n "your-django-secret-key" | gcloud secrets create recotem-secret-key --data-file=-
echo -n "postgresql://user:pass@host:5432/recotem" | gcloud secrets create recotem-database-url --data-file=-
echo -n "your-redis-password" | gcloud secrets create recotem-redis-password --data-file=-
```

3. Grant the Kubernetes service account access via Workload Identity:

```bash
gcloud secrets add-iam-policy-binding recotem-secret-key \
  --member="serviceAccount:your-project.svc.id.goog[default/recotem]" \
  --role="roles/secretmanager.secretAccessor"
```

4. Create a `SecretStore` and `ExternalSecret`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: gcp-secrets
spec:
  provider:
    gcpsm:
      projectID: your-project
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: recotem-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gcp-secrets
    kind: SecretStore
  target:
    name: recotem-secrets
  data:
    - secretKey: SECRET_KEY
      remoteRef:
        key: recotem-secret-key
    - secretKey: DATABASE_URL
      remoteRef:
        key: recotem-database-url
    - secretKey: REDIS_PASSWORD
      remoteRef:
        key: recotem-redis-password
```

This approach avoids storing secrets in Helm values or environment files, and supports automatic rotation.

## Cost Estimation

Approximate monthly costs (us-central1, minimal production setup):

| Service | Spec | ~Cost/month |
|---------|------|-------------|
| Cloud SQL PostgreSQL | db-custom-2-4096, HA | $130 |
| Memorystore Redis | 1GB, Standard HA | $80 |
| GKE Autopilot (backend x2) | 1 vCPU, 2GB | $55 |
| GKE Autopilot (worker x1) | 2 vCPU, 4GB | $55 |
| GKE Autopilot (proxy x2) | 0.25 vCPU, 0.5GB | $15 |
| Cloud Load Balancer | — | $20 |
| Cloud Storage | 10GB | $1 |
| **Total** | | **~$355** |

Costs vary by region and usage. Use the [GCP Pricing Calculator](https://cloud.google.com/products/calculator) for accurate estimates.
