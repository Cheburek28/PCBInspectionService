"""Reference photos: upload, promote an inspected photo, prepared-reference cache."""

from __future__ import annotations

import uuid

import cv2
import numpy as np
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pcb_inspection.db.models import Board, Image, Inspection, Reference
from pcb_inspection.domain.enums import ReferenceSource
from pcb_inspection.domain.errors import (
    BoardMaskNotFound,
    ImageTooSmall,
    InvalidState,
    NotFound,
    ValidationFailed,
)
from pcb_inspection.domain.ids import uuid7
from pcb_inspection.engine import imaging
from pcb_inspection.engine.base import MaskNotFoundError, MaskSpec, MaskStrategy, PreparedReference
from pcb_inspection.engine.geometry import project_points
from pcb_inspection.services import images
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.sessions import load_session, lock_session


def create_from_upload(
    ctx: ServiceContext,
    session_id: uuid.UUID,
    side: int,
    data: bytes,
    mask_strategy: MaskStrategy | None = None,
    mask_polygon: list[list[float]] | None = None,
    board_key: str | None = None,
    barcode: str | None = None,
    serial: str | None = None,
) -> Reference:
    strategy = mask_strategy or ctx.settings.default_mask_strategy
    if strategy is MaskStrategy.POLYGON and not mask_polygon:
        raise ValidationFailed("mask_polygon is required for mask_strategy=polygon")
    with ctx.db.session() as s:
        load_session(s, session_id, require_open=True)
        image, decoded = images.store_upload(ctx, s, data)
        board_id = _upsert_board(s, session_id, board_key, barcode, serial)
        return _create(
            ctx,
            s,
            session_id,
            side,
            image,
            decoded,
            strategy,
            mask_polygon,
            ReferenceSource.UPLOAD,
            None,
            board_id,
        )


def create_from_inspection(
    ctx: ServiceContext, session_id: uuid.UUID, inspection_id: uuid.UUID, side: int | None = None
) -> Reference:
    with ctx.db.session() as s:
        load_session(s, session_id, require_open=True)
        insp = s.get(Inspection, inspection_id)
        if insp is None or insp.session_id != session_id:
            raise NotFound(f"inspection {inspection_id} not found in session {session_id}")
        if side is not None and side != insp.side:
            raise ValidationFailed(f"inspection {inspection_id} is side {insp.side}, not {side}")
        old_ref = insp.reference
        polygon = None
        if old_ref.mask_strategy == MaskStrategy.POLYGON:
            if not insp.transform:
                raise InvalidState(
                    "cannot carry a polygon mask over: the inspection has no alignment transform"
                )
            m = np.asarray(insp.transform["ref_to_test"], dtype=np.float64)
            polygon = project_points(m, np.asarray(old_ref.mask_polygon, dtype=np.float64)).tolist()
        decoded = images.load_pixels(ctx, insp.image)
        return _create(
            ctx,
            s,
            session_id,
            insp.side,
            insp.image,
            decoded,
            MaskStrategy(old_ref.mask_strategy),
            polygon,
            ReferenceSource.INSPECTION,
            insp.id,
            insp.board_id,
        )


