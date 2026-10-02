from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pcb_inspection.cli import main as cli
from pcb_inspection.services import apikeys
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.services.export import export_coco
from tests.integration.conftest import Api

runner = CliRunner()


def _feedback(api: Api, ref_jpeg: bytes, defect_jpeg: bytes, clean_jpeg: bytes) -> str:
    s = api.create_session(product_code="exported")
    api.upload_reference(s["id"], ref_jpeg)
    insp = api.inspection(api.submit(s["id"], defect_jpeg, board_key="a").json()["id"])
    api.request("PUT", f"/api/v1/defects/{insp['defects'][0]['id']}/verdict", json={"verdict": "accepted"})
    api.request(
        "POST",
        f"/api/v1/inspections/{insp['id']}/defects",
        json={"bbox_test": {"x": 10, "y": 10, "w": 30, "h": 30}},
    )
    clean = api.inspection(api.submit(s["id"], clean_jpeg, board_key="b").json()["id"])
    api.request("PUT", f"/api/v1/sessions/{s['id']}/boards/b/verdict", json={"verdict": "pass"})
    return str(clean["id"])


def test_export_coco(
    api: Api, ctx: ServiceContext, ref_jpeg: bytes, defect_jpeg: bytes, clean_jpeg: bytes, tmp_path: Path
) -> None:
    _feedback(api, ref_jpeg, defect_jpeg, clean_jpeg)
    other = api.create_session(product_code="other")
    api.upload_reference(other["id"], ref_jpeg)
    api.submit(other["id"], clean_jpeg)

    summary = export_coco(ctx, tmp_path / "ds", product_code="exported")
    assert summary.images == 2
    assert summary.annotations == 2
    assert summary.metrics["tp"] == 1
    assert summary.metrics["fn"] == 1
    assert summary.metrics["precision"] == 1.0
    assert summary.metrics["recall"] == 0.5
    coco = json.loads((tmp_path / "ds" / "annotations.json").read_text())
    assert {c["name"] for c in coco["categories"]} >= {"defect", "missing_component"}
    for image in coco["images"]:
        assert (tmp_path / "ds" / image["file_name"]).is_file()
        assert (tmp_path / "ds" / image["reference_file"]).is_file()
        assert (tmp_path / "ds" / image["aligned_file"]).is_file()
    sources = sorted(a["attributes"]["source"] for a in coco["annotations"])
    assert sources == ["auto", "manual"]
    assert json.loads((tmp_path / "ds" / "metrics.json").read_text()) == summary.metrics
    assert {i["board_verdict"] for i in coco["images"]} == {None, "pass"}

    everything = export_coco(ctx, tmp_path / "all")
    assert everything.images == 3


@pytest.fixture
def cli_env(ctx: ServiceContext, monkeypatch: pytest.MonkeyPatch) -> ServiceContext:
    monkeypatch.setattr(cli, "_ctx", lambda: ctx)
    return ctx


def test_cli_apikeys(cli_env: ServiceContext) -> None:
    created = runner.invoke(cli.app, ["apikey", "create", "--station", "line-7"])
    assert created.exit_code == 0, created.output
    raw = created.output.split("key:")[1].strip()
    assert apikeys.authenticate(cli_env, raw).station == "line-7"
    prefix = raw.split("_")[1]
    listed = runner.invoke(cli.app, ["apikey", "list"])
    assert prefix in listed.output
    assert "active" in listed.output
    revoked = runner.invoke(cli.app, ["apikey", "revoke", prefix])
    assert revoked.exit_code == 0
    assert "revoked" in runner.invoke(cli.app, ["apikey", "list"]).output
    assert runner.invoke(cli.app, ["apikey", "revoke", "nope"]).exit_code != 0


def test_cli_export(
    cli_env: ServiceContext, api: Api, ref_jpeg: bytes, defect_jpeg: bytes, clean_jpeg: bytes, tmp_path: Path
) -> None:
    _feedback(api, ref_jpeg, defect_jpeg, clean_jpeg)
    r = runner.invoke(cli.app, ["export", "--out", str(tmp_path / "x"), "--product", "exported"])
    assert r.exit_code == 0, r.output
    assert "images: 2" in r.output


def test_cli_migrate(migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from pcb_inspection.settings import get_settings

    monkeypatch.setenv("PCBIS_DATABASE_URL", migrated_url)
    get_settings.cache_clear()
    try:
        r = runner.invoke(cli.app, ["migrate"])
    finally:
        get_settings.cache_clear()
    assert r.exit_code == 0, r.output


def test_health(api: Api, ctx: ServiceContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert api.client.get("/health/live").json() == {"status": "ok", "checks": {}}
    ready = api.client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "storage": "ok", "queue": "ok"}

    def down() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(ctx.queue, "ping", down)
    not_ready = api.client.get("/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.json()["checks"]["queue"] == "error"


def test_metrics(api: Api) -> None:
    api.client.get("/health/live")
    body = api.client.get("/metrics").text
    assert 'pcbis_http_requests_total{method="GET",route="/health/live",status="200"}' in body
