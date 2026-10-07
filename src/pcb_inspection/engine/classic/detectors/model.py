"""What the detectors need from the reference (computed once) and from each pool board (computed once)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.classic.detectors.common import (
    BOARD_EDGE,
    BODY_CLOSE,
    BODY_FILL,
    BODY_MARGIN,
    BODY_MIN_AREA,
    BODY_MIN_DIM,
    BODY_RING_METAL,
    DARK_BODY_L,
    FLAT_AWAY,
    FLAT_GRAD,
    FLAT_MIN_ISLAND,
    PAD_MAX_ASPECT,
    PAD_MAX_LEN,
    PAD_MAX_PX,
    PAD_MIN_PX,
    SHIFT_NEIGHBOURS,
    SHIFT_SEARCH,
    BoolMap,
    FloatMap,
    neutral_bright,
    to_lab,
)
from pcb_inspection.engine.classic.detectors.ridges import NearRidges, ridge_near
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage


@dataclass(frozen=True, slots=True)
class Pad:
    y0: int
    y1: int
    x0: int
    x1: int
    local: BoolMap  # the pad plus its dark surroundings, inside the crop
    box: BBox  # the metal blob itself
    area: int
    p25: float  # dark quartile of lightness on the reference


@dataclass(slots=True)
class ReferenceModel:
    image: BGRImage
    lab: FloatMap
    pads: list[Pad]
    parts: list[tuple[int, int, int, int]]  # x0, y0, x1, y1 of part bodies (with a margin)
    part_neighbours: NDArray[np.int64]
    gray: NDArray[np.uint8]
    flat: BoolMap
    dark: BoolMap
    body_level: FloatMap
    hair_area: BoolMap
    _ridges: NearRidges | None = None

    def ridge_near(self) -> NearRidges:
        if self._ridges is None:
            self._ridges = ridge_near(self.image)
        return self._ridges


@dataclass(slots=True)
class PoolMember:
    """A passed board aligned to the reference (working resolution) and what was measured on it."""

    aligned: BGRImage
    shift_rel: NDArray[np.float64] | None  # part displacements relative to neighbours, None if unknown
    _lab: FloatMap | None = None
    _ridges: NearRidges | None = None

    def lab(self) -> FloatMap:
        if self._lab is None:
            self._lab = to_lab(self.aligned)
        return self._lab

    def ridge_near(self) -> NearRidges:
        if self._ridges is None:
            self._ridges = ridge_near(self.aligned)
        return self._ridges


def build_reference_model(image: BGRImage, mask: NDArray[np.uint8]) -> ReferenceModel:
    lab = to_lab(image)
    L = lab[..., 0]
    parts = _bodies(lab, mask)
    if parts:
        c = np.array([[(p[0] + p[2]) / 2, (p[1] + p[3]) / 2] for p in parts])
        dist = np.hypot(c[:, None, 0] - c[None, :, 0], c[:, None, 1] - c[None, :, 1])
        neighbours = np.asarray(np.argsort(dist, axis=1)[:, 1 : SHIFT_NEIGHBOURS + 1], np.int64)
    else:
        neighbours = np.zeros((0, SHIFT_NEIGHBOURS), np.int64)
    edge = np.ones((2 * BOARD_EDGE + 1, 2 * BOARD_EDGE + 1), np.uint8)
    board = np.asarray(cv2.erode((mask > 0).astype(np.uint8), edge), np.uint8) > 0
    dark = _dark_bodies(lab)
    w = dark.astype(np.float32)
    body_level = np.asarray(
        cv2.GaussianBlur(np.where(dark, L, 0).astype(np.float32), (0, 0), 8)
        / (cv2.GaussianBlur(w, (0, 0), 8) + 1e-3),
        np.float32,
    )
    metal = (
        np.asarray(cv2.dilate(neutral_bright(lab, 170, 25).astype(np.uint8), np.ones((5, 5), np.uint8))) > 0
    )
    return ReferenceModel(
        image=image,
        lab=lab,
        pads=_pads(lab, mask),
        parts=parts,
        part_neighbours=neighbours,
        gray=np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), np.uint8),
        flat=_flat(lab, board),
        dark=dark,
        body_level=body_level,
        hair_area=board & ~metal,
    )


def _pads(lab: FloatMap, mask: NDArray[np.uint8]) -> list[Pad]:
    metal = (neutral_bright(lab, 150, 22) & (mask > 0)).astype(np.uint8)
    metal = np.asarray(cv2.morphologyEx(metal, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), np.uint8)
    n, lbl, st, _ = cv2.connectedComponentsWithStats(metal, connectivity=8)
    h, w = metal.shape
    L = lab[..., 0]
    out = []
    for i in range(1, n):
        area = int(st[i, 4])
        if not PAD_MIN_PX <= area <= PAD_MAX_PX:
            continue
        x, y, bw, bh = (int(v) for v in st[i, :4])
        if max(bw, bh) > PAD_MAX_LEN or max(bw, bh) > PAD_MAX_ASPECT * max(min(bw, bh), 1):
            continue  # a silkscreen line, not a pad
        y0, y1, x0, x1 = max(y - 4, 0), min(y + bh + 4, h), max(x - 4, 0), min(x + bw + 4, w)
        # the pad plus its dark surroundings: a missing fillet makes the dark quartile brighter
        own = (lbl[y0:y1, x0:x1] == i).astype(np.uint8)
        local = np.asarray(cv2.dilate(own, np.ones((7, 7), np.uint8))) > 0
        p25 = float(np.percentile(L[y0:y1, x0:x1][local], 25))
        out.append(Pad(y0, y1, x0, x1, local, BBox(x, y, bw, bh), area, p25))
    return out


def _bodies(lab: FloatMap, mask: NDArray[np.uint8]) -> list[tuple[int, int, int, int]]:
    L, a, b = lab[..., 0], lab[..., 1] - 128, lab[..., 2] - 128
    dark = ((L < 110) & (a > -6) & (np.hypot(a, b) < 20) & (mask > 0)).astype(np.uint8)
    # fill the light marking inside a body so the whole body is one blob
    close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (BODY_CLOSE, BODY_CLOSE))
    dark = np.asarray(cv2.morphologyEx(dark, cv2.MORPH_CLOSE, close), np.uint8)
    dark = np.asarray(cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), np.uint8)
    metal = neutral_bright(lab, 150, 25).astype(np.uint8)
    n, labels, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    lbl = np.asarray(labels, np.int32)
    h, w = L.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in st[i])
        if (
            area < BODY_MIN_AREA
            or area > 60000
            or min(bw, bh) < BODY_MIN_DIM
            or max(bw, bh) > 5 * min(bw, bh)
        ):
            continue  # fragments and thin stripes (gaps between terminals) match ambiguously
        if area < BODY_FILL * bw * bh:
            continue  # a dark ring around a bright pad, not a solid body
        if _ringed_by_metal(lbl, i, x, y, bw, bh, metal):
            continue  # marking on a metal can (quartz crystal), not a part body
        x0, y0, x1, y1 = x - BODY_MARGIN, y - BODY_MARGIN, x + bw + BODY_MARGIN, y + bh + BODY_MARGIN
        if x0 - SHIFT_SEARCH < 0 or y0 - SHIFT_SEARCH < 0 or x1 + SHIFT_SEARCH > w or y1 + SHIFT_SEARCH > h:
            continue
        out.append((x0, y0, x1, y1))
    return out


def _ringed_by_metal(
    lbl: NDArray[np.int32], i: int, x: int, y: int, bw: int, bh: int, metal: NDArray[np.uint8]
) -> bool:
    blob = np.zeros((bh + 16, bw + 16), np.uint8)
    blob[8 : 8 + bh, 8 : 8 + bw] = lbl[y : y + bh, x : x + bw] == i
    outer = np.asarray(cv2.dilate(blob, np.ones((13, 13), np.uint8)), np.uint8)
    inner = np.asarray(cv2.dilate(blob, np.ones((3, 3), np.uint8)), np.uint8)
    ring = outer & ~inner & 1
    wy0, wx0 = max(y - 8, 0), max(x - 8, 0)
    win = metal[wy0 : y + bh + 8, wx0 : x + bw + 8]
    oy, ox = 8 - (y - wy0), 8 - (x - wx0)
    rr = ring[oy : oy + win.shape[0], ox : ox + win.shape[1]]
    return bool(rr.sum()) and float(win[rr > 0].mean()) > BODY_RING_METAL


def _flat(lab: FloatMap, board: BoolMap) -> BoolMap:
    """Flat surfaces: no part edges, traces or marking, no bright metal (glare changes between boards)."""
    L = cv2.GaussianBlur(lab[..., 0], (0, 0), 1.0)
    gx = np.asarray(cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3), np.float32) / 8
    gy = np.asarray(cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3), np.float32) / 8
    grad = np.asarray(cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 1.5), np.float32)
    flat = ((grad < FLAT_GRAD) & ~neutral_bright(lab, 170, 25) & board).astype(np.uint8)
    away = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * FLAT_AWAY + 1, 2 * FLAT_AWAY + 1))
    flat = np.asarray(cv2.erode(flat, away), np.uint8)
    n, lbl, st, _ = cv2.connectedComponentsWithStats(flat)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, 4] >= FLAT_MIN_ISLAND
    return np.asarray(keep[lbl], np.bool_)


def _dark_bodies(lab: FloatMap) -> BoolMap:
    """Dark and grey package tops: their marking changes between lots and is lighter than the body."""
    med = np.asarray(cv2.medianBlur(np.clip(lab[..., 0], 0, 255).astype(np.uint8), 7), np.uint8)
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    d = ((med < DARK_BODY_L) & (chroma < 20)).astype(np.uint8)
    return np.asarray(cv2.morphologyEx(d, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)), np.uint8) > 0
