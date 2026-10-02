from __future__ import annotations

from dataclasses import replace

import pytest

from pcb_inspection.domain.enums import RejectionCode
from pcb_inspection.domain.gates import QualityThresholds, check_size, evaluate
from pcb_inspection.engine.base import QualityMetrics

T = QualityThresholds()
GOOD = QualityMetrics(
    alignment_inliers=500,
    alignment_ok=True,
    sharpness_ratio=1.0,
    lab_shift=(0.0, 0.0, 0.0),
    differences_count=3,
    differences_area_ratio=0.001,
)


def test_good_photo_passes() -> None:
    assert evaluate(GOOD, T) is None


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"alignment_ok": False}, RejectionCode.ALIGNMENT_FAILED),
        ({"alignment_inliers": 49}, RejectionCode.ALIGNMENT_FAILED),
        ({"sharpness_ratio": 0.899}, RejectionCode.IMAGE_BLURRY),
        ({"lab_shift": (8.1, 0.0, 0.0)}, RejectionCode.LIGHTING_MISMATCH),
        ({"lab_shift": (-8.1, 0.0, 0.0)}, RejectionCode.LIGHTING_MISMATCH),
        ({"lab_shift": (0.0, 5.1, 0.0)}, RejectionCode.LIGHTING_MISMATCH),
        ({"lab_shift": (0.0, 0.0, -5.1)}, RejectionCode.LIGHTING_MISMATCH),
        ({"differences_count": 61}, RejectionCode.TOO_MANY_DIFFERENCES),
        ({"differences_area_ratio": 0.021}, RejectionCode.TOO_MANY_DIFFERENCES),
    ],
)
def test_each_gate(change: dict[str, object], code: RejectionCode) -> None:
    rejection = evaluate(replace(GOOD, **change), T)  # type: ignore[arg-type]
    assert rejection is not None
    assert rejection.code is code
    assert rejection.message
    assert rejection.details


@pytest.mark.parametrize(
    "change",
    [
        {"alignment_inliers": 50},
        {"sharpness_ratio": 0.90},
        {"lab_shift": (8.0, 5.0, -5.0)},
        {"differences_count": 60},
        {"differences_area_ratio": 0.02},
    ],
)
def test_boundaries_are_inclusive(change: dict[str, object]) -> None:
    assert evaluate(replace(GOOD, **change), T) is None  # type: ignore[arg-type]


def test_first_failing_gate_wins() -> None:
    bad = replace(GOOD, sharpness_ratio=0.5, differences_count=500)
    rejection = evaluate(bad, T)
    assert rejection is not None
    assert rejection.code is RejectionCode.IMAGE_BLURRY


def test_same_as_reference_skips_photometric_gates() -> None:
    assert evaluate(replace(GOOD, same_as_reference=True, sharpness_ratio=0.1), T) is None


def test_missing_metrics_are_not_gated() -> None:
    assert evaluate(replace(GOOD, sharpness_ratio=None, lab_shift=None), T) is None


def test_check_size() -> None:
    assert check_size(1500, T) is None
    rejection = check_size(1499, T)
    assert rejection is not None
    assert rejection.code is RejectionCode.IMAGE_TOO_SMALL
