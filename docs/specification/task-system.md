# Task System Specification

## Overview

Recotem's background task system is built on Celery with a Redis broker (db0). Tasks handle computationally intensive operations: hyperparameter tuning via Optuna, model training via irspack, and scheduled retraining. Task results are persisted to PostgreSQL via `django-celery-results`, and real-time progress is pushed to connected WebSocket clients via Django Channels.

## Architecture

```
                                   Django Channels
                                   (Redis db1)
                                       ^
                                       | group_send()
                                       |
+----------+    +----------+    +------+------+
|  Celery  |    |  Redis   |    |   Celery    |
|   Beat   |--->|  Broker  |--->|   Worker    |
| (cron)   |    |  (db0)   |    |             |
+----------+    +----------+    +------+------+
                                       |
                          +------------+------------+
                          |            |            |
                    +-----v----+ +----v-----+ +----v---------+
                    | Optuna   | | irspack  | | Redis db3    |
                    | Storage  | | Training | | (model event |
                    | (Postgres)| |          | |  Pub/Sub)    |
                    +----------+ +----------+ +--------------+
```

## Task Registry

All tasks are defined in `backend/recotem/recotem/api/tasks.py` and registered with the Celery app.

| Task | Signature | Purpose |
|---|---|---|
| `run_search` | `run_search(parameter_tuning_job_id, index)` | Single Optuna study worker |
| `task_create_best_config` | `task_create_best_config(parameter_tuning_job_id)` | Save best config after tuning |
| `task_create_best_config_train_rec` | `task_create_best_config_train_rec(parameter_tuning_job_id)` | Save config + auto-train model |
| `task_train_recommender` | `task_train_recommender(model_id)` | Train model from existing config |
| `task_scheduled_retrain` | `task_scheduled_retrain(schedule_id)` | Execute scheduled retraining |

## Common Task Configuration

All tasks share the following configuration:

```python
@app.task(
    bind=True,
    time_limit=settings.CELERY_TASK_TIME_LIMIT,       # default 3600s
    soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,  # default 3480s
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
```

| Setting | Default | Description |
|---|---|---|
| `CELERY_TASK_TIME_LIMIT` | 3600 (1 hour) | Hard kill timeout |
| `CELERY_TASK_SOFT_TIME_LIMIT` | 3480 (58 min) | Raises `SoftTimeLimitExceeded` |
| Auto-retry | `ConnectionError`, `OSError` | Network failures with exponential backoff |
| Max retries | 3 | Maximum retry attempts |

## Task 1: run_search

### Purpose

Executes a subset of Optuna hyperparameter search trials. Multiple `run_search` tasks run in parallel (one per `n_tasks_parallel`) sharing the same Optuna study via PostgreSQL-backed storage.

### Flow

```
run_search(parameter_tuning_job_id, index)
  |
  +-- 1. Create TaskResult and TaskAndParameterJobLink
  +-- 2. Load job data: TrainingData, SplitConfig, EvaluationConfig
  +-- 3. Resolve recommender algorithms to search
  +-- 4. Atomically set job status: PENDING -> RUNNING (first worker wins)
  +-- 5. Send WebSocket: status=running, log="Start job N / worker M"
  +-- 6. Prepare dataset:
  |      split_dataframe_partial_user_holdout() -> train/val split
  |      Build sparse matrix + Evaluator
  +-- 7. Create/load Optuna study (shared storage)
  +-- 8. Run study.optimize():
  |      For each trial:
  |        - suggest_categorical("recommender_class_name", [...])
  |        - Get default parameters from recommender class
  |        - Train recommender on X_train
  |        - Evaluate on X_test
  |        - Return negative score (Optuna minimizes)
  |        - Callback: log trial results via WebSocket + bulk TaskLog
  +-- 9. Flush remaining buffered TaskLog entries
```

### Trial Distribution

Trials are distributed across parallel workers:

