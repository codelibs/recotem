# Data Model Specification

## Overview

Recotem's data model is implemented as Django models in `backend/recotem/recotem/api/models/`. All domain models inherit from `ModelWithInsDatetime`, which provides automatic timestamping and reverse-chronological ordering. File-based models extend `BaseFileModel` for file storage and size tracking.

## Entity Relationship Diagram

```
                               ┌───────────┐
                               │   User    │
                               │ (Django)  │
                               └─────┬─────┘
                                     │
                    ┌────────────────┬┼────────────────┐
                    │ owner          ││ created_by      │ owner
                    ▼                ▼│                 ▼
              ┌───────────┐   ┌──────┴──────┐   ┌───────────┐
              │  Project   │   │ SplitConfig  │   │  ApiKey   │
              │            │   └──────┬──────┘   │           │
              └─────┬──────┘          │          └───────────┘
                    │                 │               │
       ┌────────────┼────────────┐   │          FK to Project
       │            │            │   │
       ▼            ▼            ▼   │    ┌──────────────────┐
 ┌──────────┐ ┌──────────┐ ┌──────────┐  │ EvaluationConfig │
 │ Training │ │ ItemMeta │ │  Model   │  │                  │
 │   Data   │ │   Data   │ │  Config  │  └────────┬─────────┘
 │ (file)   │ │ (file)   │ │          │           │
 └────┬─────┘ └──────────┘ └────┬─────┘           │
      │                         │                  │
      │    ┌────────────────────┼──────────────────┘
      │    │                    │
      ▼    ▼                    ▼
 ┌─────────────────┐    ┌─────────────┐
 │ParameterTuning  │    │  Trained    │
 │     Job         │───►│   Model     │
 │                 │    │  (file)     │
 └────────┬────────┘    └──────┬──────┘
          │                    │
    ┌─────┼─────┐              │
    ▼           ▼              ▼
 ┌──────┐  ┌──────┐    ┌──────────────┐
 │Task& │  │Task& │    │ Deployment   │
 │Param │  │Model │    │    Slot      │
 │Link  │  │Link  │    └──────┬───────┘
 └──────┘  └──────┘           │
                         ┌────┼────┐
                         ▼         ▼
                   ┌──────────┐ ┌──────────────┐
                   │  ABTest  │ │ Conversion   │
                   │          │ │   Event      │
                   └──────────┘ └──────────────┘

 ┌──────────────────┐     ┌──────────────────┐
 │   Retraining     │────►│  Retraining      │
 │   Schedule       │     │    Run           │
 └──────────────────┘     └──────────────────┘
        │
   FK to Project
```

## Base Classes

### ModelWithInsDatetime

All domain models inherit from this abstract base class.

| Field | Type | Description |
|---|---|---|
| `ins_datetime` | `DateTimeField(auto_now_add=True)` | Creation timestamp |
| `updated_at` | `DateTimeField(auto_now=True)` | Last modification timestamp |

- **Meta**: `abstract = True`, `ordering = ["-id"]` (newest first)

### BaseFileModel

Inherited by models that store uploaded files (`TrainingData`, `ItemMetaData`, `TrainedModel`).

| Field | Type | Description |
|---|---|---|
| `file` | `FileField` | Stored file reference |
| `filesize` | `IntegerField` | File size in bytes (populated on save signal) |

## Model Definitions

### Project

The top-level organizational entity. Defines the column mapping for user/item interaction data.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | `CharField(max_length=256)` | Unique per owner | Human-readable project name |
| `owner` | `ForeignKey(User)` | `null=True`, CASCADE | Project owner; NULL for legacy unowned data |
| `user_column` | `CharField(max_length=256)` | Required | Name of user ID column in training data |
| `item_column` | `CharField(max_length=256)` | Required | Name of item ID column in training data |
| `time_column` | `CharField(max_length=256)` | `null=True` | Optional timestamp column name |

**Constraints**: `UniqueConstraint(fields=["owner", "name"], name="unique_project_name_per_owner")`

**Design note**: `owner` is nullable for backward compatibility with data created before multi-user support. Unowned projects (`owner=NULL`) are visible to all authenticated users via `OwnedResourceMixin`.

### TrainingData

Uploaded CSV file containing user-item interaction records.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE, indexed | Parent project |
| `file` | Inherited from `BaseFileModel` | -- | Uploaded CSV/TSV/Parquet file |
| `filesize` | Inherited from `BaseFileModel` | -- | File size (populated by `post_save` signal) |

