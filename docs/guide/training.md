# Training Models

This guide explains how to train recommendation models from configurations, compare their performance, and prepare them for serving.

## What Is Model Training?

Model training is the process of building a recommendation model from a configuration and a dataset. The configuration specifies which algorithm to use and what settings to apply. The training data provides the user-item interactions that the model learns from.

Once training is complete, the resulting model file can generate personalized recommendations for any user in the data.

## Why Train Models?

- **Serve recommendations** -- A trained model is required before you can call the inference API
- **Compare approaches** -- Train multiple models with different configurations and compare their quality
- **Keep models fresh** -- Retrain periodically as new interaction data becomes available

## Prerequisites

- A project with training data uploaded (see [Data Management](data-management.md))
- A model configuration, either from a completed tuning job (see [Tuning](tuning.md)) or created manually

## Auto-Train vs Manual Train

There are two ways to train a model:

### Auto-Train (After Tuning)

When you create a tuning job with `train_after_tuning` set to `true` (the default), the system automatically trains a model using the best configuration found during tuning. This is the simplest approach -- the tuning job produces a ready-to-use model without any extra steps.

### Manual Train

You can also train a model explicitly by specifying a configuration and a training dataset. This is useful when you want to:

- Train with a different dataset than the one used for tuning
- Retrain a model with updated data
- Train from a manually created configuration

## Training a Model

### Via the UI

1. Navigate to **Models** in the sidebar
2. Click **Train Model**
3. Select:
   - The **model configuration** to use (browse configurations from tuning results or manually created ones)
   - The **training data** to train on
4. Click **Start Training**

<!-- screenshot: Train Model form with configuration and training data selections -->

Training runs in the background. The model detail page shows the current status.

<!-- screenshot: Model detail page showing training in progress -->

### Via the API

```bash
curl -X POST http://localhost:8000/api/v1/trained_model/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "configuration": 5,
    "data_loc": 1
  }'
```

| Field | Required | Description |
|-------|----------|-------------|
| `configuration` | Yes | ID of the model configuration to use |
| `data_loc` | Yes | ID of the training data to train on |

**Response:**

```json
{
  "id": 3,
  "configuration": 5,
  "data_loc": 1,
  "file": null,
  "irspack_version": null,
  "ins_datetime": "2025-01-15T12:00:00Z",
  "basename": null,
  "filesize": null,
  "task_links": []
}
```

The `file`, `irspack_version`, `basename`, and `filesize` fields are populated after training completes.

**Note:** The configuration and training data must belong to the same project. The API returns an error if they belong to different projects.

## Monitoring Training

Training runs as a background task. You can check progress:

### Via the UI

The model detail page updates in real time via WebSocket, showing the current training status.

<!-- screenshot: Model training status with progress indicator -->

### Via the API

```bash
curl http://localhost:8000/api/v1/trained_model/3/ \
  -H "Authorization: Bearer $TOKEN"
```

When training is complete, the response includes the model file details:

```json
{
  "id": 3,
  "configuration": 5,
  "data_loc": 1,
  "file": "/media/trained_models/model_3.pkl",
  "irspack_version": "0.4.0",
  "ins_datetime": "2025-01-15T12:00:00Z",
  "basename": "model_3.pkl",
  "filesize": 2456789,
  "task_links": [
    {
      "task": {
        "task_id": "abc123",
        "status": "SUCCESS"
      }
    }
  ]
}
```

You can also view detailed training logs:

```bash
curl "http://localhost:8000/api/v1/task_log/?model_id=3" \
  -H "Authorization: Bearer $TOKEN"
```

## Model File Security

Every trained model file is signed with HMAC-SHA256 using the application's secret key. This ensures that:

- Model files have not been tampered with
- Only models created by your Recotem instance can be loaded
- Corrupted files are detected before they are used for predictions

This signing happens automatically -- you do not need to do anything special.

## Comparing Models

When you have multiple trained models, you can compare them by looking at the tuning scores and the configurations used.

### Listing Models

**Via the UI:**

The Models page shows all trained models for your project with their configuration details.

<!-- screenshot: Models list page showing multiple models with configurations and dates -->

**Via the API:**

```bash
# List all models for a project
curl "http://localhost:8000/api/v1/trained_model/?data_loc__project=1" \
  -H "Authorization: Bearer $TOKEN"
```

### Comparing Recommendation Quality

You can test a model by getting sample recommendations:

```bash
# Get sample recommendations from a model (randomly selects a user)
curl http://localhost:8000/api/v1/trained_model/3/sample_recommendation_raw/ \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "user_id": "42",
  "user_profile": ["101", "203", "305"],
  "recommendations": [
    {"item_id": "420", "score": 0.95},
    {"item_id": "112", "score": 0.87},
    {"item_id": "550", "score": 0.82}
  ]
}
```

This shows:
- `user_id` -- The randomly selected user
- `user_profile` -- Items the user has previously interacted with
- `recommendations` -- The model's top recommendations for this user

If you have uploaded item metadata, you can get enriched sample recommendations:

```bash
curl http://localhost:8000/api/v1/trained_model/3/sample_recommendation_metadata/1/ \
  -H "Authorization: Bearer $TOKEN"
```

This replaces raw item IDs with metadata (such as titles and categories), making it easier to visually assess recommendation quality.

### Getting Recommendations for a Specific User

```bash
curl "http://localhost:8000/api/v1/trained_model/3/recommendation/?user_id=42&cutoff=10" \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
[
  {"item_id": "420", "score": 0.95},
  {"item_id": "112", "score": 0.87},
  {"item_id": "550", "score": 0.82}
]
```

### New User Recommendations

You can also get recommendations for users who are not in the training data by providing a list of items they have interacted with:

```bash
curl -X POST http://localhost:8000/api/v1/trained_model/3/recommend_using_profile_interaction/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "item_ids": ["101", "203"],
    "cutoff": 10
  }'
```

**Response:**

```json
{
  "recommendations": [
    {"item_id": "305", "score": 0.91},
    {"item_id": "420", "score": 0.85}
  ]
}
```

## Deleting Models

Models can be deleted when they are no longer needed:

```bash
curl -X DELETE http://localhost:8000/api/v1/trained_model/3/ \
  -H "Authorization: Bearer $TOKEN"
```

Be careful not to delete models that are assigned to active deployment slots.

## Next Steps

Once you have a trained model you are satisfied with:

- **[Deployment Slots](deployment-slots.md)** -- Assign the model to a deployment slot for production serving
- **[Inference API](inference.md)** -- Call the inference API to get recommendations
- **[A/B Testing](ab-testing.md)** -- Compare models using real user interactions
- **[Scheduled Retraining](retraining.md)** -- Set up automatic retraining to keep models fresh

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/trained_model/?data_loc__project={id}` | List models for a project |
| `POST` | `/api/v1/trained_model/` | Train a new model |
| `GET` | `/api/v1/trained_model/{id}/` | Get model details |
| `DELETE` | `/api/v1/trained_model/{id}/` | Delete a model |
| `GET` | `/api/v1/trained_model/{id}/download/` | Download the model file |
| `GET` | `/api/v1/trained_model/{id}/sample_recommendation_raw/` | Get sample recommendations |
| `GET` | `/api/v1/trained_model/{id}/sample_recommendation_metadata/{meta_id}/` | Get sample recommendations with metadata |
| `GET` | `/api/v1/trained_model/{id}/recommendation/?user_id={uid}&cutoff={n}` | Get recommendations for a specific user |
| `POST` | `/api/v1/trained_model/{id}/recommend_using_profile_interaction/` | Get recommendations for a new user profile |
