"""Web console (``/ui``): try parameters on your own photos and browse every inspection.

Server-rendered pages (Jinja2) plus a few JSON endpoints for the page script. Enabled only when
``PCBIS_UI_PASSWORD`` is set; otherwise every ``/ui`` route answers 404.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from pcb_inspection.api.deps import Ctx, read_upload
from pcb_inspection.api.schemas import InspectionOut
from pcb_inspection.api.ui import auth
from pcb_inspection.domain.enums import DefectType, InspectionStatus, Verdict
from pcb_inspection.domain.errors import NotFound, ValidationFailed
from pcb_inspection.engine.base import MaskStrategy
from pcb_inspection.engine.crops import CropKind
from pcb_inspection.services import defects, inspections, media, playground, sessions
from pcb_inspection.services.media import ImageKind
from pcb_inspection.services.params import effective_params

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(prefix="/ui", include_in_schema=False)

PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class ParamField:
    key: str
    group: str  # "engine" | "gates"
    label: str
    hint: str
    step: str


PARAM_FIELDS = [
    ParamField(
        "threshold", "engine", "Порог отличия", "пик отличия, с которого область попадает в отчёт", "0.5"
    ),
    ParamField(
        "extent_threshold", "engine", "Порог границы области", "по нему считаются контур и площадь", "0.5"
    ),
    ParamField("min_area", "engine", "Мин. площадь, px", "в рабочем разрешении", "1"),
    ParamField("tol_px", "engine", "Допуск сдвига, px", "больше → прячет мелкие сдвиги", "1"),
    ParamField(
        "highlight_clip", "engine", "Срез бликов (яркость L)", "ярче — считается одинаковым; 255 = выкл.", "5"
    ),
    ParamField(
        "open_radius", "engine", "Подавление маркировки, px", "убирает тонкие светлые штрихи; 0 = выкл.", "1"
    ),
    ParamField(
        "background_sigma", "engine", "Выравнивание фона, px", "вычитает местную яркость; 0 = выкл.", "1"
    ),
    ParamField("min_sharpness_ratio", "gates", "Мин. резкость", "относительно эталона", "0.01"),
    ParamField("max_lab_shift_l", "gates", "Макс. сдвиг яркости ΔL", "", "0.5"),
    ParamField("max_lab_shift_ab", "gates", "Макс. сдвиг цвета Δa/Δb", "", "0.5"),
    ParamField("max_differences", "gates", "Макс. число отличий", "", "1"),
    ParamField("max_differences_area_ratio", "gates", "Макс. доля площади отличий", "0.02 = 2 %", "0.001"),
]


class LoginRequired(Exception):
    pass


def _password(request: Request) -> str:
    secret = request.app.state.ctx.settings.ui_password
    if secret is None or not secret.get_secret_value():
        raise NotFound("web console is disabled (set PCBIS_UI_PASSWORD)")
    return str(secret.get_secret_value())


def require_login(request: Request) -> None:
    if not auth.token_valid(_password(request), request.cookies.get(auth.COOKIE)):
        raise LoginRequired


Login = Depends(require_login)


def login_redirect(request: Request) -> Response:
    if request.url.path.endswith("/state") or request.method != "GET":
        return JSONResponse({"detail": "login required"}, status_code=401)
    return RedirectResponse(f"/ui/login?next={request.url.path}", status_code=303)


def _render(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


# ---------------------------------------------------------------- login


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/ui/") -> HTMLResponse:  # noqa: A002 - query name
    _password(request)
    return _render(request, "login.html", next=_safe_next(next), error=None)


@router.post("/login", response_model=None)
async def login(
    request: Request,
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/ui/",  # noqa: A002 - form field name
) -> Response:
    expected = _password(request)
    if not auth.check_password(expected, password):
        await asyncio.sleep(0.5)  # slows down guessing
        return templates.TemplateResponse(
            request, "login.html", {"next": _safe_next(next), "error": "Неверный пароль"}, status_code=401
        )
    hours = request.app.state.ctx.settings.ui_session_hours
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(
        auth.COOKIE,
        auth.make_token(expected, hours),
        max_age=hours * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


def _safe_next(target: str) -> str:
    return target if target.startswith("/ui") and "//" not in target else "/ui/"


# ---------------------------------------------------------------- playground


@router.get("/", response_class=HTMLResponse, dependencies=[Login])
def index(request: Request, ctx: Ctx) -> HTMLResponse:
    recent, _ = playground.history(ctx, limit=10)
    return _render(
        request,
        "index.html",
        fields=PARAM_FIELDS,
        defaults=effective_params(ctx.settings, None),
        mask_strategies=list(MaskStrategy),
        default_mask=ctx.settings.default_mask_strategy.value,
        work_width=ctx.settings.engine_work_width,
        recent=recent,
    )


def _overrides(form: dict[str, Any]) -> dict[str, float]:
    """Only fields the user filled in; empty means "server default"."""
    out: dict[str, float] = {}
    for f in PARAM_FIELDS:
        raw = str(form.get(f.key) or "").strip().replace(",", ".")
        if raw:
            try:
                out[f.key] = float(raw)
            except ValueError:
                raise ValidationFailed(f"{f.label}: '{raw}' is not a number") from None
    return out


def _mask(raw: str | None) -> MaskStrategy | None:
    return MaskStrategy(raw) if raw else None


@router.post("/run", dependencies=[Login])
async def run(
    request: Request,
    ctx: Ctx,
    reference: Annotated[UploadFile, File()],
    photo: Annotated[UploadFile, File()],
) -> RedirectResponse:
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str)}
    side = int(values.get("side") or 1)
    insp = await asyncio.to_thread(
        playground.run,
        ctx,
        read_upload(ctx, reference),
        read_upload(ctx, photo),
        side,
        _mask(values.get("mask_strategy")),
        None,
        _overrides(values),
        values.get("product_code") or "playground",
        values.get("note") or None,
    )
    return RedirectResponse(f"/ui/inspections/{insp.id}", status_code=303)


@router.post("/inspections/{inspection_id}/rerun", dependencies=[Login])
async def rerun(request: Request, inspection_id: uuid.UUID, ctx: Ctx) -> RedirectResponse:
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str)}
    insp = await asyncio.to_thread(
        playground.rerun, ctx, inspection_id, _overrides(values), _mask(values.get("mask_strategy"))
    )
    return RedirectResponse(f"/ui/inspections/{insp.id}", status_code=303)


# ---------------------------------------------------------------- history and details


@router.get("/inspections", response_class=HTMLResponse, dependencies=[Login])
def history_page(
    request: Request,
    ctx: Ctx,
    page: Annotated[int, Query(ge=1)] = 1,
    status: str | None = None,
    source: str | None = None,
    product: str | None = None,
) -> HTMLResponse:
    rows, total = playground.history(
        ctx, PAGE_SIZE, (page - 1) * PAGE_SIZE, status or None, source or None, product or None
    )
    return _render(
        request,
        "history.html",
        rows=rows,
        total=total,
        page=page,
        pages=max(1, -(-total // PAGE_SIZE)),
        filters={"status": status or "", "source": source or "", "product": product or ""},
        statuses=[s.value for s in InspectionStatus],
        products=playground.products(ctx),
    )


@router.get("/inspections/{inspection_id}", response_class=HTMLResponse, dependencies=[Login])
def inspection_page(request: Request, inspection_id: uuid.UUID, ctx: Ctx) -> HTMLResponse:
    insp = inspections.get(ctx, inspection_id)
    session = sessions.get_session(ctx, insp.session_id)
    data = InspectionOut.build(insp).model_dump(mode="json")
    ref_image = insp.reference.image
    return _render(
        request,
        "inspection.html",
        insp=data,
        insp_json=json.dumps(data),
        reference={
            "id": str(insp.reference_id),
            "width": ref_image.width,
            "height": ref_image.height,
            "mask_strategy": insp.reference.mask_strategy,
        },
        session=session,
        fields=PARAM_FIELDS,
        params=insp.params,
        mask_strategies=list(MaskStrategy),
        defect_types=[t.value for t in DefectType],
    )


@router.get("/inspections/{inspection_id}/state", dependencies=[Login])
def inspection_json(
    inspection_id: uuid.UUID, ctx: Ctx, wait: Annotated[float, Query(ge=0, le=30)] = 0
) -> JSONResponse:
    insp = inspections.wait(ctx, inspection_id, wait) if wait else inspections.get(ctx, inspection_id)
    return JSONResponse(InspectionOut.build(insp).model_dump(mode="json"))


@router.get("/inspections/{inspection_id}/image", dependencies=[Login])
def inspection_image(inspection_id: uuid.UUID, ctx: Ctx, kind: ImageKind = ImageKind.ORIGINAL) -> Response:
    data, content_type = media.inspection_image(ctx, inspection_id, kind)
    return Response(data, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/inspections/{inspection_id}/reference", dependencies=[Login])
def reference_image(inspection_id: uuid.UUID, ctx: Ctx) -> Response:
    insp = inspections.get(ctx, inspection_id)
    image = insp.reference.image
    return Response(
        ctx.storage.get(image.storage_key),
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/inspections/{inspection_id}/mask", dependencies=[Login])
def mask_view(inspection_id: uuid.UUID, ctx: Ctx) -> Response:
    return Response(
        media.reference_mask_view(ctx, inspection_id),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/inspections/{inspection_id}/defects/{defect_id}/crop", dependencies=[Login])
def crop(
    inspection_id: uuid.UUID,
    defect_id: uuid.UUID,
    ctx: Ctx,
    kind: CropKind = CropKind.PAIR,
    height: Annotated[int, Query(ge=32, le=2000)] = 300,
) -> Response:
    data = media.crop(ctx, inspection_id, defect_id, kind, 60, height)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/defects/{defect_id}/verdict", dependencies=[Login])
async def verdict(request: Request, defect_id: uuid.UUID, ctx: Ctx) -> JSONResponse:
    body = await request.json()
    d = await asyncio.to_thread(
        defects.set_verdict,
        ctx,
        playground.ui_api_key(ctx),
        defect_id,
        Verdict(body.get("verdict", "pending")),
        DefectType(body["defect_type"]) if body.get("defect_type") else None,
        body.get("comment") or None,
        "web-ui",
    )
    return JSONResponse({"id": str(d.id), "verdict": d.verdict, "defect_type": d.defect_type})