**Validation**: `validate_return_df()` verifies that the file contains the columns defined in the project (`user_column`, `item_column`, optional `time_column`).

### ItemMetaData

Optional item metadata file for feature-enriched recommendations.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE, indexed | Parent project |
| `valid_columns_list_json` | `JSONField` | `null=True` | List of valid feature columns |
| `file` | Inherited from `BaseFileModel` | -- | Uploaded metadata file |

### SplitConfig

Configuration for train/validation data splitting.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `CharField(max_length=256)` | `null=True` | Optional display name |
| `created_by` | `ForeignKey(User)` | `null=True`, SET_NULL | Creator (NULL for legacy) |
| `scheme` | `CharField(choices)` | `"RG"` (Random) | Split strategy: RG/TG/TU |
| `heldout_ratio` | `FloatField` | `0.1` | Fraction of interactions held out [0.0, 1.0] |
| `n_heldout` | `IntegerField` | `null=True` | Absolute number of heldout items |
| `test_user_ratio` | `FloatField` | `1.0` | Fraction of users used for testing [0.0, 1.0] |
| `n_test_users` | `IntegerField` | `null=True` | Absolute number of test users |
| `random_seed` | `IntegerField` | `42` | Random seed for reproducibility |

**Split schemes**:
- `RG` (Random): Random interaction holdout
- `TG` (Time Global): Global time-based split
- `TU` (Time User): Per-user time-based split

### EvaluationConfig

Configuration for model evaluation metrics.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `CharField(max_length=256)` | `null=True` | Optional display name |
| `cutoff` | `IntegerField` | `20` | Top-K cutoff for evaluation |
| `created_by` | `ForeignKey(User)` | `null=True`, SET_NULL | Creator (NULL for legacy) |
| `target_metric` | `CharField(choices)` | `"ndcg"` | Metric to optimize |

**Target metrics**: `ndcg`, `map`, `recall`, `hit`

### ModelConfiguration

Recommender algorithm configuration with hyperparameters.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | `CharField(max_length=256)` | `null=True` | Display name; unique per project |
| `project` | `ForeignKey(Project)` | CASCADE, indexed | Parent project |
| `recommender_class_name` | `CharField(max_length=128)` | Validated Python identifier | irspack recommender class name |
| `parameters_json` | `JSONField` | default `{}` | Hyperparameter key-value pairs |

**Constraints**: `UniqueConstraint(fields=["project", "name"], name="unique_model_config_name_per_project")`

**Validation**: `recommender_class_name` must match `^[A-Za-z_][A-Za-z0-9_]*$`.

### TrainedModel

A trained recommendation model stored as an HMAC-signed serialized file.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `configuration` | `ForeignKey(ModelConfiguration)` | CASCADE, indexed | Algorithm configuration used |
| `data_loc` | `ForeignKey(TrainingData)` | CASCADE, indexed | Training data used |
| `irspack_version` | `CharField(max_length=16)` | `null=True` | irspack version at training time |
| `file` | Inherited from `BaseFileModel` | -- | HMAC-SHA256 signed serialized file |

**File format**: `HMAC_SIGNATURE (32 bytes) + SERIALIZED_PAYLOAD`. The payload contains a dict with keys `id_mapped_recommender`, `irspack_version`, `recotem_trained_model_id`.

### ParameterTuningJob

Orchestrates hyperparameter search using Optuna.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `data` | `ForeignKey(TrainingData)` | CASCADE, indexed | Training data to tune on |
| `split` | `ForeignKey(SplitConfig)` | CASCADE | Data split configuration |
| `evaluation` | `ForeignKey(EvaluationConfig)` | CASCADE | Evaluation configuration |
| `status` | `CharField(choices)` | default `"PENDING"`, indexed | PENDING/RUNNING/COMPLETED/FAILED |
| `n_tasks_parallel` | `IntegerField` | default `1` | Number of parallel Celery workers |
| `n_trials` | `IntegerField` | default `40` | Total Optuna trials |
| `memory_budget` | `IntegerField` | default `8000` | Memory budget (MB) |
| `timeout_overall` | `IntegerField` | `null=True` | Overall timeout (seconds) |
| `timeout_singlestep` | `IntegerField` | `null=True` | Per-trial timeout (seconds) |
| `random_seed` | `IntegerField` | `null=True` | Random seed |
| `tried_algorithms_json` | `JSONField` | `null=True` | List of algorithm names to try |
| `irspack_version` | `CharField(max_length=16)` | `null=True` | irspack version used |
| `train_after_tuning` | `BooleanField` | default `True` | Auto-train best config |
| `tuned_model` | `OneToOneField(TrainedModel)` | `null=True`, SET_NULL | Resulting trained model |
| `best_config` | `OneToOneField(ModelConfiguration)` | `null=True`, SET_NULL | Best configuration found |
| `best_score` | `FloatField` | `null=True` | Best evaluation score achieved |

