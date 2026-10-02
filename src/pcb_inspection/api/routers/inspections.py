from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response

from pcb_inspection.api.deps import Auth, Ctx, parse_json_field, read_upload
from pcb_inspection.api.routers.sessions import _board
from pcb_inspection.api.schemas import (
    API_PREFIX,
    DefectOut,
    InspectionAccepted,
    InspectionOut,
    ManualDefectIn,
    Problem,
    VerdictIn,
)
from pcb_inspection.domain.errors import ValidationFailed
from pcb_inspection.engine.crops import CropKind
from pcb_inspection.observability import INSPECTIONS_SUBMITTED
from pcb_inspection.services import defects, inspections, media
from pcb_inspection.services.inspections import BoardRef
from pcb_inspection.services.media import ImageKind

router = APIRouter(
    tags=["inspections"],
    responses={
        401: {"model": Problem, "description": "Missing or invalid API key"},
        404: {"model": Problem, "description": "Not found"},
    },
)


@router.post(
    "/sessions/{session_id}/inspections",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InspectionAccepted,
    responses={
        409: {"model": Problem, "description": "NO_ACTIVE_REFERENCE, SESSION_CLOSED or IDEMPOTENCY_CONFLICT"},
        413: {"model": Problem, "description": "Upload too large"},
        415: {"model": Problem, "description": "Unsupported image format"},
        422: {"model": Problem, "description": "Validation error"},
    },
)
def submit_inspection(
    session_id: uuid.UUID,
    ctx: Ctx,
    key: Auth,
    side: Annotated[int, Form(ge=1, le=8)],
    image: Annotated[UploadFile, File(description="JPEG or PNG")],
    board: Annotated[str, Form(description='JSON: {"board_key": "...", "barcode": "...", "serial": "..."}')],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=100)],
    reference_id: Annotated[uuid.UUID | None, Form()] = None,
    params: Annotated[str | None, Form(description="JSON object with threshold overrides")] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
) -> JSONResponse:
    """Queue a photo of one board side for comparison with the reference. Poll ``poll_url`` for the result.

    Retrying with the same ``Idempotency-Key`` returns the inspection created by the first request.
    """
    board_in = _board(board)
    if board_in is None:
        raise ValidationFailed("field 'board' is required")
    overrides = parse_json_field(params, "params")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValidationFailed("'params' must be a JSON object")
    result = inspections.submit(
        ctx,
        key,
        session_id,
        side,
        read_upload(ctx, image),
        BoardRef(board_in.board_key, board_in.barcode, board_in.serial),
        idempotency_key,
        reference_id,
        overrides,
        captured_at,
    )
    insp = result.inspection
    if result.created:
        INSPECTIONS_SUBMITTED.inc()
    body = InspectionAccepted(
        id=insp.id, status=insp.status, poll_url=f"{API_PREFIX}/inspections/{insp.id}"
    ).model_dump(mode="json")
    return JSONResponse(body, status_code=status.HTTP_202_ACCEPTED, headers={"Location": body["poll_url"]})


@router.get("/inspections/{inspection_id}", response_model=InspectionOut)
def get_inspection(
    inspection_id: uuid.UUID,
    ctx: Ctx,
    key: Auth,
    wait: Annotated[
        float, Query(ge=0, le=60, description="Long-poll: seconds to wait for a final status")
    ] = 0,
) -> InspectionOut:
    insp = inspections.wait(ctx, inspection_id, wait) if wait > 0 else inspections.get(ctx, inspection_id)
    return InspectionOut.build(insp)


@router.get(
    "/inspections/{inspection_id}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}},
)
def get_inspection_image(
    inspection_id: uuid.UUID, ctx: Ctx, key: Auth, kind: ImageKind = ImageKind.ORIGINAL
) -> Response:
    data, content_type = media.inspection_image(ctx, inspection_id, kind)
    return Response(data, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


@router.get(
    "/inspections/{inspection_id}/defects/{defect_id}/crop",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}},
        409: {"model": Problem, "description": "Conflict with the current state"},
    },
)
def get_crop(
    inspection_id: uuid.UUID,
    defect_id: uuid.UUID,
    ctx: Ctx,
    key: Auth,
    kind: CropKind = CropKind.PAIR,
    pad: Annotated[
        int, Query(ge=0, le=1000, description="Context around the region, uploaded-reference pixels")
    ] = 60,
    height: Annotated[int, Query(ge=32, le=2000)] = 400,
) -> Response:
    """Reference and aligned test crops with identical geometry (``pair`` = side by side, reference left)."""
    data = media.crop(ctx, inspection_id, defect_id, kind, pad, height)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post(
    "/inspections/{inspection_id}/defects",
    status_code=status.HTTP_201_CREATED,
    response_model=DefectOut,
    tags=["defects"],
    responses={
        409: {"model": Problem, "description": "Conflict with the current state"},
        422: {"model": Problem, "description": "Validation error"},
    },
)
def add_manual_defect(inspection_id: uuid.UUID, body: ManualDefectIn, ctx: Ctx, key: Auth) -> DefectOut:
    """Region the operator marked on the test photo. Stored as accepted, mapped to reference coordinates."""
    d = defects.add_manual(
        ctx, key, inspection_id, body.bbox_test.to_bbox(), body.defect_type, body.comment, body.operator
    )
    return DefectOut.build(d)


@router.put("/defects/{defect_id}/verdict", response_model=DefectOut, tags=["defects"])
def set_defect_verdict(defect_id: uuid.UUID, body: VerdictIn, ctx: Ctx, key: Auth) -> DefectOut:
    """Operator decision; can be changed any time, every change is kept in the history."""
    d = defects.set_verdict(ctx, key, defect_id, body.verdict, body.defect_type, body.comment, body.operator)
    return DefectOut.build(d)
