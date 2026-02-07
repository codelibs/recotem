#!/bin/bash
set -euo pipefail

if ! python manage.py wait_db; then
  echo "failed to wait databases to start."
  exit 1
fi

if ! python manage.py migrate; then
  echo "failed to migrate the database."
  exit 1
fi

if ! python manage.py create_test_users; then
  echo "failed to initialize users."
  exit 1
fi

if ! python manage.py assign_owners; then
  echo "failed to assign owners."
  exit 1
fi

if ! python manage.py collectstatic --noinput; then
  echo "failed to collectstatic."
  exit 1
fi

exec daphne recotem.asgi:application -b 0.0.0.0 -p "${BACKEND_PORT:-80}"
