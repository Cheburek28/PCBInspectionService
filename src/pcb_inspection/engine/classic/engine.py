"""classic-diff: board-anchored registration + normalised tolerant colour difference, one reference."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import cv2
import numpy as np

from pcb_inspection.engine.base import (
    Difference,
    EngineResult,
    InspectParams,
    MaskSpec,
    PreparedReference,
    QualityMetrics,
)
from pcb_inspection.engine.classic import align as al
from pcb_inspection.engine.classic import diff, quality
from pcb_inspection.engine.classic.mask import build_mask
from pcb_inspection.engine.geometry import BBox, Matrix, project_bbox, scale_matrix
from pcb_inspection.engine.imaging import BGRImage, resize_to_width

# mean absolute difference below this means the test photo *is* the reference photo
SAME_IMAGE_MAD = 1.0
# differing regions are excluded from the sharpness measurement with this margin (working pixels)
SHARPNESS_PAD = 10
FEATURE_MARGIN_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (121, 121))


class _Timer:
    def __init__(self) -> None:
        self.ms: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = self.ms.get(name, 0) + round((time.perf_counter() - t0) * 1000)


class ClassicEngine:
    name = "classic-diff"
    version = "0.2.0"

    def prepare_reference(self, image: BGRImage, mask_spec: MaskSpec, work_width: int) -> PreparedReference:
        # never upscale: it adds no detail and would change the meaning of pixel-based thresholds
        work, scale = resize_to_width(image, min(work_width, image.shape[1]))
        mask = build_mask(work, mask_spec, scale)
        return PreparedReference(
            image=work,
            mask=mask,
            scale=scale,
            original_size=(image.shape[1], image.shape[0]),
            mask_spec=mask_spec,
        )

    def _features(self, ref: PreparedReference, n_features: int) -> al.Features:
        cached = ref.features
        if isinstance(cached, al.Features):
            return cached
        # keypoints only on (and just around) the board: the fixture seen through the slots and the panel
        # may shift relative to the board between photos and must not drive the alignment
        features = al.detect(
            ref.image, n_features, np.asarray(cv2.dilate(ref.mask, FEATURE_MARGIN_KERNEL), np.uint8)
        )
        ref.features = features
        return features

    def inspect(self, ref: PreparedReference, test: BGRImage, params: InspectParams) -> EngineResult:
        timer = _Timer()
        with timer.stage("resize"):
            test_work, test_scale = resize_to_width(test, ref.image.shape[1])
        with timer.stage("align"):
            alignment = al.align(
                ref.image, self._features(ref, params.sift_features), test_work, params.sift_features
            )
        if not alignment.ok or alignment.ref_to_test is None or alignment.warped is None:
            timer.ms["total"] = sum(timer.ms.values())
            return EngineResult(
                quality=QualityMetrics(alignment_inliers=alignment.inliers, alignment_ok=False),
                differences=[],
                ref_to_test=None,
                aligned=None,
                heatmap=None,
                timings_ms=timer.ms,
            )
        ref_to_test_work = alignment.ref_to_test
        raw = alignment.warped
        with timer.stage("quality"):
            shift = quality.lab_shift(raw, ref.image, ref.mask)
            same = float(np.abs(ref.image.astype(np.int16) - raw).mean()) < SAME_IMAGE_MAD
        with timer.stage("refine"):
            compared = quality.color_match(raw, ref.image, ref.mask) if params.color_match else raw
            compared = al.refine_flow(ref.image, compared)
        with timer.stage("diff"):
            if same:
                dmap = np.zeros(ref.image.shape[:2], np.float32)
            else:
                dmap = diff.diff_map(
                    ref.image,
                    compared,
                    params.tol_px,
                    diff.Normalisation(params.highlight_clip, params.open_radius, params.background_sigma),
                )
            regions, heat = diff.find_regions(
                dmap, ref.mask, params.threshold, params.min_area, params.extent_threshold
            )
        with timer.stage("quality"):
            sharp_ratio = self._sharpness_ratio(ref, test_work, ref_to_test_work, regions)
        ref_to_test_up = scale_matrix(1 / test_scale) @ ref_to_test_work @ scale_matrix(ref.scale)
        differences = self._to_uploaded(regions, ref, ref_to_test_up, (test.shape[1], test.shape[0]))
        with timer.stage("render"):
            heatmap = diff.colorize(heat, compared, ref.mask, params.threshold)
        mask_px = max(int(ref.mask.sum()), 1)
        metrics = QualityMetrics(
            alignment_inliers=alignment.inliers,
            alignment_ok=True,
            sharpness_ratio=round(sharp_ratio, 3),
            lab_shift=shift,
            differences_count=len(differences),
            differences_area_ratio=round(sum(r.area for r in regions) / mask_px, 5),
            same_as_reference=same,
        )
        timer.ms["total"] = sum(timer.ms.values())
        return EngineResult(
            quality=metrics,
            differences=differences,
            ref_to_test=ref_to_test_up,
            aligned=compared,
            heatmap=heatmap,
            timings_ms=timer.ms,
        )

    @staticmethod
    def _sharpness_ratio(
        ref: PreparedReference, test_work: BGRImage, ref_to_test_work: Matrix, regions: list[diff.Region]
    ) -> float:
        """Sharpness of the test relative to the reference, measured only where they match.

        Differing regions are excluded: a board with many missing parts has fewer edges and would
        otherwise look blurry. Measured on the unwarped test photo (warping itself softens the image).
        """
        common = ref.mask.copy()
        for r in regions:
            b = r.bbox
            common[
                max(b.y - SHARPNESS_PAD, 0) : b.y + b.h + SHARPNESS_PAD,
                max(b.x - SHARPNESS_PAD, 0) : b.x + b.w + SHARPNESS_PAD,
            ] = 0
        if common.sum() < 0.1 * ref.mask.sum():
            common = ref.mask
        test_mask = np.asarray(
            cv2.warpPerspective(
                common, ref_to_test_work, (test_work.shape[1], test_work.shape[0]), flags=cv2.INTER_NEAREST
            ),
            np.uint8,
        )
        ref_sharp = quality.sharpness(ref.image, common)
        return quality.sharpness(test_work, test_mask) / ref_sharp if ref_sharp > 0 else 0.0

    @staticmethod
    def _to_uploaded(
        regions: list[diff.Region],
        ref: PreparedReference,
        ref_to_test_up: Matrix,
        test_size: tuple[int, int],
    ) -> list[Difference]:
        out = []
        ref_w, ref_h = ref.original_size
        for region in regions:
            bbox_ref = region.bbox.scaled(1 / ref.scale).clip(ref_w, ref_h)
            if bbox_ref is None:
                continue
            bbox_test = project_bbox(ref_to_test_up, bbox_ref).clip(*test_size)
            if bbox_test is None:
                continue  # region falls outside the test photo
            out.append(
                Difference(bbox_ref=bbox_ref, bbox_test=bbox_test, score=region.score, area=region.area)
            )
        return out


def map_test_bbox_to_ref(ref_to_test: Matrix, bbox_test: BBox, ref_size: tuple[int, int]) -> BBox | None:
    """Map a box drawn on the uploaded test photo into uploaded-reference pixels."""
    return project_bbox(np.linalg.inv(ref_to_test), bbox_test).clip(*ref_size)
