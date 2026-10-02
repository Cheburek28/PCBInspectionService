from __future__ import annotations

import uuid

from celery import Celery

from pcb_inspection.queue import INSPECT_TASK


def make_celery(redis_url: str, task_time_limit_s: int = 120) -> Celery:
    app = Celery("pcbis", broker=redis_url)
    app.conf.update(
        task_acks_late=True,  # a crashed worker leaves the job in the queue
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,  # jobs are long and CPU-bound
        worker_max_tasks_per_child=200,
        task_time_limit=task_time_limit_s,
        task_soft_time_limit=max(task_time_limit_s - 10, 1),
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
        task_default_queue="inspections",
        # the service configures structlog itself; keep Celery away from stdout and the root logger
        worker_redirect_stdouts=False,
        worker_hijack_root_logger=False,
    )
    return app


class CeleryQueue:
    def __init__(self, app: Celery) -> None:
        self.app = app

    def enqueue_inspection(self, inspection_id: uuid.UUID) -> None:
        self.app.send_task(INSPECT_TASK, args=[str(inspection_id)])

    def ping(self) -> None:
        with self.app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1)