**Methods**: `study_name()` returns `"job-{id}-{ins_datetime}"` for Optuna study identification.

### TaskAndParameterJobLink

Links Celery `TaskResult` entries to `ParameterTuningJob`.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `job` | `ForeignKey(ParameterTuningJob)` | CASCADE, related `task_links` | Parent tuning job |
| `task` | `OneToOneField(TaskResult)` | CASCADE, related `tuning_job_link` | Celery task result |

### TaskAndTrainedModelLink

Links Celery `TaskResult` entries to `TrainedModel`.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `model` | `ForeignKey(TrainedModel)` | CASCADE, related `task_links` | Parent trained model |
| `task` | `OneToOneField(TaskResult)` | CASCADE, related `model_link` | Celery task result |

### ApiKey

API key for programmatic access to project resources.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE, related `api_keys` | Scoped to this project |
| `owner` | `ForeignKey(User)` | CASCADE, related `api_keys` | Key creator/owner |
| `name` | `CharField(max_length=256)` | Unique per project | Human-readable key name |
| `key_prefix` | `CharField(max_length=16)` | indexed | First 8 chars of random part for lookup |
| `hashed_key` | `CharField(max_length=256)` | -- | PBKDF2-SHA256 hash of full key |
| `scopes` | `JSONField` | default `[]` | Permission scopes: `read`, `write`, `predict` |
| `is_active` | `BooleanField` | default `True` | Whether the key is active |
| `expires_at` | `DateTimeField` | `null=True` | Optional expiration timestamp |
| `last_used_at` | `DateTimeField` | `null=True` | Last usage timestamp |

**Constraints**: `UniqueConstraint(fields=["project", "name"], name="unique_api_key_name_per_project")`

**Key format**: `rctm_<random_urlsafe_base64>` (prefix `rctm_`, 48-character random part)

### TaskLog

Log entries associated with Celery task execution.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `task` | `ForeignKey(TaskResult)` | CASCADE | Celery task result |
| `contents` | `TextField` | blank allowed | Log message content |

### RetrainingSchedule

Defines periodic model retraining configuration.

| Field | Type | Default | Description |
|---|---|---|---|
| `project` | `OneToOneField(Project)` | CASCADE, related `retraining_schedule` | One schedule per project |
| `is_enabled` | `BooleanField` | `False` | Whether schedule is active |
| `cron_expression` | `CharField(max_length=100)` | `"0 2 * * 0"` | Cron schedule |
| `training_data` | `ForeignKey(TrainingData)` | `null=True`, SET_NULL | Specific data; or latest if NULL |
| `model_configuration` | `ForeignKey(ModelConfiguration)` | `null=True`, SET_NULL | Config for train-only mode |
| `retune` | `BooleanField` | `False` | Whether to re-run tuning |
| `split_config` | `ForeignKey(SplitConfig)` | `null=True`, SET_NULL | Required if retune=True |
| `evaluation_config` | `ForeignKey(EvaluationConfig)` | `null=True`, SET_NULL | Required if retune=True |
| `max_retries` | `IntegerField` | `3` | Max retry attempts |
| `notify_on_failure` | `BooleanField` | `True` | Send failure notifications |
| `last_run_at` | `DateTimeField` | `null=True` | Timestamp of last execution |
| `last_run_status` | `CharField(choices)` | `null=True` | SUCCESS/FAILED/SKIPPED |
| `next_run_at` | `DateTimeField` | `null=True` | Next scheduled execution |
| `auto_deploy` | `BooleanField` | `False` | Auto-deploy trained model to slot |

### RetrainingRun

Record of a single retraining execution.

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `ForeignKey(RetrainingSchedule)` | CASCADE, related `runs` | Parent schedule |
| `status` | `CharField(choices)` | `"PENDING"` | PENDING/RUNNING/COMPLETED/FAILED/SKIPPED |
| `trained_model` | `ForeignKey(TrainedModel)` | `null=True`, SET_NULL | Resulting trained model |
| `tuning_job` | `ForeignKey(ParameterTuningJob)` | `null=True`, SET_NULL | Associated tuning job (if retune) |
| `error_message` | `TextField` | `""` | Error details on failure |
| `completed_at` | `DateTimeField` | `null=True` | Completion timestamp |
| `data_rows_at_trigger` | `IntegerField` | `null=True` | Data size at trigger time |

