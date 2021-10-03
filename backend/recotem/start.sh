#!/bin/bash
if ! python manage.py wait_db; then
echo "failed to wait databases to start."
exit 1;
fi
if ! python manage.py migrate; then
echo "failed to migrate the database."
exit 1;
fi
if !  python manage.py create_superuser; then
echo "failed to initialize an admin user."
exit 1;
fi

if !  python manage.py collectstatic --noinput; then
echo "failed to collectstatic."
exit 1;
fi
exec python manage.py runserver 0.0.0.0:80
