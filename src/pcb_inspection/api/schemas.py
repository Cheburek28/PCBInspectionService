"""Public API contract (v1). Changing anything here changes openapi.json — run ``make openapi``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pcb_inspection.db.models import Board, Defect, Inspection, InspectionSession, Reference
from pcb_inspection.domain.enums import (
    BoardVerdict,
    DefectSource,
    DefectType,
    InspectionStatus,
    RejectionCode,
    SessionStatus,
    Stage,
    Verdict,
)
from pcb_inspection.engine.base import MaskStrategy
from pcb_inspection.engine.geometry import BBox

API_PREFIX = "/api/v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BBoxModel(_Model):
    """Pixels of the uploaded image after EXIF orientation; (x, y) is the top-left corner."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)

    def to_bbox(self) -> BBox:
        return BBox(self.x, self.y, self.w, self.h)


class Problem(_Model):
    """RFC 9457 problem details."""

    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


# ---- sessions


class SessionCreate(_Model):
    product_code: str = Field(min_length=1, max_length=100, examples=["demo-board"])
    product_name: str | None = Field(default=None, max_length=200)
    operator: str | None = Field(default=None, max_length=100)
    client_meta: dict[str, Any] = Field(default_factory=dict)


class SessionOut(_Model):
    id: uuid.UUID
    product_code: str
    product_name: str | None
    operator: str | None
    status: SessionStatus
    station: str
    active_references: dict[int, uuid.UUID]
    client_meta: dict[str, Any]
    created_at: datetime
    closed_at: datetime | None

    @classmethod
    def build(cls, s: InspectionSession, active: dict[int, Reference]) -> SessionOut:
        return cls(
            id=s.id,
            product_code=s.product_code,
            product_name=s.product_name,
            operator=s.operator,
            status=SessionStatus(s.status),
            station=s.api_key.station,
            active_references={side: ref.id for side, ref in sorted(active.items())},
            client_meta=s.client_meta,
            created_at=s.created_at,
            closed_at=s.closed_at,
        )


# ---- references


class BoardIn(_Model):
    board_key: str = Field(
        min_length=1, max_length=100, description="Client-generated id of the physical board"
    )
    barcode: str | None = Field(default=None, max_length=100)
    serial: str | None = Field(default=None, max_length=100)


class ReferenceFromInspection(_Model):
    from_inspection_id: uuid.UUID
    side: int | None = Field(default=None, ge=1, le=8)


class ImageInfo(_Model):
    width: int
    height: int
    sha256: str | None = None
    url: str | None = None


class MaskInfo(_Model):
    strategy: MaskStrategy
    coverage: float
    url: str


class ReferenceOut(_Model):
    id: uuid.UUID
    session_id: uuid.UUID
    side: int
    is_active: bool
    source: str
    source_inspection_id: uuid.UUID | None
    image: ImageInfo
    mask: MaskInfo
    created_at: datetime

    @classmethod
    def build(cls, r: Reference) -> ReferenceOut:
        return cls(
            id=r.id,
            session_id=r.session_id,
            side=r.side,
            is_active=r.is_active,
            source=r.source,
            source_inspection_id=r.source_inspection_id,
            image=ImageInfo(width=r.image.width, height=r.image.height, sha256=r.image.sha256),
            mask=MaskInfo(
                strategy=MaskStrategy(r.mask_strategy),
                coverage=r.mask_coverage,
                url=f"{API_PREFIX}/references/{r.id}/mask.png",
            ),
            created_at=r.created_at,
        )


# ---- inspections and defects


class DefectOut(_Model):
    id: uuid.UUID
    source: DefectSource
    rank: int
    score: float | None
    area: int | None
    bbox_test: BBoxModel
    bbox_ref: BBoxModel
    verdict: Verdict
    defect_type: DefectType | None
    comment: str | None
    verdict_operator: str | None
    verdict_at: datetime | None
    crop_url: str

    @classmethod
    def build(cls, d: Defect) -> DefectOut:
        return cls(
            id=d.id,
            source=DefectSource(d.source),
            rank=d.rank,
            score=d.score,
            area=d.area,
            bbox_test=BBoxModel(x=d.test_x, y=d.test_y, w=d.test_w, h=d.test_h),
            bbox_ref=BBoxModel(x=d.ref_x, y=d.ref_y, w=d.ref_w, h=d.ref_h),
            verdict=Verdict(d.verdict),
            defect_type=DefectType(d.defect_type) if d.defect_type else None,
            comment=d.comment,
            verdict_operator=d.verdict_operator,
            verdict_at=d.verdict_at,
            crop_url=f"{API_PREFIX}/inspections/{d.inspection_id}/defects/{d.id}/crop?kind=pair",
        )


