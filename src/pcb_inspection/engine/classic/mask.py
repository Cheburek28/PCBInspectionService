"""Masks of the area that is inspected (1) versus ignored (0), at working resolution."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.base import MaskNotFoundError, MaskSpec, MaskStrategy
from pcb_inspection.engine.imaging import BGRImage

Mask = NDArray[np.uint8]

MIN_COVERAGE = 0.03


def build_mask(image: BGRImage, spec: MaskSpec, scale: float) -> Mask:
    """``scale`` converts uploaded-image pixels (polygon vertices) to working pixels."""
    if spec.strategy is MaskStrategy.FULL_FRAME:
        mask = np.ones(image.shape[:2], np.uint8)
    elif spec.strategy is MaskStrategy.POLYGON:
        mask = polygon_mask(image.shape[:2], spec.polygon, scale)
    else:
        mask = blue_fixture_mask(image)
    mask = _erode(mask, spec.margin_px)
    if mask.mean() < MIN_COVERAGE:
        raise MaskNotFoundError(
            f"inspected area covers {mask.mean():.1%} of the frame (< {MIN_COVERAGE:.0%})"
        )
    return mask


def polygon_mask(shape: tuple[int, ...], polygon: tuple[tuple[float, float], ...], scale: float) -> Mask:
    if len(polygon) < 3:
        raise MaskNotFoundError("polygon mask needs at least 3 vertices")
    mask = np.zeros(shape[:2], np.uint8)
    pts = np.round(np.asarray(polygon, dtype=np.float64) * scale).astype(np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask


def blue_fixture_mask(image: BGRImage) -> Mask:
    """Board island surrounded by blue slots of the holding fixture.

    Bridges between the slots are closed with growing kernels until an island that does not touch
    the frame edge separates from the panel frame.
    """
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (100, 90, 40), (140, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    best: tuple[Mask, int] | None = None
    # kernel sizes were tuned at 3000 px width; scale them with the image
    k_scale = w / 3000
    for k in (61, 101, 151, 201, 261, 331):
        ks = max(3, int(k * k_scale) | 1)
        closed = cv2.morphologyEx(
            blue, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
        )
        n, labels, stats, _ = cv2.connectedComponentsWithStats((closed == 0).astype(np.uint8), connectivity=4)
        for i in range(1, n):
            x, y, bw, bh, area = (int(v) for v in stats[i])
            if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
                continue  # touches the frame edge: this is the panel frame, not the board
            if area < MIN_COVERAGE * h * w:
                continue
            if best is None or area > best[1]:
                best = ((labels == i).astype(np.uint8), area)
        if best is not None:
            break
    if best is None:
        raise MaskNotFoundError("no board island surrounded by blue fixture slots was found")
    # strong closing rounds the corners: take the convex hull and cut the original slots out of it
    contours, _ = cv2.findContours(best[0], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [cv2.convexHull(np.vstack(contours))], 1)
    mask[blue > 0] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    if n > 1:
        mask = (labels == 1 + int(np.argmax(stats[1:, 4]))).astype(np.uint8)
    return mask


def _erode(mask: Mask, margin_px: int) -> Mask:
    if margin_px <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))
    # a full-frame mask must shrink from the image border too
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    return cv2.erode(padded, kernel)[1:-1, 1:-1].astype(np.uint8)
