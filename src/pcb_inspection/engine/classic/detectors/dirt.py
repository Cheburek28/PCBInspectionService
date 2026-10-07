"""Dirt: specks / drops / crumbs on flat areas, and hairs / fibres anywhere off the metal."""

from __future__ import annotations

import math

import cv2
import numpy as np

from pcb_inspection.engine.base import InspectParams
from pcb_inspection.engine.classic.detectors.common import (
    HAIR_BEND_DEG,
    HAIR_BEND_PX,
    HAIR_FRAGMENT_MAX_WIDTH,
    HAIR_FRAGMENT_MIN_LEN,
    HAIR_GAP,
    RIDGE_LOW,
    SPECK_LOW,
    Finding,
    FloatMap,
)
from pcb_inspection.engine.classic.detectors.model import PoolMember, ReferenceModel
from pcb_inspection.engine.classic.detectors.ridges import new_ridges, ridge_maps
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage


def _debias(d: FloatMap, w: FloatMap, den: FloatMap) -> FloatMap:
    """Remove slow lighting / colour differences: subtract the local mean difference over flat pixels."""
    out = np.empty_like(d)
    for c in range(3):
        out[..., c] = d[..., c] - np.asarray(cv2.GaussianBlur(d[..., c] * w, (0, 0), 12), np.float32) / den
    return out


def detect_specks(
    model: ReferenceModel, aligned_lab: FloatMap, pool: list[PoolMember], p: InspectParams
) -> list[Finding]:
    """A compact difference on a flat area from every pool board."""
    w = model.flat.astype(np.float32)
    den = np.asarray(cv2.GaussianBlur(w, (0, 0), 12), np.float32) + 1e-3
    dark = model.dark
    mag: FloatMap | None = None
    for other in [model.lab] + [m.lab() for m in pool]:
        dd = _debias(aligned_lab - other, w, den)
        m = np.asarray(np.linalg.norm(dd, axis=2), np.float32)
        m[dark] = np.maximum(0, -dd[..., 0][dark])  # package tops: only darker spots
        mag = m if mag is None else np.minimum(mag, m)
    assert mag is not None
    # package tops: also darker than the body itself, so a missing (lighter) marking does not count
    below_body = np.maximum(model.body_level - aligned_lab[..., 0], 0)
    mag[dark] = np.minimum(mag[dark], below_body[dark])
    mag = np.asarray(cv2.GaussianBlur(mag, (0, 0), 0.7), np.float32)
    mag[~model.flat] = 0
    n, lbl, st, _ = cv2.connectedComponentsWithStats((mag > SPECK_LOW).astype(np.uint8))
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in st[i])
        sel = lbl[y : y + bh, x : x + bw] == i
        peak = float(mag[y : y + bh, x : x + bw][sel].max())
        on_body = float(dark[y : y + bh, x : x + bw][sel].mean()) > 0.5
        thr, min_area = (
            (p.speck_body_threshold, p.speck_body_min_area)
            if on_body
            else (p.speck_threshold, p.speck_min_area)
        )
        if peak >= thr and area >= min_area:
            out.append(Finding("speck", BBox(x, y, bw, bh), round(peak, 1)))
    return out


Fragment = tuple[BBox, float, float, tuple[float, float]]  # box, length, direction (deg), centre


def detect_hairs(
    model: ReferenceModel, aligned: BGRImage, pool: list[PoolMember], p: InspectParams
) -> list[Finding]:
    """Long, bent thin lines that no pool board has at that place in that direction."""
    new = new_ridges(ridge_maps(aligned), [model.ridge_near()] + [m.ridge_near() for m in pool])
    new[~model.hair_area] = 0
    bw = (new > RIDGE_LOW).astype(np.uint8)
    bw = np.asarray(
        cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))), np.uint8
    )
    n, lbl, st, _ = cv2.connectedComponentsWithStats(bw)
    frags: list[Fragment] = []
    for i in range(1, n):
        x0, y0, w, h, area = (int(q) for q in st[i])
        if max(w, h) < 12:
            continue
        sel = lbl[y0 : y0 + h, x0 : x0 + w] == i
        if float(new[y0 : y0 + h, x0 : x0 + w][sel].max()) < p.hair_peak:
            continue
        ys, xs = np.nonzero(sel)
        pts = np.column_stack([xs, ys]).astype(np.float64)
        centre = pts.mean(0)
        _, evec = np.linalg.eigh(np.cov((pts - centre).T))
        proj = (pts - centre) @ evec[:, 1]
        length = max(float(proj.max() - proj.min()), math.hypot(w, h) * 0.8)
        if length < HAIR_FRAGMENT_MIN_LEN or area / max(length, 1.0) > HAIR_FRAGMENT_MAX_WIDTH:
            continue
        ang = math.degrees(math.atan2(evec[1, 1], evec[0, 1])) % 180
        frags.append((BBox(x0, y0, w, h), length, ang, (x0 + float(centre[0]), y0 + float(centre[1]))))
    return _chains(frags, p)


def _gap(a: BBox, b: BBox) -> float:
    dx = max(0, max(a.x, b.x) - min(a.x + a.w, b.x + b.w))
    dy = max(0, max(a.y, b.y) - min(a.y + a.h, b.y + b.h))
    return math.hypot(dx, dy)


def _chains(frags: list[Fragment], p: InspectParams) -> list[Finding]:
    """A hair is cut where it crosses lines of the reference: link nearby fragments, keep bent chains."""
    parent = list(range(len(frags)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(frags)):
        for j in range(i + 1, len(frags)):
            if _gap(frags[i][0], frags[j][0]) <= HAIR_GAP:
                parent[root(i)] = root(j)
    groups: dict[int, list[Fragment]] = {}
    for i, f in enumerate(frags):
        groups.setdefault(root(i), []).append(f)
    out = []
    for g in groups.values():
        total = sum(f[1] for f in g)
        if len(g) < p.hair_min_fragments or total < p.hair_min_length or not _bent(g):
            continue  # a straight chain is an edge of a part or a trace, not a hair
        x0, y0 = min(f[0].x for f in g), min(f[0].y for f in g)
        x1, y1 = max(f[0].x + f[0].w for f in g), max(f[0].y + f[0].h for f in g)
        out.append(Finding("hair", BBox(x0, y0, x1 - x0, y1 - y0), round(total, 1)))
    return out


def _bent(g: list[Fragment]) -> bool:
    angs = [f[2] for f in g]
    spread = max(min(abs(a - b), 180 - abs(a - b)) for a in angs for b in angs)
    if spread >= HAIR_BEND_DEG:
        return True
    if len(g) < 3:
        return False
    pts = np.array([f[3] for f in g])
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    a, b = (int(v) for v in np.unravel_index(int(np.argmax(d)), d.shape))
    v = pts[b] - pts[a]
    n = math.hypot(float(v[0]), float(v[1])) + 1e-6
    bend = max(abs(v[0] * (q[1] - pts[a][1]) - v[1] * (q[0] - pts[a][0])) / n for q in pts)
    return float(bend) >= HAIR_BEND_PX