class QualityOut(_Model):
    alignment_inliers: int
    alignment_ok: bool
    sharpness_ratio: float | None
    lab_shift: list[float] | None
    differences_count: int
    differences_area_ratio: float
    same_as_reference: bool


class RejectionOut(_Model):
    code: RejectionCode
    message: str
    details: dict[str, Any]


class ErrorOut(_Model):
    code: str
    message: str


class AlgorithmOut(_Model):
    name: str
    version: str | None
    params: dict[str, Any]


class InspectionAccepted(_Model):
    id: uuid.UUID
    status: InspectionStatus
    poll_url: str


class InspectionOut(_Model):
    id: uuid.UUID
    session_id: uuid.UUID
    board: BoardIn
    side: int
    reference_id: uuid.UUID
    status: InspectionStatus
    stage: Stage | None
    superseded: bool
    image: ImageInfo
    quality: QualityOut | None
    rejection: RejectionOut | None
    error: ErrorOut | None
    defects: list[DefectOut]
    algorithm: AlgorithmOut
    timings_ms: dict[str, int | None]
    attempts: int
    captured_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def build(cls, i: Inspection) -> InspectionOut:
        rejection = None
        if i.rejection_code and i.rejection:
            rejection = RejectionOut(
                code=RejectionCode(i.rejection_code),
                message=i.rejection["message"],
                details=i.rejection.get("details", {}),
            )
        return cls(
            id=i.id,
            session_id=i.session_id,
            board=BoardIn(board_key=i.board.board_key, barcode=i.board.barcode, serial=i.board.serial),
            side=i.side,
            reference_id=i.reference_id,
            status=InspectionStatus(i.status),
            stage=Stage(i.stage) if i.stage else None,
            superseded=i.superseded,
            image=ImageInfo(
                width=i.image.width,
                height=i.image.height,
                sha256=i.image.sha256,
                url=f"{API_PREFIX}/inspections/{i.id}/image?kind=original",
            ),
            quality=QualityOut(**i.quality) if i.quality else None,
            rejection=rejection,
            error=ErrorOut(**i.error) if i.error else None,
            defects=[DefectOut.build(d) for d in i.defects],
            algorithm=AlgorithmOut(name=i.engine_name, version=i.engine_version, params=i.params),
            timings_ms=i.timings or {},
            attempts=i.attempts,
            captured_at=i.captured_at,
            created_at=i.created_at,
            started_at=i.started_at,
            finished_at=i.finished_at,
        )


class VerdictIn(_Model):
    verdict: Verdict
    defect_type: DefectType | None = None
    comment: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=100)


class ManualDefectIn(_Model):
    bbox_test: BBoxModel
    defect_type: DefectType | None = None
    comment: str | None = Field(default=None, max_length=2000)
    operator: str | None = Field(default=None, max_length=100)


# ---- boards


class BoardVerdictIn(_Model):
    verdict: BoardVerdict
    operator: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)
    barcode: str | None = Field(default=None, max_length=100)
    serial: str | None = Field(default=None, max_length=100)


class BoardOut(_Model):
    board_key: str
    barcode: str | None
    serial: str | None
    verdict: BoardVerdict | None
    verdict_operator: str | None
    verdict_comment: str | None
    verdict_at: datetime | None
    inspections: dict[int, uuid.UUID] = Field(description="side -> latest inspection id")

    @classmethod
    def build(cls, b: Board, latest: dict[int, Inspection] | None = None) -> BoardOut:
        return cls(
            board_key=b.board_key,
            barcode=b.barcode,
            serial=b.serial,
            verdict=BoardVerdict(b.verdict) if b.verdict else None,
            verdict_operator=b.verdict_operator,
            verdict_comment=b.verdict_comment,
            verdict_at=b.verdict_at,
            inspections={side: i.id for side, i in sorted((latest or {}).items())},
        )


class Health(_Model):
    status: str
    checks: dict[str, str] = Field(default_factory=dict)
