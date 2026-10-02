"""Every error leaves the API as RFC 9457 ``application/problem+json``."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pcb_inspection.domain.errors import AppError
from pcb_inspection.storage import BlobNotFound

DOCS_URL = "https://github.com/Cheburek28/PCBInspectionService/blob/main/docs/errors.md"
PROBLEM_JSON = "application/problem+json"

log = structlog.get_logger(__name__)

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def problem(request: Request, status: int, code: str, title: str, detail: str, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{DOCS_URL}#{code.lower()}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }
    body.update(extra)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem(request, exc.status, exc.code, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(BlobNotFound)
    async def _blob_missing(request: Request, exc: BlobNotFound) -> JSONResponse:
        log.error("storage.blob_missing", key=str(exc))
        return problem(request, 404, "NOT_FOUND", "Resource not found", "stored file is missing")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem(
            request,
            422,
            "VALIDATION_ERROR",
            "Request validation failed",
            "see 'errors' for details",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        return problem(request, exc.status_code, code, str(exc.detail), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_error")
        return problem(
            request, 500, "INTERNAL_ERROR", "Internal server error", "unexpected error, see server logs"
        )
