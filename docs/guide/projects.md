# Projects

A project is the foundation of everything you do in Recotem. It defines a single recommendation task and acts as a container for all related data, models, and configurations.

## What Is a Project?

Think of a project as a self-contained recommendation workspace. For example, you might create separate projects for:

- "Movie Recommendations" for a streaming platform
- "Product Suggestions" for an e-commerce store
- "Article Recommendations" for a news site

Each project holds its own training data, tuning jobs, trained models, deployment slots, and API keys.

## Why Projects Matter

Projects serve two important purposes:

1. **Column mapping** -- They define how Recotem reads your data by specifying which columns contain user identifiers, item identifiers, and timestamps.
2. **Isolation** -- Everything within a project is self-contained. Models trained in one project cannot accidentally be used in another.

## Creating a Project

### Prerequisites

- You are logged in to Recotem
- You know the column names in your CSV data

### Via the UI

1. From the Dashboard, click **Create Project**
2. Fill in the following fields:

<!-- screenshot: Create Project form -->

| Field | Required | Description |
|-------|----------|-------------|
| **Name** | Yes | A descriptive name for this recommendation task. Must be unique among your projects. |
| **User Column** | Yes | The name of the column in your CSV files that identifies individual users (e.g., `user_id`, `customer_id`, `uid`). |
| **Item Column** | Yes | The name of the column in your CSV files that identifies individual items (e.g., `item_id`, `product_id`, `movie_id`). |
| **Time Column** | No | The name of the column for timestamps (e.g., `timestamp`, `date`, `created_at`). If provided, enables time-based data splitting. |

3. Click **Save**

### Via the API

```bash
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

**Response:**

```json
{
  "id": 1,
  "name": "Movie Recommendations",
  "owner": 1,
  "user_column": "user_id",
  "item_column": "movie_id",
  "time_column": "timestamp",
  "ins_datetime": "2025-01-15T10:30:00Z",
  "updated_at": "2025-01-15T10:30:00Z"
}
```

## Understanding Column Definitions

### User Column

This column identifies the people (or entities) who receive recommendations. Every row in your training data must have a value in this column.

**Examples:**
- An e-commerce site might use `customer_id`
- A streaming service might use `user_id`
- A content platform might use `reader_id`

### Item Column

This column identifies the things being recommended. Every row in your training data must have a value in this column.

**Examples:**
- An e-commerce site might use `product_id` or `sku`
- A streaming service might use `movie_id` or `show_id`
- A content platform might use `article_id`

### Time Column (Optional)

If your data includes timestamps, specifying a time column enables time-based data splitting strategies. This can lead to more realistic evaluation because the system tests on future interactions rather than randomly held-out ones.

If you do not have timestamps in your data, leave this field blank. Random splitting will be used instead.

## Viewing Project Details

### Project Summary

The project summary gives you a quick overview of the current state of your project.

**Via the UI:**

Navigate to the project and view the summary panel on the project page.

<!-- screenshot: Project summary showing counts of data, jobs, and models -->

**Via the API:**

```bash
curl http://localhost:8000/api/v1/project_summary/1/ \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "n_data": 3,
  "n_complete_jobs": 2,
  "n_models": 5,
  "ins_datetime": "2025-01-15T10:30:00Z"
}
```

| Field | Description |
|-------|-------------|
| `n_data` | Number of training data files uploaded |
| `n_complete_jobs` | Number of completed tuning jobs |
| `n_models` | Number of trained models |
| `ins_datetime` | When the project was created |

### Listing Projects

**Via the API:**

```bash
# List all your projects
curl http://localhost:8000/api/v1/project/ \
  -H "Authorization: Bearer $TOKEN"

# Filter by name
curl "http://localhost:8000/api/v1/project/?name=Movie%20Recommendations" \
  -H "Authorization: Bearer $TOKEN"
```

## Updating a Project

You can update a project's name or column definitions at any time. However, changing column definitions after uploading data may cause validation errors for future uploads if the new column names do not match your CSV files.

**Via the API:**

```bash
curl -X PATCH http://localhost:8000/api/v1/project/1/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Movie Recommendations"}'
```

## Deleting a Project

Deleting a project permanently removes it and all associated data, models, configurations, and API keys.

**Via the API:**

```bash
curl -X DELETE http://localhost:8000/api/v1/project/1/ \
  -H "Authorization: Bearer $TOKEN"
```

This action cannot be undone. Make sure you no longer need any of the project's data or models before deleting.

## Project Ownership

Each project belongs to the user who created it. This means:

- You can only see and manage projects you own
- Admin users (staff) can see all projects
- Legacy projects created before multi-user support was added are visible to all authenticated users
- Project names must be unique per user -- different users can have projects with the same name

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/project/` | List your projects |
| `POST` | `/api/v1/project/` | Create a new project |
| `GET` | `/api/v1/project/{id}/` | Get project details |
| `PATCH` | `/api/v1/project/{id}/` | Update a project |
| `DELETE` | `/api/v1/project/{id}/` | Delete a project |
| `GET` | `/api/v1/project_summary/{id}/` | Get project summary statistics |
