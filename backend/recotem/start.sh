#!/bin/bash
echo "=== Starting backend initialization ==="
echo "Step 1: Waiting for database..."
if ! python manage.py wait_db; then
  echo "ERROR: Failed to wait for database to start."
  exit 1
fi
echo "Database is ready!"

echo "Step 2: Running migrations..."
if ! python manage.py migrate; then
  echo "ERROR: Failed to migrate the database."
  exit 1
fi
echo "Migrations completed!"

echo "Step 3: Creating superuser..."
if ! python manage.py create_superuser; then
  echo "ERROR: Failed to initialize an admin user."
  exit 1
fi
echo "Superuser created!"

echo "Step 4: Collecting static files..."
if ! python manage.py collectstatic --noinput; then
  echo "ERROR: Failed to collectstatic."
  exit 1
fi
echo "Static files collected!"

echo "Step 5: Starting Django server on 0.0.0.0:80..."
exec python manage.py runserver 0.0.0.0:80
