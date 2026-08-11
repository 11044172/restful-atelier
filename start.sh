#!/usr/bin/env bash
set -o errexit
set -o nounset

worker_pid=""
web_pid=""

shutdown_services() {
  trap - EXIT TERM INT
  if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then
    kill -TERM "$worker_pid" 2>/dev/null || true
  fi
  if [[ -n "$web_pid" ]] && kill -0 "$web_pid" 2>/dev/null; then
    kill -TERM "$web_pid" 2>/dev/null || true
  fi
  [[ -z "$worker_pid" ]] || wait "$worker_pid" 2>/dev/null || true
  [[ -z "$web_pid" ]] || wait "$web_pid" 2>/dev/null || true
}

trap shutdown_services EXIT TERM INT

python manage.py process_notification_outbox --limit 0 --poll-seconds 2 &
worker_pid=$!

gunicorn config.wsgi:application --bind "0.0.0.0:${PORT}" --workers 3 --timeout 60 &
web_pid=$!

set +o errexit
wait -n "$worker_pid" "$web_pid"
exit_code=$?
set -o errexit

shutdown_services
exit "$exit_code"
