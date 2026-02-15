# API Reference Specification

## Overview

Recotem exposes two APIs: a **Management API** (Django REST Framework) for managing projects, data, models, and configuration; and an **Inference API** (FastAPI) for real-time recommendation serving. Both APIs support authentication via API keys (`X-API-Key` header) and the Management API additionally supports JWT tokens.

## Base Paths

| API | Base Path | Backward Compat | Framework |
|---|---|---|---|
| Management API | `/api/v1/` | `/api/` (deprecated) | Django REST Framework |
| Inference API | `/inference/` | -- | FastAPI |
| Django Admin | `/admin/` | -- | Django Admin |
| OpenAPI Schema | `/api/v1/schema/` | -- | drf-spectacular |

## Authentication

### JWT Authentication

- **Obtain tokens**: `POST /api/v1/auth/login/` with `{"username": "...", "password": "..."}`
- **Response**: `{"access": "...", "refresh": "...", "user": {...}}`
- **Usage**: `Authorization: Bearer <access_token>`
- **Access token lifetime**: Configurable via `ACCESS_TOKEN_LIFETIME` env var (default 300 seconds)
- **Refresh token lifetime**: 1 day

### API Key Authentication

- **Header**: `X-API-Key: rctm_<key>`
- **Scopes**: `read` (GET/HEAD/OPTIONS), `write` (POST/PUT/PATCH/DELETE), `predict` (inference)
- **Project-scoped**: Each API key is bound to a specific project

### Session Authentication

- **Cookie-based**: Django session auth (used by Django Admin)

### Authentication Priority

DRF evaluates authentication classes in order:
1. `ApiKeyAuthentication` (X-API-Key header)
2. `JWTAuthentication` (Authorization: Bearer)
3. `SessionAuthentication` (session cookie)

## Management API Endpoints

All management endpoints require authentication. API key users need appropriate scopes (`read` for safe methods, `write` for unsafe methods). Endpoints use `PageNumberPagination` with a default page size of 20.

### Projects

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/project/` | List projects owned by or shared with the user | JWT, API Key (read) |
| `POST` | `/api/v1/project/` | Create a new project | JWT, API Key (write) |
| `GET` | `/api/v1/project/{id}/` | Retrieve project details | JWT, API Key (read) |
| `PUT` | `/api/v1/project/{id}/` | Update a project | JWT, API Key (write) |
| `PATCH` | `/api/v1/project/{id}/` | Partial update a project | JWT, API Key (write) |
| `DELETE` | `/api/v1/project/{id}/` | Delete a project | JWT, API Key (write) |

**Filter fields**: Filterable via DjangoFilterBackend.

### Project Summary

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/project_summary/{id}/` | Get aggregated project statistics | JWT |

### Training Data

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/training_data/` | List training data files | JWT, API Key (read) |
| `POST` | `/api/v1/training_data/` | Upload training data CSV | JWT, API Key (write) |
| `GET` | `/api/v1/training_data/{id}/` | Retrieve training data details | JWT, API Key (read) |
| `DELETE` | `/api/v1/training_data/{id}/` | Delete training data | JWT, API Key (write) |

**Filter fields**: `project`

### Item Metadata

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/item_meta_data/` | List item metadata files | JWT, API Key (read) |
| `POST` | `/api/v1/item_meta_data/` | Upload item metadata | JWT, API Key (write) |
| `GET` | `/api/v1/item_meta_data/{id}/` | Retrieve item metadata details | JWT, API Key (read) |
| `DELETE` | `/api/v1/item_meta_data/{id}/` | Delete item metadata | JWT, API Key (write) |

**Filter fields**: `project`

### Split Configuration

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/split_config/` | List split configurations | JWT, API Key (read) |
| `POST` | `/api/v1/split_config/` | Create split configuration | JWT, API Key (write) |
| `GET` | `/api/v1/split_config/{id}/` | Retrieve split configuration | JWT, API Key (read) |
| `PUT` | `/api/v1/split_config/{id}/` | Update split configuration | JWT, API Key (write) |
| `PATCH` | `/api/v1/split_config/{id}/` | Partial update | JWT, API Key (write) |
| `DELETE` | `/api/v1/split_config/{id}/` | Delete split configuration | JWT, API Key (write) |

### Evaluation Configuration

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/evaluation_config/` | List evaluation configurations | JWT, API Key (read) |
| `POST` | `/api/v1/evaluation_config/` | Create evaluation configuration | JWT, API Key (write) |
| `GET` | `/api/v1/evaluation_config/{id}/` | Retrieve evaluation configuration | JWT, API Key (read) |
| `PUT` | `/api/v1/evaluation_config/{id}/` | Update evaluation configuration | JWT, API Key (write) |
| `PATCH` | `/api/v1/evaluation_config/{id}/` | Partial update | JWT, API Key (write) |
| `DELETE` | `/api/v1/evaluation_config/{id}/` | Delete evaluation configuration | JWT, API Key (write) |

