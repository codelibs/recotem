# Security Design Specification

## Overview

Recotem implements defense-in-depth security across multiple layers: authentication and authorization at the API level, cryptographic integrity for serialized model files, multi-tenancy isolation via ownership filtering, and rate limiting at both the reverse proxy and application layers.

## Authentication

### Authentication Stack

```
+-------------------------------------------------------+
|                    nginx (proxy)                       |
|  - Rate limiting (api, auth, recommendation zones)     |
|  - Security headers (CSP, HSTS, X-Frame-Options)      |
|  - WebSocket log sanitization (query string excluded)  |
+---------------------------+---------------------------+
                            |
              +-------------v-------------+
              |   DRF Authentication       |
              |                            |
              |  1. ApiKeyAuthentication   |
              |  2. JWTAuthentication      |
              |  3. SessionAuthentication  |
              +-------------+-------------+
                            |
              +-------------v-------------+
              |   DRF Permissions          |
              |                            |
              |  - IsAuthenticated         |
              |  - RequireManagementScope  |
              |  - DenyApiKeyAccess        |
              +---------------------------+
```

### JWT Authentication

- **Library**: `djangorestframework-simplejwt` + `dj-rest-auth`
- **Token type**: Access token (short-lived) + Refresh token (1 day)
- **Access token lifetime**: Configurable via `ACCESS_TOKEN_LIFETIME` (default 300 seconds)
- **Storage**: Tokens are returned in response body (not cookies)
- **Login endpoint**: `POST /api/v1/auth/login/`
- **Refresh endpoint**: `POST /api/v1/auth/token/refresh/`

```
POST /api/v1/auth/login/
Content-Type: application/json

{"username": "admin", "password": "secret"}

Response:
{"access": "eyJ...", "refresh": "eyJ...", "user": {...}}
```

Usage: `Authorization: Bearer eyJ...`

### API Key Authentication

API keys provide programmatic access scoped to a specific project.

#### Key Format

```
rctm_<random_urlsafe_base64_48_chars>
      +------------------------------+
       ^
       |
  First 8 chars = key_prefix (stored for lookup)
```

- **Prefix**: `rctm_` (fixed, identifies Recotem API keys)
- **Random part**: 48 characters of `secrets.token_urlsafe()`
- **Lookup key**: First 8 characters of the random part (stored as `key_prefix`)
- **Storage**: Full key is hashed with Django's PBKDF2-SHA256 (`make_password()`)

#### Key Generation

```python
def generate_api_key() -> tuple[str, str, str]:
    random_part = secrets.token_urlsafe(48)
    full_key = f"rctm_{random_part}"
    prefix = random_part[:8]
    hashed_key = make_password(full_key)
    return full_key, prefix, hashed_key
```

The full key is returned to the user exactly once at creation time. Only the prefix and hash are stored.

#### Authentication Flow

```
Client Request
  |
  |  X-API-Key: rctm_aBcDeFgHiJkLmNoPqRsTuVwX...
  |
  v
ApiKeyAuthentication.authenticate()
  |
  +-- 1. Extract header: HTTP_X_API_KEY
  +-- 2. Check prefix: starts with "rctm_"?
  +-- 3. Extract random part, take first 8 chars as prefix
  +-- 4. DB lookup: ApiKey.objects.get(key_prefix=prefix, is_active=True)
  +-- 5. Check expiration: expires_at < now?
  +-- 6. Verify hash: check_password(full_key, hashed_key)
  +-- 7. Update last_used_at (fire-and-forget)
  +-- 8. Attach key to request: request.api_key = key_obj
  +-- 9. Return (key_obj.owner, key_obj)
```

#### Scopes

API keys have JSON-array scopes that control access:

| Scope | Grants Access To |
|---|---|
| `read` | GET, HEAD, OPTIONS on management endpoints |
| `write` | POST, PUT, PATCH, DELETE on management endpoints |
| `predict` | Inference API prediction endpoints |

Scope enforcement:
- **Management API**: `RequireManagementScope` permission class checks `read`/`write`
- **Inference API**: `require_scope("predict")` FastAPI dependency
- **User management**: `DenyApiKeyAccess` unconditionally blocks API key access

#### Inference Service Compatibility

The inference service (FastAPI) verifies API keys independently using SQLAlchemy for database access. It uses `passlib.hash.django_pbkdf2_sha256` to verify keys against Django's PBKDF2-SHA256 hash format, ensuring compatibility without a Django dependency.

### WebSocket Authentication

See [WebSocket Protocol Specification](websocket-protocol.md) for details. JWT tokens are passed as `?token=<access_token>` query parameters since browsers cannot send custom headers on WebSocket upgrade requests.

## Model File Integrity (HMAC-SHA256 Signing)

### Threat Model

