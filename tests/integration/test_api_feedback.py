"""Operator feedback: verdicts, manual regions, crops, board verdicts."""

from __future__ import annotations

import uuid
from typing import Any

import cv2
import numpy as np

from pcb_inspection.db.models import Inspection
from pcb_inspection.engine import imaging
from pcb_inspection.services import defects as defect_service
from pcb_inspection.services.context import ServiceContext
from tests.integration.conftest import Api


def _inspected(api: Api, ref_jpeg: bytes, image: bytes, board_key: str = "b-1") -> tuple[str, dict[str, Any]]:
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    insp = api.inspection(api.submit(s["id"], image, board_key=board_key).json()["id"])
    return str(s["id"]), insp


def test_verdict_and_history(api: Api, ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    _, insp = _inspected(api, ref_jpeg, defect_jpeg)
    did = insp["defects"][0]["id"]
    r = api.request(
        "PUT",
        f"/api/v1/defects/{did}/verdict",
        json={"verdict": "accepted", "defect_type": "missing_component", "comment": "C4", "operator": "op"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["verdict"], d["defect_type"], d["comment"], d["verdict_operator"]) == (
        "accepted",
        "missing_component",
        "C4",
        "op",
    )
    assert d["verdict_at"] is not None
    undo = api.request("PUT", f"/api/v1/defects/{did}/verdict", json={"verdict": "pending"}).json()
    assert undo["verdict"] == "pending"
    assert undo["verdict_at"] is None
    api.request("PUT", f"/api/v1/defects/{did}/verdict", json={"verdict": "rejected"})
    events = defect_service.history(ctx, uuid.UUID(did))
    assert [e.new["verdict"] for e in events if e.new] == ["accepted", "pending", "rejected"]
    assert events[0].old == {"verdict": "pending", "defect_type": None, "comment": None}
    assert api.inspection(insp["id"])["defects"][0]["verdict"] == "rejected"


def test_verdict_validation(api: Api) -> None:
    unknown = api.request("PUT", f"/api/v1/defects/{uuid.uuid4()}/verdict", json={"verdict": "accepted"})
    assert unknown.status_code == 404
    bad = api.request("PUT", f"/api/v1/defects/{uuid.uuid4()}/verdict", json={"verdict": "maybe"})
    assert bad.status_code == 422


def test_manual_defect_is_mapped_to_reference(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, scene: Any
) -> None:
    from pcb_inspection.engine.imaging import encode_jpeg

    image, cam = scene.photo()
    _, insp = _inspected(api, ref_jpeg, encode_jpeg(image, 95))
    component = scene.spec.components[5].bbox
    drawn = cam.project(component)
    r = api.request(
        "POST",
        f"/api/v1/inspections/{insp['id']}/defects",
        json={
            "bbox_test": drawn.to_dict(),
            "defect_type": "wrong_component",
            "comment": "looks odd",
            "operator": "op",
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["source"] == "manual"
    assert d["verdict"] == "accepted"
    assert d["score"] is None
    assert d["rank"] == 1
    from pcb_inspection.engine.geometry import BBox

    assert BBox(**d["bbox_ref"]).iou(component) >= 0.7
    assert [e.event for e in defect_service.history(ctx, uuid.UUID(d["id"]))] == ["created"]
    crop = api.request("GET", d["crop_url"])
    assert crop.status_code == 200


def test_manual_defect_errors(queued_api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = queued_api.create_session()
    queued_api.upload_reference(s["id"], ref_jpeg)
    insp = queued_api.submit(s["id"], clean_jpeg).json()
    url = f"/api/v1/inspections/{insp['id']}/defects"
    box = {"bbox_test": {"x": 10, "y": 10, "w": 20, "h": 20}}
    assert queued_api.request("POST", url, json=box).json()["code"] == "INVALID_STATE"
    assert (
        queued_api.request("POST", f"/api/v1/inspections/{uuid.uuid4()}/defects", json=box).status_code == 404
    )


def test_manual_defect_outside_or_without_transform(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    from pcb_inspection import synthetic as sy
    from pcb_inspection.engine.imaging import encode_jpeg

    _, insp = _inspected(api, ref_jpeg, clean_jpeg)
    outside = api.request(
        "POST",
        f"/api/v1/inspections/{insp['id']}/defects",
        json={"bbox_test": {"x": 5000, "y": 10, "w": 5, "h": 5}},
    )
    assert outside.json()["code"] == "VALIDATION_ERROR"
    other = sy.random_board(77)
    _, misaligned = _inspected(
        api, ref_jpeg, encode_jpeg(sy.photograph(sy.render(other), sy.camera(other, 1)))
    )
    no_transform = api.request(
        "POST",
        f"/api/v1/inspections/{misaligned['id']}/defects",
        json={"bbox_test": {"x": 1, "y": 1, "w": 5, "h": 5}},
    )
    assert no_transform.status_code == 409
    assert no_transform.json()["code"] == "NO_TRANSFORM"


def test_crops(api: Api, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    _, insp = _inspected(api, ref_jpeg, defect_jpeg)
    d = insp["defects"][0]
    pair = api.request("GET", d["crop_url"])
    assert pair.status_code == 200
    img = cv2.imdecode(np.frombuffer(pair.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[0] == 400
    base = f"/api/v1/inspections/{insp['id']}/defects/{d['id']}/crop"
    ref = cv2.imdecode(
        np.frombuffer(api.request("GET", base, params={"kind": "ref", "height": 100}).content, np.uint8), 1
    )
    test = cv2.imdecode(
        np.frombuffer(api.request("GET", base, params={"kind": "test", "height": 100}).content, np.uint8), 1
    )
    assert ref.shape == test.shape
    assert ref.shape[0] == 100
    # cached on the second call: identical bytes
    assert api.request("GET", d["crop_url"]).content == pair.content
    assert (
        api.request("GET", f"/api/v1/inspections/{insp['id']}/defects/{uuid.uuid4()}/crop").status_code == 404
    )
    assert api.request("GET", base, params={"height": 5}).status_code == 422


def test_test_crop_comes_from_the_photo_not_the_flow_warped_image(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes
) -> None:
    """Optical flow can bend straight edges (uniform IC bodies, rows of leads): crops show the photo."""
    _, insp = _inspected(api, ref_jpeg, defect_jpeg)
    with ctx.db.session() as s:
        key = s.get(Inspection, uuid.UUID(insp["id"])).aligned_storage_key
    black = imaging.decode(ctx.storage.get(key)).pixels * 0
    ctx.storage.put(key, imaging.encode_jpeg(black, 90), "image/jpeg")
    d = insp["defects"][0]
    base = f"/api/v1/inspections/{insp['id']}/defects/{d['id']}/crop"
    test = cv2.imdecode(
        np.frombuffer(api.request("GET", base, params={"kind": "test", "height": 100}).content, np.uint8), 1
    )
    assert test.mean() > 30  # the board, not the (blackened) aligned image


def test_board_verdict(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    sid, insp = _inspected(api, ref_jpeg, clean_jpeg, board_key="bk")
    r = api.request(
        "PUT",
        f"/api/v1/sessions/{sid}/boards/bk/verdict",
        json={"verdict": "pass", "operator": "op", "comment": "ok", "serial": "00042"},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["verdict"], b["barcode"], b["serial"]) == ("pass", "123456", "00042")
    assert b["inspections"] == {"1": insp["id"]}
    assert api.request("GET", f"/api/v1/sessions/{sid}/boards/bk").json()["verdict"] == "pass"


def test_board_verdict_creates_unknown_board(api: Api) -> None:
    s = api.create_session()
    r = api.request("PUT", f"/api/v1/sessions/{s['id']}/boards/new-board/verdict", json={"verdict": "fail"})
    assert r.status_code == 200
    assert r.json()["inspections"] == {}
    assert api.request("GET", f"/api/v1/sessions/{s['id']}/boards/missing").status_code == 404
    assert (
        api.request(
            "PUT", f"/api/v1/sessions/{uuid.uuid4()}/boards/x/verdict", json={"verdict": "fail"}
        ).status_code
        == 404
    )
