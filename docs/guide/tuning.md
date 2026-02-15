# Hyperparameter Tuning

This guide explains how Recotem automatically finds the best recommendation algorithm and settings for your data.

## What Is Hyperparameter Tuning?

Recommendation algorithms have settings (called hyperparameters) that control how they learn from data. For example, one setting might control how many hidden factors to use, while another controls the learning rate.

Finding the right combination of algorithm and settings can be tedious if done manually. Recotem automates this process using a technique called hyperparameter optimization, powered by Optuna. It systematically tries different algorithms and settings, evaluates each one, and keeps track of the best-performing configuration.

## Why Tune?

- **Better recommendations** -- The right settings can dramatically improve recommendation quality
- **No ML expertise needed** -- You do not need to understand the algorithms in detail; the system finds good settings automatically
- **Reproducible results** -- Tuning jobs record every configuration tried, so results can be reproduced

## Prerequisites

Before running a tuning job, you need:

1. A **project** with training data uploaded (see [Getting Started](getting-started.md))
2. A **split config** that defines how to divide data for evaluation
3. An **evaluation config** that defines which metric to optimize

## Step 1: Create a Split Config

A split config tells Recotem how to split your data into a training set and a test set. The training set is used to build the model, and the test set is used to measure how well the model predicts interactions it has not seen.

### Split Schemes

| Scheme | Code | How It Works | Best For |
|--------|------|-------------|----------|
| **Random** | `RG` | Randomly selects a fraction of each user's interactions for testing | General-purpose; works with any data |
| **Time Global** | `TG` | Uses the most recent interactions (by timestamp) across all users for testing | Data with timestamps where you want to simulate predicting future behavior |
| **Time User** | `TU` | Uses the most recent interactions per user for testing | Data with timestamps where each user has enough history |

### Key Settings

| Setting | Description | Default |
|---------|-------------|---------|
| **Heldout Ratio** | Fraction of interactions held out for testing (0.0 to 1.0). A value of 0.1 means 10% of data is used for testing. | `0.1` |
| **Test User Ratio** | Fraction of users included in the evaluation (0.0 to 1.0). A value of 1.0 means all users are evaluated. | `1.0` |
| **Random Seed** | A number that ensures the same split is produced every time. Use the same seed for reproducible comparisons. | `42` |

### Creating via the UI

1. Navigate to **Split Config** in the sidebar
2. Click **Create**
3. Choose a scheme and adjust settings as needed
4. Click **Save**

<!-- screenshot: Split Config creation form with scheme dropdown -->

### Creating via the API

```bash
curl -X POST http://localhost:8000/api/v1/split_config/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "80/20 Random Split",
    "scheme": "RG",
    "heldout_ratio": 0.2,
    "test_user_ratio": 1.0,
    "random_seed": 42
  }'
```

## Step 2: Create an Evaluation Config

An evaluation config defines how model quality is measured.

### Metrics

| Metric | Description | When to Use |
|--------|-------------|-------------|
| **NDCG** (Normalized Discounted Cumulative Gain) | Measures ranking quality, giving more credit to relevant items ranked higher in the list | Default choice; good for most use cases |
| **MAP** (Mean Average Precision) | Average precision at each position where a relevant item appears | When you care about precision at every position |
| **Recall** | Fraction of all relevant items that appear in the top-K recommendations | When you want to maximize coverage of relevant items |
| **Hit** (Hit Rate) | Whether at least one relevant item appears in the top-K | When any relevant recommendation is a success |

### Cutoff

The cutoff determines how many top items to evaluate. For example, a cutoff of 20 means the system checks whether relevant items appear in the top 20 recommendations. Choose a cutoff that matches how many items you plan to show to users.

### Creating via the UI

1. Navigate to **Evaluation Config** in the sidebar
2. Click **Create**
3. Select a metric and set the cutoff
4. Click **Save**

<!-- screenshot: Evaluation Config creation form -->

### Creating via the API

```bash
curl -X POST http://localhost:8000/api/v1/evaluation_config/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NDCG@20",
    "cutoff": 20,
    "target_metric": "ndcg"
  }'
```

## Step 3: Run a Tuning Job

### Creating via the UI

1. Navigate to **Tuning Jobs** in the sidebar
2. Click **Create Tuning Job**
3. Select your training data, split config, and evaluation config
4. Adjust tuning settings if desired
5. Click **Start**

<!-- screenshot: Create Tuning Job form -->

### Creating via the API

```bash
curl -X POST http://localhost:8000/api/v1/parameter_tuning_job/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "data": 1,
    "split": 1,
    "evaluation": 1,
    "n_trials": 40,
    "train_after_tuning": true
  }'
```

### Tuning Job Settings

| Setting | Description | Default |
|---------|-------------|---------|
| **Number of Trials** (`n_trials`) | How many different configurations to try. More trials may find better settings but take longer. | `40` |
| **Train After Tuning** (`train_after_tuning`) | Automatically train a model using the best configuration found. | `true` |
| **Memory Budget** (`memory_budget`) | Maximum memory (in MB) available for the tuning process. | `8000` |
| **Timeout Overall** (`timeout_overall`) | Maximum time (in seconds) for the entire tuning job. Leave empty for no limit. | None |
| **Timeout Single Step** (`timeout_singlestep`) | Maximum time (in seconds) for a single trial. | None |
| **Random Seed** (`random_seed`) | Seed for reproducibility. | None |
| **Tried Algorithms** (`tried_algorithms_json`) | A list of specific algorithm names to try. If empty, the system tries a default set. | None (uses defaults) |

