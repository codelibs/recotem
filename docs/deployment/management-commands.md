# Management Commands

Recotem includes several Django management commands for administration, deployment, and maintenance tasks. All commands are run via `python manage.py <command>` (or `uv run python manage.py <command>` in a local development setup).

When running inside Docker, prefix with `docker compose exec backend`:

```bash
docker compose exec backend python manage.py <command>
```

---

## create_superuser

Create the initial admin account. This command runs automatically during container startup. If any users already exist in the database, it does nothing.

### Usage

```bash
python manage.py create_superuser
```

### Behavior

- If **no users** exist in the database, creates a superuser with username `admin`.
- If the `DEFAULT_ADMIN_PASSWORD` environment variable is set, that value is used as the password.
- If `DEFAULT_ADMIN_PASSWORD` is not set, a random 12-character password is generated and printed to stdout.
- If **any users** already exist, the command exits immediately without creating anything.

### Arguments

This command takes no arguments. Configuration is via environment variable only.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DEFAULT_ADMIN_PASSWORD` | No | Password for the admin user. If omitted, a random password is generated and displayed. |

### When to Use

- **Initial deployment**: The command is included in the Docker entrypoint so the first admin account is created automatically.
- **Manual setup**: Run it manually when setting up a development environment without Docker.

### Examples

```bash
# In Docker (automatic — included in entrypoint)
docker compose up backend

# Local development with a specific password
DEFAULT_ADMIN_PASSWORD=mysecretpassword uv run python manage.py create_superuser

# Local development with a random password (printed to stdout)
uv run python manage.py create_superuser
```

---

## create_api_key

Create an API key for a project from the command line. The raw key is printed to stdout (this is the only time the full key is visible).

### Usage

```bash
python manage.py create_api_key \
  --project-id <ID> \
  --name <KEY_NAME> \
  [--scopes <SCOPES>] \
  [--expires-in-days <DAYS>] \
  [--owner <USERNAME>]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--project-id` | Yes | -- | ID of the project the key belongs to. |
| `--name` | Yes | -- | A descriptive name for the key (must be unique within the project). |
| `--scopes` | No | `predict` | Comma-separated list of scopes: `read`, `write`, `predict`. |
| `--expires-in-days` | No | No expiry | Number of days until the key expires. |
| `--owner` | No | `admin` | Username of the key owner. Must match the project owner. |

### Validations

- The specified project ID must exist.
- The specified owner username must exist.
- The owner must match the project's owner (if the project has an owner set).
- The key name must not already exist for the same project.
- All scopes must be valid (`read`, `write`, or `predict`).

### When to Use

- **CI/CD pipelines**: Create API keys non-interactively as part of deployment scripts.
- **Docker entrypoints**: Provision inference keys during initial setup.
- **Scripting**: Generate keys for automated integrations without using the web UI.

### Examples

```bash
# Create a predict-only key for project 1
python manage.py create_api_key --project-id 1 --name "Production Inference"

# Create a key with multiple scopes and 90-day expiry
python manage.py create_api_key \
  --project-id 1 \
  --name "CI Pipeline" \
  --scopes "read,write,predict" \
  --expires-in-days 90

# Create a key owned by a specific user
python manage.py create_api_key \
  --project-id 2 \
  --name "Partner Integration" \
  --scopes "predict" \
  --owner "alice"

# Capture the key in a shell variable
API_KEY=$(python manage.py create_api_key --project-id 1 --name "Automated" 2>/dev/null)
echo "Key: $API_KEY"
```

---

## create_test_users

Create or update test user accounts. Primarily used for E2E testing and development environments.

### Usage

```bash
python manage.py create_test_users \
  --user <username:password> \
  [--user <username:password> ...]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--user` | Yes (repeatable) | User credential pair in the format `username:password`. Can be specified multiple times to create several users. |

### Behavior

- If the user already exists, their password is updated to the specified value.
- If the user does not exist, a new (non-superuser) account is created.
- Prints whether each user was created or updated.

### When to Use

- **E2E test setup**: Create deterministic test accounts before running Playwright tests.
- **Development environments**: Quickly set up multiple users for manual testing.

### Examples

```bash
# Create a single test user
python manage.py create_test_users --user testuser:testpassword

# Create multiple test users
python manage.py create_test_users \
  --user alice:password123 \
  --user bob:password456

