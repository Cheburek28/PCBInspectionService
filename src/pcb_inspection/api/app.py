"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from pcb_inspection import __version__
from pcb_inspection.api import errors
from pcb_inspection.api.routers import boards, health, inspections, sessions
from pcb_inspection.api.schemas import API_PREFIX
from pcb_inspection.api.ui import router as ui
from pcb_inspection.observability import HTTP_LATENCY, HTTP_REQUESTS, configure_logging
from pcb_inspection.services.bootstrap import build_context
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.settings import get_settings

DESCRIPTION = """
Compares a photo of a printed circuit board with a reference photo of the same board side,
returns the differing regions and collects operator feedback on them as a training dataset.

All coordinates are pixels of the uploaded image after EXIF orientation is applied.
Errors are returned as RFC 9457 `application/problem+json`.
"""


def create_app(ctx: ServiceContext | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "ctx", None) is None:
            settings = get_settings()
            configure_logging(settings.log_level, settings.log_format)
            app.state.ctx = build_context(settings)
        yield
        app.state.ctx.db.dispose()

    app = FastAPI(
        title="PCB Inspection Service",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
    )
    app.state.ctx = ctx
    errors.install(app)
    app.include_router(health.router)
    for router in (sessions.router, inspections.router, boards.router):
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(ui.router)

    @app.exception_handler(ui.LoginRequired)
    async def _ui_login(request: Request, exc: ui.LoginRequired) -> Response:
        return ui.login_redirect(request)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        # path template (not the raw path) keeps metric label cardinality bounded
        route = str(getattr(request.scope.get("route"), "path", "unmatched"))
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - t0)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        response.headers["X-Request-ID"] = request_id
        return response

    return app