## Available Algorithms

Recotem uses the irspack library, which includes several recommendation algorithms. By default, the tuning process tries multiple algorithms and picks the best one. Common algorithms include:

| Algorithm | Description |
|-----------|-------------|
| **IALSRecommender** | Implicit Alternating Least Squares -- a widely used collaborative filtering method that works well with implicit feedback data (views, clicks, purchases) |
| **CosineKNNRecommender** | K-Nearest Neighbors with cosine similarity -- recommends items similar to what a user has interacted with |
| **TopPopRecommender** | Recommends the most popular items -- serves as a simple baseline |
| **AsymmetricCosineKNNRecommender** | A variant of KNN that considers directional similarity between items |
| **TverskyIndexKNNRecommender** | KNN using Tversky similarity, which allows asymmetric comparison |
| **DenseSLIMRecommender** | A linear model approach that learns item-to-item weights |
| **P3alphaRecommender** | A graph-based method that uses random walks on the user-item graph |
| **RP3betaRecommender** | An extension of P3alpha that accounts for item popularity |

You do not need to choose an algorithm manually. The tuning process explores multiple algorithms and their hyperparameters automatically.

To restrict tuning to specific algorithms, pass them in the `tried_algorithms_json` field:

```bash
curl -X POST http://localhost:8000/api/v1/parameter_tuning_job/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "data": 1,
    "split": 1,
    "evaluation": 1,
    "n_trials": 40,
    "tried_algorithms_json": ["IALSRecommender", "CosineKNNRecommender"]
  }'
```

## Monitoring Progress

### Via the UI

The tuning job detail page shows real-time progress. As each trial completes, you can see:

- The algorithm and parameters tried
- The score achieved
- Whether it is the best configuration so far

Updates are delivered in real time via WebSocket, so you do not need to refresh the page.

<!-- screenshot: Tuning job progress page with trial results table -->

### Via the API

Check the job status:

```bash
curl http://localhost:8000/api/v1/parameter_tuning_job/1/ \
  -H "Authorization: Bearer $TOKEN"
```

**Job statuses:**

| Status | Meaning |
|--------|---------|
| `PENDING` | Job is queued and waiting to start |
| `RUNNING` | Tuning is in progress |
| `COMPLETED` | Tuning finished successfully |
| `FAILED` | An error occurred during tuning |

You can also view the task logs for detailed output:

```bash
curl "http://localhost:8000/api/v1/task_log/?tuning_job_id=1" \
  -H "Authorization: Bearer $TOKEN"
```

## Understanding Results

When a tuning job completes, the results include:

- **Best Configuration** (`best_config`) -- The ID of the model configuration that achieved the highest score. This configuration is saved automatically and can be used for training.
- **Best Score** (`best_score`) -- The value of the target metric achieved by the best configuration.
- **Tuned Model** (`tuned_model`) -- If "Train after tuning" was enabled, this is the ID of the model trained with the best configuration.

```bash
curl http://localhost:8000/api/v1/parameter_tuning_job/1/ \
  -H "Authorization: Bearer $TOKEN"
```

**Example response (completed job):**

```json
{
  "id": 1,
  "status": "COMPLETED",
  "best_score": 0.342,
  "best_config": 5,
  "tuned_model": 3,
  "n_trials": 40,
  "data": 1,
  "split": 1,
  "evaluation": 1,
  "train_after_tuning": true,
  "ins_datetime": "2025-01-15T11:00:00Z"
}
```

You can then view the best configuration's details:

```bash
curl http://localhost:8000/api/v1/model_configuration/5/ \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "id": 5,
  "name": "IALSRecommender-best",
  "project": 1,
  "recommender_class_name": "IALSRecommender",
  "parameters_json": {
    "n_components": 64,
    "alpha": 1.0,
    "reg": 0.01
  },
  "ins_datetime": "2025-01-15T11:30:00Z"
}
```

## API Reference

### Split Config

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/split_config/` | List split configs |
| `POST` | `/api/v1/split_config/` | Create a split config |
| `GET` | `/api/v1/split_config/{id}/` | Get details |
| `PATCH` | `/api/v1/split_config/{id}/` | Update |
| `DELETE` | `/api/v1/split_config/{id}/` | Delete |

### Evaluation Config

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/evaluation_config/` | List evaluation configs |
| `POST` | `/api/v1/evaluation_config/` | Create an evaluation config |
| `GET` | `/api/v1/evaluation_config/{id}/` | Get details |
| `PATCH` | `/api/v1/evaluation_config/{id}/` | Update |
| `DELETE` | `/api/v1/evaluation_config/{id}/` | Delete |

### Tuning Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/parameter_tuning_job/` | List tuning jobs |
| `POST` | `/api/v1/parameter_tuning_job/` | Create and start a tuning job |
| `GET` | `/api/v1/parameter_tuning_job/{id}/` | Get job details and results |
| `DELETE` | `/api/v1/parameter_tuning_job/{id}/` | Delete a tuning job |

### Model Configurations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/model_configuration/?project={id}` | List configurations for a project |
| `POST` | `/api/v1/model_configuration/` | Create a configuration manually |
| `GET` | `/api/v1/model_configuration/{id}/` | Get configuration details |
| `PATCH` | `/api/v1/model_configuration/{id}/` | Update a configuration |
| `DELETE` | `/api/v1/model_configuration/{id}/` | Delete a configuration |
