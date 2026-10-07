"""Engine contract: what every inspection algorithm receives and returns."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.geometry import BBox, Matrix
from pcb_inspection.engine.imaging import BGRImage


class MaskStrategy(StrEnum):
    FULL_FRAME = "full_frame"
    BLUE_FIXTURE = "blue_fixture"
    GREEN_BOARD = "green_board"
    POLYGON = "polygon"


@dataclass(frozen=True, slots=True)
class MaskSpec:
    strategy: MaskStrategy = MaskStrategy.FULL_FRAME
    # polygon vertices in pixels of the uploaded reference image (strategy POLYGON only)
    polygon: tuple[tuple[float, float], ...] = ()
    margin_px: int = 6


class MaskNotFoundError(ValueError):
    """The board area could not be located on the reference image."""


@dataclass(frozen=True, slots=True)
class InspectParams:
    work_width: int = 3000
    threshold: float = 20.0  # report a region when its peak difference reaches this
    extent_threshold: float = 12.0  # region outline (and area) is where the difference exceeds this
    min_area: int = 40
    tol_px: int = 2
    color_match: bool = True
    sift_features: int = 6000
    # normalisation applied to both images before comparing (working pixels / 8-bit Lab L):
    highlight_clip: int = 100  # lightness above this is treated as equal (glare on solder); 255 = off
    open_radius: int = 4  # morphological opening removes thin bright strokes (markings); 0 = off
    background_sigma: float = 20.0  # subtract local mean lightness (body tone, uneven light); 0 = off
    # targeted detectors (classic.detectors), compared with the reference pool; all off by default
    detect_solder: bool = False  # missing solder fillet
    solder_delta: float = 25.0  # dark quartile of a pad brighter than every pool board by more than this (L)
    solder_min_pad_px: int = 500
    detect_shift: bool = False  # part body moved relative to its neighbours
    shift_px: float = 4.0
    detect_specks: bool = False  # specks, drops, crumbs on flat areas
    speck_threshold: float = 30.0  # Lab distance
    speck_min_area: int = 20
    speck_body_threshold: float = 40.0  # on dark package tops (lot marking varies there)
    speck_body_min_area: int = 40
    detect_hairs: bool = False  # hairs and fibres
    hair_peak: float = 55.0
    hair_min_length: float = 35.0
    hair_min_fragments: int = 2


@dataclass(slots=True)
class PreparedReference:
    """Reference image converted to the working resolution plus everything reusable between inspections."""

    image: BGRImage  # working resolution
    mask: NDArray[np.uint8]  # 1 = inspect, 0 = ignore; working resolution
    scale: float  # working / uploaded
    original_size: tuple[int, int]  # (width, height) of the uploaded image
    mask_spec: MaskSpec
    features: object | None = None  # engine-specific cache (e.g. SIFT keypoints)
    detector_model: object | None = None  # engine-specific cache for targeted detectors

    @property
    def mask_coverage(self) -> float:
        return float(self.mask.mean())


@dataclass(frozen=True, slots=True)
class PoolPhoto:
    """A passed board of the same reference: its aligned photo (working resolution) and stored measures."""

    key: str  # stable id (cache key): the photo is loaded only when the engine has not cached it
    load: Callable[[], BGRImage]  # aligned photo at working resolution
    measures: dict[str, Any] | None
    # the photo in the reference frame by the board-wide transform only (no local flow); used to measure
    # what an older inspection did not store
    load_warped: Callable[[], BGRImage] | None = None


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    alignment_inliers: int
    alignment_ok: bool
    sharpness_ratio: float | None = None
    lab_shift: tuple[float, float, float] | None = None
    differences_count: int = 0
    differences_area_ratio: float = 0.0
    same_as_reference: bool = False


@dataclass(frozen=True, slots=True)
class Difference:
    """One region that differs from the reference, in uploaded-image pixels of both images."""

    bbox_ref: BBox
    bbox_test: BBox
    score: float
    area: int  # pixels at working resolution
    kind: str = "diff"  # diff = difference map; solder | shift | speck | hair = targeted detectors


@dataclass(slots=True)
class EngineResult:
    quality: QualityMetrics
    differences: list[Difference]
    # 3x3 matrix mapping uploaded-reference pixels to uploaded-test pixels; None if alignment failed
    ref_to_test: Matrix | None
    aligned: BGRImage | None  # test warped into the reference frame, working resolution
    heatmap: NDArray[np.uint8] | None  # colorized difference map, working resolution
    timings_ms: dict[str, int] = field(default_factory=dict)
    # per-photo measurements kept with the inspection; needed when this photo becomes a pool member
    measures: dict[str, Any] | None = None


class Engine(Protocol):
    name: str
    version: str

    def prepare_reference(self, image: BGRImage, mask_spec: MaskSpec, work_width: int) -> PreparedReference:
        """Raises MaskNotFoundError when the board area cannot be found."""
        ...

    def inspect(
        self,
        ref: PreparedReference,
        test: BGRImage,
        params: InspectParams,
        pool: Sequence[PoolPhoto] = (),
    ) -> EngineResult:
        """``pool``: passed boards of the same reference, compared against by the targeted detectors."""
        ...
