from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError

from pcb_inspection.api.deps import Auth, Ctx, parse_json_field, read_upload
from pcb_inspection.api.schemas import (
    BoardIn,
    Problem,
    ReferenceFromInspection,
    ReferenceOut,
    SessionCreate,
    SessionOut,
)
from pcb_inspection.domain.errors import ValidationFailed
from pcb_inspection.engine.base import MaskStrategy
from pcb_inspection.services import references, sessions

router = APIRouter(tags=["sessions"], responses={401: {"model": Problem}, 404: {"model": Problem}})


@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=SessionOut)
def create_session(body: SessionCreate, ctx: Ctx, key: Auth) -> SessionOut:
    s = sessions.create_session(
        ctx, key, body.product_code, body.product_name, body.operator, body.client_meta
    )
    return SessionOut.build(sessions.get_session(ctx, s.id), {})


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: uuid.UUID, ctx: Ctx, key: Auth) -> SessionOut:
    s = sessions.get_session(ctx, session_id)
    return SessionOut.build(s, sessions.active_references(ctx, session_id))


@router.post("/sessions/{session_id}/close", response_model=SessionOut)
def close_session(session_id: uuid.UUID, ctx: Ctx, key: Auth) -> SessionOut:
    s = sessions.close_session(ctx, session_id)
    return SessionOut.build(sessions.get_session(ctx, s.id), sessions.active_references(ctx, session_id))


@router.post(
    "/sessions/{session_id}/references",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceOut,
    tags=["references"],
    responses={
        409: {"model": Problem},
        413: {"model": Problem},
        415: {"model": Problem},
        422: {"model": Problem},
    },
)
def upload_reference(
    session_id: uuid.UUID,
    ctx: Ctx,
    key: Auth,
    side: Annotated[int, Form(ge=1, le=8)],
    image: Annotated[UploadFile, File(description="JPEG or PNG")],
    board: Annotated[
        str | None, Form(description='JSON: {"board_key": "...", "barcode": "...", "serial": "..."}')
    ] = None,
    mask_strategy: Annotated[MaskStrategy | None, Form()] = None,
    mask_polygon: Annotated[
        str | None, Form(description="JSON [[x, y], ...] in uploaded-image pixels")
    ] = None,
) -> ReferenceOut:
    """Upload a reference photo for one side. It becomes the active reference of that side."""
    board_in = _board(board)
    polygon = parse_json_field(mask_polygon, "mask_polygon")
    if polygon is not None and (
        not isinstance(polygon, list) or not all(isinstance(p, list) and len(p) == 2 for p in polygon)
    ):
        raise ValidationFailed("mask_polygon must be a JSON list of [x, y] pairs")
    ref = references.create_from_upload(
        ctx,
        session_id,
        side,
        read_upload(ctx, image),
        mask_strategy,
        polygon,
        board_key=board_in.board_key if board_in else None,
        barcode=board_in.barcode if board_in else None,
        serial=board_in.serial if board_in else None,
    )
    return ReferenceOut.build(ref)


@router.post(
    "/sessions/{session_id}/references/from-inspection",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceOut,
    tags=["references"],
    responses={409: {"model": Problem}, 422: {"model": Problem}},
)
def reference_from_inspection(
    session_id: uuid.UUID, body: ReferenceFromInspection, ctx: Ctx, key: Auth
) -> ReferenceOut:
    """Make the photo of an existing inspection the active reference of its side (no re-upload)."""
    return ReferenceOut.build(
        references.create_from_inspection(ctx, session_id, body.from_inspection_id, body.side)
    )


@router.get("/references/{reference_id}", response_model=ReferenceOut, tags=["references"])
def get_reference(reference_id: uuid.UUID, ctx: Ctx, key: Auth) -> ReferenceOut:
    return ReferenceOut.build(references.get_reference(ctx, reference_id))


@router.get(
    "/references/{reference_id}/mask.png",
    tags=["references"],
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_mask(reference_id: uuid.UUID, ctx: Ctx, key: Auth) -> Response:
    return Response(references.mask_png(ctx, reference_id), media_type="image/png")


def _board(raw: str | None) -> BoardIn | None:
    value = parse_json_field(raw, "board")
    if value is None:
        return None
    try:
        return BoardIn.model_validate(value)
    except ValidationError as exc:
        raise ValidationFailed(f"invalid 'board': {exc.errors()[0]['msg']}") from exc
