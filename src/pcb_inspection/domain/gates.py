"""Quality gates: decide whether a processed photo is usable. The first failing gate wins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pcb_inspection.domain.enums import RejectionCode
from pcb_inspection.engine.base import QualityMetrics


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    min_width: int = 1500
    min_inliers: int = 50
    min_sharpness_ratio: float = 0.90
    max_lab_shift_l: float = 8.0
    max_lab_shift_ab: float = 5.0
    max_differences: int = 60
    max_differences_area_ratio: float = 0.02


@dataclass(frozen=True, slots=True)
class Rejection:
    code: RejectionCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def check_size(width: int, t: QualityThresholds) -> Rejection | None:
    if width < t.min_width:
        return Rejection(
            RejectionCode.IMAGE_TOO_SMALL,
            f"Image is {width} px wide, at least {t.min_width} px is required.",
            {"width": width, "min_width": t.min_width},
        )
    return None


def evaluate(q: QualityMetrics, t: QualityThresholds) -> Rejection | None:
    if not q.alignment_ok or q.alignment_inliers < t.min_inliers:
        return Rejection(
            RejectionCode.ALIGNMENT_FAILED,
            "The photo does not match the reference (other board, other side or wrong position).",
            {"alignment_inliers": q.alignment_inliers, "min_inliers": t.min_inliers},
        )
    if q.same_as_reference:
        return None
    if q.sharpness_ratio is not None and q.sharpness_ratio < t.min_sharpness_ratio:
        return Rejection(
            RejectionCode.IMAGE_BLURRY,
            f"Image is less sharp than the reference "
            f"({q.sharpness_ratio:.2f} < {t.min_sharpness_ratio:.2f}). Retake the photo.",
            {"sharpness_ratio": q.sharpness_ratio, "threshold": t.min_sharpness_ratio},
        )
    if q.lab_shift is not None:
        dl, da, db = q.lab_shift
        if abs(dl) > t.max_lab_shift_l or max(abs(da), abs(db)) > t.max_lab_shift_ab:
            return Rejection(
                RejectionCode.LIGHTING_MISMATCH,
                "Brightness or colour differs from the reference. Check the lighting and retake the photo.",
                {"lab_shift": list(q.lab_shift), "max_l": t.max_lab_shift_l, "max_ab": t.max_lab_shift_ab},
            )
    if q.differences_count > t.max_differences or q.differences_area_ratio > t.max_differences_area_ratio:
        return Rejection(
            RejectionCode.TOO_MANY_DIFFERENCES,
            "Too many differences from the reference. Check manually; the reference may be outdated.",
            {
                "differences_count": q.differences_count,
                "differences_area_ratio": q.differences_area_ratio,
                "max_differences": t.max_differences,
                "max_differences_area_ratio": t.max_differences_area_ratio,
            },
        )
    return None
