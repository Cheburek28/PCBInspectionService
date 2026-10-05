from __future__ import annotations

import json
import uuid

import cv2
import numpy as np

from pcb_inspection import synthetic as sy
from pcb_inspection.engine.imaging import encode_jpeg, encode_png
from tests.conftest import Scene, make_scene
from tests.integration.conftest import Api


def test_upload_reference_and_mask(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    r = api.upload_reference(
        s["id"], ref_jpeg, side=1, board=json.dumps({"board_key": "first", "barcode": "1"})
    )
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["is_active"]
    assert ref["source"] == "upload"
    assert ref["image"] == {"width": 1200, "height": 800, "sha256": ref["image"]["sha256"], "url": None}
    assert ref["mask"]["strategy"] == "full_frame"
    assert 0.9 < ref["mask"]["coverage"] <= 1

    mask = api.request("GET", ref["mask"]["url"])
    assert mask.status_code == 200
    assert mask.headers["content-type"] == "image/png"
    decoded = cv2.imdecode(np.frombuffer(mask.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded.shape == (800, 1200)

    assert api.request("GET", f"/api/v1/references/{ref['id']}").json()["id"] == ref["id"]
    session = api.request("GET", f"/api/v1/sessions/{s['id']}").json()
    assert session["active_references"] == {"1": ref["id"]}


def test_new_reference_replaces_the_active_one(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = api.create_session()
    first = api.upload_reference(s["id"], ref_jpeg, side=2).json()
    second = api.upload_reference(s["id"], clean_jpeg, side=2).json()
    assert api.request("GET", f"/api/v1/references/{first['id']}").json()["is_active"] is False
    session = api.request("GET", f"/api/v1/sessions/{s['id']}").json()
    assert session["active_references"] == {"2": second["id"]}


def test_polygon_mask(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    poly = [[100, 100], [1100, 100], [1100, 700], [100, 700]]
    r = api.upload_reference(s["id"], ref_jpeg, mask_strategy="polygon", mask_polygon=json.dumps(poly))
    assert r.status_code == 201, r.text
    assert 0.55 < r.json()["mask"]["coverage"] < 0.65


def test_polygon_mask_validation(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    assert (
        api.upload_reference(s["id"], ref_jpeg, mask_strategy="polygon").json()["code"] == "VALIDATION_ERROR"
    )
    r = api.upload_reference(s["id"], ref_jpeg, mask_strategy="polygon", mask_polygon="[1, 2]")
    assert r.status_code == 422
    r = api.upload_reference(s["id"], ref_jpeg, mask_strategy="polygon", mask_polygon="not json")
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_blue_fixture_mask_not_found(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    r = api.upload_reference(s["id"], ref_jpeg, mask_strategy="blue_fixture")
    assert r.status_code == 422
    assert r.json()["code"] == "BOARD_MASK_NOT_FOUND"


def test_blue_fixture_mask_found(api: Api, fixture_scene: Scene) -> None:
    s = api.create_session()
    r = api.upload_reference(s["id"], encode_jpeg(fixture_scene.reference, 95), mask_strategy="blue_fixture")
    assert r.status_code == 201, r.text
    assert 0.5 < r.json()["mask"]["coverage"] < 0.75


def test_green_board_mask_on_a_white_fixture(api: Api) -> None:
    scene = make_scene(seed=3, blue_fixture=True, panel_bgr=sy.BOARD_BGR, slot_bgr=(235, 235, 235))
    s = api.create_session()
    r = api.upload_reference(s["id"], encode_jpeg(scene.reference, 95), mask_strategy="green_board")
    assert r.status_code == 201, r.text
    assert r.json()["mask"]["strategy"] == "green_board"
    assert 0.5 < r.json()["mask"]["coverage"] < 0.75


def test_bad_uploads(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    gif = api.request(
        "POST",
        f"/api/v1/sessions/{s['id']}/references",
        data={"side": "1"},
        files={"image": ("x.gif", b"GIF89a....", "image/gif")},
    )
    assert gif.status_code == 415
    broken = api.upload_reference(s["id"], b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    assert broken.status_code == 422
    assert broken.json()["code"] == "IMAGE_UNREADABLE"
    small = api.upload_reference(s["id"], encode_png(np.zeros((100, 200, 3), np.uint8)))
    assert small.json()["code"] == "IMAGE_TOO_SMALL"
    big = api.upload_reference(s["id"], b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024))
    assert big.status_code == 413
    assert api.upload_reference(s["id"], ref_jpeg, side=9).status_code == 422
    assert api.upload_reference(s["id"], ref_jpeg, board="{bad").json()["code"] == "VALIDATION_ERROR"
    assert (
        api.upload_reference(s["id"], ref_jpeg, board='{"barcode": "1"}').json()["code"] == "VALIDATION_ERROR"
    )


def test_closed_session_rejects_references(api: Api, ref_jpeg: bytes) -> None:
    s = api.create_session()
    api.request("POST", f"/api/v1/sessions/{s['id']}/close")
    r = api.upload_reference(s["id"], ref_jpeg)
    assert r.status_code == 409
    assert r.json()["code"] == "SESSION_CLOSED"


def test_reference_from_inspection(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = api.create_session()
    first = api.upload_reference(s["id"], ref_jpeg).json()
    insp = api.submit(s["id"], clean_jpeg).json()
    r = api.request(
        "POST",
        f"/api/v1/sessions/{s['id']}/references/from-inspection",
        json={"from_inspection_id": insp["id"]},
    )
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["source"] == "inspection"
    assert ref["source_inspection_id"] == insp["id"]
    assert ref["side"] == 1
    assert ref["id"] != first["id"]
    assert api.request("GET", f"/api/v1/sessions/{s['id']}").json()["active_references"] == {"1": ref["id"]}


def test_reference_from_inspection_carries_polygon(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = api.create_session()
    poly = [[100, 100], [1100, 100], [1100, 700], [100, 700]]
    api.upload_reference(s["id"], ref_jpeg, mask_strategy="polygon", mask_polygon=json.dumps(poly))
    insp = api.submit(s["id"], clean_jpeg).json()
    ref = api.request(
        "POST",
        f"/api/v1/sessions/{s['id']}/references/from-inspection",
        json={"from_inspection_id": insp["id"]},
    ).json()
    assert ref["mask"]["strategy"] == "polygon"
    assert 0.55 < ref["mask"]["coverage"] < 0.65


def test_reference_from_inspection_errors(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    insp = api.submit(s["id"], clean_jpeg).json()
    url = f"/api/v1/sessions/{s['id']}/references/from-inspection"
    wrong_side = api.request("POST", url, json={"from_inspection_id": insp["id"], "side": 2})
    assert wrong_side.json()["code"] == "VALIDATION_ERROR"
    missing = api.request("POST", url, json={"from_inspection_id": str(uuid.uuid4())})
    assert missing.status_code == 404
    other = api.create_session()
    foreign = api.request(
        "POST",
        f"/api/v1/sessions/{other['id']}/references/from-inspection",
        json={"from_inspection_id": insp["id"]},
    )
    assert foreign.status_code == 404