# In Docker (e.g., as part of test setup)
docker compose exec backend python manage.py create_test_users \
  --user e2e_user:e2e_password
```

---

## resign_models

Sign all existing unsigned trained model files with HMAC-SHA256. This command is needed when migrating from an older Recotem version that did not sign model files, or when the `SECRET_KEY` has been rotated.

### Usage

```bash
python manage.py resign_models [--dry-run]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--dry-run` | No | Show which models would be signed without actually modifying any files. |

### Behavior

1. Scans all `TrainedModel` records that have an associated file.
2. For each file, checks whether it already has a valid HMAC-SHA256 signature.
3. If unsigned, reads the file, prepends an HMAC signature, and writes it back.
4. Prints a summary showing how many models were already signed, newly signed, and any errors.

### Output Summary

The command prints counts for:
- **Already signed** -- files that already have a valid signature (skipped).
- **Newly signed** -- files that were unsigned and have now been signed.
- **Errors** -- files that could not be read or written (e.g., missing from disk).

### When to Use

- **After upgrading Recotem** to a version that introduced model signing. Run this once to sign all legacy model files.
- **After rotating `SECRET_KEY`**. Old signatures become invalid with a new key, so re-sign all models.
- **Before enforcing signature verification**. After running this command, set `PICKLE_ALLOW_LEGACY_UNSIGNED=false` in your environment to reject any unsigned model files.

### Examples

```bash
# Preview which models need signing (no changes made)
python manage.py resign_models --dry-run

# Sign all unsigned models
python manage.py resign_models

# Full migration sequence
python manage.py resign_models
# Verify no errors in output, then enforce signing:
# Set PICKLE_ALLOW_LEGACY_UNSIGNED=false in your environment

# In Docker
docker compose exec backend python manage.py resign_models --dry-run
docker compose exec backend python manage.py resign_models
```

---

## wait_db

Wait for the PostgreSQL database to become available. Retries the connection with a 2-second delay between attempts.

### Usage

```bash
python manage.py wait_db
```

### Arguments

This command takes no arguments.

### Behavior

- Attempts to connect to the default database up to **30 times** (60 seconds total).
- Waits 2 seconds between each attempt.
- Prints progress messages showing the current attempt number.
- Exits with code 0 on success, or code 1 if the database is still unavailable after all retries.

### When to Use

- **Docker entrypoints**: Run before `migrate` or `create_superuser` to ensure the database is accepting connections. This is critical because the `backend` container may start before `db` is ready.
- **Kubernetes init containers**: Use as a readiness check in init containers before the main application starts.

### Examples

```bash
# In a Docker entrypoint script (typical usage)
python manage.py wait_db && python manage.py migrate && python manage.py create_superuser

# In Docker Compose (already included in the backend entrypoint)
docker compose up backend

# In a Kubernetes init container
command: ["python", "manage.py", "wait_db"]
```

---

## assign_owners

Assign an owner to Projects, SplitConfigs, and EvaluationConfigs that currently have no owner. This is a data migration tool for transitioning from single-user to multi-user mode.

### Usage

```bash
python manage.py assign_owners --user <USERNAME> [--dry-run]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--user` | Yes | Username to assign as the owner/created_by for all unowned records. |
| `--dry-run` | No | Show what would be changed without making any modifications. |

### Behavior

Scans three model types for records with no owner:

| Model | Field Updated |
|-------|--------------|
| `Project` | `owner` |
| `SplitConfig` | `created_by` |
| `EvaluationConfig` | `created_by` |

For each model, all records where the ownership field is `NULL` are updated to the specified user. The command prints a count of affected records for each model.

### When to Use

- **After upgrading to multi-user support**: If you have existing data from a single-user deployment, run this command to assign all legacy records to a user. Without an owner, these records may not be visible through the API's ownership-filtered views.
- **Data migration**: When consolidating records under a specific user account.

### Examples

```bash
# Preview which records would be updated
python manage.py assign_owners --user admin --dry-run

# Assign all unowned records to the admin user
python manage.py assign_owners --user admin

# Assign to a specific user
python manage.py assign_owners --user alice

# In Docker
docker compose exec backend python manage.py assign_owners --user admin --dry-run
docker compose exec backend python manage.py assign_owners --user admin
```
