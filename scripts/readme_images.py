"""Regenerate the README illustration from a synthetic board: uv run python scripts/readme_images.py"""

from pathlib import Path

from pcb_inspection import synthetic as sy
from pcb_inspection.engine.base import InspectParams, MaskSpec
from pcb_inspection.engine.classic.engine import ClassicEngine
from pcb_inspection.engine.crops import CropKind, make_crop
from pcb_inspection.engine.imaging import encode_jpeg

out = Path(__file__).resolve().parents[1] / "docs" / "img"
spec = sy.random_board(1)
engine = ClassicEngine()
ref = engine.prepare_reference(sy.photograph(sy.render(spec), sy.identity_camera(spec)), MaskSpec(), 1200)
photo = sy.photograph(sy.render(spec, sy.Defects(removed=frozenset({3}))), sy.camera(spec, 42))
result = engine.inspect(ref, photo, InspectParams(work_width=1200))
assert result.aligned is not None
crop = make_crop(ref.image, result.aligned, result.differences[0].bbox_ref, CropKind.PAIR, 60, 240)
(out / "crop-pair.jpg").write_bytes(encode_jpeg(crop, 88))
print("written", out / "crop-pair.jpg")
