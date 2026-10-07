"""Missing solder fillet and shifted part body — both measured per reference feature (pad, part)."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.base import InspectParams
from pcb_inspection.engine.classic.detectors.common import (
    BODY_UNIQUE,
    SHIFT_MIN_SCORE,
    SHIFT_SEARCH,
    SHIFT_UNSTABLE_PX,
    Finding,
    FloatMap,
)
from pcb_inspection.engine.classic.detectors.model import PoolMember, ReferenceModel
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage


def detect_solder(
    model: ReferenceModel, aligned_lab: FloatMap, pool: list[PoolMember], p: InspectParams
) -> list[Finding]:
    """Missing fillet: the dark quartile of a pad (its fillet shadow) is brighter than on every pool board."""
    L = aligned_lab[..., 0]
    pool_L = [m.lab()[..., 0] for m in pool]
    out = []
    for pad in model.pads:
        if pad.area < p.solder_min_pad_px:
            continue
        crop = (slice(pad.y0, pad.y1), slice(pad.x0, pad.x1))
        p_test = float(np.percentile(L[crop][pad.local], 25))
        deltas = [p_test - pad.p25] + [
            p_test - float(np.percentile(pl[crop][pad.local], 25)) for pl in pool_L
        ]
        if min(deltas) > p.solder_delta:
            out.append(Finding("solder", pad.box, round(min(deltas), 1)))
    return out


def shift_displacements(
    model: ReferenceModel, warped: BGRImage
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Displacement of every part body relative to its neighbours, measured on the photo aligned by the
    board-wide transform only (local flow would pull a shifted part back), and the match score
    (0 when the position is ambiguous)."""
    g = np.asarray(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY), np.uint8)
    r = SHIFT_SEARCH
    d, score = [], []
    for x0, y0, x1, y1 in model.parts:
        cc = np.asarray(
            cv2.matchTemplate(
                g[y0 - r : y1 + r, x0 - r : x1 + r], model.gray[y0:y1, x0:x1], cv2.TM_CCOEFF_NORMED
            ),
            np.float32,
        )
        _, best, _, loc = cv2.minMaxLoc(cc)
        other = cc.copy()
        cv2.circle(other, loc, 2, -1.0, -1)  # second best outside the peak: a ridge = ambiguous position
        d.append((loc[0] - r, loc[1] - r))
        score.append(float(best) if best - float(other.max()) >= BODY_UNIQUE else 0.0)
    disp = np.array(d, np.float64).reshape(-1, 2)
    if len(disp) == 0:
        return disp, np.zeros(0)
    rel = disp - np.median(disp[model.part_neighbours], axis=1)
    return rel, np.array(score)


def detect_shift(
    model: ReferenceModel,
    rel: NDArray[np.float64],
    score: NDArray[np.float64],
    pool: list[PoolMember],
    p: InspectParams,
) -> list[Finding]:
    """A part moved relative to its neighbours by at least ``shift_px`` against the median of the pool;
    parts whose position already varies across the pool are skipped."""
    if len(rel) == 0:
        return []
    known = [m.shift_rel for m in pool if m.shift_rel is not None and m.shift_rel.shape == rel.shape]
    refs = np.stack([np.zeros_like(rel), *known])
    base = np.median(refs, axis=0)
    stable = (
        np.max(np.hypot(refs[..., 0] - base[:, 0], refs[..., 1] - base[:, 1]), axis=0) <= SHIFT_UNSTABLE_PX
    )
    dist = np.hypot(rel[:, 0] - base[:, 0], rel[:, 1] - base[:, 1])
    out = []
    for (x0, y0, x1, y1), dv, sc, ok in zip(model.parts, dist, score, stable, strict=True):
        if ok and dv >= p.shift_px and sc >= SHIFT_MIN_SCORE:
            out.append(Finding("shift", BBox(x0, y0, x1 - x0, y1 - y0), round(float(dv), 1)))
    return out
