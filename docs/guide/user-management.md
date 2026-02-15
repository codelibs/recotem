# User Management

This guide covers how to manage users in Recotem, including creating accounts, assigning roles, and changing passwords.

## What You Need to Know

Recotem supports multiple users with role-based access. Each user has their own projects, data, and models. Admin users can manage other users and see all resources across the system.

## User Roles

Recotem has two user roles:

| Role | Description | Capabilities |
|------|-------------|-------------|
| **Regular user** | A standard account for everyday use | Create and manage their own projects, data, models, and API keys |
| **Admin** (staff) | An administrator with elevated privileges | Everything a regular user can do, plus: manage other users, see all projects, and access the Django admin panel |

## Data Ownership

Resources in Recotem are owned by the user who created them:

- **Projects** belong to their creator. Each user can only see and manage their own projects.
- **Training data, models, and configurations** are visible based on the project they belong to.
- **Admin users** can see all resources across all users.
- **Legacy resources** created before multi-user support was added (with no owner) are visible to all authenticated users.

## Creating Users

Only admin users can create new user accounts.

### Via the UI

1. Navigate to the **User Management** page (available in the admin sidebar)
2. Click **Create User**
3. Fill in the user details:

<!-- screenshot: Create User form with username, email, password, and admin toggle -->

| Field | Required | Description |
|-------|----------|-------------|
| **Username** | Yes | A unique username for login |
| **Email** | No | The user's email address |
| **Password** | Yes | Must meet Django's password validation rules (minimum 8 characters, not too common, not entirely numeric) |
| **Admin (Staff)** | No | Whether this user has admin privileges. Defaults to regular user. |

4. Click **Create**

### Via the API

```bash
curl -X POST http://localhost:8000/api/v1/users/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "email": "alice@example.com",
    "password": "secure_password_123",
    "is_staff": false
  }'
```

**Response:**

```json
{
  "id": 2,
  "username": "alice",
  "email": "alice@example.com",
  "is_staff": false,
  "is_active": true,
  "date_joined": "2025-01-15T15:00:00Z",
  "last_login": null
}
```

The password is never included in the response.

## Listing Users

Admin users can view all user accounts:

### Via the API

```bash
curl http://localhost:8000/api/v1/users/ \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
[
  {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "is_staff": true,
    "is_active": true,
    "date_joined": "2025-01-01T00:00:00Z",
    "last_login": "2025-01-15T10:00:00Z"
  },
  {
    "id": 2,
    "username": "alice",
    "email": "alice@example.com",
    "is_staff": false,
    "is_active": true,
    "date_joined": "2025-01-15T15:00:00Z",
    "last_login": null
  }
]
```

## Updating User Details

Admin users can update another user's email, staff status, or active status:

```bash
curl -X PATCH http://localhost:8000/api/v1/users/2/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "alice.new@example.com",
    "is_staff": true
  }'
```

Note that the username cannot be changed after creation.

## Changing Passwords

### Self-Service Password Change

Any logged-in user can change their own password without needing admin help. This is available to all users regardless of role.

**Via the UI:**

1. Click on your username or profile area
2. Select **Change Password**
3. Enter your current password and your new password
4. Click **Save**

<!-- screenshot: Password change form with old and new password fields -->

**Via the API:**

```bash
curl -X POST http://localhost:8000/api/v1/users/change_password/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "current_password",
    "new_password": "new_secure_password_456"
  }'
```

**Response:**

```json
{
  "detail": "Password changed successfully."
}
```

The new password must meet Django's password validation requirements:
- At least 8 characters long
- Not too similar to your username or email
- Not a commonly used password
- Not entirely numeric

### Admin Password Reset

Admin users can reset another user's password when the user has forgotten it or needs to be locked out and given new credentials.

**Via the API:**

```bash
curl -X POST http://localhost:8000/api/v1/users/2/reset_password/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "new_password": "temporary_password_789"
  }'
```

**Response:**

```json
{
  "detail": "Password has been reset."
}
```

After resetting, communicate the new password to the user through a secure channel. They should change it immediately after logging in.

## Deactivating and Activating Users

Instead of deleting user accounts, Recotem uses a soft-delete approach. Deactivating a user prevents them from logging in while preserving all their data.

### Deactivating a User

```bash
curl -X POST http://localhost:8000/api/v1/users/2/deactivate/ \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "id": 2,
  "username": "alice",
  "email": "alice@example.com",
  "is_staff": false,
  "is_active": false,
  "date_joined": "2025-01-15T15:00:00Z",
  "last_login": "2025-01-15T16:00:00Z"
}
```

A deactivated user:
- Cannot log in
- Cannot use API keys
- Their projects and data remain intact
- Can be reactivated at any time

**Note:** You cannot deactivate your own account. This prevents accidentally locking yourself out.

### Reactivating a User

```bash
curl -X POST http://localhost:8000/api/v1/users/2/activate/ \
  -H "Authorization: Bearer $TOKEN"
```

The user can immediately log in again after reactivation.

## Initial Admin Account

When Recotem is deployed for the first time, an admin account is created automatically using the `DEFAULT_ADMIN_PASSWORD` environment variable. The default username is `admin`.

For production deployments, make sure to:

1. Set a strong `DEFAULT_ADMIN_PASSWORD` before first deployment
2. Change the admin password after first login
3. Create individual user accounts for team members instead of sharing the admin account

## Security Considerations

- **API keys cannot manage users** -- User management endpoints reject API key authentication entirely. Only JWT-authenticated admin users can manage accounts.
- **Password validation** -- All passwords are validated against Django's built-in password validators (minimum length, complexity, common password check).
- **Session security** -- After changing a password, existing JWT tokens remain valid until they expire. For immediate revocation, the user should log out of all sessions.

## API Reference

| Method | Endpoint | Description | Who Can Use |
|--------|----------|-------------|-------------|
| `GET` | `/api/v1/users/` | List all users | Admin only |
| `POST` | `/api/v1/users/` | Create a new user | Admin only |
| `GET` | `/api/v1/users/{id}/` | Get user details | Admin only |
| `PATCH` | `/api/v1/users/{id}/` | Update user (email, staff status) | Admin only |
| `POST` | `/api/v1/users/{id}/deactivate/` | Deactivate a user | Admin only |
| `POST` | `/api/v1/users/{id}/activate/` | Reactivate a user | Admin only |
| `POST` | `/api/v1/users/{id}/reset_password/` | Reset another user's password | Admin only |
| `POST` | `/api/v1/users/change_password/` | Change your own password | Any logged-in user |
