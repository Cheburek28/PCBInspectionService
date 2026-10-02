# Configuration

All settings are environment variables with the `PCBIS_` prefix (a `.env` file in the working directory is
also read). Source of truth: [`settings.py`](../src/pcb_inspection/settings.py).

## Infrastructure

| Variable | Default | |
|---|---|---|
| `PCBIS_DATABASE_URL` | `postgresql+psycopg://pcbis:pcbis@localhost:5432/pcbis` | SQLAlchemy URL (psycopg 3) |
| `PCBIS_REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `PCBIS_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `PCBIS_STORAGE_PATH` | `./data` (`/data` in Docker) | root for `local` |
| `PCBIS_S3_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` / `_REGION` | — / `pcbis` / — / — / `us-east-1` | for `s3` (MinIO, AWS, …) |
| `PCBIS_UI_PASSWORD` | — | enables the web console at `/ui` ([web-console.md](web-console.md)) |
| `PCBIS_UI_SESSION_HOURS` | 12 | console login lifetime |
| `PCBIS_LOG_LEVEL` | `INFO` | |
| `PCBIS_LOG_FORMAT` | `json` | `json` or `console` |

Read by the container entrypoint only:

| Variable | Default | |
|---|---|---|
| `PCBIS_API_WORKERS` | 2 | uvicorn worker processes |
| `PCBIS_WORKER_CONCURRENCY` | 2 | Celery worker processes (each runs one inspection at a time) |

## Limits and behaviour

| Variable | Default | |
|---|---|---|
| `PCBIS_MAX_UPLOAD_MB` | 25 | upload size limit |
| `PCBIS_TASK_TIME_LIMIT_S` | 120 | hard limit per inspection (soft limit 10 s earlier → `TIMEOUT`) |
| `PCBIS_TASK_MAX_RETRIES` | 2 | retries on infrastructure errors |
| `PCBIS_MAX_POLL_WAIT_S` | 30 | upper bound for `?wait=` |
| `PCBIS_REFERENCE_CACHE_SIZE` | 8 | prepared references kept per process |

## Engine

| Variable | Default | Per request (`params`) |
|---|---|---|
| `PCBIS_ENGINE` | `classic-diff` | no |
| `PCBIS_ENGINE_WORK_WIDTH` | 3000 | no |
| `PCBIS_ENGINE_THRESHOLD` | 20.0 | `threshold` |
| `PCBIS_ENGINE_EXTENT_THRESHOLD` | 12.0 | `extent_threshold` |
| `PCBIS_ENGINE_MIN_AREA` | 40 | `min_area` |
| `PCBIS_ENGINE_TOL_PX` | 2 | `tol_px` |
| `PCBIS_ENGINE_HIGHLIGHT_CLIP` | 100 | `highlight_clip` |
| `PCBIS_ENGINE_OPEN_RADIUS` | 4 | `open_radius` |
| `PCBIS_ENGINE_BACKGROUND_SIGMA` | 20.0 | `background_sigma` |
| `PCBIS_DEFAULT_MASK_STRATEGY` | `full_frame` | per reference (`mask_strategy`) |

## Quality gates

| Variable | Default | Per request (`params`) |
|---|---|---|
| `PCBIS_GATE_MIN_WIDTH` | 1500 | no |
| `PCBIS_GATE_MIN_INLIERS` | 50 | no |
| `PCBIS_GATE_MIN_SHARPNESS_RATIO` | 0.90 | `min_sharpness_ratio` |
| `PCBIS_GATE_MAX_LAB_SHIFT_L` | 8.0 | `max_lab_shift_l` |
| `PCBIS_GATE_MAX_LAB_SHIFT_AB` | 5.0 | `max_lab_shift_ab` |
| `PCBIS_GATE_MAX_DIFFERENCES` | 60 | `max_differences` |
| `PCBIS_GATE_MAX_DIFFERENCES_AREA_RATIO` | 0.02 | `max_differences_area_ratio` |

The effective values are stored with every inspection (`algorithm.params`).

## Production compose (`compose.prod.yaml`)

| Variable | |
|---|---|
| `PCBIS_DOMAIN` | public host name; Caddy obtains a Let's Encrypt certificate for it |
| `PCBIS_VERSION` | image tag (`latest`, `0.1`, `0.1.0`) |
| `POSTGRES_PASSWORD` | database password |
| `PCBIS_WORKER_CPUS`, `PCBIS_WORKER_MEMORY` | worker container limits (3, 6g) |
| `BACKUP_KEEP_DAYS` | daily `pg_dump` retention (14) |