### Parameter Tuning Jobs

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/parameter_tuning_job/` | List tuning jobs | JWT, API Key (read) |
| `POST` | `/api/v1/parameter_tuning_job/` | Create and start a tuning job | JWT, API Key (write) |
| `GET` | `/api/v1/parameter_tuning_job/{id}/` | Retrieve tuning job details | JWT, API Key (read) |
| `DELETE` | `/api/v1/parameter_tuning_job/{id}/` | Delete tuning job | JWT, API Key (write) |

**Filter fields**: `data`, `status`

### Model Configuration

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/model_configuration/` | List model configurations | JWT, API Key (read) |
| `POST` | `/api/v1/model_configuration/` | Create model configuration | JWT, API Key (write) |
| `GET` | `/api/v1/model_configuration/{id}/` | Retrieve model configuration | JWT, API Key (read) |
| `PUT` | `/api/v1/model_configuration/{id}/` | Update model configuration | JWT, API Key (write) |
| `PATCH` | `/api/v1/model_configuration/{id}/` | Partial update | JWT, API Key (write) |
| `DELETE` | `/api/v1/model_configuration/{id}/` | Delete model configuration | JWT, API Key (write) |

**Filter fields**: `project`

### Trained Models

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/trained_model/` | List trained models | JWT, API Key (read) |
| `POST` | `/api/v1/trained_model/` | Create and train a model | JWT, API Key (write) |
| `GET` | `/api/v1/trained_model/{id}/` | Retrieve trained model details | JWT, API Key (read) |
| `DELETE` | `/api/v1/trained_model/{id}/` | Delete trained model | JWT, API Key (write) |

**Filter fields**: `configuration`, `data_loc`

### Task Logs

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/task_log/` | List task log entries | JWT |
| `GET` | `/api/v1/task_log/{id}/` | Retrieve task log entry | JWT |

**Filter fields**: `task`

### API Keys

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/api_keys/` | List API keys (keys are masked) | JWT |
| `POST` | `/api/v1/api_keys/` | Create a new API key (full key returned once) | JWT |
| `GET` | `/api/v1/api_keys/{id}/` | Retrieve API key details | JWT |
| `PUT` | `/api/v1/api_keys/{id}/` | Update API key metadata | JWT |
| `PATCH` | `/api/v1/api_keys/{id}/` | Partial update API key | JWT |
| `DELETE` | `/api/v1/api_keys/{id}/` | Revoke/delete API key | JWT |

**Note**: API key management endpoints deny access to API-key-authenticated requests (`DenyApiKeyAccess` permission). Only JWT/session users can manage API keys.

### Retraining Schedule

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/retraining_schedule/` | List retraining schedules | JWT, API Key (read) |
| `POST` | `/api/v1/retraining_schedule/` | Create retraining schedule | JWT, API Key (write) |
| `GET` | `/api/v1/retraining_schedule/{id}/` | Retrieve schedule details | JWT, API Key (read) |
| `PUT` | `/api/v1/retraining_schedule/{id}/` | Update schedule | JWT, API Key (write) |
| `PATCH` | `/api/v1/retraining_schedule/{id}/` | Partial update schedule | JWT, API Key (write) |
| `DELETE` | `/api/v1/retraining_schedule/{id}/` | Delete schedule | JWT, API Key (write) |

### Retraining Runs

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/retraining_run/` | List retraining run records | JWT, API Key (read) |
| `GET` | `/api/v1/retraining_run/{id}/` | Retrieve run details | JWT, API Key (read) |

### Deployment Slots

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/deployment_slot/` | List deployment slots | JWT, API Key (read) |
| `POST` | `/api/v1/deployment_slot/` | Create deployment slot | JWT, API Key (write) |
| `GET` | `/api/v1/deployment_slot/{id}/` | Retrieve slot details | JWT, API Key (read) |
| `PUT` | `/api/v1/deployment_slot/{id}/` | Update slot | JWT, API Key (write) |
| `PATCH` | `/api/v1/deployment_slot/{id}/` | Partial update slot | JWT, API Key (write) |
| `DELETE` | `/api/v1/deployment_slot/{id}/` | Delete slot | JWT, API Key (write) |

**Filter fields**: `project`

### A/B Tests

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/ab_test/` | List A/B tests | JWT, API Key (read) |
| `POST` | `/api/v1/ab_test/` | Create A/B test | JWT, API Key (write) |
| `GET` | `/api/v1/ab_test/{id}/` | Retrieve A/B test details | JWT, API Key (read) |
| `PUT` | `/api/v1/ab_test/{id}/` | Update A/B test | JWT, API Key (write) |
| `PATCH` | `/api/v1/ab_test/{id}/` | Partial update | JWT, API Key (write) |
| `DELETE` | `/api/v1/ab_test/{id}/` | Delete A/B test | JWT, API Key (write) |

**Custom actions**:

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/ab_test/{id}/start/` | Start test (DRAFT -> RUNNING) |
| `POST` | `/api/v1/ab_test/{id}/stop/` | Stop test (RUNNING -> COMPLETED) |
| `GET` | `/api/v1/ab_test/{id}/results/` | Get statistical results |
| `POST` | `/api/v1/ab_test/{id}/promote_winner/` | Promote winner slot (body: `{"slot_id": N}`) |

