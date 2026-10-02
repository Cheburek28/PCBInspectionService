"""Builds a ServiceContext from settings for the real processes."""

from __future__ import annotations

from pcb_inspection.db.database import Database
from pcb_inspection.engine.registry import get_engine
from pcb_inspection.queue import TaskQueue
from pcb_inspection.queue.celery_queue import CeleryQueue, make_celery
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.settings import Settings
from pcb_inspection.storage import build_storage


def build_context(settings: Settings, queue: TaskQueue | None = None) -> ServiceContext:
    if queue is None:
        queue = CeleryQueue(make_celery(settings.redis_url, settings.task_time_limit_s))
    return ServiceContext(
        settings=settings,
        db=Database(settings.database_url),
        storage=build_storage(settings),
        queue=queue,
        engine=get_engine(settings.engine),
    )
