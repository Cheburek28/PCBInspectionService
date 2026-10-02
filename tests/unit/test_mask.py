from __future__ import annotations

import numpy as np
import pytest

from pcb_inspection.engine.base import MaskNotFoundError, MaskSpec, MaskStrategy
from pcb_inspection.engine.classic.mask import build_mask
from tests.conftest import Scene


def test_full_frame_mask_keeps_a_margin(scene: Scene) -> None:
    mask = build_mask(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME, margin_px=6), 1.0)
    assert mask.dtype == np.uint8
    assert mask[0].max() == 0
    assert mask[:, -1].max() == 0
    assert mask[400, 600] == 1
    assert 0.95 < mask.mean() < 1


def test_full_frame_without_margin_is_all_ones(scene: Scene) -> None:
    mask = build_mask(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME, margin_px=0), 1.0)
    assert mask.min() == 1


def test_polygon_mask_is_scaled(scene: Scene) -> None:
    polygon = ((200.0, 200.0), (1000.0, 200.0), (1000.0, 600.0), (200.0, 600.0))
    mask = build_mask(scene.reference, MaskSpec(MaskStrategy.POLYGON, polygon, margin_px=0), 0.5)
    assert mask[150, 250] == 1  # (500, 300) in uploaded pixels
    assert mask[350, 250] == 0
    assert mask.mean() == pytest.approx(400 * 200 / mask.size, rel=0.02)


def test_polygon_needs_three_vertices(scene: Scene) -> None:
    with pytest.raises(MaskNotFoundError, match="3 vertices"):
        build_mask(scene.reference, MaskSpec(MaskStrategy.POLYGON, ((0, 0), (1, 1))), 1.0)


def test_tiny_polygon_is_rejected(scene: Scene) -> None:
    tiny = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))
    with pytest.raises(MaskNotFoundError, match="covers"):
        build_mask(scene.reference, MaskSpec(MaskStrategy.POLYGON, tiny), 1.0)


def test_blue_fixture_finds_the_board_island(fixture_scene: Scene) -> None:
    mask = build_mask(fixture_scene.reference, MaskSpec(MaskStrategy.BLUE_FIXTURE), 1.0)
    a = fixture_scene.spec.board_area
    inside = mask[a.y + 20 : a.y + a.h - 20, a.x + 20 : a.x + a.w - 20]
    assert inside.mean() > 0.97
    assert mask[:30].max() == 0  # panel frame above the slots
    assert abs(mask.mean() - a.area / mask.size) < 0.05


def test_blue_fixture_missing(scene: Scene) -> None:
    with pytest.raises(MaskNotFoundError, match="blue fixture"):
        build_mask(scene.reference, MaskSpec(MaskStrategy.BLUE_FIXTURE), 1.0)
