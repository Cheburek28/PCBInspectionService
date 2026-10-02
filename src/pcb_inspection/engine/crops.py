"""Side-by-side crops of a difference: reference vs aligned test, same geometry."""

from __future__ import annotations

from enum import StrEnum

import cv2
import numpy as np

from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage

SEPARATOR_PX = 4
OUTLINE_BGR = (0, 200, 255)


class CropKind(StrEnum):
    REF = "ref"
    TEST = "test"
    PAIR = "pair"


def crop_window(bbox: BBox, pad: int, width: int, height: int) -> BBox:
    """Box expanded by ``pad`` on each side, shifted (not shrunk) to stay inside the image when possible."""
    w = min(bbox.w + 2 * pad, width)
    h = min(bbox.h + 2 * pad, height)
    x = min(max(bbox.x - pad, 0), width - w)
    y = min(max(bbox.y - pad, 0), height - h)
    return BBox(x, y, w, h)


def make_crop(
    ref: BGRImage,
    aligned: BGRImage,
    bbox_ref: BBox,
    kind: CropKind,
    pad: int,
    out_height: int,
    outline: bool = True,
) -> BGRImage:
    """``ref`` and ``aligned`` must share the same frame and size; ``bbox_ref`` is in that frame."""
    if ref.shape != aligned.shape:
        raise ValueError("reference and aligned image must have the same shape")
    h, w = ref.shape[:2]
    win = crop_window(bbox_ref, pad, w, h)

    def cut(img: BGRImage) -> BGRImage:
        part = img[win.y : win.y + win.h, win.x : win.x + win.w].copy()
        if outline:
            x0, y0 = bbox_ref.x - win.x, bbox_ref.y - win.y
            cv2.rectangle(part, (x0, y0), (x0 + bbox_ref.w - 1, y0 + bbox_ref.h - 1), OUTLINE_BGR, 1)
        factor = out_height / part.shape[0]
        interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC
        out = cv2.resize(
            part, (max(1, round(part.shape[1] * factor)), out_height), interpolation=interpolation
        )
        return out.astype(np.uint8)

    if kind is CropKind.REF:
        return cut(ref)
    if kind is CropKind.TEST:
        return cut(aligned)
    sep = np.full((out_height, SEPARATOR_PX, 3), 255, np.uint8)
    return np.hstack([cut(ref), sep, cut(aligned)])
