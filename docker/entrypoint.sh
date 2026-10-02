#!/bin/sh
# One image, several roles: api | worker | migrate | cli <args> | <any command>
set -eu

role="${1:-api}"
[ "$#" -gt 0 ] && shift

case "$role" in
  api)
    exec uvicorn pcb_inspection.api.app:create_app --factory \
      --host 0.0.0.0 --port 8000 \
      --workers "${PCBIS_API_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips '*' \
      --no-access-log "$@"
    ;;
  worker)
    exec celery -A pcb_inspection.worker.celery_app worker \
      --queues inspections \
      --concurrency "${PCBIS_WORKER_CONCURRENCY:-2}" \
      --loglevel "${PCBIS_LOG_LEVEL:-INFO}" "$@"
    ;;
  migrate)
    exec pcbis migrate "$@"
    ;;
  cli)
    exec pcbis "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
