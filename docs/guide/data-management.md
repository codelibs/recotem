# Data Management

This guide explains how to prepare, upload, and manage the data that powers your recommendation models.

## What You Need to Know

Recotem works with interaction data -- records of users engaging with items. This data is the foundation for training recommendation models. You can also upload item metadata to enrich your recommendations with descriptive information about items.

## Prerequisites

- A project has been created (see [Projects](projects.md))
- Your data is in CSV or TSV format
- You know which columns match your project's user, item, and time column definitions

## Training Data

### What Is Training Data?

Training data is a file where each row represents one interaction between a user and an item. For example:

- A user watched a movie
- A customer purchased a product
- A reader clicked on an article

The system uses these interactions to learn patterns and generate recommendations.

### CSV Format Requirements

Your CSV file must include at least the columns defined in your project:

- The **user column** (e.g., `user_id`) -- required
- The **item column** (e.g., `movie_id`) -- required
- The **time column** (e.g., `timestamp`) -- required only if defined in the project

Additional columns (such as ratings or categories) are allowed and will be preserved, but only the user, item, and time columns are used for model training.

**Example -- Minimal format (no timestamps):**

```csv
user_id,movie_id
1,101
1,203
2,101
2,305
3,203
```

**Example -- With ratings and timestamps:**

```csv
user_id,movie_id,rating,timestamp
1,101,5,2024-01-15
1,203,4,2024-01-16
2,101,3,2024-01-15
2,305,5,2024-01-17
3,203,4,2024-01-18
```

**Example -- E-commerce interactions:**

```csv
customer_id,product_id,action,date
C001,P100,purchase,2024-03-01
C001,P200,view,2024-03-02
C002,P100,view,2024-03-01
C002,P300,purchase,2024-03-03
```

### Supported File Formats

| Format | Extension | Delimiter |
|--------|-----------|-----------|
| CSV | `.csv` | Comma (`,`) |
| TSV | `.tsv` | Tab |

The system detects the format automatically based on the file extension.

### Uploading Training Data

#### Via the UI

1. Navigate to your project
2. Click **Training Data** in the sidebar
3. Click **Upload**
4. Select your CSV or TSV file
5. The system validates the file immediately

<!-- screenshot: Training Data list page with Upload button -->

<!-- screenshot: File upload dialog -->

After a successful upload, you will see the file listed with its name, size, and upload date.

<!-- screenshot: Training Data list showing uploaded file with details -->

#### Via the API

```bash
curl -X POST http://localhost:8000/api/v1/training_data/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "project=1" \
  -F "file=@/path/to/interactions.csv"
```

**Response:**

```json
{
  "id": 1,
  "project": 1,
  "file": "/media/training_data/interactions.csv",
  "ins_datetime": "2025-01-15T10:35:00Z",
  "basename": "interactions.csv",
  "filesize": 524288
}
```

### Previewing Data

You can preview the first rows of an uploaded file to verify it was parsed correctly.

**Via the API:**

```bash
curl http://localhost:8000/api/v1/training_data/1/preview/?n_rows=10 \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "columns": ["user_id", "movie_id", "rating", "timestamp"],
  "rows": [
    [1, 101, 5, "2024-01-15"],
    [1, 203, 4, "2024-01-16"]
  ],
  "total_rows": 2
}
```

### Data Validation

When you upload a file, Recotem checks the following:

| Check | Error Message |
|-------|--------------|
| User column exists | `Column "user_id" not found in the upload file.` |
| Item column exists | `Column "movie_id" not found in the upload file.` |
| Time column exists (if configured) | `Column "timestamp" not found in the upload file.` |
| Time column is parseable as dates (if configured) | `Could not interpret "timestamp" as datetime.` |
| File is not empty | `file is required.` |

If validation fails, the upload is rejected and the error message tells you exactly what went wrong.

### Tips for Preparing Training Data

- **More data is better** -- Recommendation models improve with more interactions
- **Unique users and items** -- Ensure consistent identifiers (do not mix `user_1` and `User_1`)
- **Remove duplicates** -- If a user interacted with the same item multiple times, decide whether to keep all records or just the latest
- **Column names must match exactly** -- The column names in your CSV must match the project's column definitions (case-sensitive)

## Item Metadata

### What Is Item Metadata?

Item metadata provides descriptive information about items, such as titles, categories, or prices. While not required for training, it enriches the sample recommendation view in the UI by showing you what items are actually being recommended.

### Format

The metadata CSV must include the item column defined in your project. All other columns are treated as descriptive attributes.

**Example:**

```csv
movie_id,title,genre,year
101,The Matrix,Sci-Fi,1999
203,Inception,Sci-Fi,2010
305,The Godfather,Drama,1972
420,Pulp Fiction,Crime,1994
```

### Uploading Item Metadata

#### Via the UI

1. Navigate to your project
2. Click **Item Metadata** in the sidebar
3. Click **Upload**
4. Select your CSV file

<!-- screenshot: Item Metadata upload page -->

#### Via the API

```bash
curl -X POST http://localhost:8000/api/v1/item_meta_data/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "project=1" \
  -F "file=@/path/to/movies.csv"
```

**Response:**

```json
{
  "id": 1,
  "project": 1,
  "file": "/media/item_meta_data/movies.csv",
  "valid_columns_list_json": ["title", "genre", "year"],
  "ins_datetime": "2025-01-15T10:40:00Z",
  "basename": "movies.csv",
  "filesize": 12345
}
```

The `valid_columns_list_json` field lists which columns from the metadata file can be displayed in the UI (columns that cannot be serialized to JSON are excluded automatically).

### Metadata Validation

The system checks that the item column (e.g., `movie_id`) exists in the metadata file. If it is missing, the upload is rejected.

## Managing Uploaded Files

### Listing Files

**Training data:**

```bash
# All training data for a project
curl "http://localhost:8000/api/v1/training_data/?project=1" \
  -H "Authorization: Bearer $TOKEN"

# A specific file
curl http://localhost:8000/api/v1/training_data/1/ \
  -H "Authorization: Bearer $TOKEN"
```

**Item metadata:**

```bash
curl "http://localhost:8000/api/v1/item_meta_data/?project=1" \
  -H "Authorization: Bearer $TOKEN"
```

### Downloading Files

You can download a previously uploaded file:

```bash
curl http://localhost:8000/api/v1/training_data/1/download/ \
  -H "Authorization: Bearer $TOKEN" \
  -o training_data.csv
```

### Deleting Files

Deleting a training data file removes the file from storage but keeps any models that were trained on it (those models remain usable).

```bash
curl -X DELETE http://localhost:8000/api/v1/training_data/1/ \
  -H "Authorization: Bearer $TOKEN"
```

## API Reference

### Training Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/training_data/?project={id}` | List training data for a project |
| `POST` | `/api/v1/training_data/` | Upload new training data (multipart form) |
| `GET` | `/api/v1/training_data/{id}/` | Get file details |
| `GET` | `/api/v1/training_data/{id}/preview/` | Preview first N rows |
| `GET` | `/api/v1/training_data/{id}/download/` | Download the file |
| `DELETE` | `/api/v1/training_data/{id}/` | Delete the file |

### Item Metadata

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/item_meta_data/?project={id}` | List metadata files for a project |
| `POST` | `/api/v1/item_meta_data/` | Upload new metadata (multipart form) |
| `GET` | `/api/v1/item_meta_data/{id}/` | Get file details |
| `GET` | `/api/v1/item_meta_data/{id}/download/` | Download the file |
| `DELETE` | `/api/v1/item_meta_data/{id}/` | Delete the file |
