# A/B Testing

Recotem supports A/B testing of recommendation models through deployment slots with weighted traffic splitting and statistical analysis.

## Concepts

### What is A/B Testing?

A/B testing is a method for comparing two options to figure out which one works better. Imagine you have two recommendation models and you want to know which one leads to more clicks from your users. Instead of guessing, you split your traffic so that some users get recommendations from Model A and others get recommendations from Model B. After collecting enough data, you use statistics to determine which model actually performed better.

**Why use A/B testing with recommendations?**

- **Make data-driven decisions** -- instead of assuming a new model is better, prove it with real user behavior.
- **Reduce risk** -- roll out a new model gradually rather than switching all traffic at once.
- **Measure impact** -- quantify exactly how much a new model improves (or hurts) key metrics like click-through rate or purchase rate.

In Recotem, A/B testing is built into the deployment system. You assign different models to deployment slots, split traffic between them, and Recotem tracks how each model performs.

### Deployment Slots

A deployment slot assigns a trained model to a project with a traffic weight. When the inference API receives a project-level prediction request, it selects a slot based on weights.

- Each project can have multiple deployment slots
- Slots have a `weight` (0-100) controlling traffic distribution
- Slots can be activated/deactivated independently
- Slot selection is proportional to weight (e.g., two slots with weights 50 and 50 get equal traffic)

### A/B Tests

An A/B test compares two deployment slots (control vs. variant) using conversion events to determine which model performs better.

- Uses a two-proportion Z-test for statistical significance
- Configurable target metric, minimum sample size, and confidence level
- Results include p-value, z-score, lift percentage, and confidence interval

### Conversion Events

Events recorded against deployment slots for analysis. Types: `impression`, `click`, `purchase`.

## Workflow

### 1. Create Deployment Slots

```bash
# Control: existing model
curl -X POST http://localhost:8000/api/v1/deployment_slot/ \
  -H "Authorization: Bearer <jwt_token>" \
  -d '{"project": 1, "name": "Control", "trained_model": 3, "weight": 50}'

# Variant: new model
curl -X POST http://localhost:8000/api/v1/deployment_slot/ \
  -H "Authorization: Bearer <jwt_token>" \
  -d '{"project": 1, "name": "Variant A", "trained_model": 7, "weight": 50}'
```

### 2. Create an A/B Test

```bash
curl -X POST http://localhost:8000/api/v1/ab_test/ \
  -H "Authorization: Bearer <jwt_token>" \
  -d '{
    "project": 1,
    "name": "New Algorithm Test",
    "control_slot": 1,
    "variant_slot": 2,
    "target_metric_name": "ctr",
    "min_sample_size": 1000,
    "confidence_level": 0.95
  }'
```

### 3. Start the Test

```bash
curl -X POST http://localhost:8000/api/v1/ab_test/1/start/ \
  -H "Authorization: Bearer <jwt_token>"
```

### 4. Serve Recommendations

Call the project-level inference endpoint. The service routes traffic based on slot weights:

```bash
curl -X POST http://localhost:8000/inference/predict/project/1 \
  -H "X-API-Key: rctm_your_key" \
  -d '{"user_id": "42", "cutoff": 10}'
```

The response includes `slot_id`, `slot_name`, and `request_id` for tracking.

### 5. Record Conversion Events

**Impressions are recorded automatically.** When the project-level inference endpoint returns recommendations, it records an `impression` event in the background. You do not need to track impressions manually. This is enabled by default and can be disabled with `INFERENCE_AUTO_RECORD_IMPRESSIONS=false`.

For click and purchase events, record them via the conversion event API:

```bash
# Single event
curl -X POST http://localhost:8000/api/v1/conversion_event/ \
  -H "X-API-Key: rctm_your_key" \
  -d '{
    "project": 1,
    "deployment_slot": 2,
    "user_id": "42",
    "event_type": "click",
    "item_id": "101",
    "recommendation_request_id": "a1b2c3d4-..."
  }'

# Batch (up to 1000 events)
curl -X POST http://localhost:8000/api/v1/conversion_event/batch/ \
  -H "X-API-Key: rctm_your_key" \
  -d '{
    "events": [
      {"project": 1, "deployment_slot": 1, "user_id": "42", "event_type": "click", "item_id": "101"},
      {"project": 1, "deployment_slot": 1, "user_id": "42", "event_type": "purchase", "item_id": "101"}
    ]
  }'
```

### 6. Check Results

```bash
curl http://localhost:8000/api/v1/ab_test/1/results/ \
  -H "Authorization: Bearer <jwt_token>"
```

**Response:**

```json
{
  "control": {
    "slot_id": 1,
    "slot_name": "Control",
    "impressions": 5230,
    "conversions": 412,
    "conversion_rate": 0.0788
  },
  "variant": {
    "slot_id": 2,
    "slot_name": "Variant A",
    "impressions": 5180,
    "conversions": 467,
    "conversion_rate": 0.0902
  },
  "z_score": 2.14,
  "p_value": 0.032,
  "significant": true,
  "lift": 0.145,
  "confidence_interval": [0.012, 0.278]
}
```

### 7. Promote the Winner

When the test reaches significance, promote the winning model:

```bash
curl -X POST http://localhost:8000/api/v1/ab_test/1/promote_winner/ \
  -H "Authorization: Bearer <jwt_token>"
```

This sets the winning slot's weight to 100 and deactivates the other slot.

## Statistical Method

The analysis uses a **two-proportion Z-test**:

- **Metric**: Conversion rate (conversions / impressions) for the configured `target_metric_name`
- **Null hypothesis**: Both slots have the same conversion rate
- **Test statistic**: Z-score from the pooled proportion
- **Significance**: Determined by the configured `confidence_level` (default: 0.95, i.e., p < 0.05)
- **Lift**: Relative improvement of variant over control
- **Confidence interval**: 95% CI for the difference in conversion rates

## Event Types

| Type | Typical Use |
|------|------------|
| `impression` | User was shown recommendations (denominator for CTR) |
| `click` | User clicked on a recommended item (numerator for CTR) |
| `purchase` | User purchased a recommended item (for purchase rate metrics) |

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/v1/deployment_slot/` | List/create deployment slots |
| PATCH/DELETE | `/api/v1/deployment_slot/{id}/` | Update/delete a slot |
| GET/POST | `/api/v1/ab_test/` | List/create A/B tests |
| POST | `/api/v1/ab_test/{id}/start/` | Start test (status → RUNNING) |
| POST | `/api/v1/ab_test/{id}/stop/` | Stop test (status → COMPLETED) |
| GET | `/api/v1/ab_test/{id}/results/` | Get statistical results |
| POST | `/api/v1/ab_test/{id}/promote_winner/` | Promote winning slot |
| POST | `/api/v1/conversion_event/` | Record a single event |
| POST | `/api/v1/conversion_event/batch/` | Record events in bulk |
