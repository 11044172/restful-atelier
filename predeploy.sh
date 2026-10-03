#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

echo "==> Pre-deploy: starting database migrations"
python -u manage.py migrate --noinput
echo "==> Pre-deploy: database migrations completed"
echo "==> Pre-deploy: exiting successfully"
