# API Key Authentication

API keys provide programmatic access to Recotem resources. Keys are scoped to a project and support granular permissions.

## Overview

- Keys are prefixed with `rctm_` for easy identification
- Each key is tied to a specific project
- Permissions are controlled via scopes: `read`, `write`, `predict`
- Keys are hashed before storage (the full key is shown only once at creation)
- Keys can have optional expiration dates

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

The `key` field is only included in the creation response. Store it securely.

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

A key can have multiple scopes. For inference-only integrations, use `["predict"]`.

## Managing Keys

### List Keys

```bash
curl -H "Authorization: Bearer <jwt_token>" \
  "http://localhost:8000/api/v1/api_keys/?project=1"
```

### Revoke a Key

Revoking deactivates a key without deleting it:

```bash
curl -X POST http://localhost:8000/api/v1/api_keys/1/revoke/ \
  -H "Authorization: Bearer <jwt_token>"
```

### Delete a Key

```bash
curl -X DELETE http://localhost:8000/api/v1/api_keys/1/ \
  -H "Authorization: Bearer <jwt_token>"
```

## Security

- Keys are stored as hashed values using Django's `make_password` (PBKDF2-SHA256)
- The first 8 characters (prefix) are stored in plaintext for database lookup
- Full keys are never stored and cannot be recovered
- The `last_used_at` field is updated on each successful authentication
- Set `expires_at` for time-limited access
