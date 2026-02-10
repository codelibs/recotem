# Separate Frontend Deployment

By default, Recotem serves the Vue SPA through the nginx proxy container alongside the backend. For advanced deployments (e.g., CDN-hosted frontend, different scaling needs), you can deploy the frontend separately.

## Architecture

```
┌──────────┐    ┌───────────────┐
│ CDN/S3   │    │ Backend       │
│ Frontend │    │ (API + WS)    │
│ SPA      │───▶│ :80           │
└──────────┘    └───────────────┘
```

## Build the Frontend

```bash
cd frontend

# Set the backend API URL (required for cross-origin)
export VITE_API_BASE_URL=https://api.example.com/api/v1
export VITE_WS_BASE_URL=wss://api.example.com/ws

npm ci
npm run build
```

The built files are in `frontend/dist/`. Deploy them to any static hosting (S3, CloudFront, Vercel, Netlify, nginx, etc.).

## Backend Configuration

When the frontend is on a different origin, configure CORS and CSRF on the backend:

```env
# Required: whitelist the frontend origin
CORS_ALLOWED_ORIGINS=https://app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com

# If the backend serves API only (no SPA), you can still keep DEBUG=false
ALLOWED_HOSTS=api.example.com
```

## Environment Variables

### Frontend (build-time)

| Variable | Description | Example |
|----------|-------------|---------|
| `VITE_API_BASE_URL` | Backend REST API base URL | `https://api.example.com/api/v1` |
| `VITE_WS_BASE_URL` | Backend WebSocket base URL | `wss://api.example.com/ws` |

### Backend (runtime)

| Variable | Description | Example |
|----------|-------------|---------|
| `CORS_ALLOWED_ORIGINS` | Frontend origin(s), comma-separated | `https://app.example.com` |
| `CSRF_TRUSTED_ORIGINS` | Frontend origin(s) for CSRF | `https://app.example.com` |
| `ALLOWED_HOSTS` | Backend hostname(s) | `api.example.com` |

## WebSocket Configuration

WebSocket connections go directly from the browser to the backend. Ensure:

1. The backend is accessible via `wss://` (TLS required for production)
2. Your load balancer/reverse proxy supports WebSocket upgrades
3. `VITE_WS_BASE_URL` matches the backend's WebSocket endpoint

### nginx example (backend-only proxy)

```nginx
server {
    listen 443 ssl;
    server_name api.example.com;

    location /api/ {
        proxy_pass http://backend:80;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://backend:80;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

## Kubernetes Deployment

When using the Helm chart with a separate frontend:

1. Disable the proxy deployment (or repurpose it as API-only reverse proxy)
2. Deploy the frontend via a separate Deployment + Service, or use an external CDN

```yaml
# values-separate-frontend.yaml
proxy:
  replicaCount: 0  # disable proxy if frontend is hosted externally

config:
  corsAllowedOrigins: "https://app.example.com"
  csrfTrustedOrigins: "https://app.example.com"
```

## Docker Compose (Development)

For local development with separate frontend:

```bash
# Terminal 1: Backend + infrastructure
docker compose -f compose-dev.yaml up -d
cd backend/recotem && python manage.py runserver 8000

# Terminal 2: Frontend dev server
cd frontend
VITE_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

The Vite dev server automatically proxies `/api` and `/ws` requests, so `VITE_API_BASE_URL` is only needed if you want to bypass the proxy.
