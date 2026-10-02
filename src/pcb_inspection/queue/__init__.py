"""Hand-off of inspection jobs to workers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

INSPECT_TASK = "pcbis.inspect"


class TaskQueue(Protocol):
    def enqueue_inspection(self, inspection_id: uuid.UUID) -> None: ...

    def ping(self) -> None: ...


class InlineQueue:
    """Runs the job immediately in the caller's thread. For tests and single-process demos."""

    def __init__(self, run: Callable[[uuid.UUID], None]) -> None:
        self._run = run
        self.enqueued: list[uuid.UUID] = []

    def enqueue_inspection(self, inspection_id: uuid.UUID) -> None:
        self.enqueued.append(inspection_id)
        self._run(inspection_id)

    def ping(self) -> None:
        return None


class RecordingQueue:
    """Only records job ids (tests that check the queued state)."""

    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    def enqueue_inspection(self, inspection_id: uuid.UUID) -> None:
        self.enqueued.append(inspection_id)

    def ping(self) -> None:
        return None
