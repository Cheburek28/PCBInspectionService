"""The real Celery task through a real Redis broker."""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient

from pcb_inspection.api.app import create_app
from pcb_inspection.queue.celery_queue import CeleryQueue
from pcb_inspection.services import apikeys, pipeline
from pcb_inspection.services.bootstrap import build_context
from pcb_inspection.settings import get_settings
from tests.conftest import WORK_WIDTH
from tests.integration.conftest import Api, settings_for, truncate_all


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture
def worker_module(
    redis_url: str, migrated_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ModuleType]:
    truncate_all(migrated_url)
    env = {
        "PCBIS_REDIS_URL": redis_url,
        "PCBIS_DATABASE_URL": migrated_url,
        "PCBIS_STORAGE_PATH": str(tmp_path / "blobs"),
        "PCBIS_ENGINE_WORK_WIDTH": str(WORK_WIDTH),
        "PCBIS_GATE_MIN_WIDTH": "600",
        "PCBIS_TASK_MAX_RETRIES": "1",
        "PCBIS_LOG_FORMAT": "console",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    module = importlib.import_module("pcb_inspection.worker.celery_app")
    module = importlib.reload(module)
    yield module
    if module._ctx is not None:
        module._ctx.db.dispose()
    get_settings.cache_clear()


@pytest.fixture
def worker_api(worker_module: ModuleType, migrated_url: str, tmp_path: Path) -> Iterator[Api]:
    ctx = build_context(settings_for(migrated_url, tmp_path / "blobs"), queue=CeleryQueue(worker_module.app))
    _, raw = apikeys.create_api_key(ctx, "worker-test")
    with (
        start_worker(worker_module.app, pool="solo", perform_ping_check=False, shutdown_timeout=30),
        TestClient(create_app(ctx)) as client,
    ):
        yield Api(client, raw)
    ctx.db.dispose()


def test_inspection_through_celery(worker_api: Api, ref_jpeg: bytes, defect_jpeg: bytes) -> None:
    s = worker_api.create_session()
    worker_api.upload_reference(s["id"], ref_jpeg)
    accepted = worker_api.submit(s["id"], defect_jpeg).json()
    assert accepted["status"] == "queued"
    insp = worker_api.inspection(accepted["id"], wait=30)
    assert insp["status"] == "completed", insp
    assert len(insp["defects"]) == 1
    ready = worker_api.client.get("/health/ready").json()
    assert ready["checks"]["queue"] == "ok"


def test_task_retries_then_marks_failed(
    worker_module: ModuleType,
    queued_api: Api,
    ref_jpeg: bytes,
    clean_jpeg: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eager execution runs Celery's retry loop synchronously (countdown is ignored)."""
    calls: list[uuid.UUID] = []

    def flaky(ctx: Any, inspection_id: uuid.UUID) -> Any:
        calls.append(inspection_id)
        raise ConnectionError("database went away")

    s = queued_api.create_session()
    queued_api.upload_reference(s["id"], ref_jpeg)
    iid = queued_api.submit(s["id"], clean_jpeg).json()["id"]
    monkeypatch.setattr(pipeline, "run_inspection", flaky)
    worker_module.inspect.apply(args=[iid])
    insp = queued_api.inspection(iid)
    assert insp["status"] == "failed"
    assert insp["error"]["code"] == "INTERNAL_ERROR"
    assert "database went away" in insp["error"]["message"]
    assert len(calls) == 2  # first attempt + one retry


def test_soft_time_limit_marks_failed(
    worker_module: ModuleType,
    queued_api: Api,
    ref_jpeg: bytes,
    clean_jpeg: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery.exceptions import SoftTimeLimitExceeded

    def slow(ctx: Any, inspection_id: uuid.UUID) -> Any:
        raise SoftTimeLimitExceeded

    s = queued_api.create_session()
    queued_api.upload_reference(s["id"], ref_jpeg)
    iid = queued_api.submit(s["id"], clean_jpeg).json()["id"]
    monkeypatch.setattr(pipeline, "run_inspection", slow)
    assert worker_module.inspect.apply(args=[iid]).get() == "failed"
    assert queued_api.inspection(iid)["error"]["code"] == "TIMEOUT"
