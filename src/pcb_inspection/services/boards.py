from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from pcb_inspection.db.models import Board, Inspection
from pcb_inspection.domain.enums import BoardVerdict
from pcb_inspection.domain.errors import NotFound
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.inspections import now
from pcb_inspection.services.references import upsert_board
from pcb_inspection.services.sessions import load_session


@dataclass(frozen=True, slots=True)
class BoardState:
    board: Board
    latest: dict[int, Inspection]  # side -> latest (not superseded) inspection


def set_verdict(
    ctx: ServiceContext,
    session_id: uuid.UUID,
    board_key: str,
    verdict: BoardVerdict,
    operator: str | None = None,
    comment: str | None = None,
    barcode: str | None = None,
    serial: str | None = None,
) -> Board:
    with ctx.db.session() as s:
        load_session(s, session_id)
        board = upsert_board(s, session_id, board_key, barcode, serial)
        board.verdict = verdict.value
        board.verdict_operator = operator
        board.verdict_comment = comment
        board.verdict_at = now()
        s.flush()
        s.refresh(board)
        return board


def get_board(ctx: ServiceContext, session_id: uuid.UUID, board_key: str) -> BoardState:
    with ctx.db.session() as s:
        board = s.scalar(select(Board).where(Board.session_id == session_id, Board.board_key == board_key))
        if board is None:
            raise NotFound(f"board {board_key!r} not found in session {session_id}")
        rows = s.scalars(
            select(Inspection)
            .where(Inspection.board_id == board.id, ~Inspection.superseded)
            .order_by(Inspection.side, Inspection.created_at)
        )
        latest = {i.side: i for i in rows}
        return BoardState(board, latest)
