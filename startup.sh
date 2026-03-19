#!/bin/bash
export DJANGO_SETTINGS_MODULE=id_verificator.settings
export PYTHONUNBUFFERED=1

python manage.py collectstatic --noinput
python manage.py migrate --noinput

gunicorn id_verificator.wsgi:application --bind 0.0.0.0:$PORT
