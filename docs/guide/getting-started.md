# Getting Started with Recotem

This guide walks you through the complete workflow: from your first login to getting real-time recommendations from a trained model.

## What You Will Learn

By the end of this guide, you will have:

- Created a recommendation project
- Uploaded user-item interaction data
- Tuned and trained a recommendation model
- Retrieved personalized recommendations via the API

## Prerequisites

- Recotem is running and accessible in your browser (see [Docker Compose Deployment](../deployment/docker-compose.md) or [Kubernetes Deployment](../deployment/kubernetes.md))
- A modern web browser (Chrome, Firefox, Safari, or Edge)
- A CSV file with user-item interaction data (or use the example below)

## Step 1: Log In

Open your browser and navigate to your Recotem instance (by default, `http://localhost:8000`).

You will see the login page. Enter the admin credentials that were configured during deployment:

- **Username**: `admin` (default)
- **Password**: The value of the `DEFAULT_ADMIN_PASSWORD` environment variable set during deployment

<!-- screenshot: Login page with username and password fields -->

After logging in, you will be taken to the Dashboard, which shows an overview of your projects.

<!-- screenshot: Dashboard page showing empty project list -->

## Step 2: Create a Project

A **project** is the top-level container for a recommendation task. It defines which columns in your data represent users, items, and (optionally) timestamps.

1. Click **Create Project** on the Dashboard
2. Fill in the project details:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | A descriptive name for your project | `Movie Recommendations` |
| **User Column** | The column name in your CSV that identifies users | `user_id` |
| **Item Column** | The column name in your CSV that identifies items | `movie_id` |
| **Time Column** | (Optional) The column name for timestamps. Leave blank if your data has no timestamps | `timestamp` |

<!-- screenshot: Create Project form with fields filled in -->

3. Click **Save**

You can also create a project via the API:

```bash
# First, obtain a JWT token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}' \
  | jq -r '.access')

# Create the project
curl -X POST http://localhost:8000/api/v1/project/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Movie Recommendations",
    "user_column": "user_id",
    "item_column": "movie_id",
    "time_column": "timestamp"
  }'
```

## Step 3: Upload Training Data

Training data is a CSV file where each row represents an interaction between a user and an item. At minimum, your CSV must contain the user and item columns you defined in the project.

**Example CSV format:**

```csv
user_id,movie_id,rating,timestamp
1,101,5,2024-01-15
1,203,4,2024-01-16
2,101,3,2024-01-15
2,305,5,2024-01-17
3,203,4,2024-01-18
3,305,2,2024-01-19
```

To upload:

1. Navigate to your project
2. Click **Training Data** in the sidebar
3. Click **Upload**
4. Select your CSV file
5. The system validates that the required columns exist in your file

<!-- screenshot: Training Data upload page with file selector -->

Recotem validates the file immediately after upload. If the required columns are missing, you will see an error message explaining which column was not found.

**Via API:**

```bash
curl -X POST http://localhost:8000/api/v1/training_data/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "project=1" \
  -F "file=@/path/to/your/interactions.csv"
```

## Step 4: Create a Split Config

A **split config** tells Recotem how to divide your data into training and test sets for evaluation. This is important because it lets the system measure how well the model performs on data it has not seen.

1. Navigate to **Split Config** in the sidebar
2. Click **Create**
3. Configure the split:

| Field | Description | Default |
|-------|-------------|---------|
| **Name** | A label for this config (optional) | |
| **Scheme** | How to split the data | `Random` |
| **Heldout Ratio** | Fraction of each user's interactions reserved for testing (0.0 to 1.0) | `0.1` |
| **Test User Ratio** | Fraction of users included in evaluation (0.0 to 1.0) | `1.0` |
| **Random Seed** | Seed for reproducibility | `42` |

<!-- screenshot: Split Config creation form -->

**Available split schemes:**

- **Random** -- Randomly holds out a fraction of interactions per user
- **Time Global** -- Uses the most recent interactions globally as the test set
- **Time User** -- Uses the most recent interactions per user as the test set

**Via API:**

```bash
curl -X POST http://localhost:8000/api/v1/split_config/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Default Split",
    "scheme": "RG",
    "heldout_ratio": 0.1,
    "test_user_ratio": 1.0,
    "random_seed": 42
  }'
```

## Step 5: Create an Evaluation Config

An **evaluation config** defines which metric to optimize and how many items to consider when measuring model quality.

1. Navigate to **Evaluation Config** in the sidebar
2. Click **Create**
3. Configure the evaluation:

| Field | Description | Default |
|-------|-------------|---------|
| **Name** | A label for this config (optional) | |
| **Cutoff** | Number of top items to consider when computing metrics | `20` |
| **Target Metric** | The metric to optimize during tuning | `ndcg` |

<!-- screenshot: Evaluation Config creation form -->

**Available metrics:**

