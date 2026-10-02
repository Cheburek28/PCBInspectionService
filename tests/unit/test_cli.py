from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pcb_inspection import synthetic as sy
from pcb_inspection.cli.main import app
from pcb_inspection.engine.imaging import encode_jpeg
from tests.conftest import WORK_WIDTH, Scene

runner = CliRunner()


def test_openapi_command(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    r = runner.invoke(app, ["openapi", "--out", str(out)])
    assert r.exit_code == 0, r.output
    schema = json.loads(out.read_text())
    assert schema["info"]["title"] == "PCB Inspection Service"
    assert "/api/v1/sessions/{session_id}/inspections" in schema["paths"]


def test_committed_openapi_is_up_to_date(tmp_path: Path) -> None:
    """The contract in the repository must match the code (run `make openapi` after API changes)."""
    out = tmp_path / "openapi.json"
    runner.invoke(app, ["openapi", "--out", str(out)])
    committed = Path(__file__).resolve().parents[2] / "openapi.json"
    assert json.loads(committed.read_text()) == json.loads(out.read_text())


def test_bench_command(scene: Scene, tmp_path: Path) -> None:
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(encode_jpeg(scene.reference, 95))
    good, _ = scene.photo()
    bad, _ = scene.photo(sy.Defects(removed=frozenset({3})))
    (tmp_path / "good.jpg").write_bytes(encode_jpeg(good, 95))
    (tmp_path / "bad.jpg").write_bytes(encode_jpeg(bad, 95))
    r = runner.invoke(
        app,
        [
            "bench",
            str(ref),
            str(tmp_path / "good.jpg"),
            str(tmp_path / "bad.jpg"),
            "--work-width",
            str(WORK_WIDTH),
        ],
        env={"PCBIS_GATE_MIN_WIDTH": "600"},
    )
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0].startswith("good.jpg: ok differences=0")
    assert any(line.startswith("bad.jpg: ok differences=1") for line in lines)
    assert any("score=" in line for line in lines)


def test_help() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for command in ("apikey", "migrate", "export", "openapi", "bench"):
        assert command in r.output
