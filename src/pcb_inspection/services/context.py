"""Everything a use case needs, wired once per process (API app, worker, CLI)."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from pcb_inspection.db.database import Database
from pcb_inspection.engine.base import Engine, PreparedReference
from pcb_inspection.queue import TaskQueue
from pcb_inspection.settings import Settings
from pcb_inspection.storage import BlobStorage


class ReferenceCache:
    """Thread-safe LRU of prepared references (decoded image, mask, keypoints)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[uuid.UUID, PreparedReference] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_load(self, key: uuid.UUID, load: Callable[[], PreparedReference]) -> PreparedReference:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = load()
        self.put(key, value)
        return value

    def put(self, key: uuid.UUID, value: PreparedReference) -> None:
        if self.size <= 0:
            return
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class ServiceContext:
    settings: Settings
    db: Database
    storage: BlobStorage
    queue: TaskQueue
    engine: Engine
    ref_cache: ReferenceCache = field(init=False)

    def __post_init__(self) -> None:
        self.ref_cache = ReferenceCache(self.settings.reference_cache_size)
