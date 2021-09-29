#!/bin/bash
if ! python manage.py wait_db; then
echo "failed to wait databases to start."
exit 1;
fi
exec celery -A recotem worker --loglevel=INFO
