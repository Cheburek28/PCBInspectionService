"""Integration fixtures: real PostgreSQL (testcontainers or PCBIS_TEST_DATABASE_URL), migrated once."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from pcb_inspection.api.app import create_app
from pcb_inspection.db.models import Base
from pcb_inspection.engine.imaging import encode_jpeg
from pcb_inspection.queue import InlineQueue, RecordingQueue, TaskQueue
from pcb_inspection.services import apikeys, pipeline
from pcb_inspection.services.bootstrap import build_context
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.settings import Settings
from tests.conftest import WORK_WIDTH, Scene

ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    url = os.environ.get("PCBIS_TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


def truncate_all(url: str) -> None:
    engine = create_engine(url)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    engine.dispose()


def settings_for(url: str, storage: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=url,
        storage_path=str(storage),
        engine_work_width=WORK_WIDTH,
        gate_min_width=600,
        max_upload_mb=2,
        log_format="console",
    )


def make_ctx(url: str, storage: Path, queue: TaskQueue | None = None) -> ServiceContext:
    holder: dict[str, ServiceContext] = {}
    q = queue or InlineQueue(lambda iid: pipeline.run_inspection(holder["ctx"], iid))
    ctx = build_context(settings_for(url, storage), queue=q)
    holder["ctx"] = ctx
    return ctx


class Api:
    """Thin helper around TestClient that knows the API shapes."""

    def __init__(self, client: TestClient, key: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {key}"}

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self.headers, **kw.pop("headers", {})}
        return self.client.request(method, url, headers=headers, **kw)

    def create_session(self, **body: Any) -> dict[str, Any]:
        r = self.request("POST", "/api/v1/sessions", json={"product_code": "demo-board", **body})
        assert r.status_code == 201, r.text
        return dict(r.json())

    def upload_reference(self, session_id: str, image: bytes, side: int = 1, **form: str) -> httpx.Response:
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/references",
            data={"side": str(side), **form},
            files={"image": ("ref.jpg", image, "image/jpeg")},
        )

    def submit(
        self,
        session_id: str,
        image: bytes,
        side: int = 1,
        board_key: str = "board-1",
        idempotency_key: str | None = None,
        **form: str,
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/api/v1/sessions/{session_id}/inspections",
            data={
                "side": str(side),
                "board": json.dumps({"board_key": board_key, "barcode": "123456"}),
                **form,
            },
            files={"image": ("test.jpg", image, "image/jpeg")},
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )

    def inspection(self, inspection_id: str, wait: float = 0) -> dict[str, Any]:
        r = self.request("GET", f"/api/v1/inspections/{inspection_id}", params={"wait": wait})
        assert r.status_code == 200, r.text
        return dict(r.json())


@pytest.fixture
def ctx(migrated_url: str, tmp_path: Path) -> Iterator[ServiceContext]:
    truncate_all(migrated_url)
    context = make_ctx(migrated_url, tmp_path / "blobs")
    yield context
    context.db.dispose()


@pytest.fixture
def queued_ctx(migrated_url: str, tmp_path: Path) -> Iterator[ServiceContext]:
    """Jobs are only recorded, never run: inspections stay 'queued' until the test runs them."""
    truncate_all(migrated_url)
    context = make_ctx(migrated_url, tmp_path / "blobs", RecordingQueue())
    yield context
    context.db.dispose()


def _api(context: ServiceContext) -> Iterator[Api]:
    _, raw = apikeys.create_api_key(context, "test-station")
    with TestClient(create_app(context)) as client:
        yield Api(client, raw)


@pytest.fixture
def api(ctx: ServiceContext) -> Iterator[Api]:
    yield from _api(ctx)


@pytest.fixture
def queued_api(queued_ctx: ServiceContext) -> Iterator[Api]:
    yield from _api(queued_ctx)


@pytest.fixture(scope="session")
def ref_jpeg(scene: Scene) -> bytes:
    return encode_jpeg(scene.reference, 95)


@pytest.fixture(scope="session")
def clean_jpeg(scene: Scene) -> bytes:
    image, _ = scene.photo()
    return encode_jpeg(image, 95)


@pytest.fixture(scope="session")
def defect_jpeg(scene: Scene) -> bytes:
    from pcb_inspection import synthetic as sy

    image, _ = scene.photo(sy.Defects(removed=frozenset({3})))
    return encode_jpeg(image, 95)


@pytest.fixture(scope="session")
def blurry_jpeg(scene: Scene) -> bytes:
    image, _ = scene.photo(blur_sigma=3.0)
    return encode_jpeg(image, 95)
