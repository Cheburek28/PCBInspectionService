"""Targeted detectors on a hand-drawn board with exact ground truth."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from pcb_inspection.engine.base import InspectParams, MaskSpec, MaskStrategy, PoolPhoto
from pcb_inspection.engine.classic import detectors as det
from pcb_inspection.engine.classic.detectors.common import to_lab
from pcb_inspection.engine.classic.detectors.dirt import _chains, detect_hairs, detect_specks
from pcb_inspection.engine.classic.detectors.solder_shift import (
    detect_shift,
    detect_solder,
    shift_displacements,
)
from pcb_inspection.engine.classic.engine import ClassicEngine, _new_findings
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.imaging import BGRImage
from tests.conftest import WORK_WIDTH, Scene

GREEN = (60, 110, 40)
BODY = (45, 45, 50)
METAL = (200, 200, 200)
BODIES = [(40 + i * 45, 200 + (i % 2) * 40) for i in range(8)]
PAD = BBox(40, 40, 41, 22)
ALL = InspectParams(detect_solder=True, detect_shift=True, detect_specks=True, detect_hairs=True)


def board(
    shift: tuple[int, int, int] | None = None,
    fillet: bool = True,
    dot: tuple[int, int] | None = None,
    line: bool = False,
) -> BGRImage:
    """Green board: one pad with a fillet shadow, 8 dark part bodies, a flat area on the right."""
    img = np.full((320, 480, 3), GREEN, np.uint8)
    cv2.rectangle(img, (40, 40), (80, 70), METAL, -1)
    if fillet:
        cv2.rectangle(img, (40, 62), (80, 70), (90, 90, 90), -1)
    for i, (x, y) in enumerate(BODIES):
        dx, dy = (shift[1], shift[2]) if shift and shift[0] == i else (0, 0)
        cv2.rectangle(img, (x + dx, y + dy), (x + dx + 24, y + dy + 16), BODY, -1)
    if dot:
        cv2.circle(img, dot, 4, (20, 20, 20), -1)
    if line:
        cv2.line(img, (280, 110), (404, 110), (215, 215, 215), 1, cv2.LINE_AA)
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 0.6), np.uint8)


@pytest.fixture(scope="module")
def model() -> det.ReferenceModel:
    ref = board()
    return det.build_reference_model(ref, np.ones(ref.shape[:2], np.uint8))


def _part(model: det.ReferenceModel, body: int) -> int:
    """Index in the model of the drawn body ``body`` (parts are listed in image scan order)."""
    x, y = BODIES[body]
    return next(i for i, (x0, y0, x1, y1) in enumerate(model.parts) if x0 <= x < x1 and y0 <= y < y1)


def _all(
    model: det.ReferenceModel, img: BGRImage, pool: list[det.PoolMember] | None = None
) -> list[det.Finding]:
    pool = pool or []
    lab = to_lab(img)
    rel, score = shift_displacements(model, img)
    return (
        detect_solder(model, lab, pool, ALL)
        + detect_shift(model, rel, score, pool, ALL)
        + detect_specks(model, lab, pool, ALL)
        + detect_hairs(model, img, pool, ALL)
    )


def test_reference_model_finds_pads_parts_and_flat_area(model: det.ReferenceModel) -> None:
    assert [p.box for p in model.pads] == [PAD]
    assert len(model.parts) == len(BODIES)
    assert model.flat.mean() > 0.8


def test_identical_board_has_no_findings(model: det.ReferenceModel) -> None:
    assert _all(model, board()) == []


def test_missing_fillet_is_found(model: det.ReferenceModel) -> None:
    found = detect_solder(model, to_lab(board(fillet=False)), [], ALL)
    assert [(f.kind, f.bbox) for f in found] == [("solder", PAD)]
    assert found[0].score > ALL.solder_delta


def test_pool_board_with_the_same_look_suppresses_a_finding(model: det.ReferenceModel) -> None:
    """A fillet shape that a passed board also has is normal variation, not a defect."""
    pool = [det.PoolMember(board(fillet=False), None)]
    assert detect_solder(model, to_lab(board(fillet=False)), pool, ALL) == []


@pytest.mark.parametrize(("dx", "expected"), [(6, True), (2, False)])
def test_shifted_part_is_found_only_beyond_the_threshold(
    model: det.ReferenceModel, dx: int, expected: bool
) -> None:
    img = board(shift=(3, dx, 0))
    rel, score = shift_displacements(model, img)
    found = detect_shift(model, rel, score, [], ALL)
    assert bool(found) is expected
    if expected:
        x0, y0, x1, y1 = model.parts[_part(model, 3)]
        assert found[0].bbox == BBox(x0, y0, x1 - x0, y1 - y0)
        assert found[0].score == pytest.approx(dx, abs=1)


def test_part_moving_on_the_pool_boards_is_not_reported(model: det.ReferenceModel) -> None:
    img = board(shift=(3, 6, 0))
    rel, score = shift_displacements(model, img)
    unstable = np.zeros_like(rel)
    unstable[_part(model, 3)] = (5, 0)  # the same part already sits elsewhere on a passed board
    pool = [det.PoolMember(board(), unstable)]
    assert detect_shift(model, rel, score, pool, ALL) == []


def test_speck_on_a_flat_area_is_found(model: det.ReferenceModel) -> None:
    found = detect_specks(model, to_lab(board(dot=(150, 120))), [], ALL)
    assert len(found) == 1
    b = found[0].bbox
    assert b.x <= 150 <= b.x + b.w
    assert b.y <= 120 <= b.y + b.h


def test_straight_new_line_is_not_a_hair(model: det.ReferenceModel) -> None:
    """Straight lines are part edges and traces seen slightly differently, hairs are bent."""
    assert detect_hairs(model, board(line=True), [], ALL) == []


def _frag(x: int, y: int, angle: float) -> det.dirt.Fragment:
    return (BBox(x, y, 30, 6), 30.0, angle, (x + 15.0, y + 3.0))


def test_bent_chain_of_fragments_is_a_hair() -> None:
    found = _chains([_frag(0, 0, 10), _frag(40, 0, 60)], ALL)
    assert [f.kind for f in found] == ["hair"]
    assert found[0].bbox == BBox(0, 0, 70, 6)


def test_straight_or_single_fragment_is_not_a_hair() -> None:
    assert _chains([_frag(0, 0, 10), _frag(40, 0, 12)], ALL) == []  # straight
    assert _chains([_frag(0, 0, 10)], ALL) == []  # one piece
    assert _chains([_frag(0, 0, 10), _frag(200, 0, 60)], ALL) == []  # too far apart to be one hair


def test_finding_touching_a_difference_region_is_dropped() -> None:
    a = det.Finding("speck", BBox(10, 10, 5, 5), 50)
    b = det.Finding("hair", BBox(100, 100, 5, 5), 60)
    dup = det.Finding("solder", BBox(101, 101, 3, 3), 30)
    assert _new_findings([a, b, dup], [BBox(12, 12, 20, 20)]) == [b]


def test_engine_runs_detectors_with_a_pool_and_returns_measures(scene: Scene, engine: ClassicEngine) -> None:
    prepared = engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), WORK_WIDTH)
    params = InspectParams(work_width=WORK_WIDTH, detect_solder=True, detect_shift=True, detect_specks=True)
    first = engine.inspect(prepared, scene.photo()[0], params)
    assert first.measures is not None
    assert len(first.measures["shift_rel"]) > 0
    assert {"det_solder", "det_shift", "det_specks", "detectors_wait"} <= set(first.timings_ms)
    loads: list[int] = []

    def load() -> BGRImage:
        loads.append(1)
        assert first.aligned is not None
        return first.aligned

    pool = [PoolPhoto("first", load, first.measures)]
    r1 = engine.inspect(prepared, scene.photo(seed=7)[0], params, pool)
    r2 = engine.inspect(prepared, scene.photo(seed=8)[0], params, pool)
    assert len(loads) == 1  # the pool photo is decoded once, then cached
    for r in (r1, r2):
        assert {d.kind for d in r.differences} <= {"diff", "solder", "shift", "speck"}
        assert r.quality.differences_count == sum(d.kind == "diff" for d in r.differences)


def test_pool_member_without_measures_is_measured_from_its_warped_photo(
    scene: Scene, engine: ClassicEngine
) -> None:
    prepared = engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), WORK_WIDTH)
    params = InspectParams(work_width=WORK_WIDTH, detect_shift=True)
    first = engine.inspect(prepared, scene.photo()[0], params)
    assert first.aligned is not None
    aligned = first.aligned
    warped: list[int] = []

    def load_warped() -> BGRImage:
        warped.append(1)
        return aligned

    pool = [PoolPhoto("old", lambda: aligned, None, load_warped)]
    engine.inspect(prepared, scene.photo(seed=5)[0], params, pool)
    assert warped == [1]


def test_detectors_are_off_by_default(scene: Scene, engine: ClassicEngine, prepared: object) -> None:
    r = engine.inspect(prepared, scene.photo()[0], InspectParams(work_width=WORK_WIDTH))  # type: ignore[arg-type]
    assert r.measures is None
    assert not any(k.startswith("det") for k in r.timings_ms)


def test_pool_photo_of_another_size_is_skipped(scene: Scene, engine: ClassicEngine) -> None:
    prepared = engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), WORK_WIDTH)
    small = np.zeros((10, 10, 3), np.uint8)
    params = InspectParams(work_width=WORK_WIDTH, detect_specks=True)
    r = engine.inspect(prepared, scene.photo()[0], params, [PoolPhoto("tiny", lambda: small, None)])
    assert r.aligned is not None
