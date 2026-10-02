"""Masks of the area that is inspected (1) versus ignored (0), at working resolution."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.base import MaskNotFoundError, MaskSpec, MaskStrategy
from pcb_inspection.engine.imaging import BGRImage

Mask = NDArray[np.uint8]

MIN_COVERAGE = 0.03
SLOT_MIN_AREA = 0.0005  # fraction of the frame; smaller blue blobs are not fixture slots


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
    blue = _slot_pixels(image)
    best: tuple[Mask, int, int] | None = None
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
                best = ((labels == i).astype(np.uint8), area, ks)
        if best is not None:
            break
    if best is None:
        raise MaskNotFoundError("no board island surrounded by blue fixture slots was found")
    # restore at least as far as the strongest closing could have eaten, whatever kernel separated the island
    return _restore_island_edges(best[0], blue, max(best[2], int(261 * k_scale) | 1))


def _slot_pixels(image: BGRImage) -> NDArray[np.uint8]:
    """Saturated blue of the fixture seen through the slots.

    Solder mask can be blue-green (hue ~100-110) and fairly saturated, so the range is kept tight and
    only large blobs survive: slots are big, stray board pixels are not.
    """
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (110, 110, 70), (135, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(blue, connectivity=8)
    keep = np.zeros(n, np.uint8)
    keep[1:] = stats[1:, 4] >= SLOT_MIN_AREA * h * w
    slots: NDArray[np.uint8] = (keep[labels] * 255).astype(np.uint8)
    return slots


def _restore_island_edges(island: Mask, blue: NDArray[np.uint8], ks: int) -> Mask:
    """Closing rounds the island's corners and cuts them off unevenly from photo to photo.

    Grow the rough island back up to the inner edges of the slots, but never past the ring of slots
    that surrounds it (the convex hull of the nearby slot pixels): the panel outside stays excluded,
    bridges between neighbouring slots are kept as part of the board.
    """
    h, w = island.shape
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
    near = cv2.dilate(island, grow)
    ring = np.argwhere((blue > 0) & (near > 0))
    hull = np.zeros((h, w), np.uint8)
    if len(ring) >= 3:
        cv2.fillPoly(hull, [cv2.convexHull(ring[:, ::-1].astype(np.int32))], 1)
    else:
        hull[:] = 1
    candidate = ((blue == 0) & (near > 0) & (hull > 0)).astype(np.uint8)
    n, labels = cv2.connectedComponents(candidate, connectivity=4)
    if n <= 1:
        return island
    overlap = np.bincount(labels[island > 0].ravel(), minlength=n)
    overlap[0] = 0
    restored: Mask = (np.asarray(labels) == int(np.argmax(overlap))).astype(np.uint8)
    return restored


def _erode(mask: Mask, margin_px: int) -> Mask:
    if margin_px <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))
    # a full-frame mask must shrink from the image border too
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    return cv2.erode(padded, kernel)[1:-1, 1:-1].astype(np.uint8)
