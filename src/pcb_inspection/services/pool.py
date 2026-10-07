"""Reference pool of the targeted detectors: the first boards passed by the operator, same reference.

A board joins the pool after its own inspection: it is used for later boards when it was passed (board
verdict ``pass``) and none of its regions was confirmed as a defect. Its aligned photo is already stored;
the per-photo measurements the detectors need are stored next to it (``measures.json``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from pcb_inspection.db.models import Board, Defect, Inspection
from pcb_inspection.domain.enums import BoardVerdict, InspectionStatus, Verdict
from pcb_inspection.engine import imaging
from pcb_inspection.engine.base import InspectParams, PoolPhoto
from pcb_inspection.engine.imaging import BGRImage
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.storage import BlobStorage


def measures_key(inspection_id: uuid.UUID) -> str:
    return f"inspections/{inspection_id}/measures.json"


def save_measures(ctx: ServiceContext, inspection_id: uuid.UUID, measures: dict[str, Any]) -> None:
    ctx.storage.put(measures_key(inspection_id), json.dumps(measures).encode(), "application/json")


def detectors_enabled(p: InspectParams) -> bool:
    return p.detect_solder or p.detect_shift or p.detect_specks or p.detect_hairs


def pool_for(ctx: ServiceContext, insp: Inspection, params: InspectParams) -> list[PoolPhoto]:
    size = ctx.settings.reference_pool_size
    if size == 0 or not detectors_enabled(params):
        return []
    confirmed = (
        select(Defect.id)
        .where(Defect.inspection_id == Inspection.id, Defect.verdict == Verdict.ACCEPTED)
        .exists()
    )
    with ctx.db.session() as s:
        rows = s.execute(
            select(Inspection.id, Inspection.aligned_storage_key)
            .join(Board, Board.id == Inspection.board_id)
            .where(
                Inspection.session_id == insp.session_id,
                Inspection.side == insp.side,
                Inspection.reference_id == insp.reference_id,
                Inspection.id != insp.id,
                Inspection.status == InspectionStatus.COMPLETED,
                ~Inspection.superseded,
                Inspection.created_at < insp.created_at,
                Inspection.aligned_storage_key.is_not(None),
                Board.verdict == BoardVerdict.PASS,
                ~confirmed,
            )
            .order_by(Inspection.created_at)
            .limit(size)
        ).all()
    return [
        PoolPhoto(str(iid), _loader(ctx.storage, key), _measures(ctx.storage, iid))
        for iid, key in rows
        if key is not None
    ]


def _loader(storage: BlobStorage, key: str) -> Callable[[], BGRImage]:
    return lambda: imaging.decode(storage.get(key)).pixels


def _measures(storage: BlobStorage, inspection_id: uuid.UUID) -> dict[str, Any] | None:
    key = measures_key(inspection_id)
    if not storage.exists(key):
        return None  # inspected before the detectors existed: its photo is still usable
    value = json.loads(storage.get(key))
    return value if isinstance(value, dict) else None
