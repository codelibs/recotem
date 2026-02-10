# Recotem One Pager

## 1. Purpose

Recotem is a Docker-first web application for building, tuning, training, and previewing recommender models through a UI.

## 2. Product Scope

Implemented user-facing capabilities:
- Create projects (user/item/time column definitions)
- Upload training data and item metadata files
- Create split and evaluation configurations
- Run parameter tuning jobs (Celery + Optuna + irspack)
- Auto-train model after tuning (optional)
- Train models manually from model configurations
- Preview recommendations from trained models
- Browse job logs and model/data artifacts

Target usage:
- Multiple users use the same deployment
- Atlaskit-level component compliance is not required
- Same-domain operation is expected
- Separate frontend/backend instances may be introduced later

## 3. High-Level Architecture

Current deployment composition:
- `proxy` (Nginx): serves SPA, proxies `/api`, `/admin`, `/ws`
- `backend` (Django + DRF + Channels + Daphne): API + WebSocket endpoints
- `worker` (Celery): tuning/training background tasks
- `db` (PostgreSQL): application data + Optuna studies
- `redis` (Redis): Celery broker + channel layer + cache backend

Tech stack:
- Frontend: Vue 3 + TypeScript + Vite + PrimeVue + Pinia
- Backend: Django 5.1, DRF, dj-rest-auth, simplejwt, celery, channels
- ML/Tuning: irspack + Optuna

## 4. Core Data Model

Main entities:
- `Project`
- `TrainingData`
- `ItemMetaData`
- `SplitConfig`
- `EvaluationConfig`
- `ModelConfiguration`
- `ParameterTuningJob`
- `TrainedModel`
- `TaskLog`

Storage:
- DB metadata in PostgreSQL
- Uploaded datasets and trained model files in Django storage (`MEDIA_ROOT` or S3 if configured)

## 5. Main Flow

Standard workflow:
1. Create a project (`user_column`, `item_column`, optional `time_column`)
2. Upload training data file
3. Create split and evaluation configs
4. Create parameter tuning job
5. Background tasks run tuning; best config is persisted
6. Model can be trained (automatically or manually)
7. Recommendation preview endpoints are called from UI

## 6. API and Realtime

API exposure:
- Versioned: `/api/v1/...`
- Backward-compatible unversioned routes also exist via backend routing

Realtime:
- WebSocket endpoints exist for job status/log channels (`/ws/job/{id}/...`)
- Current backend tasks persist logs to DB; websocket push wiring is incomplete (DB polling path exists in UI)

## 7. Authentication and Multi-User Status

Auth:
- dj-rest-auth + JWT endpoints are used for login and user fetch
- Access/refresh tokens are stored in browser localStorage

Important multi-user note (current state):
- Data ownership boundaries are not modeled strongly yet (`Project` has no owner field)
- Most querysets are global and filtered by request parameters, not by user ownership
- This is a key gap for secure multi-user production usage

## 8. Deployment and Operations

### Same-domain deployment (current best-fit)

Current Nginx setup is aligned to same-domain path-based routing:
- `/` -> frontend SPA
- `/api/` -> backend API
- `/ws/` -> backend websocket

### Separate instances (future-compatible with changes)

If frontend/backend are split across instances while keeping same domain:
- Prefer ingress/reverse-proxy path routing
- Add explicit backend base URL handling in frontend env configuration
- Add explicit CORS/CSRF trusted origin settings in backend

## 9. Environment Variables

Currently used core variables:
- `DATABASE_URL`
- `CELERY_BROKER_URL`
- `CACHE_REDIS_URL`
- `DEBUG`
- `SECRET_KEY`
- `DEFAULT_ADMIN_PASSWORD`
- `ACCESS_TOKEN_LIFETIME`
- `RECOTEM_STORAGE_TYPE` (+ optional S3 vars)

Current status:
- `.env.example` exists
- `dev.env` / `production.env` templates exist
- Production template still contains weak placeholder-like defaults and must be hardened before public deployment

## 10. CI/CD and Quality Gates

GitHub Actions currently include:
- `pre-commit` (ruff + basic hooks)
- Test workflow (Playwright + pytest + coverage upload)
- Release workflow (multi-arch container build/push + Trivy scan + release artifact)
- CodeQL workflow
- Dependabot updates (pip/npm/actions/docker)

Current quality status:
- Frontend unit tests: scripts exist but no test files committed
- Frontend E2E tests: scripts/workflow exist but no test files committed
- Frontend lint script currently requires ESLint flat config migration
- Backend tests exist for data upload and tuning flows

## 11. Known Gaps (As-Is)

Highest-impact items:
- Frontend/Backend API mismatch in model recommendation call path
- Incomplete data isolation for true multi-user security
- Missing/disabled frontend test assets despite CI expectations
- Security hardening required for production defaults (`SECRET_KEY`, hosts, credentials)
- Duplicate utility logic in backend service/util layers
- Documentation still contains partial legacy instructions in README

## 12. Immediate Priorities

P0:
- Enforce tenant/user ownership filtering in backend models/querysets/permissions
- Fix API contract mismatch in recommendation endpoint usage
- Restore runnable frontend test suites or adjust CI to match reality
- Harden production security defaults and env handling

P1:
- Add configurable frontend API base URL for split-instance deployments
- Complete websocket event push pipeline from Celery/backend to channels
- Remove duplicated backend utility paths and consolidate service boundaries

## 13. Reference Files

Primary implementation references:
- `compose.yaml`
- `compose-dev.yaml`
- `backend/recotem/recotem/settings.py`
- `backend/recotem/recotem/api/tasks.py`
- `backend/recotem/recotem/api/views/`
- `frontend/src/api/client.ts`
- `frontend/src/pages/`
- `.github/workflows/`
