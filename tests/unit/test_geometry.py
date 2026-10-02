from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pcb_inspection.engine.geometry import (
    BBox,
    affine_to_matrix,
    is_plausible_board_transform,
    project_bbox,
    project_points,
    scale_matrix,
)


def test_bbox_rejects_empty() -> None:
    with pytest.raises(ValueError, match="positive size"):
        BBox(0, 0, 0, 5)


def test_bbox_area_and_dict() -> None:
    b = BBox(1, 2, 3, 4)
    assert b.area == 12
    assert b.to_dict() == {"x": 1, "y": 2, "w": 3, "h": 4}


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        (BBox(10, 10, 5, 5), BBox(10, 10, 5, 5)),
        (BBox(-5, -5, 10, 10), BBox(0, 0, 5, 5)),
        (BBox(95, 95, 10, 10), BBox(95, 95, 5, 5)),
        (BBox(200, 200, 5, 5), None),
    ],
)
def test_clip(box: BBox, expected: BBox | None) -> None:
    assert box.clip(100, 100) == expected


def test_iou() -> None:
    a = BBox(0, 0, 10, 10)
    assert a.iou(a) == 1
    assert a.iou(BBox(5, 0, 10, 10)) == pytest.approx(50 / 150)
    assert a.iou(BBox(20, 20, 5, 5)) == 0


def test_scaled_covers_original() -> None:
    assert BBox(3, 3, 3, 3).scaled(0.5) == BBox(1, 1, 2, 2)
    assert BBox(10, 20, 30, 40).scaled(2) == BBox(20, 40, 60, 80)


def test_from_bounds_never_empty() -> None:
    assert BBox.from_bounds(1.2, 1.2, 1.3, 1.3) == BBox(1, 1, 1, 1)


def test_project_bbox_with_translation() -> None:
    m = np.array([[1, 0, 5], [0, 1, -3], [0, 0, 1]], dtype=np.float64)
    assert project_bbox(m, BBox(10, 10, 4, 4)) == BBox(15, 7, 4, 4)


def test_project_points_at_infinity() -> None:
    m = np.array([[1, 0, 0], [0, 1, 0], [1, 0, 0]], dtype=np.float64)
    with pytest.raises(ValueError, match="infinity"):
        project_points(m, np.array([[0.0, 0.0]]))


def _rotation(deg: float, tx: float, ty: float, s: float = 1.0) -> np.ndarray:
    a = math.radians(deg)
    return np.array(
        [[s * math.cos(a), -s * math.sin(a), tx], [s * math.sin(a), s * math.cos(a), ty], [0, 0, 1]],
        dtype=np.float64,
    )


@settings(max_examples=200, deadline=None)
@given(
    x=st.integers(0, 2000),
    y=st.integers(0, 2000),
    w=st.integers(1, 300),
    h=st.integers(1, 300),
    deg=st.floats(-3, 3),
    tx=st.floats(-50, 50),
    ty=st.floats(-50, 50),
    s=st.floats(0.5, 2.0),
)
def test_round_trip_through_scaling_and_rotation(
    x: int, y: int, w: int, h: int, deg: float, tx: float, ty: float, s: float
) -> None:
    """ref → test → ref keeps the box within the growth caused by rotating an axis-aligned box."""
    box = BBox(x, y, w, h)
    m = scale_matrix(1 / s) @ _rotation(deg, tx, ty) @ scale_matrix(s)
    back = project_bbox(np.linalg.inv(m), project_bbox(m, box))
    # rotating a box by θ grows each side by at most (w+h)·sin|θ|, twice (there and back) + 1 px rounding
    slack = 2 * (w + h) * math.sin(math.radians(abs(deg))) + 2
    assert back.x <= box.x + 1
    assert back.y <= box.y + 1
    assert back.x + back.w >= box.x + box.w - 1
    assert back.y + back.h >= box.y + box.h - 1
    assert back.w <= w + 2 * slack
    assert back.h <= h + 2 * slack


def test_affine_to_matrix() -> None:
    m = affine_to_matrix(np.array([[1, 0, 2], [0, 1, 3]], dtype=np.float32))
    assert m.shape == (3, 3)
    assert m[2].tolist() == [0, 0, 1]


@pytest.mark.parametrize(
    ("matrix", "ok"),
    [
        (np.eye(3), True),
        (_rotation(2, 10, 10), True),
        (_rotation(0, 0, 0, s=1.5), False),  # too much zoom
        (np.diag([-1.0, 1.0, 1.0]), False),  # mirrored
        (np.array([[1, 0, 0], [0, 1, 0], [0.01, 0, 1]], dtype=np.float64), False),  # strong perspective
        (np.full((3, 3), np.nan), False),
    ],
)
def test_plausible_transform(matrix: np.ndarray, ok: bool) -> None:
    assert is_plausible_board_transform(matrix, 0.8, 1.25) is ok
