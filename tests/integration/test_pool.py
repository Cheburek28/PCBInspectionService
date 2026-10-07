"""Targeted detectors in the service: reference pool selection, stored measures, detector of each region."""

from __future__ import annotations

import uuid

from pcb_inspection.db.models import Inspection
from pcb_inspection.engine.base import InspectParams
from pcb_inspection.services import pool
from pcb_inspection.services.context import ServiceContext
from tests.integration.conftest import Api

DETECTORS = {"detect_solder": True, "detect_shift": True, "detect_specks": True}


def _enable(ctx: ServiceContext) -> InspectParams:
    for k, v in DETECTORS.items():
        setattr(ctx.settings, k, v)
    return ctx.settings.inspect_params()


def _board(api: Api, sid: str, key: str, image: bytes, verdict: str | None = None) -> dict[str, object]:
    r = api.submit(sid, image, board_key=key)
    assert r.status_code in (201, 202), r.text
    insp = api.inspection(r.json()["id"], wait=10)
    assert insp["status"] == "completed", insp
    if verdict:
        v = api.request("PUT", f"/api/v1/sessions/{sid}/boards/{key}/verdict", json={"verdict": verdict})
        assert v.status_code == 200, v.text
    return insp


def test_regions_carry_their_detector_and_measures_are_stored(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes
) -> None:
    _enable(ctx)
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    insp = _board(api, s["id"], "b1", defect_jpeg)
    assert insp["defects"]
    assert {d["detector"] for d in insp["defects"]} <= {"diff", "solder", "shift", "speck", "hair"}
    assert any(d["detector"] == "diff" for d in insp["defects"])
    assert ctx.storage.exists(pool.measures_key(uuid.UUID(insp["id"])))
    assert "det_shift" in insp["timings_ms"]


def test_detectors_off_store_no_measures(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    insp = _board(api, s["id"], "b1", clean_jpeg)
    assert not ctx.storage.exists(pool.measures_key(uuid.UUID(insp["id"])))


def test_pool_is_the_first_passed_boards_without_confirmed_defects(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes, defect_jpeg: bytes
) -> None:
    params = _enable(ctx)
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    passed = _board(api, s["id"], "passed", clean_jpeg, verdict="pass")
    repaired = _board(api, s["id"], "repaired", defect_jpeg, verdict="pass")
    defect = next(d for d in repaired["defects"] if d["detector"] == "diff")
    r = api.request("PUT", f"/api/v1/defects/{defect['id']}/verdict", json={"verdict": "accepted"})
    assert r.status_code == 200
    _board(api, s["id"], "undecided", clean_jpeg)
    failed = _board(api, s["id"], "failed", clean_jpeg, verdict="fail")
    current = _board(api, s["id"], "current", clean_jpeg)
    with ctx.db.session() as db:
        row = db.get(Inspection, uuid.UUID(current["id"]))
        assert row is not None
    photos = pool.pool_for(ctx, row, params)
    assert [p.key for p in photos] == [passed["id"]]
    assert photos[0].measures is not None
    assert "shift_rel" in photos[0].measures
    aligned = photos[0].load()
    assert aligned.ndim == 3
    # a board inspected earlier is never a pool member of itself or of boards inspected before it
    with ctx.db.session() as db:
        first = db.get(Inspection, uuid.UUID(passed["id"]))
        assert first is not None
    assert pool.pool_for(ctx, first, params) == []
    assert failed["id"] not in [p.key for p in photos]


def test_pool_is_empty_when_detectors_are_off(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    _board(api, s["id"], "passed", clean_jpeg, verdict="pass")
    current = _board(api, s["id"], "current", clean_jpeg)
    with ctx.db.session() as db:
        row = db.get(Inspection, uuid.UUID(current["id"]))
        assert row is not None
    assert pool.pool_for(ctx, row, ctx.settings.inspect_params()) == []