**Filter fields**: `project`, `status`

### Conversion Events

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/conversion_event/` | List conversion events | JWT, API Key (read) |
| `POST` | `/api/v1/conversion_event/` | Record conversion event | JWT, API Key (write) |
| `GET` | `/api/v1/conversion_event/{id}/` | Retrieve event details | JWT, API Key (read) |

### Users

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/v1/users/` | List users (admin) or self | JWT |
| `GET` | `/api/v1/users/{id}/` | Retrieve user details | JWT |
| `POST` | `/api/v1/users/{id}/change_password/` | Change own password | JWT |

**Note**: User management denies API key access (`DenyApiKeyAccess`).

### Utility Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/ping/` | None | Health check, returns `{"status": "ok"}` |

### Authentication Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login/` | Obtain JWT access and refresh tokens |
| `POST` | `/api/v1/auth/logout/` | Invalidate tokens |
| `POST` | `/api/v1/auth/token/refresh/` | Refresh access token |
| `GET` | `/api/v1/auth/user/` | Get current user details |

**Rate limiting**: Login endpoint is rate-limited to 5 requests/minute (`LoginRateThrottle`).

### OpenAPI Schema

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/schema/` | None | OpenAPI 3.0 schema (YAML/JSON) |
| `GET` | `/api/v1/schema/swagger-ui/` | None | Swagger UI |
| `GET` | `/api/v1/schema/redoc/` | None | ReDoc documentation |

## Inference API Endpoints

All inference endpoints require API key authentication with `predict` scope.

### Single User Prediction

```
POST /inference/predict/{model_id}
```

**Headers**: `X-API-Key: rctm_...`, `Content-Type: application/json`

**Request body**:
```json
{
  "user_id": "42",
  "cutoff": 10
}
```

**Response** (`200 OK`):
```json
{
  "items": [
    {"item_id": "101", "score": 0.95},
    {"item_id": "203", "score": 0.87}
  ],
  "model_id": 5,
  "request_id": "a1b2c3d4-..."
}
```

**Constraints**: `cutoff` must be between 1 and 1000.

### Batch Prediction

```
POST /inference/predict/{model_id}/batch
```

**Request body**:
```json
{
  "user_ids": ["42", "43", "44"],
  "cutoff": 10
}
```

**Response** (`200 OK`):
```json
{
  "results": [
    {"items": [...], "model_id": 5, "request_id": "..."},
    {"items": [...], "model_id": 5, "request_id": "..."},
    {"items": [...], "model_id": 5, "request_id": "..."}
  ]
}
```

**Constraints**: Maximum 100 users per batch. Unknown users return empty item lists.

### Project-Level Prediction (A/B Routing)

```
POST /inference/predict/project/{project_id}
```

**Request body**:
```json
{
  "user_id": "42",
  "cutoff": 10
}
```

**Response** (`200 OK`):
```json
{
  "items": [...],
  "model_id": 5,
  "slot_id": 3,
  "slot_name": "production-v2",
  "request_id": "a1b2c3d4-..."
}
```

**Behavior**: Selects an active deployment slot using weighted random selection based on slot weights. The `slot_id` and `slot_name` in the response identify which slot (and therefore which model) served the recommendation.

### Health Check

```
GET /inference/health
```

**Response** (`200 OK`, no auth required):
```json
{
  "status": "healthy",
  "loaded_models": 3
}
```

### List Loaded Models

```
GET /inference/models
```

**Response** (`200 OK`, no auth required):
```json
{
  "models": [1, 5, 12],
  "count": 3
}
```

## Rate Limiting

### nginx Layer

| Zone | Rate | Burst | Applies To |
|---|---|---|---|
| `api` | 30 req/s | 20 | `/api/` endpoints |
| `auth` | 5 req/min | 3 | `/api/auth/login/` |
| `recommendation` | 30 req/min | 10 | Model recommendation endpoints |

### DRF Layer

| Scope | Default Rate | Description |
|---|---|---|
| `anon` | 20/min | Anonymous requests |
| `user` | 100/min | Authenticated user requests |
| `login` | 5/min | Login attempts |
| `recommendation` | 30/min | Recommendation endpoint |

### Inference Layer (slowapi)

- Default: `100/minute` per API key (configurable via `INFERENCE_RATE_LIMIT`)
- Rate limit key: API key prefix (first 8 chars of random part), falls back to IP address

## Error Responses

Standard DRF error format:

```json
{
  "detail": "Error message here."
}
```

For validation errors:

```json
{
  "field_name": ["Error message."]
}
```

## Pagination

All list endpoints use `PageNumberPagination`:

```json
{
  "count": 42,
  "next": "http://localhost:8000/api/v1/project/?page=2",
  "previous": null,
  "results": [...]
}
```

Default page size: 20.
