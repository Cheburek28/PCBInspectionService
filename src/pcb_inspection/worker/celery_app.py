"""Celery worker: ``celery -A pcb_inspection.worker.celery_app worker``.

Tasks are thin: they only translate between Celery and ``services.pipeline``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown

from pcb_inspection.observability import configure_logging
from pcb_inspection.queue import INSPECT_TASK
from pcb_inspection.queue.celery_queue import CeleryQueue, make_celery
from pcb_inspection.services import pipeline
from pcb_inspection.services.bootstrap import build_context
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.settings import get_settings

settings = get_settings()
app = make_celery(settings.redis_url, settings.task_time_limit_s)
log = structlog.get_logger(__name__)

_ctx: ServiceContext | None = None


def get_ctx() -> ServiceContext:
    """One context per worker process, created after fork (DB connections must not cross fork)."""
    global _ctx  # noqa: PLW0603
    if _ctx is None:
        _ctx = build_context(settings, queue=CeleryQueue(app))
    return _ctx


@setup_logging.connect
def _setup_logging(**_: Any) -> None:
    """Connecting to this signal stops Celery from configuring logging its own way."""
    configure_logging(settings.log_level, settings.log_format)


@worker_process_init.connect
def _init_process(**_: Any) -> None:
    configure_logging(settings.log_level, settings.log_format)
    get_ctx()


@worker_process_shutdown.connect
def _shutdown_process(**_: Any) -> None:
    if _ctx is not None:
        _ctx.db.dispose()


@app.task(name=INSPECT_TASK, bind=True, max_retries=settings.task_max_retries)
def inspect(self: Task, inspection_id: str) -> str:
    iid = uuid.UUID(inspection_id)
    structlog.contextvars.bind_contextvars(inspection_id=inspection_id)
    ctx = get_ctx()
    try:
        return pipeline.run_inspection(ctx, iid).value
    except SoftTimeLimitExceeded:
        pipeline.mark_failed(ctx, iid, "TIMEOUT", f"analysis exceeded {settings.task_time_limit_s} s")
        return "failed"
    except Exception as exc:
        if self.request.retries >= settings.task_max_retries:
            log.exception("inspection.giving_up")
            pipeline.mark_failed(ctx, iid, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")
            return "failed"
        log.warning("inspection.retry", error=str(exc), retry=self.request.retries + 1)
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc
    finally:
        structlog.contextvars.unbind_contextvars("inspection_id")
