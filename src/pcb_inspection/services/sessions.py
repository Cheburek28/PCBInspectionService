from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pcb_inspection.db.models import ApiKey, InspectionSession, Reference
from pcb_inspection.domain.enums import SessionStatus
from pcb_inspection.domain.errors import NotFound, SessionClosed
from pcb_inspection.services.context import ServiceContext


def create_session(
    ctx: ServiceContext,
    api_key: ApiKey,
    product_code: str,
    product_name: str | None = None,
    operator: str | None = None,
    client_meta: dict[str, Any] | None = None,
) -> InspectionSession:
    with ctx.db.session() as s:
        session = InspectionSession(
            api_key_id=api_key.id,
            product_code=product_code,
            product_name=product_name,
            operator=operator,
            client_meta=client_meta or {},
            status=SessionStatus.OPEN,
        )
        s.add(session)
        s.flush()
        s.refresh(session)
    return session


def load_session(s: Session, session_id: uuid.UUID, *, require_open: bool = False) -> InspectionSession:
    session = s.get(InspectionSession, session_id)
    if session is None:
        raise NotFound(f"session {session_id} not found")
    if require_open and session.status != SessionStatus.OPEN:
        raise SessionClosed(f"session {session_id} is closed")
    return session


def lock_session(s: Session, session_id: uuid.UUID) -> None:
    """Row lock on the session until the transaction ends: serialises changes of its active references.

    Only the id column is selected, so no outer joins (FOR UPDATE cannot lock the nullable side of one).
    """
    s.execute(select(InspectionSession.id).where(InspectionSession.id == session_id).with_for_update())


def get_session(ctx: ServiceContext, session_id: uuid.UUID) -> InspectionSession:
    with ctx.db.session() as s:
        return load_session(s, session_id)


def close_session(ctx: ServiceContext, session_id: uuid.UUID) -> InspectionSession:
    with ctx.db.session() as s:
        session = load_session(s, session_id)
        if session.status != SessionStatus.CLOSED:
            session.status = SessionStatus.CLOSED
            session.closed_at = datetime.now(UTC)
    return session


def active_references(ctx: ServiceContext, session_id: uuid.UUID) -> dict[int, Reference]:
    with ctx.db.session() as s:
        refs = s.scalars(select(Reference).where(Reference.session_id == session_id, Reference.is_active))
        return {r.side: r for r in refs}
