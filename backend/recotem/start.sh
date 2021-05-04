#!/bin/bash
python manage.py wait_db
python manage.py migrate
python manage.py create_superuser
python manage.py runserver 0.0.0.0:80