| Metric | Full Name | What It Measures |
|--------|-----------|-----------------|
| `ndcg` | Normalized Discounted Cumulative Gain | How well the recommended items are ranked, giving more weight to items at the top of the list |
| `map` | Mean Average Precision | Average precision across all relevant items |
| `recall` | Recall | Fraction of relevant items that appear in the recommendations |
| `hit` | Hit Rate | Whether at least one relevant item appears in the recommendations |

**Via API:**

```bash
curl -X POST http://localhost:8000/api/v1/evaluation_config/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Default Evaluation",
    "cutoff": 20,
    "target_metric": "ndcg"
  }'
```

## Step 6: Run Parameter Tuning

**Parameter tuning** is an automated process that searches for the best algorithm and settings for your data. Recotem uses Optuna to efficiently explore different combinations.

1. Navigate to **Tuning Jobs** in the sidebar
2. Click **Create Tuning Job**
3. Select:
   - The **training data** you uploaded
   - The **split config** you created
   - The **evaluation config** you created
4. Optionally adjust:
   - **Number of trials** (default: 40) -- how many different configurations to try
   - **Train after tuning** (default: enabled) -- automatically train a model with the best configuration found

<!-- screenshot: Create Tuning Job form with data, split, and evaluation selections -->

5. Click **Start**

The tuning job runs in the background. You can monitor its progress on the tuning job detail page. The UI updates in real time via WebSocket.

<!-- screenshot: Tuning job progress page showing status and trial results -->

When the job completes, it saves the best model configuration automatically. If "Train after tuning" was enabled, a trained model is also created.

**Via API:**

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

The response includes the job ID. You can check the status:

```bash
curl http://localhost:8000/api/v1/parameter_tuning_job/1/ \
  -H "Authorization: Bearer $TOKEN"
```

Job statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`.

## Step 7: Train a Model

If you did not enable "Train after tuning" in the previous step, or if you want to train additional models from a specific configuration, you can do so manually.

1. Navigate to **Models** in the sidebar
2. Click **Train Model**
3. Select:
   - The **model configuration** (the best config from tuning, or any other configuration)
   - The **training data** to train on

<!-- screenshot: Train Model form with configuration and data selections -->

4. Click **Start Training**

Training runs in the background. Once complete, the model file is saved and signed with HMAC-SHA256 for security.

**Via API:**

```bash
curl -X POST http://localhost:8000/api/v1/trained_model/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "configuration": 1,
    "data_loc": 1
  }'
```

## Step 8: Create an API Key

To call the inference API, you need an API key with the `predict` scope.

1. Navigate to **API Keys** in the sidebar
2. Click **Create API Key**
3. Enter a name and select the `predict` scope
4. Click **Create**
5. **Copy the key immediately** -- it will not be shown again

<!-- screenshot: API Key creation form and the displayed key -->

**Via API:**

```bash
curl -X POST http://localhost:8000/api/v1/api_keys/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My First Key",
    "project": 1,
    "scopes": ["predict"]
  }'
```

The response includes a `key` field with the full API key (prefixed with `rctm_`). Store it securely.

For more details, see the [API Keys guide](api-keys.md).

## Step 9: Get Recommendations

Now you can call the inference API to get personalized recommendations for any user in your training data.

**Single user recommendations:**

```bash
curl -X POST http://localhost:8000/inference/predict/1 \
  -H "X-API-Key: rctm_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "1",
    "cutoff": 10
  }'
```

**Response:**

```json
{
  "items": [
    {"item_id": "305", "score": 0.95},
    {"item_id": "420", "score": 0.87},
    {"item_id": "112", "score": 0.82}
  ],
  "model_id": 1,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

Each item in the list is a recommended item for the user, sorted by score (highest first). The `cutoff` parameter controls how many items to return.

**Batch recommendations (multiple users at once):**

```bash
curl -X POST http://localhost:8000/inference/predict/1/batch \
  -H "X-API-Key: rctm_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "user_ids": ["1", "2", "3"],
    "cutoff": 10
  }'
```

For the complete inference API reference, see the [Inference API guide](inference.md).

## What's Next

Now that you have a working recommendation pipeline, explore these topics:

- **[Projects](projects.md)** -- Learn more about managing projects
- **[Data Management](data-management.md)** -- Preparing and uploading different data formats
- **[Tuning](tuning.md)** -- Advanced tuning options and available algorithms
- **[Training](training.md)** -- Model training details and comparison
- **[Deployment Slots](deployment-slots.md)** -- Deploy models for production serving with traffic splitting
- **[A/B Testing](ab-testing.md)** -- Compare models with statistical analysis
- **[Scheduled Retraining](retraining.md)** -- Keep models fresh with automatic retraining
- **[API Keys](api-keys.md)** -- Manage API access and scopes
- **[User Management](user-management.md)** -- Add users and manage permissions
