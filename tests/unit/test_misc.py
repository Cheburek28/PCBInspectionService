"""Small pure units: ids, params, settings, reference cache, synthetic generator, errors."""

from __future__ import annotations

import time
import uuid

import numpy as np
import pytest

from pcb_inspection import synthetic as sy
from pcb_inspection.domain.enums import FINAL_STATUSES, InspectionStatus
from pcb_inspection.domain.errors import AppError, NotFound, ValidationFailed
from pcb_inspection.domain.ids import uuid7
from pcb_inspection.engine.base import MaskSpec, PreparedReference
from pcb_inspection.services.context import ReferenceCache
from pcb_inspection.services.params import OVERRIDABLE, effective_params, engine_params, gate_thresholds
from pcb_inspection.settings import Settings


def test_uuid7_is_version_7_and_time_ordered() -> None:
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a.version == 7
    assert a.variant == uuid.RFC_4122
    assert a < b


def test_final_statuses() -> None:
    assert InspectionStatus.COMPLETED.is_final
    assert not InspectionStatus.PROCESSING.is_final
    assert len(FINAL_STATUSES) == 3


def test_app_error_carries_extra() -> None:
    err = NotFound("x missing", hint="check id")
    assert isinstance(err, AppError)
    assert (err.status, err.code, err.detail, err.extra) == (
        404,
        "NOT_FOUND",
        "x missing",
        {"hint": "check id"},
    )


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_effective_params_defaults() -> None:
    snap = effective_params(_settings(), None)
    assert snap["engine"]["threshold"] == 20.0
    assert snap["gates"]["max_differences"] == 60
    assert engine_params(snap).work_width == 3000
    assert gate_thresholds(snap).min_inliers == 50


def test_effective_params_overrides_are_typed() -> None:
    snap = effective_params(_settings(), {"threshold": 20, "min_area": 15.0, "max_differences": 10})
    assert snap["engine"]["threshold"] == 20.0
    assert isinstance(snap["engine"]["threshold"], float)
    assert snap["engine"]["min_area"] == 15
    assert isinstance(snap["engine"]["min_area"], int)
    assert snap["gates"]["max_differences"] == 10


@pytest.mark.parametrize(
    "overrides", [{"work_width": 10}, {"threshold": "high"}, {"threshold": -1}, {"tol_px": True}]
)
def test_effective_params_rejects_bad_overrides(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        effective_params(_settings(), overrides)


def test_overridable_keys_are_documented() -> None:
    assert "threshold" in OVERRIDABLE
    assert "work_width" not in OVERRIDABLE


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCBIS_MAX_UPLOAD_MB", "3")
    monkeypatch.setenv("PCBIS_GATE_MIN_INLIERS", "7")
    s = _settings()
    assert s.max_upload_bytes == 3 * 1024 * 1024
    assert s.thresholds().min_inliers == 7


def _ref() -> PreparedReference:
    return PreparedReference(
        np.zeros((2, 2, 3), np.uint8), np.ones((2, 2), np.uint8), 1.0, (2, 2), MaskSpec()
    )


def test_reference_cache_is_lru() -> None:
    cache = ReferenceCache(2)
    keys = [uuid.uuid4() for _ in range(3)]
    loads: list[uuid.UUID] = []

    def loader(k: uuid.UUID) -> PreparedReference:
        loads.append(k)
        return _ref()

    cache.get_or_load(keys[0], lambda: loader(keys[0]))
    cache.get_or_load(keys[1], lambda: loader(keys[1]))
    cache.get_or_load(keys[0], lambda: loader(keys[0]))  # hit, becomes most recent
    cache.get_or_load(keys[2], lambda: loader(keys[2]))  # evicts keys[1]
    cache.get_or_load(keys[1], lambda: loader(keys[1]))  # reload
    assert loads == [keys[0], keys[1], keys[2], keys[1]]
    assert len(cache) == 2


def test_reference_cache_disabled() -> None:
    cache = ReferenceCache(0)
    cache.put(uuid.uuid4(), _ref())
    assert len(cache) == 0


def test_synthetic_generator_is_deterministic() -> None:
    a = sy.photograph(sy.render(sy.random_board(5)), sy.camera(sy.random_board(5), 1))
    b = sy.photograph(sy.render(sy.random_board(5)), sy.camera(sy.random_board(5), 1))
    assert np.array_equal(a, b)


def test_synthetic_defect_truth_boxes() -> None:
    spec = sy.random_board(5)
    d = sy.Defects(
        removed=frozenset({0}), shifted={1: (-5, 3)}, recolored={2: (0, 0, 0)}, blobs=((50, 60, 4),)
    )
    boxes = d.affected(spec)
    assert len(boxes) == 4
    shifted = spec.components[1].bbox
    assert boxes[2].x == shifted.x - 5
    assert boxes[2].w == shifted.w + 5
    assert boxes[3].to_dict() == {"x": 46, "y": 56, "w": 8, "h": 8}
