"""Worker side of an inspection: run the engine, apply quality gates, persist the result.

Error policy:
- bad input / algorithm errors are deterministic → status ``failed`` immediately, no retry;
- infrastructure errors (database, storage) propagate so that the task runner can retry,
  and ``mark_failed`` is called after the last attempt.
"""

from __future__ import annotations

import uuid
from typing import Any

import cv2
import structlog
from sqlalchemy import select

from pcb_inspection.db.models import Defect, Inspection
from pcb_inspection.domain import gates
from pcb_inspection.domain.enums import DefectSource, InspectionStatus, Stage, Verdict
from pcb_inspection.engine import imaging
from pcb_inspection.engine.base import EngineResult
from pcb_inspection.services import images, references
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.inspections import now, queue_wait_ms
from pcb_inspection.services.params import engine_params, gate_thresholds

log = structlog.get_logger(__name__)


class AnalysisError(Exception):
    """Deterministic failure that a retry cannot fix."""


def run_inspection(ctx: ServiceContext, inspection_id: uuid.UUID) -> InspectionStatus:
    claimed = _claim(ctx, inspection_id)
    if claimed is None:
        return get_status(ctx, inspection_id)
    insp = claimed
    log.info("inspection.start", inspection_id=str(inspection_id), attempt=insp.attempts)
    try:
        status = _analyze_and_save(ctx, insp)
    except AnalysisError as exc:
        mark_failed(ctx, inspection_id, "ANALYSIS_ERROR", str(exc))
        return InspectionStatus.FAILED
    log.info("inspection.done", inspection_id=str(inspection_id), status=status.value)
    return status


def _claim(ctx: ServiceContext, inspection_id: uuid.UUID) -> Inspection | None:
    """Move to PROCESSING. Returns None if the job is already final (duplicate delivery)."""
    with ctx.db.session() as s:
        # lock only the inspection row; eager joins are outer joins, which FOR UPDATE cannot lock
        insp = s.scalar(
            select(Inspection).where(Inspection.id == inspection_id).with_for_update(of=Inspection)
        )
        if insp is None:
            log.warning("inspection.missing", inspection_id=str(inspection_id))
            return None
        if InspectionStatus(insp.status).is_final:
            return None
        insp.status = InspectionStatus.PROCESSING
        insp.stage = Stage.DECODE
        insp.attempts += 1
        insp.started_at = insp.started_at or now()
        return insp


def get_status(ctx: ServiceContext, inspection_id: uuid.UUID) -> InspectionStatus:
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        return InspectionStatus(insp.status) if insp else InspectionStatus.FAILED


def _set_stage(ctx: ServiceContext, inspection_id: uuid.UUID, stage: Stage) -> None:
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        if insp is not None:
            insp.stage = stage


def _analyze_and_save(ctx: ServiceContext, insp: Inspection) -> InspectionStatus:
    thresholds = gate_thresholds(insp.params)
    try:
        test = images.load_pixels(ctx, insp.image)
    except imaging.ImageDecodeError as exc:
        raise AnalysisError(f"stored image cannot be decoded: {exc}") from exc
    size_rejection = gates.check_size(test.width, thresholds)
    if size_rejection is not None:
        return _save(ctx, insp.id, None, size_rejection)
    _set_stage(ctx, insp.id, Stage.ANALYZE)
    prepared = references.prepared(ctx, insp.reference)
    try:
        result = ctx.engine.inspect(prepared, test.pixels, engine_params(insp.params))
    except (cv2.error, ValueError) as exc:
        raise AnalysisError(f"engine failed: {exc}") from exc
    rejection = gates.evaluate(result.quality, thresholds)
    _set_stage(ctx, insp.id, Stage.SAVE)
    return _save(ctx, insp.id, result, rejection)


def _save(
    ctx: ServiceContext,
    inspection_id: uuid.UUID,
    result: EngineResult | None,
    rejection: gates.Rejection | None,
) -> InspectionStatus:
    aligned_key = heatmap_key = None
    if result is not None and result.aligned is not None:
        aligned_key = f"inspections/{inspection_id}/aligned.jpg"
        ctx.storage.put(aligned_key, imaging.encode_jpeg(result.aligned, 92), "image/jpeg")
    if result is not None and result.heatmap is not None:
        heatmap_key = f"inspections/{inspection_id}/heatmap.jpg"
        ctx.storage.put(heatmap_key, imaging.encode_jpeg(result.heatmap, 85), "image/jpeg")
    status = InspectionStatus.REJECTED if rejection else InspectionStatus.COMPLETED
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        assert insp is not None
        insp.status = status
        insp.stage = None
        insp.finished_at = now()
        insp.aligned_storage_key = aligned_key
        insp.heatmap_storage_key = heatmap_key
        insp.engine_version = ctx.engine.version
        if rejection is not None:
            insp.rejection_code = rejection.code.value
            insp.rejection = {"message": rejection.message, "details": rejection.details}
        timings: dict[str, Any] = {"queue": queue_wait_ms(insp)}
        if result is not None:
            insp.quality = _quality_json(result)
            if result.ref_to_test is not None:
                insp.transform = {"ref_to_test": result.ref_to_test.tolist()}
            timings |= result.timings_ms
            for rank, d in enumerate(result.differences, start=1):
                s.add(
                    Defect(
                        inspection_id=inspection_id,
                        source=DefectSource.AUTO,
                        rank=rank,
                        score=d.score,
                        area=d.area,
                        test_x=d.bbox_test.x,
                        test_y=d.bbox_test.y,
                        test_w=d.bbox_test.w,
                        test_h=d.bbox_test.h,
                        ref_x=d.bbox_ref.x,
                        ref_y=d.bbox_ref.y,
                        ref_w=d.bbox_ref.w,
                        ref_h=d.bbox_ref.h,
                        verdict=Verdict.PENDING,
                    )
                )
        insp.timings = timings
    return status


def _quality_json(result: EngineResult) -> dict[str, Any]:
    q = result.quality
    return {
        "alignment_inliers": q.alignment_inliers,
        "alignment_ok": q.alignment_ok,
        "sharpness_ratio": q.sharpness_ratio,
        "lab_shift": list(q.lab_shift) if q.lab_shift is not None else None,
        "differences_count": q.differences_count,
        "differences_area_ratio": q.differences_area_ratio,
        "same_as_reference": q.same_as_reference,
    }


def mark_failed(ctx: ServiceContext, inspection_id: uuid.UUID, code: str, message: str) -> None:
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        if insp is None or InspectionStatus(insp.status).is_final:
            return
        insp.status = InspectionStatus.FAILED
        insp.stage = None
        insp.finished_at = now()
        insp.error = {"code": code, "message": message}
    log.error("inspection.failed", inspection_id=str(inspection_id), code=code, message=message)
