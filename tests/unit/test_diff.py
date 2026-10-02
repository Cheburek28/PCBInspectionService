"""Difference map normalisation and region extraction."""

from __future__ import annotations

import cv2
import numpy as np

from pcb_inspection.engine.classic.diff import Normalisation, diff_map, find_regions, normalise

GREEN = (40, 110, 35)
OFF = Normalisation(255, 0, 0)
ON = Normalisation(100, 4, 20.0)


def board(h: int = 200, w: int = 300) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = GREEN
    return img


def peak(ref: np.ndarray, test: np.ndarray, n: Normalisation) -> float:
    return float(diff_map(ref, test, 2, n).max())


def test_glare_on_solder_is_ignored() -> None:
    ref, test = board(), board()
    cv2.rectangle(ref, (100, 80), (140, 120), (230, 230, 230), -1)  # shiny pad
    cv2.rectangle(test, (100, 80), (140, 120), (170, 170, 170), -1)  # matte pad
    assert peak(ref, test, OFF) > 20
    assert peak(ref, test, ON) < 5


def test_part_marking_is_removed_by_normalisation() -> None:
    """Thin bright strokes on a dark body disappear from the lightness channel; the body stays."""
    plain = board()
    cv2.rectangle(plain, (60, 50), (240, 150), (30, 30, 32), -1)  # chip body
    marked = plain.copy()
    cv2.putText(marked, "K84", (80, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (160, 160, 160), 1)  # ~2 px strokes
    body = (slice(60, 140), slice(70, 230))
    before = np.abs(normalise(marked, OFF)[..., 0] - normalise(plain, OFF)[..., 0])[body].max()
    after = np.abs(normalise(marked, ON)[..., 0] - normalise(plain, ON)[..., 0])[body].max()
    assert before > 50
    assert after < 1  # strokes thinner than the opening diameter vanish


def test_uniform_tone_change_of_a_body_is_ignored() -> None:
    ref, test = board(), board()
    cv2.rectangle(ref, (60, 50), (240, 150), (30, 30, 32), -1)
    cv2.rectangle(test, (60, 50), (240, 150), (48, 48, 50), -1)  # same body, slightly lighter
    assert peak(ref, test, OFF) > 12
    assert peak(ref, test, ON) < 12


def test_missing_component_survives_normalisation() -> None:
    ref, test = board(), board()
    cv2.rectangle(ref, (120, 80), (160, 110), (30, 30, 32), -1)  # resistor body only on the reference
    assert peak(ref, test, ON) > 30


def test_rotated_component_survives_normalisation() -> None:
    ref, test = board(), board()
    cv2.rectangle(ref, (120, 90), (170, 110), (30, 30, 32), -1)
    pts = cv2.boxPoints(((145, 100), (50, 20), 35)).astype(np.int32)
    cv2.fillPoly(test, [pts], (30, 30, 32))
    assert peak(ref, test, ON) > 20


def _bump(
    peak_value: float, sigma: float, center: tuple[int, int], shape: tuple[int, int] = (200, 300)
) -> np.ndarray:
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return (peak_value * np.exp(-((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / (2 * sigma**2))).astype(
        np.float32
    )


def test_hysteresis_keeps_the_full_extent_of_a_weak_region() -> None:
    dmap = _bump(24.0, 8.0, (100, 100))
    mask = np.ones(dmap.shape, np.uint8)
    single, _ = find_regions(dmap, mask, threshold=20, min_area=40)
    hyst, _ = find_regions(dmap, mask, threshold=20, min_area=40, extent_threshold=12)
    assert len(single) == 1
    assert len(hyst) == 1
    assert hyst[0].area > 3 * single[0].area  # extent measured at 12, not at 20
    assert hyst[0].score >= 20


def test_regions_below_the_reporting_threshold_are_dropped() -> None:
    dmap = _bump(16.0, 10.0, (100, 100)) + _bump(30.0, 10.0, (220, 100))
    regions, _ = find_regions(
        dmap, np.ones(dmap.shape, np.uint8), threshold=20, min_area=40, extent_threshold=12
    )
    assert len(regions) == 1
    assert regions[0].bbox.x > 150


def test_extent_threshold_above_threshold_is_clamped() -> None:
    dmap = _bump(24.0, 8.0, (100, 100))
    regions, _ = find_regions(
        dmap, np.ones(dmap.shape, np.uint8), threshold=20, min_area=10, extent_threshold=30
    )
    assert len(regions) == 1
