# Runbook

`dc` below means `docker compose -f compose.prod.yaml`.

## Health

| Check | Command |
|---|---|
| Liveness | `curl -s https://$PCBIS_DOMAIN/health/live` |
| Readiness (DB, storage, queue) | `curl -s https://$PCBIS_DOMAIN/health/ready` → `checks` |
| Containers | `dc ps` |
| Logs | `dc logs -f --tail 200 api worker` (JSON; filter by `request_id` or `inspection_id`) |
| Metrics | `dc exec api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())"` |
| Queue length | `dc exec redis redis-cli llen inspections` |

Useful metrics: `pcbis_http_requests_total{route,status}`, `pcbis_http_request_seconds`,
`pcbis_inspections_submitted_total`.

## Incidents

**Inspections stay `queued`.** Worker down or Redis unreachable: `dc ps worker`, `dc logs worker`,
`dc restart worker`. Jobs are acknowledged late, so jobs of a crashed worker are redelivered.

**Many `failed` with `TIMEOUT`.** Worker is CPU-starved: check `docker stats`, lower
`PCBIS_WORKER_CONCURRENCY` or add CPU; very large photos → let clients downscale to 3000 px.

**Many `rejected`.** Group by code:

```sql
SELECT rejection_code, count(*) FROM inspections
WHERE created_at > now() - interval '1 day' GROUP BY 1 ORDER BY 2 DESC;
```

`IMAGE_BLURRY` → camera focus; `LIGHTING_MISMATCH` → lighting; `ALIGNMENT_FAILED` → wrong side/board or
reference from another revision; `TOO_MANY_DIFFERENCES` → thresholds too tight or outdated reference.

**`/health/ready` 503 storage.** Volume full or not writable: `df -h`, `docker system df`.

**Restoring a broken deployment.** `dc down` (volumes are kept), set the previous `PCBIS_VERSION`,
`dc up -d --wait`. Never run `dc down -v` in production — it deletes the data volumes.

## Routine

- Weekly: check that backups exist and are non-empty; `dc pull` for security updates of base images.
- Monthly: `pcbis export` and review `metrics.json` (precision / recall) to tune thresholds.
- Rotate API keys when a client station is decommissioned (`pcbis apikey revoke`).
