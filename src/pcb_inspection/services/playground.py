"""Web-console use cases: one-off comparisons with custom parameters and the inspection history.

Every playground run gets its own session (``client_meta.source = "web-ui"``) so it never supersedes
or mixes with inspections sent by production clients.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from pcb_inspection.db.models import ApiKey, Inspection, InspectionSession
from pcb_inspection.domain.enums import DefectSource, Verdict
from pcb_inspection.engine.base import MaskStrategy
from pcb_inspection.services import inspections, references, sessions
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.inspections import BoardRef

UI_STATION = "web-ui"
UI_KEY_PREFIX = "webui"  # cannot authenticate over the API: the stored hash matches no secret
UI_SOURCE = "web-ui"


def ui_api_key(ctx: ServiceContext) -> ApiKey:
    with ctx.db.session() as s:
        key = s.scalar(select(ApiKey).where(ApiKey.prefix == UI_KEY_PREFIX))
        if key is None:
            key = ApiKey(station=UI_STATION, prefix=UI_KEY_PREFIX, key_hash=secrets.token_hex(32))
            s.add(key)
            s.flush()
        return key


def run(
    ctx: ServiceContext,
    reference: bytes,
    photo: bytes,
    side: int = 1,
    mask_strategy: MaskStrategy | None = None,
    mask_polygon: list[list[float]] | None = None,
    overrides: dict[str, Any] | None = None,
    product_code: str = "playground",
    note: str | None = None,
    rerun_of: uuid.UUID | None = None,
) -> Inspection:
    key = ui_api_key(ctx)
    meta: dict[str, Any] = {"source": UI_SOURCE}
    if note:
        meta["note"] = note
    if rerun_of:
        meta["rerun_of"] = str(rerun_of)
    session = sessions.create_session(ctx, key, product_code or "playground", client_meta=meta)
    references.create_from_upload(ctx, session.id, side, reference, mask_strategy, mask_polygon)
    submitted = inspections.submit(
        ctx,
        key,
        session.id,
        side,
        photo,
        BoardRef(board_key=f"ui-{uuid.uuid4().hex[:12]}"),
        idempotency_key=str(uuid.uuid4()),
        overrides=overrides,
    )
    return submitted.inspection


def rerun(
    ctx: ServiceContext,
    inspection_id: uuid.UUID,
    overrides: dict[str, Any] | None,
    mask_strategy: MaskStrategy | None = None,
) -> Inspection:
    """Same reference and photo, new parameters, in a fresh playground session."""
    original = inspections.get(ctx, inspection_id)
    ref = original.reference
    strategy = mask_strategy or MaskStrategy(ref.mask_strategy)
    session = sessions.get_session(ctx, original.session_id)
    return run(
        ctx,
        reference=ctx.storage.get(ref.image.storage_key),
        photo=ctx.storage.get(original.image.storage_key),
        side=original.side,
        mask_strategy=strategy,
        mask_polygon=ref.mask_polygon if strategy is MaskStrategy.POLYGON else None,
        overrides=overrides,
        product_code=session.product_code,
        rerun_of=original.id,
    )


@dataclass(frozen=True, slots=True)
class HistoryRow:
    inspection: Inspection
    session: InspectionSession

    @property
    def source(self) -> str:
        return str(self.session.client_meta.get("source") or self.session.api_key.station)

    @property
    def auto_count(self) -> int:
        return sum(1 for d in self.inspection.defects if d.source == DefectSource.AUTO)

    @property
    def verdict_counts(self) -> dict[str, int]:
        counts = {v.value: 0 for v in Verdict}
        for d in self.inspection.defects:
            counts[d.verdict] += 1
        return counts


def history(
    ctx: ServiceContext,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    source: str | None = None,
    product: str | None = None,
) -> tuple[list[HistoryRow], int]:
    """Newest first. ``source``: ``web-ui`` or ``api`` (everything sent by client stations)."""
    query = select(Inspection, InspectionSession).join(
        InspectionSession, Inspection.session_id == InspectionSession.id
    )
    if status:
        query = query.where(Inspection.status == status)
    if product:
        query = query.where(InspectionSession.product_code == product)
    is_ui = InspectionSession.client_meta["source"].astext == UI_SOURCE
    if source == UI_SOURCE:
        query = query.where(is_ui)
    elif source == "api":
        query = query.where(~is_ui | InspectionSession.client_meta["source"].is_(None))
    with ctx.db.session() as s:
        total = s.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            s.execute(query.order_by(Inspection.created_at.desc()).limit(limit).offset(offset)).unique().all()
        )
        return [HistoryRow(i, sess) for i, sess in rows], total


def products(ctx: ServiceContext) -> list[str]:
    with ctx.db.session() as s:
        return list(
            s.scalars(
                select(InspectionSession.product_code).distinct().order_by(InspectionSession.product_code)
            )
        )
