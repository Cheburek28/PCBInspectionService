"""Engine and session factory. One ``Database`` per process."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, url: str, echo: bool = False) -> None:
        self.engine = create_engine(url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=10)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Unit of work: commits on success, rolls back on error."""
        with self._factory() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
