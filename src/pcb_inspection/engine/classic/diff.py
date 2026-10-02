"""Difference map and extraction of differing regions."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage

FloatMap = NDArray[np.float32]

# size of a patch that must match the reference as a whole
PATCH_SIGMA = 4.0


@dataclass(frozen=True, slots=True)
class Region:
    bbox: BBox  # working-reference pixels
    area: int
    score: float


def diff_map(ref: BGRImage, test: BGRImage, tol_px: int) -> FloatMap:
    """Per-pixel difference in ~Lab units that tolerates misregistration of up to ``tol_px``.

    A pixel differs only if its value is outside the min..max range of the other image's neighbourhood,
    checked in both directions.
    """
    r = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2LAB), (0, 0), 1.0).astype(np.float32)
    t = cv2.GaussianBlur(cv2.cvtColor(test, cv2.COLOR_BGR2LAB), (0, 0), 1.0).astype(np.float32)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tol_px + 1, 2 * tol_px + 1))
    rmax, rmin = cv2.dilate(r, k), cv2.erode(r, k)
    tmax, tmin = cv2.dilate(t, k), cv2.erode(t, k)
    d1 = np.maximum(0, np.maximum(rmin - t, t - rmax))
    d2 = np.maximum(0, np.maximum(tmin - r, r - tmax))
    d = np.minimum(d1, d2)
    # chroma weighted higher: a missing / different component often changes colour more than brightness
    out = np.sqrt(d[..., 0] ** 2 + 1.5 * (d[..., 1] ** 2 + d[..., 2] ** 2))
    return np.asarray(cv2.GaussianBlur(out.astype(np.float32), (0, 0), PATCH_SIGMA), dtype=np.float32)


def find_regions(
    dmap: FloatMap, mask: NDArray[np.uint8], threshold: float, min_area: int
) -> tuple[list[Region], FloatMap]:
    """Connected regions above ``threshold`` inside the mask, sorted by score descending."""
    m = np.asarray(cv2.GaussianBlur(dmap, (0, 0), 1.5), dtype=np.float32)
    m[mask == 0] = 0
    bw = cv2.morphologyEx(
        (m > threshold).astype(np.uint8),
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
    )
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    regions = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < min_area:
            continue
        score = float(m[labels == i].max())
        regions.append(Region(BBox(x, y, w, h), area, round(score, 1)))
    regions.sort(key=lambda r: -r.score)
    return regions, m


def colorize(heat: FloatMap, base: BGRImage, mask: NDArray[np.uint8], threshold: float) -> NDArray[np.uint8]:
    scaled = np.clip(heat / (threshold * 2) * 255, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(scaled, cv2.COLORMAP_JET)
    out = cv2.addWeighted(base, 0.5, colored, 0.5, 0)
    outside = mask == 0
    out[outside] = base[outside] // 3
    return out.astype(np.uint8)
