"""Shared types and constants of the targeted detectors (pixel sizes tuned for a 3000 px working width)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage

FloatMap = NDArray[np.float32]
BoolMap = NDArray[np.bool_]

# solder: a pad = bright neutral metal blob of the reference
PAD_MIN_PX, PAD_MAX_PX = 120, 20000
# white silkscreen lines are bright and neutral too: a pad is compact
PAD_MAX_ASPECT, PAD_MAX_LEN = 8.0, 150
# part bodies (shift): dark solid blobs, template-matched within +-SHIFT_SEARCH px
BODY_CLOSE, BODY_MIN_AREA, BODY_MIN_DIM, BODY_MARGIN = 7, 100, 7, 5
BODY_FILL, BODY_RING_METAL, BODY_UNIQUE = 0.55, 0.6, 0.02
SHIFT_SEARCH, SHIFT_NEIGHBOURS, SHIFT_UNSTABLE_PX, SHIFT_MIN_SCORE = 12, 6, 2.0, 0.6
# specks: flat areas of the reference
FLAT_GRAD, FLAT_AWAY, FLAT_MIN_ISLAND, BOARD_EDGE = 6.0, 3, 150, 8
DARK_BODY_L, SPECK_LOW = 130, 10.0
# more specks than this on one photo: reference and photo differ in sharpness (focus), edges of traces and
# silkscreen show up as specks. Then only coarse specks are kept (both images blurred) and hairs are skipped.
SPECK_INCOMPARABLE, SPECK_COARSE_SIGMA = 15, 2.0
# hairs: thin lines (Hessian ridges) not present on the pool
RIDGE_SCALES = (1.0, 1.5, 2.2)
RIDGE_BINS, RIDGE_TOL, RIDGE_LOW = 6, 3, 10.0
HAIR_GAP, HAIR_FRAGMENT_MIN_LEN, HAIR_FRAGMENT_MAX_WIDTH = 15.0, 10.0, 6.0
HAIR_BEND_DEG, HAIR_BEND_PX = 25.0, 3.0


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str  # solder | shift | speck | hair
    bbox: BBox  # working pixels of the reference
    score: float


@dataclass(slots=True)
class DetectorOutput:
    findings: list[Finding]
    measures: dict[str, Any]  # stored with the inspection; used when this photo joins a pool
    timings_ms: dict[str, int] = field(default_factory=dict)


def to_lab(img: BGRImage) -> FloatMap:
    return np.asarray(cv2.cvtColor(img, cv2.COLOR_BGR2LAB), np.float32)


def neutral_bright(lab: FloatMap, l_min: float, chroma_max: float) -> BoolMap:
    """Bright and colourless: solder, tinned pads, white terminations."""
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    return np.asarray((lab[..., 0] > l_min) & (chroma < chroma_max), np.bool_)