```python
n_trials_per_worker = job.n_trials // job.n_tasks_parallel
# Extra trials assigned to early workers
if index < (job.n_trials % job.n_tasks_parallel):
    n_trials_per_worker += 1
```

Example: 40 trials across 3 workers = [14, 13, 13]

### Algorithm Resolution

The `_get_search_recommender_classes()` function resolves algorithm names:

1. If `tried_algorithms_json` is `None`, use defaults: `IALSRecommender`, `CosineKNNRecommender`, `TopPopRecommender`
2. Otherwise, resolve each name (handling `*Optimizer` -> `*Recommender` suffix mapping)
3. If no names resolve, fall back to defaults

### Log Buffering

Trial log messages are buffered in memory and bulk-inserted every 10 trials for performance:

```python
log_buffer: list[TaskLog] = []
BULK_FLUSH_SIZE = 10

def callback(study, trial):
    log_buffer.append(TaskLog(task=task_result, contents=message))
    if len(log_buffer) >= BULK_FLUSH_SIZE:
        TaskLog.objects.bulk_create(log_buffer)
        log_buffer.clear()
    _send_ws_log(job_id, message)  # Real-time WebSocket push
```

A `finally` block ensures remaining buffer entries are flushed even on failure.

## Task 2: task_create_best_config

### Purpose

After all `run_search` workers complete, this task reads the best trial from the shared Optuna study and creates a `ModelConfiguration` record.

### Flow

```
task_create_best_config(parameter_tuning_job_id)
  |
  +-- 1. Create TaskResult
  +-- 2. create_best_config_fun():
  |      +-- Lock job row (select_for_update)
  |      +-- Load best trial from Optuna storage
  |      +-- Extract parameters (strip prefixes)
  |      +-- Resolve recommender class name
  |      +-- Create ModelConfiguration record
  |      +-- Update job: best_config, best_score, irspack_version
  |      +-- If best_score == 0.0: set FAILED, raise error
  |      +-- Set job status: COMPLETED
  |      +-- Log and send WebSocket: completed with score
  +-- 3. Return config_id
```

### Parameter Extraction

Optuna stores parameters with algorithm-specific prefixes. The extraction logic:

1. Collect `trial.params` and `trial.user_attrs` (if valid param names)
2. Strip prefixes: `re.sub(r"^([^\.]*\.)", "", key)` (removes `ClassName.` prefix)
3. Extract `recommender_class_name` from user attrs or params
4. Remove internal keys (`optimizer_name`, `recommender_class_name`)

### Atomic Update

The best config creation uses `transaction.atomic()` with `select_for_update()` on the job row to prevent race conditions if multiple config-save tasks execute.

## Task 3: task_create_best_config_train_rec

### Purpose

Combines best config extraction and model training in one task. Used when `train_after_tuning=True`.

### Flow

```
task_create_best_config_train_rec(parameter_tuning_job_id)
  |
  +-- 1. create_best_config_fun() -> config_id
  +-- 2. Create TrainedModel record
  +-- 3. train_recommender_func(task_result, model.id, job_id)
  |      +-- Train model using training_service.train_and_save_model()
  |      +-- Link task result to model
  |      +-- Update job.tuned_model on success
  +-- 4. On success: _finalize_retraining_run(job, model)
  |      +-- Update RetrainingRun status -> COMPLETED
  |      +-- If auto_deploy: create/update DeploymentSlot
  +-- 5. On failure: _fail_retraining_run_for_job(job_id)
  |      +-- Update RetrainingRun status -> FAILED
```

## Task 4: task_train_recommender

### Purpose

Trains a single model from an existing `ModelConfiguration`. Used for manual training (not part of a tuning flow).

### Flow

