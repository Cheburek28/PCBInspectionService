"""Thin lines (ridges) with their direction: multi-scale Hessian, used by the hair detector."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.classic.detectors.common import RIDGE_BINS, RIDGE_SCALES, RIDGE_TOL, FloatMap
from pcb_inspection.engine.imaging import BGRImage

Ridges = dict[str, tuple[FloatMap, NDArray[np.int8]]]
NearRidges = dict[str, NDArray[np.uint8]]


def ridge_maps(img: BGRImage) -> Ridges:
    """Per polarity (``b`` bright line, ``d`` dark line): ridge strength and its direction bin (30° bins)."""
    L = np.asarray(cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[..., 0], np.float32)
    best: Ridges = {p: (np.zeros_like(L), np.zeros(L.shape, np.int8)) for p in ("b", "d")}
    for s in RIDGE_SCALES:
        g = cv2.GaussianBlur(L, (0, 0), s)
        xx = np.asarray(cv2.Sobel(g, cv2.CV_32F, 2, 0, ksize=3), np.float32)
        yy = np.asarray(cv2.Sobel(g, cv2.CV_32F, 0, 2, ksize=3), np.float32)
        xy = np.asarray(cv2.Sobel(g, cv2.CV_32F, 1, 1, ksize=3), np.float32)
        tr, det = xx + yy, xx * yy - xy * xy
        disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
        l1, l2 = tr / 2 + disc, tr / 2 - disc
        first_big = np.abs(l1) > np.abs(l2)
        big = np.where(first_big, l1, l2)
        small = np.where(first_big, l2, l1)
        strength = (np.abs(big) - np.abs(small)) * s * s
        ang = 0.5 * np.arctan2(2 * xy, xx - yy)
        across = np.where(first_big, ang, ang + np.pi / 2)
        bins = (((across + np.pi / 2) % np.pi) / np.pi * RIDGE_BINS).astype(np.int8) % RIDGE_BINS
        for p, sel in (("b", big < 0), ("d", big > 0)):
            r = np.where(sel, strength, 0).astype(np.float32)
            upd = r > best[p][0]
            best[p] = (np.where(upd, r, best[p][0]), np.where(upd, bins, best[p][1]).astype(np.int8))
    return best


def ridge_near(img: BGRImage) -> NearRidges:
    """For subtraction: per polarity and per direction bin j, the strongest ridge of bins j-1..j+1 within
    RIDGE_TOL px, stored /2 as uint8 (6 bins x 2 polarities of a 3000 px image ~ 72 MB)."""
    k = np.ones((2 * RIDGE_TOL + 1, 2 * RIDGE_TOL + 1), np.uint8)
    out = {}
    for p, (s, b) in ridge_maps(img).items():
        per_bin = [
            np.asarray(cv2.dilate(np.where(b == j, s, 0).astype(np.float32), k), np.float32)
            for j in range(RIDGE_BINS)
        ]
        near = np.stack(
            [
                np.maximum(
                    np.maximum(per_bin[j], per_bin[(j - 1) % RIDGE_BINS]), per_bin[(j + 1) % RIDGE_BINS]
                )
                for j in range(RIDGE_BINS)
            ]
        )
        out[p] = np.clip(near / 2, 0, 255).astype(np.uint8)
    return out


def new_ridges(test: Ridges, refs: list[NearRidges]) -> FloatMap:
    """Ridge strength of the photo minus the same-polarity, same-direction ridge of each reference nearby;
    the minimum over references (a line any reference has is not new)."""
    new: FloatMap | None = None
    for pol in ("b", "d"):
        s, b = test[pol]
        idx = b[None].astype(np.int64)
        n_p = s.copy()
        for r in refs:
            sub = np.take_along_axis(r[pol], idx, 0)[0].astype(np.float32) * 2
            n_p = np.minimum(n_p, s - sub)
        new = n_p if new is None else np.maximum(new, n_p)
    assert new is not None
    return new
