from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path

from pcb_inspection.api.deps import Auth, Ctx
from pcb_inspection.api.schemas import BoardOut, BoardVerdictIn, Problem
from pcb_inspection.services import boards

router = APIRouter(
    tags=["boards"],
    responses={
        401: {"model": Problem, "description": "Missing or invalid API key"},
        404: {"model": Problem, "description": "Not found"},
    },
)

BoardKey = Annotated[str, Path(min_length=1, max_length=100)]


@router.put("/sessions/{session_id}/boards/{board_key}/verdict", response_model=BoardOut)
def set_board_verdict(
    session_id: uuid.UUID, board_key: BoardKey, body: BoardVerdictIn, ctx: Ctx, key: Auth
) -> BoardOut:
    """Final operator decision for the physical board (pass / fail)."""
    board = boards.set_verdict(
        ctx, session_id, board_key, body.verdict, body.operator, body.comment, body.barcode, body.serial
    )
    state = boards.get_board(ctx, session_id, board.board_key)
    return BoardOut.build(state.board, state.latest)


@router.get("/sessions/{session_id}/boards/{board_key}", response_model=BoardOut)
def get_board(session_id: uuid.UUID, board_key: BoardKey, ctx: Ctx, key: Auth) -> BoardOut:
    state = boards.get_board(ctx, session_id, board_key)
    return BoardOut.build(state.board, state.latest)