```
task_train_recommender(model_id)
  |
  +-- 1. Create TaskResult
  +-- 2. train_recommender_func(task_result, model_id)
  |      +-- Load TrainedModel
  |      +-- Create TaskAndTrainedModelLink
  |      +-- training_service.train_and_save_model(model)
  |           +-- Load training data CSV
  |           +-- Build sparse matrix
  |           +-- Train irspack recommender
  |           +-- Serialize model with IDMappedRecommender
  |           +-- Sign with HMAC-SHA256
  |           +-- Save to storage
  |           +-- Publish model_trained event via Redis Pub/Sub
  +-- 3. On SoftTimeLimitExceeded: log timeout, create TaskLog
  +-- 4. On other error: log failure, create TaskLog
```

## Task 5: task_scheduled_retrain

### Purpose

Executed by Celery Beat on a cron schedule. Runs either a retune+train cycle or a train-only cycle depending on schedule configuration.

### Flow

```
task_scheduled_retrain(schedule_id)
  |
  +-- 1. Load RetrainingSchedule with related objects
  +-- 2. Check: is_enabled? If not, skip
  +-- 3. Determine training data:
  |      If schedule.training_data is set, use it
  |      Otherwise, use the latest TrainingData for the project
  +-- 4. Create RetrainingRun record (status=RUNNING)
  +-- 5. Branch:
  |
  |   [retune=True + split_config + evaluation_config]
  |   +-- Create ParameterTuningJob
  |   +-- start_tuning_job(job) -> async chord
  |   +-- Link run.tuning_job = job
  |   +-- Set schedule status = RUNNING
  |   +-- Return (tuning completion tracked asynchronously)
  |
  |   [retune=False + model_configuration]
  |   +-- Create TrainedModel
  |   +-- train_and_save_model(model) -> synchronous
  |   +-- Link run.trained_model = model
  |   +-- Set run status = COMPLETED
  |   +-- If auto_deploy: create/update DeploymentSlot
  |
  |   [Neither configured]
  |   +-- Set run status = SKIPPED
  |
  +-- 6. Update schedule: last_run_at, last_run_status
```

## Orchestration: start_tuning_job

### Purpose

Coordinates the parallel tuning workflow using Celery's `chain` and `group` primitives.

### Task Graph

```
                      +------- run_search(job_id, 0) --------+
                      |                                       |
start_tuning_job() -> +------- run_search(job_id, 1) --------+ -> task_create_best_config
                      |           (parallel group)            |    or
                      +------- run_search(job_id, N-1) ------+    task_create_best_config_train_rec
                                                                   (chain continuation)
```

### Implementation

```python
def start_tuning_job(job: ParameterTuningJob) -> None:
    job.status = ParameterTuningJob.Status.PENDING
    job.save(update_fields=["status"])

    # Create Optuna study
    optuna.create_study(storage=optuna_storage, study_name=study_name, ...)

    if job.train_after_tuning:
        chain(
            group(run_search.si(job.id, i) for i in range(n_parallel)),
            task_create_best_config_train_rec.si(job.id),
        ).delay()
    else:
        chain(
            group(run_search.si(job.id, i) for i in range(n_parallel)),
            task_create_best_config.si(job.id),
        ).delay()
```

### Celery Primitives

- **`group(...)`**: Runs all `run_search` tasks in parallel across available workers
- **`chain(group, task)`**: After all group tasks complete, runs the config/train task
- **`.si()`**: Immutable signature -- prevents result passing between tasks (each task loads its own data)
- **`.delay()`**: Enqueues the entire chain to the broker

### Error Handling in Orchestration

If `start_tuning_job()` fails to enqueue tasks:
1. The exception is caught
2. Job status is set to FAILED
3. The error is re-raised

## WebSocket Integration

### Status Updates

Tasks push status changes to WebSocket clients via Django Channels:

```python
def _send_ws_status(job_id: int, status: str, data: dict = None) -> None:
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"job_{job_id}_status",
        {"type": "job_status_update", "status": status, "data": data or {}},
    )
```

### Log Messages

Tasks push log entries to WebSocket clients:

```python
def _send_ws_log(job_id: int, message: str) -> None:
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"job_{job_id}_logs",
        {"type": "task_log_message", "message": message},
    )
```

### Failure Resilience

