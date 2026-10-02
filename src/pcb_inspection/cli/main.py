"""``pcbis`` command line: API keys, migrations, dataset export, OpenAPI, benchmark."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from pcb_inspection.queue import RecordingQueue
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.settings import get_settings

app = typer.Typer(help="PCB Inspection Service administration.", no_args_is_help=True)
apikey_app = typer.Typer(help="Manage API keys (one per client station).", no_args_is_help=True)
app.add_typer(apikey_app, name="apikey")


def _ctx() -> ServiceContext:
    from pcb_inspection.services.bootstrap import build_context

    # the CLI never enqueues jobs
    return build_context(get_settings(), queue=RecordingQueue())


@apikey_app.command("create")
def apikey_create(
    station: Annotated[str, typer.Option(help="Name of the client station, e.g. line-1")],
) -> None:
    """Create a key. The raw key is printed once — store it in the client configuration."""
    from pcb_inspection.services.apikeys import create_api_key

    key, raw = create_api_key(_ctx(), station)
    typer.echo(f"station: {key.station}\nprefix:  {key.prefix}\nkey:     {raw}")


@apikey_app.command("list")
def apikey_list() -> None:
    from pcb_inspection.services.apikeys import list_api_keys

    for k in list_api_keys(_ctx()):
        state = f"revoked {k.revoked_at:%Y-%m-%d}" if k.revoked_at else "active"
        typer.echo(f"{k.prefix}  {k.station:<20} {k.created_at:%Y-%m-%d}  {state}")


@apikey_app.command("revoke")
def apikey_revoke(prefix: str) -> None:
    from pcb_inspection.services.apikeys import revoke_api_key

    key = revoke_api_key(_ctx(), prefix)
    typer.echo(f"revoked {key.prefix} ({key.station})")


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(_alembic_ini())), revision)


def _alembic_ini() -> Path:
    for base in (Path.cwd(), Path(__file__).resolve().parents[3]):
        if (base / "alembic.ini").is_file():
            return base / "alembic.ini"
    raise typer.BadParameter("alembic.ini not found (run from the project root)")


@app.command()
def export(
    out: Annotated[Path, typer.Option(help="Output directory")],
    product: Annotated[str | None, typer.Option(help="Only this product_code")] = None,
    since: Annotated[datetime | None, typer.Option(help="Only inspections created at or after")] = None,
) -> None:
    """Export the feedback dataset (COCO annotations + images + metrics.json)."""
    from pcb_inspection.services.export import export_coco

    summary = export_coco(_ctx(), out, product, since)
    typer.echo(f"images: {summary.images}, annotations: {summary.annotations}")
    typer.echo(json.dumps(summary.metrics, indent=1))


@app.command()
def openapi(out: Annotated[Path, typer.Option()] = Path("openapi.json")) -> None:
    """Write the OpenAPI schema (the public contract) to a file."""
    from pcb_inspection.api.app import create_app

    schema = create_app(ctx=None).openapi()
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    typer.echo(f"written {out}")


@app.command()
def bench(
    reference: Annotated[Path, typer.Argument(help="Reference photo")],
    photos: Annotated[list[Path], typer.Argument(help="Photos to compare")],
    work_width: int = 3000,
) -> None:
    """Run the engine directly on local files (no database): differences, quality gates, timings."""
    from pcb_inspection.domain.gates import evaluate
    from pcb_inspection.engine import imaging
    from pcb_inspection.engine.base import MaskSpec
    from pcb_inspection.engine.registry import get_engine

    settings = get_settings()
    engine = get_engine(settings.engine)
    ref = engine.prepare_reference(
        imaging.decode(reference.read_bytes()).pixels, MaskSpec(settings.default_mask_strategy), work_width
    )
    params = replace(settings.inspect_params(), work_width=work_width)
    for photo in photos:
        result = engine.inspect(ref, imaging.decode(photo.read_bytes()).pixels, params)
        rejection = evaluate(result.quality, settings.thresholds())
        verdict = rejection.code.value if rejection else "ok"
        typer.echo(
            f"{photo.name}: {verdict} differences={len(result.differences)} "
            f"inliers={result.quality.alignment_inliers} sharpness={result.quality.sharpness_ratio} "
            f"lab_shift={result.quality.lab_shift} total_ms={result.timings_ms.get('total')}"
        )
        for d in result.differences[:10]:
            typer.echo(f"    score={d.score:6.1f} test={d.bbox_test.to_dict()}")


if __name__ == "__main__":
    app()
