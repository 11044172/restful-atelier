#!/usr/bin/env bash
set -o errexit

python -m pip install -r requirements.lock
python manage.py collectstatic --no-input
