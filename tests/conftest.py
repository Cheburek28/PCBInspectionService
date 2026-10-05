from __future__ import annotations

from dataclasses import dataclass

import pytest

from pcb_inspection import synthetic as sy
from pcb_inspection.engine.base import InspectParams, MaskSpec, MaskStrategy, PreparedReference
from pcb_inspection.engine.classic.engine import ClassicEngine
from pcb_inspection.engine.imaging import BGRImage, encode_jpeg
from pcb_inspection.settings import Settings, get_settings

WORK_WIDTH = 1200  # synthetic boards are 1200 px wide; keeps tests fast


@pytest.fixture(autouse=True)
def _ignore_local_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not depend on a developer's .env (e.g. PCBIS_DEFAULT_MASK_STRATEGY=blue_fixture)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()


@dataclass(frozen=True)
class Scene:
    """A synthetic board, its reference photo and helpers to photograph variants."""

    spec: sy.BoardSpec
    reference: BGRImage

    def photo(
        self, defects: sy.Defects | None = None, seed: int = 42, **optics: float
    ) -> tuple[BGRImage, sy.Camera]:
        cam = sy.camera(self.spec, seed, **optics)
        return sy.photograph(sy.render(self.spec, defects), cam), cam

    def truth(self, defects: sy.Defects, cam: sy.Camera) -> list[tuple[object, object]]:
        """(bbox in reference photo, bbox in test photo) for every defect."""
        return [(b, cam.project(b)) for b in defects.affected(self.spec)]


def make_scene(seed: int = 1, blue_fixture: bool = False, **colors: tuple[int, int, int]) -> Scene:
    spec = sy.random_board(seed, blue_fixture=blue_fixture, **colors)
    return Scene(spec, sy.photograph(sy.render(spec), sy.identity_camera(spec)))


@pytest.fixture(scope="session")
def scene() -> Scene:
    return make_scene()


@pytest.fixture(scope="session")
def fixture_scene() -> Scene:
    return make_scene(seed=3, blue_fixture=True)


@pytest.fixture(scope="session")
def engine() -> ClassicEngine:
    return ClassicEngine()


@pytest.fixture(scope="session")
def params() -> InspectParams:
    return InspectParams(work_width=WORK_WIDTH)


@pytest.fixture(scope="session")
def prepared(engine: ClassicEngine, scene: Scene) -> PreparedReference:
    return engine.prepare_reference(scene.reference, MaskSpec(MaskStrategy.FULL_FRAME), WORK_WIDTH)


@pytest.fixture(scope="session")
def jpeg_reference(scene: Scene) -> bytes:
    return encode_jpeg(scene.reference, 95)