def _create(
    ctx: ServiceContext,
    s: Session,
    session_id: uuid.UUID,
    side: int,
    image: Image,
    decoded: imaging.DecodedImage,
    strategy: MaskStrategy,
    polygon: list[list[float]] | None,
    source: ReferenceSource,
    source_inspection_id: uuid.UUID | None,
    board_id: uuid.UUID | None,
) -> Reference:
    if decoded.width < ctx.settings.gate_min_width:
        raise ImageTooSmall(
            f"image is {decoded.width} px wide, at least {ctx.settings.gate_min_width} px is required"
        )
    spec = MaskSpec(strategy=strategy, polygon=tuple((float(x), float(y)) for x, y in polygon or ()))
    try:
        prepared = ctx.engine.prepare_reference(decoded.pixels, spec, ctx.settings.engine_work_width)
    except MaskNotFoundError as exc:
        raise BoardMaskNotFound(str(exc)) from exc
    ref_id = uuid7()
    mask_key = f"references/{ref_id}/mask.png"
    ctx.storage.put(mask_key, imaging.encode_png(prepared.mask * 255), "image/png")
    ref = Reference(
        id=ref_id,
        session_id=session_id,
        side=side,
        image_id=image.id,
        mask_storage_key=mask_key,
        mask_strategy=strategy.value,
        mask_polygon=polygon,
        mask_coverage=round(prepared.mask_coverage, 4),
        source=source.value,
        source_inspection_id=source_inspection_id,
        board_id=board_id,
        is_active=True,
    )
    # the expensive part (mask, resize) is done; now swap the active reference under the session lock,
    # otherwise two parallel uploads for one side both insert an "active" row
    lock_session(s, session_id)
    s.execute(
        update(Reference)
        .where(Reference.session_id == session_id, Reference.side == side, Reference.is_active)
        .values(is_active=False)
    )
    s.add(ref)
    s.flush()
    s.refresh(ref)
    ctx.ref_cache.put(ref.id, prepared)
    return ref


def _upsert_board(
    s: Session, session_id: uuid.UUID, board_key: str | None, barcode: str | None, serial: str | None
) -> uuid.UUID | None:
    if not board_key:
        return None
    return upsert_board(s, session_id, board_key, barcode, serial).id


def upsert_board(
    s: Session, session_id: uuid.UUID, board_key: str, barcode: str | None, serial: str | None
) -> Board:
    """Get or create the board. Safe under concurrency: clients send both sides of a board in parallel.

    INSERT ... ON CONFLICT DO NOTHING waits for a concurrent insert of the same key to commit instead of
    failing; the following SELECT then sees that row.
    """
    s.execute(
        pg_insert(Board)
        .values(id=uuid7(), session_id=session_id, board_key=board_key)
        .on_conflict_do_nothing(index_elements=["session_id", "board_key"])
    )
    board = s.scalars(
        select(Board)
        .where(Board.session_id == session_id, Board.board_key == board_key)
        .execution_options(populate_existing=True)
    ).one()
    if barcode is not None:
        board.barcode = barcode
    if serial is not None:
        board.serial = serial
    s.flush()
    return board


def get_reference(ctx: ServiceContext, reference_id: uuid.UUID) -> Reference:
    with ctx.db.session() as s:
        ref = s.get(Reference, reference_id)
        if ref is None:
            raise NotFound(f"reference {reference_id} not found")
        return ref


def mask_png(ctx: ServiceContext, reference_id: uuid.UUID) -> bytes:
    return ctx.storage.get(get_reference(ctx, reference_id).mask_storage_key)


def prepared(ctx: ServiceContext, ref: Reference) -> PreparedReference:
    """Prepared reference from cache, or rebuilt from the stored image and mask (mask is not recomputed)."""

    def load() -> PreparedReference:
        decoded = images.load_pixels(ctx, ref.image)
        work_width = min(ctx.settings.engine_work_width, decoded.width)
        work, scale = imaging.resize_to_width(decoded.pixels, work_width)
        mask_img = cv2.imdecode(
            np.frombuffer(ctx.storage.get(ref.mask_storage_key), np.uint8), cv2.IMREAD_GRAYSCALE
        )
        if mask_img is None:
            raise ValueError(f"stored mask of reference {ref.id} cannot be decoded")
        if mask_img.shape != work.shape[:2]:
            mask_img = cv2.resize(mask_img, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_NEAREST)
        polygon = tuple((float(x), float(y)) for x, y in ref.mask_polygon or ())
        return PreparedReference(
            image=work,
            mask=(np.asarray(mask_img) > 127).astype(np.uint8),
            scale=scale,
            original_size=(decoded.width, decoded.height),
            mask_spec=MaskSpec(MaskStrategy(ref.mask_strategy), polygon),
        )

    return ctx.ref_cache.get_or_load(ref.id, load)


def active_reference_for(s: Session, session_id: uuid.UUID, side: int) -> Reference | None:
    return s.scalar(
        select(Reference).where(
            Reference.session_id == session_id, Reference.side == side, Reference.is_active
        )
    )
