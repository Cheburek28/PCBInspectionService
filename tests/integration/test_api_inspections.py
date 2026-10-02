from __future__ import annotations

import json
import threading
import time
import uuid

import cv2
import numpy as np

from pcb_inspection.queue import RecordingQueue
from pcb_inspection.services import pipeline
from pcb_inspection.services.context import ServiceContext
from tests.integration.conftest import Api


def _session_with_reference(api: Api, ref_jpeg: bytes) -> str:
    s = api.create_session()
    assert api.upload_reference(s["id"], ref_jpeg).status_code == 201
    return str(s["id"])


def test_defect_is_reported(api: Api, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    r = api.submit(sid, defect_jpeg, board_key="b-1")
    assert r.status_code == 202, r.text
    accepted = r.json()
    assert r.headers["Location"] == accepted["poll_url"]
    insp = api.inspection(accepted["id"], wait=5)
    assert insp["status"] == "completed"
    assert insp["rejection"] is None
    assert insp["board"] == {"board_key": "b-1", "barcode": "123456", "serial": None}
    assert insp["image"]["width"] == 1200
    assert insp["quality"]["alignment_ok"] is True
    assert insp["algorithm"]["name"] == "classic-diff"
    assert insp["algorithm"]["version"] == "0.2.0"
    assert insp["algorithm"]["params"]["engine"]["work_width"] == 1200
    assert insp["timings_ms"]["total"] > 0
    assert insp["attempts"] == 1
    assert len(insp["defects"]) == 1
    d = insp["defects"][0]
    assert d["source"] == "auto"
    assert d["verdict"] == "pending"
    assert d["rank"] == 1
    assert d["bbox_test"]["w"] > 20


def test_clean_board(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    insp = api.inspection(api.submit(sid, clean_jpeg).json()["id"])
    assert insp["status"] == "completed"
    assert insp["defects"] == []


def test_blurry_photo_is_rejected(api: Api, ref_jpeg: bytes, blurry_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    insp = api.inspection(api.submit(sid, blurry_jpeg).json()["id"])
    assert insp["status"] == "rejected"
    assert insp["rejection"]["code"] == "IMAGE_BLURRY"
    assert "sharpness_ratio" in insp["rejection"]["details"]


def test_other_board_fails_alignment(api: Api, ref_jpeg: bytes) -> None:
    from pcb_inspection import synthetic as sy
    from pcb_inspection.engine.imaging import encode_jpeg

    other = sy.random_board(77)
    sid = _session_with_reference(api, ref_jpeg)
    insp = api.inspection(
        api.submit(sid, encode_jpeg(sy.photograph(sy.render(other), sy.camera(other, 1)))).json()["id"]
    )
    assert insp["status"] == "rejected"
    assert insp["rejection"]["code"] == "ALIGNMENT_FAILED"
    assert insp["defects"] == []
    r = api.request("GET", f"/api/v1/inspections/{insp['id']}/image", params={"kind": "aligned"})
    assert r.status_code == 404


def test_too_small_photo_is_rejected_by_the_gate(api: Api, ref_jpeg: bytes, ctx: ServiceContext) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    small = cv2.imencode(".jpg", np.full((300, 500, 3), 90, np.uint8))[1].tobytes()
    insp = api.inspection(api.submit(sid, small).json()["id"])
    assert insp["status"] == "rejected"
    assert insp["rejection"]["code"] == "IMAGE_TOO_SMALL"


def test_params_override_is_applied_and_stored(api: Api, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    r = api.submit(sid, defect_jpeg, params=json.dumps({"threshold": 500}))
    insp = api.inspection(r.json()["id"])
    assert insp["algorithm"]["params"]["engine"]["threshold"] == 500
    assert insp["defects"] == []


def test_bad_params(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    assert (
        api.submit(sid, clean_jpeg, params=json.dumps({"work_width": 10})).json()["code"]
        == "VALIDATION_ERROR"
    )
    assert api.submit(sid, clean_jpeg, params="[1]").json()["code"] == "VALIDATION_ERROR"


def test_no_active_reference(api: Api, clean_jpeg: bytes) -> None:
    s = api.create_session()
    r = api.submit(s["id"], clean_jpeg, side=2)
    assert r.status_code == 409
    assert r.json()["code"] == "NO_ACTIVE_REFERENCE"


def test_explicit_reference(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    s = api.create_session()
    old = api.upload_reference(s["id"], ref_jpeg).json()
    api.upload_reference(s["id"], clean_jpeg)
    insp = api.inspection(api.submit(s["id"], clean_jpeg, reference_id=old["id"]).json()["id"])
    assert insp["reference_id"] == old["id"]
    wrong_side = api.submit(s["id"], clean_jpeg, side=2, reference_id=old["id"])
    assert wrong_side.json()["code"] == "VALIDATION_ERROR"
    unknown = api.submit(s["id"], clean_jpeg, reference_id=str(uuid.uuid4()))
    assert unknown.status_code == 404


def test_closed_session(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    api.request("POST", f"/api/v1/sessions/{sid}/close")
    assert api.submit(sid, clean_jpeg).json()["code"] == "SESSION_CLOSED"


def test_board_and_idempotency_key_are_required(api: Api, ref_jpeg: bytes, clean_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    no_key = api.request(
        "POST",
        f"/api/v1/sessions/{sid}/inspections",
        data={"side": "1", "board": '{"board_key": "x"}'},
        files={"image": ("t.jpg", clean_jpeg, "image/jpeg")},
    )
    assert no_key.status_code == 422
    no_board = api.request(
        "POST",
        f"/api/v1/sessions/{sid}/inspections",
        data={"side": "1", "board": ""},
        files={"image": ("t.jpg", clean_jpeg, "image/jpeg")},
        headers={"Idempotency-Key": "k"},
    )
    assert no_board.json()["code"] == "VALIDATION_ERROR"


def test_idempotent_retry(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    sid = _session_with_reference(queued_api, ref_jpeg)
    first = queued_api.submit(sid, clean_jpeg, idempotency_key="retry-1").json()
    second = queued_api.submit(sid, clean_jpeg, idempotency_key="retry-1").json()
    assert first["id"] == second["id"]
    assert first["status"] == "queued"
    assert isinstance(queued_ctx.queue, RecordingQueue)
    assert queued_ctx.queue.enqueued == [uuid.UUID(first["id"])]
    conflict = queued_api.submit(sid, ref_jpeg, idempotency_key="retry-1")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_retake_supersedes_previous_inspection(
    api: Api, ref_jpeg: bytes, clean_jpeg: bytes, defect_jpeg: bytes
) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    first = api.submit(sid, defect_jpeg, board_key="b").json()
    second = api.submit(sid, clean_jpeg, board_key="b").json()
    other_board = api.submit(sid, clean_jpeg, board_key="c").json()
    assert api.inspection(first["id"])["superseded"] is True
    assert api.inspection(second["id"])["superseded"] is False
    assert api.inspection(other_board["id"])["superseded"] is False
    board = api.request("GET", f"/api/v1/sessions/{sid}/boards/b").json()
    assert board["inspections"] == {"1": second["id"]}


def test_long_poll_returns_when_the_job_finishes(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    sid = _session_with_reference(queued_api, ref_jpeg)
    iid = queued_api.submit(sid, clean_jpeg).json()["id"]
    assert queued_api.inspection(iid)["status"] == "queued"
    assert queued_api.inspection(iid, wait=0.3)["status"] == "queued"  # times out

    def finish_later() -> None:
        time.sleep(0.5)
        pipeline.run_inspection(queued_ctx, uuid.UUID(iid))

    worker = threading.Thread(target=finish_later)
    worker.start()
    t0 = time.monotonic()
    insp = queued_api.inspection(iid, wait=20)
    elapsed = time.monotonic() - t0
    worker.join()
    assert insp["status"] == "completed"
    assert elapsed < 10
    assert insp["timings_ms"]["queue"] >= 0


def test_inspection_images(api: Api, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    sid = _session_with_reference(api, ref_jpeg)
    insp = api.inspection(api.submit(sid, defect_jpeg).json()["id"])
    for kind in ("original", "aligned", "heatmap"):
        r = api.request("GET", f"/api/v1/inspections/{insp['id']}/image", params={"kind": kind})
        assert r.status_code == 200, kind
        assert r.headers["content-type"] == "image/jpeg"
    original = api.request("GET", insp["image"]["url"]).content
    assert original == defect_jpeg


def test_unknown_inspection(api: Api) -> None:
    assert api.request("GET", f"/api/v1/inspections/{uuid.uuid4()}").status_code == 404
