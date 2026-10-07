"""Targeted detectors next to the difference map: missing solder fillet, shifted part, specks, hairs.

All four compare the photo with a *pool*: the batch reference plus the first boards the operator passed
(their photos aligned to the same reference). A finding must differ from every pool member, so whatever
varies between good boards (a lot code, the shape of a fillet) is not reported. Everything that depends
only on the reference is computed once (``ReferenceModel``), everything that depends on a pool photo once
per photo (``PoolMember``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

from pcb_inspection.engine.base import InspectParams
from pcb_inspection.engine.classic.detectors.common import DetectorOutput, Finding, to_lab
from pcb_inspection.engine.classic.detectors.dirt import detect_hairs, detect_specks
from pcb_inspection.engine.classic.detectors.model import PoolMember, ReferenceModel, build_reference_model
from pcb_inspection.engine.classic.detectors.solder_shift import (
    detect_shift,
    detect_solder,
    shift_displacements,
)
from pcb_inspection.engine.imaging import BGRImage

__all__ = [
    "DetectorJob",
    "DetectorOutput",
    "Finding",
    "PoolMember",
    "ReferenceModel",
    "build_reference_model",
]

# per process; an inspection uses up to 3 (the job itself, specks, hairs) next to the engine's own thread
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="detect")
T = TypeVar("T")


def enabled(p: InspectParams) -> bool:
    return p.detect_solder or p.detect_shift or p.detect_specks or p.detect_hairs


class DetectorJob:
    """Runs the enabled detectors in background threads while the engine computes its difference map."""

    def __init__(self, model: ReferenceModel, pool: list[PoolMember], params: InspectParams) -> None:
        self.model, self.pool, self.params = model, pool, params

    def start(self, warped: BGRImage, aligned: BGRImage) -> Future[DetectorOutput]:
        """``warped``: board-wide transform only (homography + ECC); ``aligned``: after local flow."""
        return _executor.submit(self._run, warped, aligned)

    def _run(self, warped: BGRImage, aligned: BGRImage) -> DetectorOutput:
        p, model, pool = self.params, self.model, self.pool
        timings: dict[str, int] = {}

        def timed(name: str, fn: Callable[..., T], *args: Any) -> T:
            t0 = time.perf_counter()
            try:
                return fn(*args)
            finally:
                timings[name] = round((time.perf_counter() - t0) * 1000)

        hairs = (
            _executor.submit(timed, "hairs", detect_hairs, model, aligned, pool, p)
            if p.detect_hairs
            else None
        )
        lab = to_lab(aligned)
        specks = (
            _executor.submit(timed, "specks", detect_specks, model, lab, pool, p) if p.detect_specks else None
        )
        findings: list[Finding] = []
        if p.detect_solder:
            findings += timed("solder", detect_solder, model, lab, pool, p)
        # displacements are always measured: this photo may become a pool member of later boards
        rel, score = timed("shift", shift_displacements, model, warped)
        if p.detect_shift:
            findings += detect_shift(model, rel, score, pool, p)
        if specks is not None:
            findings += specks.result()
        if hairs is not None:
            findings += hairs.result()
        return DetectorOutput(findings, {"shift_rel": rel.round(2).tolist()}, timings)
