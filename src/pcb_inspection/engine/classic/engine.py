"""classic-diff: board-anchored registration + normalised tolerant colour difference, one reference."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from concurrent.futures import Future
from contextlib import contextmanager

import cv2
import numpy as np

from pcb_inspection.engine.base import (
    Difference,
    EngineResult,
    InspectParams,
    MaskSpec,
    PoolPhoto,
    PreparedReference,
    QualityMetrics,
)
from pcb_inspection.engine.classic import align as al
from pcb_inspection.engine.classic import detectors as det
from pcb_inspection.engine.classic import diff, quality
from pcb_inspection.engine.classic.mask import build_mask
from pcb_inspection.engine.geometry import BBox, Matrix, project_bbox, scale_matrix
from pcb_inspection.engine.imaging import BGRImage, resize_to_width

# mean absolute difference below this means the test photo *is* the reference photo
SAME_IMAGE_MAD = 1.0
# differing regions are excluded from the sharpness measurement with this margin (working pixels)
SHARPNESS_PAD = 10
FEATURE_MARGIN_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (121, 121))
# a detector finding this close to a difference-map region is the same thing (working pixels)
SAME_REGION_PAD = 3
# pool photos kept decoded with their derived maps (~100 MB each at 3000 px)
POOL_CACHE_SIZE = 4


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
    version = "0.4.0"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool_cache: OrderedDict[str, det.PoolMember] = OrderedDict()

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

    def _detector_model(self, ref: PreparedReference) -> det.ReferenceModel:
        with self._lock:
            if not isinstance(ref.detector_model, det.ReferenceModel):
                ref.detector_model = det.build_reference_model(ref.image, ref.mask)
            return ref.detector_model

    def _pool_members(
        self, ref: PreparedReference, model: det.ReferenceModel, pool: Sequence[PoolPhoto]
    ) -> list[det.PoolMember]:
        out = []
        for photo in pool:
            with self._lock:
                member = self._pool_cache.get(photo.key)
            if member is None:
                aligned = photo.load()
                if aligned.shape != ref.image.shape:
                    continue  # aligned to another reference size: not comparable
                rel = (photo.measures or {}).get("shift_rel")
                shift_rel = np.asarray(rel, np.float64) if rel is not None else None
                if shift_rel is None and photo.load_warped is not None:
                    warped = photo.load_warped()
                    if warped.shape == ref.image.shape:
                        shift_rel = det.shift_displacements(model, warped)[0]
                member = det.PoolMember(aligned, shift_rel)
            with self._lock:
                self._pool_cache[photo.key] = member
                self._pool_cache.move_to_end(photo.key)
                while len(self._pool_cache) > POOL_CACHE_SIZE:
                    self._pool_cache.popitem(last=False)
            out.append(member)
        return out

    def inspect(
        self,
        ref: PreparedReference,
        test: BGRImage,
        params: InspectParams,
        pool: Sequence[PoolPhoto] = (),
    ) -> EngineResult:
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
        pending: Future[det.DetectorOutput] | None = None
        if det.enabled(params):
            with timer.stage("detectors_prepare"):
                model = self._detector_model(ref)
                job = det.DetectorJob(model, self._pool_members(ref, model, pool), params)
            # runs in background threads while the difference map is computed below
            pending = job.start(raw, compared)
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
        measures = None
        n_diff = len(differences)  # quality gates judge the difference map only
        if pending is not None:
            with timer.stage("detectors_wait"):
                out = pending.result()
            timer.ms |= {f"det_{k}": v for k, v in out.timings_ms.items()}
            measures = out.measures
            new = _new_findings(out.findings, [r.bbox for r in regions])
            differences += self._to_uploaded(
                [diff.Region(bbox=f.bbox, area=f.bbox.w * f.bbox.h, score=f.score) for f in new],
                ref,
                ref_to_test_up,
                (test.shape[1], test.shape[0]),
                [f.kind for f in new],
            )
        mask_px = max(int(ref.mask.sum()), 1)
        metrics = QualityMetrics(
            alignment_inliers=alignment.inliers,
            alignment_ok=True,
            sharpness_ratio=round(sharp_ratio, 3),
            lab_shift=shift,
            differences_count=n_diff,
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
            measures=measures,
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
        kinds: Sequence[str] | None = None,
    ) -> list[Difference]:
        out = []
        ref_w, ref_h = ref.original_size
        for i, region in enumerate(regions):
            bbox_ref = region.bbox.scaled(1 / ref.scale).clip(ref_w, ref_h)
            if bbox_ref is None:
                continue
            bbox_test = project_bbox(ref_to_test_up, bbox_ref).clip(*test_size)
            if bbox_test is None:
                continue  # region falls outside the test photo
            kind = kinds[i] if kinds is not None else "diff"
            out.append(
                Difference(
                    bbox_ref=bbox_ref, bbox_test=bbox_test, score=region.score, area=region.area, kind=kind
                )
            )
        return out


def _new_findings(findings: list[det.Finding], shown: list[BBox]) -> list[det.Finding]:
    """Drop findings that touch an already reported box (difference map or an earlier finding)."""
    boxes = list(shown)
    out = []
    for f in findings:
        if any(_touch(f.bbox, b) for b in boxes):
            continue
        boxes.append(f.bbox)
        out.append(f)
    return out


def _touch(a: BBox, b: BBox, pad: int = SAME_REGION_PAD) -> bool:
    return not (
        a.x + a.w + pad < b.x or b.x + b.w + pad < a.x or a.y + a.h + pad < b.y or b.y + b.h + pad < a.y
    )


def map_test_bbox_to_ref(ref_to_test: Matrix, bbox_test: BBox, ref_size: tuple[int, int]) -> BBox | None:
    """Map a box drawn on the uploaded test photo into uploaded-reference pixels."""
    return project_bbox(np.linalg.inv(ref_to_test), bbox_test).clip(*ref_size)
