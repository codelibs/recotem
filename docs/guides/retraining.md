# Scheduled Retraining

Recotem supports automatic periodic retraining of recommendation models using cron-based schedules powered by django-celery-beat.

## Overview

Each project can have one retraining schedule. When enabled, Celery Beat triggers a retraining task at the specified interval. The task can either:

- **Retrain** using an existing model configuration
- **Retune + Retrain** by running a new hyperparameter search before training

## Setting Up a Schedule

### Via UI

1. Navigate to your project
2. Go to **Retraining** in the sidebar
3. Configure the schedule:
   - **Cron Expression** — When to run (e.g., `0 2 * * 0` for every Sunday at 2:00 AM)
   - **Training Data** — Which dataset to use (defaults to latest if unset)
   - **Model Configuration** — Which config to train with (required unless retune is enabled)
   - **Retune** — Whether to run hyperparameter tuning before training
   - **Auto Deploy** — Whether to automatically update deployment slots after training
4. Toggle **Enabled** to activate

### Via API

```bash
# Create a schedule
curl -X POST http://localhost:8000/api/v1/retraining_schedule/ \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "is_enabled": true,
    "cron_expression": "0 2 * * 0",
    "model_configuration": 5,
    "retune": false,
    "auto_deploy": true
  }'

# Trigger a manual run
curl -X POST http://localhost:8000/api/v1/retraining_schedule/1/trigger/ \
  -H "Authorization: Bearer <jwt_token>"
```

## Cron Expression Format

Standard 5-field cron syntax: `minute hour day_of_month month day_of_week`

| Expression | Schedule |
|-----------|---------|
| `0 2 * * 0` | Every Sunday at 2:00 AM |
| `0 3 * * *` | Every day at 3:00 AM |
| `0 */6 * * *` | Every 6 hours |
| `0 2 1 * *` | First day of each month at 2:00 AM |

## Retraining Logic

When a scheduled retraining task runs:

1. **Data check** — If the training data row count hasn't changed since the last run, the task is skipped (status: `SKIPPED`)
2. **Retune** (if enabled) — Runs Optuna hyperparameter search using the specified split/evaluation configs
3. **Train** — Trains a model using the best (or specified) configuration
4. **Deploy** (if `auto_deploy` is enabled) — Updates the project's deployment slots with the new model
5. **Notify** — Records the run result; if the run fails and `notify_on_failure` is enabled, the failure is logged

## Viewing Run History

### Via UI

The retraining page shows a table of recent runs with status, timestamps, and linked model/tuning job.

### Via API

```bash
curl -H "Authorization: Bearer <jwt_token>" \
  "http://localhost:8000/api/v1/retraining_run/?schedule=1"
```

**Run statuses:**

| Status | Meaning |
|--------|---------|
| `PENDING` | Task queued, not yet started |
| `RUNNING` | Currently executing |
| `COMPLETED` | Successfully trained a new model |
| `FAILED` | Error occurred (see `error_message`) |
| `SKIPPED` | No new data since last run |

## Configuration

The schedule is synced to django-celery-beat's `PeriodicTask` table automatically when you create or update it. The Celery Beat process (`beat` service in Docker Compose) reads from this table to schedule tasks.

**Required service:** The `beat` container must be running for scheduled retraining to work:

```bash
# Docker Compose (included by default)
docker compose up beat

# Local development
cd backend/recotem
uv run celery -A recotem beat --loglevel=INFO \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Retry Behavior

Failed retraining tasks are retried up to `max_retries` times (default: 3) with exponential backoff. Each retry creates a new `RetrainingRun` record.
