# Deployment Slots

This guide explains how to deploy trained models for serving real-time recommendations in production.

## What Are Deployment Slots?

A deployment slot connects a trained model to a project for serving predictions through the inference API. Think of it as assigning a model to "go live" for a project.

Each project can have multiple deployment slots, each pointing to a different model. When the inference API receives a project-level prediction request, it selects one of the active slots based on their weights. This enables smooth traffic distribution and A/B testing.

## Why Use Deployment Slots?

- **Controlled rollouts** -- Gradually shift traffic from an old model to a new one
- **A/B testing** -- Compare two models side by side with real users (see [A/B Testing](ab-testing.md))
- **Easy rollback** -- If a new model underperforms, deactivate its slot and revert to the previous one
- **Zero-downtime updates** -- Swap models without restarting any services

## Prerequisites

- A trained model (see [Training](training.md))
- An API key with `predict` scope for calling the inference API (see [API Keys](api-keys.md))

## Creating a Deployment Slot

### Via the UI

1. Navigate to your project
2. Click **Deployment Slots** in the sidebar
3. Click **Create Slot**
4. Fill in the details:

<!-- screenshot: Create Deployment Slot form -->

| Field | Required | Description |
|-------|----------|-------------|
| **Name** | Yes | A descriptive label (e.g., "Production", "Candidate Model", "Variant A") |
| **Trained Model** | Yes | The model to serve. Must belong to the same project. |
| **Weight** | Yes | Traffic weight (0 to 100). Controls what fraction of requests this slot handles relative to other active slots. |
| **Active** | Yes | Whether this slot is currently serving traffic. Defaults to active. |

5. Click **Save**

### Via the API

```bash
curl -X POST http://localhost:8000/api/v1/deployment_slot/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "name": "Production",
    "trained_model": 3,
    "weight": 100,
    "is_active": true
  }'
```

**Response:**

```json
{
  "id": 1,
  "project": 1,
  "name": "Production",
  "trained_model": 3,
  "weight": 100.0,
  "is_active": true,
  "ins_datetime": "2025-01-15T14:00:00Z",
  "updated_at": "2025-01-15T14:00:00Z"
}
```

## How Traffic Distribution Works

When you call the project-level inference endpoint (`POST /inference/predict/project/{project_id}`), the service selects a slot based on the weights of all **active** slots for that project.

**Example: Single slot**

If you have one active slot with weight 100, all requests go to that slot's model.

```
Slot "Production" (weight: 100) --> 100% of traffic
```

**Example: Two slots for A/B testing**

If you have two active slots with equal weights, traffic is split evenly:

```
Slot "Control"   (weight: 50) --> 50% of traffic
Slot "Variant A" (weight: 50) --> 50% of traffic
```

**Example: Gradual rollout**

Start with most traffic on the existing model, then gradually increase the new model's share:

```
Slot "Current Model" (weight: 90) --> 90% of traffic
Slot "New Model"     (weight: 10) --> 10% of traffic
```

The weight values do not need to add up to 100. The system calculates proportions based on the relative weights. For instance, slots with weights 30 and 70 produce the same split as slots with weights 3 and 7.

## Calling the Inference API with Slots

Once you have active deployment slots, use the project-level inference endpoint:

```bash
curl -X POST http://localhost:8000/inference/predict/project/1 \
  -H "X-API-Key: rctm_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "42",
    "cutoff": 10
  }'
```

**Response:**

```json
{
  "items": [
    {"item_id": "305", "score": 0.95},
    {"item_id": "420", "score": 0.87}
  ],
  "model_id": 3,
  "slot_id": 1,
  "slot_name": "Production",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

The response includes `slot_id` and `slot_name` so your application can track which model served each request. The `request_id` is useful for recording conversion events in A/B tests.

If no active deployment slots exist for the project, the API returns a 404 error.

## Managing Deployment Slots

### Listing Slots

```bash
# All slots for a project
curl "http://localhost:8000/api/v1/deployment_slot/?project=1" \
  -H "Authorization: Bearer $TOKEN"

# Only active slots
curl "http://localhost:8000/api/v1/deployment_slot/?project=1&is_active=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Updating a Slot

You can change a slot's model, weight, or active status:

```bash
# Change the model a slot points to
curl -X PATCH http://localhost:8000/api/v1/deployment_slot/1/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trained_model": 7}'

# Adjust traffic weight
curl -X PATCH http://localhost:8000/api/v1/deployment_slot/1/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"weight": 75}'
```

### Activating and Deactivating Slots

Deactivating a slot removes it from traffic routing without deleting it. This is useful for pausing a model or performing maintenance.

```bash
# Deactivate a slot
curl -X PATCH http://localhost:8000/api/v1/deployment_slot/2/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# Reactivate a slot
curl -X PATCH http://localhost:8000/api/v1/deployment_slot/2/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_active": true}'
```

### Deleting a Slot

```bash
curl -X DELETE http://localhost:8000/api/v1/deployment_slot/2/ \
  -H "Authorization: Bearer $TOKEN"
```

## Common Patterns

### Single Production Model

The simplest setup: one active slot serving all traffic.

```bash
curl -X POST http://localhost:8000/api/v1/deployment_slot/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "name": "Production",
    "trained_model": 3,
    "weight": 100
  }'
```

### Model Swap

To replace the production model with a new one:

```bash
# Update the existing slot to point to the new model
curl -X PATCH http://localhost:8000/api/v1/deployment_slot/1/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trained_model": 7}'
```

### Canary Deployment

Send a small percentage of traffic to a new model to verify it works correctly:

```bash
# Existing production slot (already at weight 100)
# Create a canary slot with low weight
curl -X POST http://localhost:8000/api/v1/deployment_slot/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project": 1,
    "name": "Canary",
    "trained_model": 7,
    "weight": 5
  }'
```

This sends about 5% of traffic to the new model (`5 / (100 + 5) = ~4.8%`).

### A/B Testing

For formal A/B testing with statistical analysis, use deployment slots in combination with the A/B Testing feature. See the [A/B Testing guide](ab-testing.md) for details.

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/deployment_slot/?project={id}` | List slots for a project |
| `POST` | `/api/v1/deployment_slot/` | Create a new slot |
| `GET` | `/api/v1/deployment_slot/{id}/` | Get slot details |
| `PATCH` | `/api/v1/deployment_slot/{id}/` | Update a slot (model, weight, active status) |
| `DELETE` | `/api/v1/deployment_slot/{id}/` | Delete a slot |
| `POST` | `/inference/predict/project/{project_id}` | Get recommendations via slot routing |
