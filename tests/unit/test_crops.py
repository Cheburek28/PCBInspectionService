from __future__ import annotations

import numpy as np
import pytest

from pcb_inspection.engine.crops import SEPARATOR_PX, CropKind, crop_window, make_crop
from pcb_inspection.engine.geometry import BBox

REF = np.zeros((200, 300, 3), np.uint8)
ALIGNED = np.full((200, 300, 3), 255, np.uint8)


def test_window_is_padded() -> None:
    assert crop_window(BBox(100, 100, 10, 10), 20, 300, 200) == BBox(80, 80, 50, 50)


def test_window_is_shifted_inside_at_the_border() -> None:
    assert crop_window(BBox(0, 190, 10, 10), 20, 300, 200) == BBox(0, 150, 50, 50)


def test_window_never_exceeds_image() -> None:
    assert crop_window(BBox(0, 0, 300, 200), 50, 300, 200) == BBox(0, 0, 300, 200)


@pytest.mark.parametrize("kind", [CropKind.REF, CropKind.TEST])
def test_single_crop_height_and_content(kind: CropKind) -> None:
    out = make_crop(REF, ALIGNED, BBox(100, 100, 20, 10), kind, pad=10, out_height=80, outline=False)
    assert out.shape[0] == 80
    assert out.shape[1] == round(40 * 80 / 30)
    assert out.mean() == (0 if kind is CropKind.REF else 255)


def test_pair_puts_reference_left() -> None:
    out = make_crop(REF, ALIGNED, BBox(100, 100, 20, 20), CropKind.PAIR, pad=10, out_height=40, outline=False)
    assert out.shape == (40, 40 + SEPARATOR_PX + 40, 3)
    assert out[:, :40].max() == 0
    assert out[:, -40:].min() == 255


def test_outline_is_drawn() -> None:
    out = make_crop(REF, ALIGNED, BBox(100, 100, 20, 20), CropKind.REF, pad=10, out_height=40)
    assert out.max() > 0


def test_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="same shape"):
        make_crop(REF, ALIGNED[:100], BBox(0, 0, 5, 5), CropKind.PAIR, 5, 40)
