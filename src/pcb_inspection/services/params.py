"""Effective algorithm parameters: server defaults + allowed per-request overrides."""

from __future__ import annotations

from dataclasses import asdict, fields, replace
from typing import Any

from pcb_inspection.domain.errors import ValidationFailed
from pcb_inspection.domain.gates import QualityThresholds
from pcb_inspection.engine.base import InspectParams
from pcb_inspection.settings import Settings

ENGINE_OVERRIDABLE = frozenset(
    {
        "threshold",
        "extent_threshold",
        "min_area",
        "tol_px",
        "highlight_clip",
        "open_radius",
        "background_sigma",
    }
)
GATE_OVERRIDABLE = frozenset(
    {
        "min_sharpness_ratio",
        "max_lab_shift_l",
        "max_lab_shift_ab",
        "max_differences",
        "max_differences_area_ratio",
    }
)
OVERRIDABLE = ENGINE_OVERRIDABLE | GATE_OVERRIDABLE


def effective_params(settings: Settings, overrides: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """JSON-serialisable snapshot stored on the inspection (reproducibility)."""
    overrides = overrides or {}
    unknown = set(overrides) - OVERRIDABLE
    if unknown:
        raise ValidationFailed(
            f"parameters cannot be overridden: {sorted(unknown)}; allowed: {sorted(OVERRIDABLE)}"
        )
    for key, value in overrides.items():
        if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
            raise ValidationFailed(f"parameter {key!r} must be a non-negative number")
    engine = settings.inspect_params()
    gates = settings.thresholds()
    engine = replace(
        engine, **_typed(InspectParams, {k: v for k, v in overrides.items() if k in ENGINE_OVERRIDABLE})
    )
    gates = replace(
        gates, **_typed(QualityThresholds, {k: v for k, v in overrides.items() if k in GATE_OVERRIDABLE})
    )
    return {"engine": asdict(engine), "gates": asdict(gates)}


def _typed(cls: type, values: dict[str, Any]) -> dict[str, Any]:
    kinds = {f.name: f.type for f in fields(cls)}
    return {k: int(v) if kinds[k] in ("int", int) else float(v) for k, v in values.items()}


def engine_params(snapshot: dict[str, dict[str, Any]]) -> InspectParams:
    return InspectParams(**snapshot["engine"])


def gate_thresholds(snapshot: dict[str, dict[str, Any]]) -> QualityThresholds:
    return QualityThresholds(**snapshot["gates"])