WebSocket push failures (Redis connection errors) are caught and logged as warnings. Tasks continue execution because:
1. Log messages are also persisted to `TaskLog` records in the database
2. Job status is persisted to the `ParameterTuningJob` model
3. WebSocket consumers support late-join buffering (clients reconnecting see the current state)

## Auto-Deploy After Training

When a `RetrainingSchedule` has `auto_deploy=True`, successfully trained models are automatically deployed:

```python
def _auto_deploy_model(schedule, model):
    slot_name = f"auto-deploy-{schedule.project.name}"
    DeploymentSlot.objects.update_or_create(
        project=schedule.project,
        name=slot_name,
        defaults={
            "trained_model": model,
            "weight": 100,
            "is_active": True,
        },
    )
```

- Creates a new deployment slot named `auto-deploy-<project_name>`
- Or updates an existing slot with the same name
- Sets weight to 100 (full traffic)
- Sets the slot as active

## Retraining Run Lifecycle

### State Machine

```
          +-- RUNNING --+
          |             |
 PENDING -+             +-- COMPLETED
                        |
                        +-- FAILED
                        |
                        +-- SKIPPED
```

### Tracking for Async Retraining

When `retune=True`, the retraining flow is asynchronous:
1. `task_scheduled_retrain` creates the `RetrainingRun` and `ParameterTuningJob`
2. The tuning chord runs asynchronously
3. On completion, `_finalize_retraining_run()` updates the run status
4. On failure, `_fail_retraining_run_for_job()` marks the run as failed

This is handled in `task_create_best_config_train_rec` which calls the finalization functions after the tuning+training pipeline completes or fails.

## Optuna Storage

Optuna studies are stored in PostgreSQL (shared with the application database):

```python
@lru_cache(maxsize=1)
def get_optuna_storage() -> RDBStorage:
    db_url = settings.DATABASE_URL
    # Convert to psycopg3 dialect for SQLAlchemy
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return RDBStorage(db_url, engine_kwargs={"pool_size": 5, "max_overflow": 10})
```

- Connection pooling: 5 connections + 10 overflow
- Cached singleton (one storage instance per process)
- Parallel workers share the study via database-level synchronization

## Celery Beat Configuration

Celery Beat is configured with `django-celery-beat`'s `DatabaseScheduler`:

```
celery -A recotem beat --loglevel=INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

This reads periodic task schedules from the Django database (`django_celery_beat_*` tables), which are managed via the `RetrainingSchedule` model and the Django Admin interface.

## Result Storage

Task results are stored in PostgreSQL via `django-celery-results`:

```python
CELERY_RESULT_BACKEND = "django-db"
CELERY_RESULT_EXPIRES = 604800  # 7 days
```

Results are linked to domain objects via:
- `TaskAndParameterJobLink`: Links `TaskResult` to `ParameterTuningJob`
- `TaskAndTrainedModelLink`: Links `TaskResult` to `TrainedModel`
- `TaskLog`: Free-text log entries linked to `TaskResult`

## Error Recovery

### Task-Level Recovery

| Error Type | Behavior |
|---|---|
| `ConnectionError` / `OSError` | Auto-retry with exponential backoff (max 3) |
| `SoftTimeLimitExceeded` | Log timeout, set job FAILED, create TaskLog, re-raise |
| Other exceptions | Log error, set job FAILED, create TaskLog, re-raise |

### Retraining Run Recovery

When a tuning job linked to a retraining run fails:
1. `_fail_retraining_run_for_job()` is called
2. Finds the `RetrainingRun` linked to the `ParameterTuningJob`
3. Sets run status to FAILED with error message
4. Updates schedule's `last_run_status` to FAILED

### Data Consistency

- Best config creation uses `transaction.atomic()` + `select_for_update()` to prevent race conditions
- Job status transitions use atomic field updates: `ParameterTuningJob.objects.filter(...).update(status=...)`
- Task log buffering uses `finally` blocks to ensure flushing even on errors
