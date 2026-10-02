"""Worker-side behaviour: duplicates, deterministic failures, infrastructure failures."""

from __future__ import annotations

import uuid

import pytest

from pcb_inspection.db.models import Inspection
from pcb_inspection.domain.enums import InspectionStatus
from pcb_inspection.engine.base import EngineResult, InspectParams, PreparedReference
from pcb_inspection.services import pipeline
from pcb_inspection.services.context import ServiceContext
from tests.integration.conftest import Api


def _queued(api: Api, ref_jpeg: bytes, image: bytes) -> uuid.UUID:
    s = api.create_session()
    api.upload_reference(s["id"], ref_jpeg)
    return uuid.UUID(api.submit(s["id"], image).json()["id"])


def test_duplicate_delivery_is_ignored(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes
) -> None:
    iid = _queued(queued_api, ref_jpeg, defect_jpeg)
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.COMPLETED
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.COMPLETED
    insp = queued_api.inspection(str(iid))
    assert insp["attempts"] == 1
    assert len(insp["defects"]) == 1


def test_unknown_inspection_is_reported_failed(queued_ctx: ServiceContext) -> None:
    assert pipeline.run_inspection(queued_ctx, uuid.uuid4()) is InspectionStatus.FAILED


def test_reference_cache_is_rebuilt_from_storage(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes
) -> None:
    """A fresh worker process has an empty cache: the stored mask is used, not recomputed."""
    iid = _queued(queued_api, ref_jpeg, defect_jpeg)
    queued_ctx.ref_cache = type(queued_ctx.ref_cache)(4)
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.COMPLETED
    assert len(queued_ctx.ref_cache) == 1


def test_corrupt_stored_image_fails_without_retry(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    iid = _queued(queued_api, ref_jpeg, clean_jpeg)
    with queued_ctx.db.session() as s:
        key = s.get(Inspection, iid).image.storage_key  # type: ignore[union-attr]
    queued_ctx.storage.put(key, b"\xff\xd8\xff\xe0garbage", "image/jpeg")
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.FAILED
    insp = queued_api.inspection(str(iid))
    assert insp["status"] == "failed"
    assert insp["error"]["code"] == "ANALYSIS_ERROR"


def test_engine_error_fails(
    queued_api: Api,
    queued_ctx: ServiceContext,
    ref_jpeg: bytes,
    clean_jpeg: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iid = _queued(queued_api, ref_jpeg, clean_jpeg)

    def boom(ref: PreparedReference, test: object, params: InspectParams) -> EngineResult:
        raise ValueError("synthetic engine failure")

    monkeypatch.setattr(queued_ctx.engine, "inspect", boom)
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.FAILED
    assert "synthetic engine failure" in queued_api.inspection(str(iid))["error"]["message"]


def test_storage_outage_propagates_for_retry(
    queued_api: Api,
    queued_ctx: ServiceContext,
    ref_jpeg: bytes,
    clean_jpeg: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iid = _queued(queued_api, ref_jpeg, clean_jpeg)

    def down(key: str) -> bytes:
        raise ConnectionError("storage down")

    monkeypatch.setattr(queued_ctx.storage, "get", down)
    with pytest.raises(ConnectionError):
        pipeline.run_inspection(queued_ctx, iid)
    insp = queued_api.inspection(str(iid))
    assert insp["status"] == "processing"  # the task runner retries, then calls mark_failed
    monkeypatch.undo()
    assert pipeline.run_inspection(queued_ctx, iid) is InspectionStatus.COMPLETED
    assert queued_api.inspection(str(iid))["attempts"] == 2


def test_mark_failed_does_not_override_final_status(
    queued_api: Api, queued_ctx: ServiceContext, ref_jpeg: bytes, clean_jpeg: bytes
) -> None:
    iid = _queued(queued_api, ref_jpeg, clean_jpeg)
    pipeline.mark_failed(queued_ctx, iid, "TIMEOUT", "too slow")
    assert queued_api.inspection(str(iid))["error"] == {"code": "TIMEOUT", "message": "too slow"}
    pipeline.mark_failed(queued_ctx, iid, "OTHER", "ignored")
    assert queued_api.inspection(str(iid))["error"]["code"] == "TIMEOUT"
    pipeline.mark_failed(queued_ctx, uuid.uuid4(), "X", "missing inspection is fine")
