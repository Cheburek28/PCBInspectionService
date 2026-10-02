from __future__ import annotations

from collections.abc import Callable

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from pcb_inspection.api.deps import Ctx
from pcb_inspection.api.errors import problem
from pcb_inspection.api.schemas import Health

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)


@router.get("/health/live", response_model=Health)
def live() -> Health:
    return Health(status="ok")


@router.get("/health/ready", response_model=Health, responses={503: {"description": "a dependency is down"}})
def ready(request: Request, ctx: Ctx) -> Response:
    checks: dict[str, Callable[[], None]] = {
        "database": ctx.db.ping,
        "storage": ctx.storage.ping,
        "queue": ctx.queue.ping,
    }
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:
            log.warning("health.check_failed", check=name, error=str(exc))
            results[name] = "error"
    if all(v == "ok" for v in results.values()):
        return JSONResponse(Health(status="ok", checks=results).model_dump())
    failed = ", ".join(k for k, v in results.items() if v != "ok")
    return problem(request, 503, "NOT_READY", "Service not ready", f"failed checks: {failed}", checks=results)


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
