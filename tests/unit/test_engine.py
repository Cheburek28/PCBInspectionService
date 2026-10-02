"""Behaviour of classic-diff on synthetic boards with exact ground truth."""

from __future__ import annotations

import numpy as np
import pytest

from pcb_inspection import synthetic as sy
from pcb_inspection.domain.enums import RejectionCode
from pcb_inspection.domain.gates import QualityThresholds, evaluate
from pcb_inspection.engine.base import (
    Difference,
    EngineResult,
    InspectParams,
    MaskSpec,
    MaskStrategy,
    PreparedReference,
)
from pcb_inspection.engine.classic.engine import ClassicEngine, map_test_bbox_to_ref
from pcb_inspection.engine.geometry import BBox
from pcb_inspection.engine.registry import available_engines, get_engine
from tests.conftest import WORK_WIDTH, Scene

GATES = QualityThresholds(min_width=600)


def _best_match(diffs: list[Difference], truth_test: BBox) -> Difference:
    return max(diffs, key=lambda d: d.bbox_test.iou(truth_test))


def _run(
    engine: ClassicEngine, prepared: PreparedReference, image: np.ndarray, params: InspectParams
) -> EngineResult:
    return engine.inspect(prepared, image, params)


def test_reference_itself_has_no_differences(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    r = _run(engine, prepared, scene.reference, params)
    assert r.quality.same_as_reference
    assert r.differences == []
    assert evaluate(r.quality, GATES) is None


def test_moved_camera_and_light_noise_give_no_differences(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    image, _ = scene.photo(gain=1.05, offset=-3)
    r = _run(engine, prepared, image, params)
    assert r.quality.alignment_ok
    assert r.differences == []
    assert evaluate(r.quality, GATES) is None


def test_removed_component_is_found_in_both_frames(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    defects = sy.Defects(removed=frozenset({3}))
    image, cam = scene.photo(defects)
    r = _run(engine, prepared, image, params)
    assert len(r.differences) == 1
    ((truth_ref, truth_test),) = scene.truth(defects, cam)
    d = r.differences[0]
    assert d.bbox_ref.iou(truth_ref) >= 0.5
    assert d.bbox_test.iou(truth_test) >= 0.5
    assert d.score > params.threshold
    assert evaluate(r.quality, GATES) is None


def test_several_defects_are_ranked_by_score(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    defects = sy.Defects(removed=frozenset({3}), recolored={10: (30, 30, 220)}, blobs=((300, 400, 10),))
    image, cam = scene.photo(defects)
    r = _run(engine, prepared, image, params)
    for _, truth_test in scene.truth(defects, cam):
        assert _best_match(r.differences, truth_test).bbox_test.iou(truth_test) >= 0.3
    scores = [d.score for d in r.differences]
    assert scores == sorted(scores, reverse=True)


def test_blurry_photo_is_rejected(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    image, _ = scene.photo(blur_sigma=3.0)
    rejection = evaluate(_run(engine, prepared, image, params).quality, GATES)
    assert rejection is not None
    assert rejection.code is RejectionCode.IMAGE_BLURRY


def test_other_board_fails_alignment(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    other = sy.random_board(99)
    image = sy.photograph(sy.render(other), sy.camera(other, 5))
    r = _run(engine, prepared, image, params)
    assert r.ref_to_test is None
    assert r.aligned is None
    rejection = evaluate(r.quality, GATES)
    assert rejection is not None
    assert rejection.code is RejectionCode.ALIGNMENT_FAILED


def test_pure_noise_fails_alignment(
    engine: ClassicEngine, prepared: PreparedReference, params: InspectParams
) -> None:
    noise = np.random.default_rng(0).integers(0, 255, (800, 1200, 3), dtype=np.uint8)
    r = _run(engine, prepared, noise, params)
    assert not r.quality.alignment_ok


def test_overexposed_photo_is_rejected_for_lighting(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    image, _ = scene.photo(gain=1.4)
    rejection = evaluate(_run(engine, prepared, image, params).quality, GATES)
    assert rejection is not None
    assert rejection.code is RejectionCode.LIGHTING_MISMATCH


def test_many_changes_are_rejected(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    defects = sy.Defects(removed=frozenset(range(0, len(scene.spec.components), 2)))
    image, _ = scene.photo(defects)
    rejection = evaluate(_run(engine, prepared, image, params).quality, GATES)
    assert rejection is not None
    assert rejection.code is RejectionCode.TOO_MANY_DIFFERENCES


def test_photo_at_another_resolution_maps_to_its_own_pixels(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    """Uploading a 2x photo returns test boxes in 2x pixels."""
    defects = sy.Defects(removed=frozenset({3}))
    cam = sy.camera(scene.spec, 42, out_size=(2400, 1600))
    image = sy.photograph(sy.render(scene.spec, defects), cam)
    r = _run(engine, prepared, image, params)
    ((truth_ref, truth_test),) = scene.truth(defects, cam)
    best = _best_match(r.differences, truth_test)
    assert best.bbox_test.iou(truth_test) >= 0.5
    assert best.bbox_ref.iou(truth_ref) >= 0.5


def test_manual_box_maps_back_to_reference(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    image, cam = scene.photo()
    r = _run(engine, prepared, image, params)
    assert r.ref_to_test is not None
    component = scene.spec.components[5].bbox
    mapped = map_test_bbox_to_ref(r.ref_to_test, cam.project(component), (1200, 800))
    assert mapped is not None
    assert mapped.iou(component) >= 0.7


def test_result_carries_images_and_timings(
    engine: ClassicEngine, prepared: PreparedReference, scene: Scene, params: InspectParams
) -> None:
    image, _ = scene.photo()
    r = _run(engine, prepared, image, params)
    assert r.aligned is not None
    assert r.aligned.shape == prepared.image.shape
    assert r.heatmap is not None
    assert r.heatmap.shape == prepared.image.shape
    assert {"align", "diff", "total"} <= set(r.timings_ms)


def test_features_are_cached_on_the_reference(
    engine: ClassicEngine, scene: Scene, params: InspectParams
) -> None:
    ref = engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), WORK_WIDTH)
    assert ref.features is None
    engine.inspect(ref, scene.reference, params)
    cached = ref.features
    assert cached is not None
    engine.inspect(ref, scene.reference, params)
    assert ref.features is cached


def test_registry() -> None:
    assert available_engines() == ["classic-diff"]
    assert get_engine().name == "classic-diff"
    with pytest.raises(ValueError, match="unknown engine"):
        get_engine("nope")


def test_small_reference_is_not_upscaled(engine: ClassicEngine, scene: Scene) -> None:
    ref = engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), 3000)
    assert ref.scale == 1.0
    assert ref.image.shape[1] == 1200
