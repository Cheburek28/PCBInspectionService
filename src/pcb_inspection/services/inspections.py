"""Submitting inspections and reading their state."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from pcb_inspection.db.models import ApiKey, Inspection, Reference
from pcb_inspection.domain.enums import FINAL_STATUSES, InspectionStatus
from pcb_inspection.domain.errors import IdempotencyConflict, NoActiveReference, NotFound, ValidationFailed
from pcb_inspection.services import images
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.params import effective_params
from pcb_inspection.services.references import active_reference_for, upsert_board
from pcb_inspection.services.sessions import load_session

POLL_INTERVAL_S = 0.25


@dataclass(frozen=True, slots=True)
class BoardRef:
    board_key: str
    barcode: str | None = None
    serial: str | None = None


@dataclass(frozen=True, slots=True)
class Submitted:
    inspection: Inspection
    created: bool  # False when an idempotent retry returned an existing inspection


def submit(
    ctx: ServiceContext,
    api_key: ApiKey,
    session_id: uuid.UUID,
    side: int,
    data: bytes,
    board: BoardRef,
    idempotency_key: str,
    reference_id: uuid.UUID | None = None,
    overrides: dict[str, Any] | None = None,
    captured_at: datetime | None = None,
) -> Submitted:
    if not idempotency_key or len(idempotency_key) > 100:
        raise ValidationFailed("Idempotency-Key header is required (max 100 characters)")
    params = effective_params(ctx.settings, overrides)
    request_hash = _request_hash(data, side, board.board_key, reference_id, params)
    existing = _by_idempotency_key(ctx, api_key.id, idempotency_key, request_hash)
    if existing is not None:
        return Submitted(existing, created=False)
    try:
        with ctx.db.session() as s:
            load_session(s, session_id, require_open=True)
            if reference_id is not None:
                ref = s.get(Reference, reference_id)
                if ref is None or ref.session_id != session_id:
                    raise NotFound(f"reference {reference_id} not found in session {session_id}")
                if ref.side != side:
                    raise ValidationFailed(f"reference {reference_id} is side {ref.side}, not {side}")
            else:
                ref = active_reference_for(s, session_id, side)
                if ref is None:
                    raise NoActiveReference(f"session {session_id} has no active reference for side {side}")
            image, _ = images.store_upload(ctx, s, data)
            board_row = upsert_board(s, session_id, board.board_key, board.barcode, board.serial)
            s.execute(
                update(Inspection)
                .where(Inspection.board_id == board_row.id, Inspection.side == side, ~Inspection.superseded)
                .values(superseded=True)
            )
            insp = Inspection(
                session_id=session_id,
                board_id=board_row.id,
                side=side,
                reference_id=ref.id,
                image_id=image.id,
                api_key_id=api_key.id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status=InspectionStatus.QUEUED,
                engine_name=ctx.settings.engine,
                params=params,
                captured_at=captured_at,
            )
            s.add(insp)
            s.flush()
            inspection_id = insp.id
    except IntegrityError:
        # a concurrent retry with the same idempotency key won the race
        existing = _by_idempotency_key(ctx, api_key.id, idempotency_key, request_hash)
        if existing is None:
            raise
        return Submitted(existing, created=False)
    ctx.queue.enqueue_inspection(inspection_id)
    return Submitted(get(ctx, inspection_id), created=True)


def _request_hash(
    data: bytes, side: int, board_key: str, reference_id: uuid.UUID | None, params: dict[str, Any]
) -> str:
    h = hashlib.sha256(data)
    h.update(json.dumps([side, board_key, str(reference_id), params], sort_keys=True).encode())
    return h.hexdigest()


def _by_idempotency_key(
    ctx: ServiceContext, api_key_id: uuid.UUID, key: str, request_hash: str
) -> Inspection | None:
    with ctx.db.session() as s:
        insp = s.scalar(
            select(Inspection).where(Inspection.api_key_id == api_key_id, Inspection.idempotency_key == key)
        )
    if insp is not None and insp.request_hash != request_hash:
        raise IdempotencyConflict(f"Idempotency-Key {key!r} was already used for a different request")
    return insp


def get(ctx: ServiceContext, inspection_id: uuid.UUID) -> Inspection:
    with ctx.db.session() as s:
        insp = s.get(Inspection, inspection_id)
        if insp is None:
            raise NotFound(f"inspection {inspection_id} not found")
        return insp


def wait(ctx: ServiceContext, inspection_id: uuid.UUID, wait_s: float) -> Inspection:
    """Long-poll: return as soon as the inspection is final or ``wait_s`` elapsed."""
    deadline = time.monotonic() + min(wait_s, ctx.settings.max_poll_wait_s)
    while True:
        insp = get(ctx, inspection_id)
        if insp.status in FINAL_STATUSES or time.monotonic() >= deadline:
            return insp
        time.sleep(POLL_INTERVAL_S)


def queue_wait_ms(insp: Inspection) -> int | None:
    if insp.started_at is None:
        return None
    return max(0, round((insp.started_at - insp.created_at).total_seconds() * 1000))


def now() -> datetime:
    return datetime.now(UTC)
