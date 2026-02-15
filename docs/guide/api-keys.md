# API Key Authentication

API keys let your applications and scripts talk to Recotem without requiring a user to log in. Think of an API key as a password for machines -- you give it to your app so it can fetch recommendations, upload data, or read project information on your behalf.

## When to Use API Keys

API keys are the right choice when you need to:

- **Integrate recommendations into your application** -- your web or mobile app calls Recotem's inference API to show personalized recommendations to users.
- **Run automated scripts** -- batch jobs, data pipelines, or CI/CD workflows that need to interact with Recotem without human intervention.
- **Connect third-party services** -- external tools such as analytics platforms, marketing automation systems, or custom dashboards that pull data from Recotem.

If you only need to manage projects and models through the web UI, you do not need an API key -- your normal user login is sufficient.

## Overview

- Keys are prefixed with `rctm_` so you can easily recognize them in configuration files and logs
- Each key belongs to a specific project -- it cannot access other projects
- Permissions are controlled via scopes (`read`, `write`, `predict`), so you can limit what a key is allowed to do
- Keys are hashed before storage for security -- the full key is shown only once when you create it, so copy it right away
- Keys can have optional expiration dates to automatically stop working after a certain time

## Creating an API Key

### Via UI

1. Navigate to your project
2. Go to **API Keys** in the sidebar
3. Click **Create API Key**
4. Enter a name and select scopes
5. Copy the displayed key immediately — it will not be shown again

### Via API

```bash
curl -X POST http://localhost:8000/api/v1/api_keys/ \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Service",
    "project": 1,
    "scopes": ["predict"]
  }'
```

**Response:**

```json
{
  "id": 1,
  "name": "Production Service",
  "project": 1,
  "key_prefix": "rctm_abc1",
  "scopes": ["predict"],
  "is_active": true,
  "expires_at": null,
  "last_used_at": null,
  "key": "rctm_abc1defg2hijklmn3opqrstu4vwxyz..."
}
```

The `key` field is only included in the creation response. Copy and store it in a secure location (such as a secrets manager or environment variable) -- you will not be able to see it again.

## Using an API Key

Pass the key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: rctm_your_key_here" \
  http://localhost:8000/inference/predict/1 \
  -d '{"user_id": "42", "cutoff": 10}'
```

API keys work with both the management API (`/api/v1/`) and the inference API (`/inference/`).

## Scopes

| Scope | Grants access to |
|-------|-----------------|
| `read` | Read project data, models, configurations |
| `write` | Create/update training data, configurations, models |
| `predict` | Call inference endpoints, record conversion events |

A key can have multiple scopes. For most production integrations where you only need to serve recommendations, use `["predict"]`. Add `read` or `write` only if the key also needs to manage project data.

## Managing Keys

### List Keys

```bash
curl -H "Authorization: Bearer <jwt_token>" \
  "http://localhost:8000/api/v1/api_keys/?project=1"
```

### Revoke a Key

Revoking deactivates a key immediately so it stops working, but the key record is kept for audit purposes:

```bash
curl -X POST http://localhost:8000/api/v1/api_keys/1/revoke/ \
  -H "Authorization: Bearer <jwt_token>"
```

### Delete a Key

```bash
curl -X DELETE http://localhost:8000/api/v1/api_keys/1/ \
  -H "Authorization: Bearer <jwt_token>"
```

## Security Best Practices

- **Keys are hashed before storage** using PBKDF2-SHA256 -- even if the database is compromised, the raw keys cannot be recovered.
- The first 8 characters (the prefix, such as `rctm_abc1`) are stored in plaintext so Recotem can quickly look up which key is being used.
- Full keys are never stored and cannot be recovered. If you lose a key, revoke it and create a new one.
- The `last_used_at` field is updated on each successful use, so you can identify unused keys.
- Set `expires_at` for time-limited access -- this is especially useful for keys shared with external partners or temporary integrations.
