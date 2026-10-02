"""Web console: login, playground run, history, details, rerun, verdicts, media."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from pcb_inspection.api.app import create_app
from pcb_inspection.services.context import ServiceContext
from tests.integration.conftest import Api

PASSWORD = "test-password-123"


@pytest.fixture
def ui(ctx: ServiceContext) -> Iterator[TestClient]:
    ctx.settings = ctx.settings.model_copy(update={"ui_password": SecretStr(PASSWORD)})
    with TestClient(create_app(ctx), follow_redirects=False) as client:
        yield client


def login(client: TestClient) -> None:
    r = client.post("/ui/login", data={"password": PASSWORD, "next": "/ui/"})
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/"


def run(client: TestClient, ref: bytes, photo: bytes, **form: str) -> str:
    r = client.post(
        "/ui/run",
        data={"side": "1", **form},
        files={"reference": ("ref.jpg", ref, "image/jpeg"), "photo": ("photo.jpg", photo, "image/jpeg")},
    )
    assert r.status_code == 303, r.text
    match = re.fullmatch(r"/ui/inspections/([0-9a-f-]{36})", r.headers["location"])
    assert match
    return match.group(1)


def test_disabled_without_password(api: Api) -> None:
    assert api.client.get("/ui/login").status_code == 404
    assert api.client.get("/ui/", follow_redirects=False).status_code == 404


def test_login_required(ui: TestClient) -> None:
    r = ui.get("/ui/inspections")
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/login?next=/ui/inspections"
    assert ui.get(f"/ui/inspections/{'0' * 8}-0000-0000-0000-000000000000/state").status_code == 401
    assert ui.get("/", follow_redirects=False).headers["location"] == "/ui/"


def test_wrong_password(ui: TestClient) -> None:
    r = ui.post("/ui/login", data={"password": "nope"})
    assert r.status_code == 401
    assert "Неверный пароль" in r.text
    assert "pcbis_ui" not in r.cookies


def test_open_redirect_is_blocked(ui: TestClient) -> None:
    r = ui.post("/ui/login", data={"password": PASSWORD, "next": "https://evil.example"})
    assert r.headers["location"] == "/ui/"


def test_login_and_logout(ui: TestClient) -> None:
    assert ui.get("/ui/login").status_code == 200
    login(ui)
    page = ui.get("/ui/")
    assert page.status_code == 200
    assert "Сравнение с эталоном" in page.text
    ui.post("/ui/logout")
    assert ui.get("/ui/").status_code == 303


def test_playground_flow(ui: TestClient, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    login(ui)
    iid = run(ui, ref_jpeg, defect_jpeg, product_code="demo", note="first try", threshold="")
    page = ui.get(f"/ui/inspections/{iid}")
    assert page.status_code == 200
    assert "completed" in page.text
    assert "first try" in page.text

    state = ui.get(f"/ui/inspections/{iid}/state").json()
    assert state["status"] == "completed"
    assert len(state["defects"]) == 1
    assert state["algorithm"]["params"]["engine"]["threshold"] == 12.0
    defect = state["defects"][0]

    for url in (
        f"/ui/inspections/{iid}/image?kind=original",
        f"/ui/inspections/{iid}/image?kind=aligned",
        f"/ui/inspections/{iid}/image?kind=heatmap",
        f"/ui/inspections/{iid}/reference",
        f"/ui/inspections/{iid}/defects/{defect['id']}/crop?kind=pair&height=200",
    ):
        r = ui.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"].startswith("image/")

    v = ui.post(
        f"/ui/defects/{defect['id']}/verdict",
        json={"verdict": "accepted", "defect_type": "missing_component"},
    )
    assert v.json() == {"id": defect["id"], "verdict": "accepted", "defect_type": "missing_component"}

    history = ui.get("/ui/inspections?source=web-ui")
    assert history.status_code == 200
    assert iid in history.text
    assert "demo" in history.text


def test_rerun_with_other_threshold(ui: TestClient, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    login(ui)
    first = run(ui, ref_jpeg, defect_jpeg)
    r = ui.post(f"/ui/inspections/{first}/rerun", data={"threshold": "500", "min_area": "40"})
    assert r.status_code == 303
    second = r.headers["location"].rsplit("/", 1)[1]
    assert second != first
    state = ui.get(f"/ui/inspections/{second}/state").json()
    assert state["algorithm"]["params"]["engine"]["threshold"] == 500
    assert state["defects"] == []
    assert f"/ui/inspections/{first}" in ui.get(f"/ui/inspections/{second}").text
    # the original is untouched
    assert ui.get(f"/ui/inspections/{first}/state").json()["superseded"] is False


def test_bad_parameter(ui: TestClient, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    login(ui)
    r = ui.post(
        "/ui/run",
        data={"side": "1", "threshold": "abc"},
        files={"reference": ("r.jpg", ref_jpeg, "image/jpeg"), "photo": ("p.jpg", clean_jpeg, "image/jpeg")},
    )
    assert r.status_code == 422


def test_history_shows_api_inspections_and_filters(
    ui: TestClient, api: Api, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    s = api.create_session(product_code="line-product")
    api.upload_reference(s["id"], ref_jpeg)
    api_iid = api.submit(s["id"], clean_jpeg).json()["id"]
    login(ui)
    ui_iid = run(ui, ref_jpeg, clean_jpeg)
    everything = ui.get("/ui/inspections").text
    assert api_iid in everything
    assert ui_iid in everything
    only_api = ui.get("/ui/inspections?source=api").text
    assert api_iid in only_api
    assert ui_iid not in only_api
    by_product = ui.get("/ui/inspections?product=line-product&status=completed").text
    assert api_iid in by_product
    assert ui_iid not in by_product
    detail = ui.get(f"/ui/inspections/{api_iid}")
    assert detail.status_code == 200
    assert "test-station" in detail.text