Trained recommendation models are serialized for persistence. Serialized files can potentially execute code on deserialization if tampered with. An attacker who can write to the model storage volume could inject malicious serialized files.

### Signing Architecture

```
Training (Celery Worker)                 Serving (Inference / Backend)
+------------------------+              +------------------------+
|                        |              |                        |
|  1. Train model        |              |  1. Read file from     |
|  2. Serialize model    |              |     storage            |
|  3. sign_bytes()       |              |  2. verify_and_extract |
|     HMAC = SHA256(     |              |     Verify HMAC        |
|       SECRET_KEY,      |              |     Extract payload    |
|       payload)         |              |  3. Deserialize model  |
|  4. Write: HMAC +      |              |                        |
|     payload to file    |              |                        |
|                        |              |                        |
+------------------------+              +------------------------+
```

### File Format

```
Offset    Length    Content
0x00      32        HMAC-SHA256 signature
0x20      variable  Serialized payload (model data)
```

Total file size = 32 + len(payload) bytes.

### Signing (signing_core module)

```python
def sign_bytes(key: bytes, payload: bytes) -> bytes:
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return signature + payload
```

- **Key**: `SECRET_KEY.encode("utf-8")` (Django's SECRET_KEY)
- **Algorithm**: HMAC-SHA256 (32-byte digest)
- **Called by**: `training_service.train_and_save_model()` after serialization

### Verification (signing_core module)

The verification function performs these steps:

1. If data is 32 bytes or fewer: treat as legacy (if allowed) or reject
2. Split data: `signature = data[:32]`, `payload = data[32:]`
3. Compute expected HMAC: `HMAC-SHA256(key, payload)`
4. If `hmac.compare_digest(signature, expected)`: return payload (verified)
5. If `data[0] == 0x80` and `allow_legacy`: return data as-is (unsigned legacy file, warning logged)
6. Otherwise: raise `ValueError` (tampering detected)

Uses `hmac.compare_digest()` for constant-time comparison to prevent timing attacks.

### Legacy Unsigned File Handling

Files created before HMAC signing was introduced do not have signatures. These are detected by the `0x80` byte at position 0, which is a protocol marker for the serialization format.

| Scenario | `allow_legacy=True` | `allow_legacy=False` |
|---|---|---|
| Valid HMAC signature | Return payload | Return payload |
| No HMAC, starts with `0x80` | Return data (warning logged) | Raise `ValueError` |
| Invalid HMAC, no `0x80` marker | Raise `ValueError` | Raise `ValueError` |
| Data <= 32 bytes | Return data (warning logged) | Raise `ValueError` |

Controlled by `PICKLE_ALLOW_LEGACY_UNSIGNED` setting (default `True`). After running `manage.py resign_models`, set to `False` to reject all unsigned files.

### Shared Signing Module

The signing core module has no Django dependencies. It is used by:
1. **Backend** (`services/signing.py`): Wraps core with Django settings for `SECRET_KEY` and `PICKLE_ALLOW_LEGACY_UNSIGNED`
2. **Inference service** (`signing.py`): Independent implementation using Pydantic settings

Both services must share the same `SECRET_KEY` for signature verification.

## Multi-Tenancy

### Ownership Model

```
+--------------------------------------------------------+
|                     User (Django)                       |
|                                                         |
|  owns --> Project --> TrainingData, ModelConfig, etc.    |
|                                                         |
|  created_by --> SplitConfig, EvaluationConfig            |
|                                                         |
|  owns --> ApiKey (scoped to Project)                     |
+---------------------------------------------------------+
```

### OwnedResourceMixin

Applied to ViewSets for models with an ownership chain through `Project.owner`:

```python
class OwnedResourceMixin:
    owner_lookup: str = "owner"

    def get_owner_filter(self):
        user = self.request.user
        if user.is_staff:
            return Q()  # Staff sees everything
        q = Q(**{owner_lookup: user}) | Q(**{f"{owner_lookup}__isnull": True})
        # API key project scope
        api_key = getattr(self.request, "api_key", None)
        if api_key is not None:
            q &= Q(**{project_lookup: api_key.project_id})
        return q
```

**Behavior**:
- **Regular users**: See own resources + legacy unowned resources (`owner=NULL`)
- **Staff users**: See all resources
- **API key users**: Further filtered to the API key's project scope

### CreatedByResourceMixin

Applied to ViewSets for `SplitConfig` and `EvaluationConfig` which use `created_by` instead of project ownership:

```python
class CreatedByResourceMixin:
    created_by_lookup: str = "created_by"

    def get_owner_filter(self):
        user = self.request.user
        if user.is_staff:
            return Q()
        return Q(**{created_by_lookup: user}) | Q(**{f"{created_by_lookup}__isnull": True})
```

### ViewSet Owner Lookup Configuration

| ViewSet | Mixin | `owner_lookup` |
|---|---|---|
| `ProjectViewSet` | `OwnedResourceMixin` | `"owner"` |
| `TrainingDataViewset` | `OwnedResourceMixin` | `"project__owner"` |
| `ModelConfigurationViewset` | `OwnedResourceMixin` | `"project__owner"` |
| `TrainedModelViewset` | `OwnedResourceMixin` | `"configuration__project__owner"` |
| `ParameterTuningJobViewSet` | `OwnedResourceMixin` | `"data__project__owner"` |
| `ABTestViewSet` | `OwnedResourceMixin` | `"project__owner"` |
| `DeploymentSlotViewSet` | `OwnedResourceMixin` | `"project__owner"` |
| `ApiKeyViewSet` | `OwnedResourceMixin` | `"owner"` |
| `SplitConfigViewSet` | `CreatedByResourceMixin` | `"created_by"` |
| `EvaluationConfigViewSet` | `CreatedByResourceMixin` | `"created_by"` |

## Rate Limiting

### Three-Layer Rate Limiting

```
Layer 1: nginx (connection-level)
  |  api:          30 req/s   per IP
  |  auth:          5 req/min per IP
  |  recommendation: 30 req/min per IP
  |
  v
Layer 2: DRF Throttling (application-level)
  |  anon:          20/min
  |  user:         100/min
  |  login:          5/min
  |  recommendation: 30/min
  |
  v
Layer 3: slowapi (inference-level)
     Per API key:  100/min (configurable)
```

### Login Brute-Force Protection

Login is rate-limited at three levels:
1. nginx `auth` zone: 5 requests/minute per IP with burst of 3
2. DRF `LoginRateThrottle`: 5/min scope (`AnonRateThrottle` subclass)
3. Django password validators (minimum length, common password check, numeric check)

## Security Headers

### nginx Security Headers

```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-XSS-Protection: 0
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; ...
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Permitted-Cross-Domain-Policies: none
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
X-Request-ID: <auto-generated>
```

### Django Security Settings (Production)

When `DEBUG=False`:
- `SECURE_HSTS_SECONDS`: 31536000 (1 year)
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`: True
- `SECURE_HSTS_PRELOAD`: True
- `SECURE_SSL_REDIRECT`: True
- `SESSION_COOKIE_SECURE`: True
- `CSRF_COOKIE_SECURE`: True
- `SESSION_COOKIE_HTTPONLY`: True

### Production Safety Checks

Django settings include runtime assertions that prevent deployment with insecure defaults:
- `SECRET_KEY` must not be the default value
- `SECRET_KEY` must be at least 50 characters
- `ALLOWED_HOSTS` must not contain `"*"` or be empty

## CORS and CSRF Configuration

### CORS

- **Same-origin deployments** (via nginx proxy): CORS is not needed
- **Cross-origin deployments**: Set `CORS_ALLOWED_ORIGINS` environment variable
- **Development**: Allows `localhost:5173` and `localhost:8000`
- **Credentials**: `CORS_ALLOW_CREDENTIALS = True`

### CSRF

- `CSRF_TRUSTED_ORIGINS`: Auto-derived from `ALLOWED_HOSTS` when not explicitly set
- Can be overridden via `CSRF_TRUSTED_ORIGINS` environment variable
- Required for Django Admin on non-HTTPS origins

## Logging and Sensitive Data Protection

### Sensitive Data Filter

A custom logging filter (`_SensitiveDataFilter`) masks sensitive patterns in log output:

| Pattern | Masked As |
|---|---|
| `://user:password@host` | `://user:***@host` |
| `AWS_SECRET_ACCESS_KEY=value` | `AWS_SECRET_ACCESS_KEY=***` |
| `AWS_SESSION_TOKEN=value` | `AWS_SESSION_TOKEN=***` |

### WebSocket Log Sanitization

nginx uses a custom log format (`ws_sanitized`) for WebSocket requests that excludes query strings, preventing JWT tokens from appearing in access logs:

```
log_format ws_sanitized '$remote_addr ... "$request_method $uri $server_protocol" ...';
```

## Summary of Security Controls

| Concern | Control |
|---|---|
| Authentication (API) | JWT access tokens + API keys with scoped permissions |
| Authentication (WebSocket) | JWT via query parameter |
| Authorization | OwnedResourceMixin / CreatedByResourceMixin for data isolation |
| Model file integrity | HMAC-SHA256 signing with SECRET_KEY |
| API key storage | PBKDF2-SHA256 hashing (Django `make_password`) |
| Rate limiting | nginx zones + DRF throttling + slowapi |
| Login protection | 3-layer rate limiting + Django password validators |
| Transport security | HSTS, SSL redirect, secure cookies (production) |
| Content security | CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff |
| Log hygiene | Sensitive data filter + WebSocket log sanitization |
| Deployment safety | Runtime checks for SECRET_KEY and ALLOWED_HOSTS |
