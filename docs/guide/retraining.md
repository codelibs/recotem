# Scheduled Retraining

Recotem supports automatic periodic retraining of recommendation models using cron-based schedules powered by django-celery-beat.

## When to Set Up Retraining

Scheduled retraining is useful when:

- **Your product gets new user interaction data regularly** -- as users click, purchase, and browse, the underlying data changes. Models trained on old data gradually become less accurate. Automatic retraining keeps your recommendations fresh.
- **You want models to stay up-to-date without manual intervention** -- instead of remembering to retrain models yourself, set a schedule and let Recotem handle it automatically.
- **You want to retrain on a regular cadence** -- for example, retrain every night so that today's user activity is reflected in tomorrow's recommendations, or retrain weekly if your data changes more slowly.

If your training data rarely changes or you prefer to retrain manually after each data upload, you may not need scheduled retraining.

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

Cron expressions tell Recotem when to run retraining. They use 5 fields separated by spaces:

```
minute  hour  day_of_month  month  day_of_week
  0       2       *           *         0
```

- **minute** (0-59) -- which minute of the hour
- **hour** (0-23) -- which hour of the day (24-hour format)
- **day_of_month** (1-31) -- which day of the month
- **month** (1-12) -- which month
- **day_of_week** (0-6) -- which day of the week (0 = Sunday, 1 = Monday, ..., 6 = Saturday)

Use `*` to mean "every" and `*/N` to mean "every N units."

Here are common schedules you can copy directly:

| Expression | What it means |
|-----------|---------|
| `0 2 * * 0` | Every Sunday at 2:00 AM -- good for weekly retraining |
| `0 3 * * *` | Every day at 3:00 AM -- good for daily retraining |
| `0 */6 * * *` | Every 6 hours (at 0:00, 6:00, 12:00, 18:00) -- for frequently changing data |
| `0 2 1 * *` | First day of each month at 2:00 AM -- for monthly retraining |
| `30 1 * * 1-5` | Weekdays at 1:30 AM -- skip weekends |

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
