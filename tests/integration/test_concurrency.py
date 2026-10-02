"""Concurrent requests for the same board / side must never end in 500.

Clients (progblock) send both sides of a board in parallel: two transactions create the same board row,
or two references for the same side race for the single "active" slot.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from pcb_inspection.db.models import Board, Reference
from pcb_inspection.domain.enums import BoardVerdict
from pcb_inspection.services import apikeys, boards, inspections, references, sessions
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.inspections import BoardRef

ROUNDS = 8


def race(*calls: Callable[[], Any]) -> list[BaseException]:
    """Start all calls at the same moment; return the exceptions they raised."""
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException] = []

    def run(call: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            call()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def board_count(ctx: ServiceContext, session_id: uuid.UUID, board_key: str) -> int:
    with ctx.db.session() as s:
        return int(
            s.scalar(
                select(func.count())
                .select_from(Board)
                .where(Board.session_id == session_id, Board.board_key == board_key)
            )
            or 0
        )


def test_both_sides_of_a_board_as_references_in_parallel(
    ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    key, _ = apikeys.create_api_key(ctx, "race")
    for _ in range(ROUNDS):
        session = sessions.create_session(ctx, key, "race")
        board_key = str(uuid.uuid4())
        errors = race(
            lambda sid=session.id, bk=board_key: references.create_from_upload(
                ctx, sid, 1, ref_jpeg, board_key=bk, barcode="1"
            ),
            lambda sid=session.id, bk=board_key: references.create_from_upload(
                ctx, sid, 2, clean_jpeg, board_key=bk, barcode="1"
            ),
        )
        assert errors == []
        assert board_count(ctx, session.id, board_key) == 1
        assert set(sessions.active_references(ctx, session.id)) == {1, 2}


def test_same_side_reference_twice_in_parallel_keeps_one_active(
    ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    key, _ = apikeys.create_api_key(ctx, "race")
    for _ in range(ROUNDS):
        session = sessions.create_session(ctx, key, "race")
        errors = race(
            lambda sid=session.id: references.create_from_upload(ctx, sid, 1, ref_jpeg),
            lambda sid=session.id: references.create_from_upload(ctx, sid, 1, clean_jpeg),
        )
        assert errors == []
        with ctx.db.session() as s:
            active = s.scalar(
                select(func.count())
                .select_from(Reference)
                .where(Reference.session_id == session.id, Reference.side == 1, Reference.is_active)
            )
        assert active == 1


def test_both_sides_submitted_in_parallel(
    queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    ctx = queued_ctx
    key, _ = apikeys.create_api_key(ctx, "race")
    session = sessions.create_session(ctx, key, "race")
    references.create_from_upload(ctx, session.id, 1, ref_jpeg)
    references.create_from_upload(ctx, session.id, 2, ref_jpeg)
    for _ in range(ROUNDS):
        board = BoardRef(str(uuid.uuid4()), barcode="42")
        errors = race(
            lambda b=board: inspections.submit(ctx, key, session.id, 1, clean_jpeg, b, str(uuid.uuid4())),
            lambda b=board: inspections.submit(ctx, key, session.id, 2, clean_jpeg, b, str(uuid.uuid4())),
        )
        assert errors == []
        assert board_count(ctx, session.id, board.board_key) == 1


def test_board_verdict_racing_a_submission(
    queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    ctx = queued_ctx
    key, _ = apikeys.create_api_key(ctx, "race")
    session = sessions.create_session(ctx, key, "race")
    references.create_from_upload(ctx, session.id, 1, ref_jpeg)
    for _ in range(ROUNDS):
        board = BoardRef(str(uuid.uuid4()))
        errors = race(
            lambda b=board: inspections.submit(ctx, key, session.id, 1, clean_jpeg, b, str(uuid.uuid4())),
            lambda b=board: boards.set_verdict(ctx, session.id, b.board_key, BoardVerdict.FAIL, serial="7"),
        )
        assert errors == []
        state = boards.get_board(ctx, session.id, board.board_key)
        assert state.board.verdict == "fail"
        assert state.board.serial == "7"


def test_unexpected_integrity_conflict_is_a_409_not_a_500(api: Any, monkeypatch: Any) -> None:
    """Safety net: a race we did not foresee must reach the client as a retryable conflict."""
    from sqlalchemy.exc import IntegrityError

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key value violates unique constraint"))

    monkeypatch.setattr(sessions, "create_session", boom)
    r = api.request("POST", "/api/v1/sessions", json={"product_code": "x"})
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["code"] == "CONFLICT"
