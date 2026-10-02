from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, Header, Request, UploadFile

from pcb_inspection.db.models import ApiKey
from pcb_inspection.domain.errors import PayloadTooLarge, ValidationFailed
from pcb_inspection.services import apikeys
from pcb_inspection.services.context import ServiceContext


def get_ctx(request: Request) -> ServiceContext:
    ctx: ServiceContext = request.app.state.ctx
    return ctx


Ctx = Annotated[ServiceContext, Depends(get_ctx)]


def get_api_key(ctx: Ctx, request: Request, authorization: Annotated[str | None, Header()] = None) -> ApiKey:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    key = apikeys.authenticate(ctx, raw)
    request.state.station = key.station
    return key


Auth = Annotated[ApiKey, Depends(get_api_key)]


def read_upload(ctx: ServiceContext, upload: UploadFile) -> bytes:
    limit = ctx.settings.max_upload_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(f"upload exceeds {ctx.settings.max_upload_mb} MB")
    return data


def parse_json_field(raw: str | None, name: str) -> Any:
    """Multipart requests carry structured fields as JSON strings."""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed(f"field {name!r} must be valid JSON: {exc.msg}") from exc
