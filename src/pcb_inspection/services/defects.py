"""Operator feedback on differences: verdicts and manually marked regions."""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pcb_inspection.db.models import ApiKey, Defect, DefectEvent, Inspection
from pcb_inspection.domain.enums import DefectSource, DefectType, InspectionStatus, Verdict
from pcb_inspection.domain.errors import InvalidState, NotFound, NoTransform, ValidationFailed
from pcb_inspection.engine.classic.engine import map_test_bbox_to_ref
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.inspections import now


def _snapshot(d: Defect) -> dict[str, Any]:
    return {"verdict": d.verdict, "defect_type": d.defect_type, "comment": d.comment}


def set_verdict(
    ctx: ServiceContext,
    api_key: ApiKey,
    defect_id: uuid.UUID,
    verdict: Verdict,
    defect_type: DefectType | None = None,
    comment: str | None = None,
    operator: str | None = None,
) -> Defect:
    with ctx.db.session() as s:
        defect = s.get(Defect, defect_id)
        if defect is None:
            raise NotFound(f"defect {defect_id} not found")
        old = _snapshot(defect)
        defect.verdict = verdict.value
        defect.defect_type = defect_type.value if defect_type else None
        defect.comment = comment
        defect.verdict_operator = operator
        defect.verdict_at = now() if verdict is not Verdict.PENDING else None
        s.add(
            DefectEvent(
                defect_id=defect.id,
                event="verdict_changed",
                old=old,
                new=_snapshot(defect),
                operator=operator,
                api_key_id=api_key.id,
            )
        )
        s.flush()
        s.refresh(defect)
        return defect


def add_manual(
    ctx: ServiceContext,
    api_key: ApiKey,
    inspection_id: uuid.UUID,
    bbox_test: BBox,
    defect_type: DefectType | None = None,
    comment: str | None = None,
    operator: str | None = None,
) -> Defect:
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        if insp is None:
            raise NotFound(f"inspection {inspection_id} not found")
        if insp.status not in (InspectionStatus.COMPLETED, InspectionStatus.REJECTED):
            raise InvalidState(f"inspection is {insp.status}; wait until it is completed or rejected")
        if not insp.transform:
            raise NoTransform("the photo could not be aligned with the reference, a region cannot be mapped")
        clipped = bbox_test.clip(insp.image.width, insp.image.height)
        if clipped is None:
            raise ValidationFailed("bbox_test lies outside the image")
        ref_image = insp.reference.image
        bbox_ref = map_test_bbox_to_ref(
            np.asarray(insp.transform["ref_to_test"], dtype=np.float64),
            clipped,
            (ref_image.width, ref_image.height),
        )
        if bbox_ref is None:
            raise ValidationFailed("bbox_test maps outside the reference image")
        defect = Defect(
            inspection_id=insp.id,
            source=DefectSource.MANUAL,
            rank=_next_rank(s, insp.id),
            score=None,
            area=None,
            test_x=clipped.x,
            test_y=clipped.y,
            test_w=clipped.w,
            test_h=clipped.h,
            ref_x=bbox_ref.x,
            ref_y=bbox_ref.y,
            ref_w=bbox_ref.w,
            ref_h=bbox_ref.h,
            verdict=Verdict.ACCEPTED,
            defect_type=defect_type.value if defect_type else None,
            comment=comment,
            verdict_operator=operator,
            verdict_at=now(),
        )
        s.add(defect)
        s.flush()
        s.add(
            DefectEvent(
                defect_id=defect.id,
                event="created",
                old=None,
                new=_snapshot(defect),
                operator=operator,
                api_key_id=api_key.id,
            )
        )
        s.flush()
        s.refresh(defect)
        return defect


def _next_rank(s: Session, inspection_id: uuid.UUID) -> int:
    current = s.scalar(select(func.max(Defect.rank)).where(Defect.inspection_id == inspection_id))
    return (current or 0) + 1


def get_defect(ctx: ServiceContext, inspection_id: uuid.UUID, defect_id: uuid.UUID) -> Defect:
    with ctx.db.session() as s:
        defect = s.get(Defect, defect_id)
        if defect is None or defect.inspection_id != inspection_id:
            raise NotFound(f"defect {defect_id} not found in inspection {inspection_id}")
        return defect


def history(ctx: ServiceContext, defect_id: uuid.UUID) -> list[DefectEvent]:
    with ctx.db.session() as s:
        return list(
            s.scalars(select(DefectEvent).where(DefectEvent.defect_id == defect_id).order_by(DefectEvent.at))
        )