### DeploymentSlot

A slot that maps a trained model to serving with a traffic weight for A/B testing.

| Field | Type | Default | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE, related `deployment_slots` | Parent project |
| `name` | `CharField(max_length=256)` | -- | Slot display name |
| `trained_model` | `ForeignKey(TrainedModel)` | CASCADE | Model served by this slot |
| `weight` | `FloatField` | `100` | Traffic weight [0.0, 100.0] |
| `is_active` | `BooleanField` | `True` | Whether slot is active |

### ABTest

A/B test comparing two deployment slots.

| Field | Type | Default | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE, related `ab_tests` | Parent project |
| `name` | `CharField(max_length=256)` | -- | Test name |
| `status` | `CharField(choices)` | `"DRAFT"` | DRAFT/RUNNING/COMPLETED/CANCELLED |
| `control_slot` | `ForeignKey(DeploymentSlot)` | CASCADE, related `control_tests` | Control (baseline) slot |
| `variant_slot` | `ForeignKey(DeploymentSlot)` | CASCADE, related `variant_tests` | Variant (challenger) slot |
| `target_metric_name` | `CharField(max_length=50)` | `"ctr"` | Metric: ctr/purchase_rate/conversion_rate |
| `min_sample_size` | `IntegerField` | `1000` | Minimum impressions before analysis |
| `confidence_level` | `FloatField` | `0.95` | Statistical confidence [0.5, 0.99] |
| `started_at` | `DateTimeField` | `null=True` | Test start timestamp |
| `ended_at` | `DateTimeField` | `null=True` | Test end timestamp |
| `winner_slot` | `ForeignKey(DeploymentSlot)` | `null=True`, SET_NULL, related `won_tests` | Promoted winner slot |

### ConversionEvent

Tracking event for A/B test analysis. Note: This model inherits directly from `models.Model` (not `ModelWithInsDatetime`).

| Field | Type | Constraints | Description |
|---|---|---|---|
| `project` | `ForeignKey(Project)` | CASCADE | Parent project |
| `deployment_slot` | `ForeignKey(DeploymentSlot)` | CASCADE | Slot that served the recommendation |
| `user_id` | `CharField(max_length=256)` | -- | User identifier |
| `item_id` | `CharField(max_length=256)` | default `""` | Item identifier |
| `event_type` | `CharField(choices)` | -- | impression/click/purchase |
| `recommendation_request_id` | `UUIDField` | `null=True` | Links to inference request ID |
| `timestamp` | `DateTimeField(auto_now_add=True)` | -- | Event timestamp |
| `metadata_json` | `JSONField` | default `{}` | Arbitrary metadata |

**Indexes**: Composite index on `(project, deployment_slot, event_type, timestamp)` for efficient A/B test result queries.

## Signals

1. **`create_auth_token`**: `post_save` on `User` -- creates a DRF `Token` for each new user (legacy auth support).
2. **`save_file_size`**: `post_save` on `TrainingData` -- populates `filesize` after file upload.

## Key Relationships Summary

```
User ──1:N──► Project ──1:N──► TrainingData
                       ──1:N──► ItemMetaData
                       ──1:N──► ModelConfiguration
                       ──1:N──► DeploymentSlot
                       ──1:N──► ABTest
                       ──1:N──► ApiKey
                       ──1:1──► RetrainingSchedule

TrainingData ──1:N──► ParameterTuningJob
             ──1:N──► TrainedModel

ModelConfiguration ──1:N──► TrainedModel

ParameterTuningJob ──1:1──► ModelConfiguration (best_config)
                   ──1:1──► TrainedModel (tuned_model)
                   ──1:N──► TaskAndParameterJobLink

TrainedModel ──1:N──► DeploymentSlot
             ──1:N──► TaskAndTrainedModelLink

DeploymentSlot ──1:N──► ABTest (control or variant)
               ──1:N──► ConversionEvent

RetrainingSchedule ──1:N──► RetrainingRun
```

## Database Tables

All Django model tables use the prefix `api_` (from the app label `recotem.api`). Table names follow Django convention: `api_project`, `api_trainingdata`, `api_trainedmodel`, etc. Additionally, `django_celery_results_taskresult` and `django_celery_beat_*` tables are managed by their respective packages.
