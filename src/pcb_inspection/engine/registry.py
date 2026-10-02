"""Engine lookup by name. A future ML engine is registered here next to classic-diff."""

from __future__ import annotations

from collections.abc import Callable

from pcb_inspection.engine.base import Engine
from pcb_inspection.engine.classic.engine import ClassicEngine

_FACTORIES: dict[str, Callable[[], Engine]] = {ClassicEngine.name: ClassicEngine}

DEFAULT_ENGINE = ClassicEngine.name


def get_engine(name: str = DEFAULT_ENGINE) -> Engine:
    try:
        return _FACTORIES[name]()
    except KeyError:
        raise ValueError(f"unknown engine {name!r}; available: {sorted(_FACTORIES)}") from None


def available_engines() -> list[str]:
    return sorted(_FACTORIES)
