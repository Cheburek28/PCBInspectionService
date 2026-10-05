"""Masks of the area that is inspected (1) versus ignored (0), at working resolution."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.base import MaskNotFoundError, MaskSpec, MaskStrategy
from pcb_inspection.engine.imaging import BGRImage

Mask = NDArray[np.uint8]

MIN_COVERAGE = 0.03
SLOT_MIN_AREA = 0.0005  # fraction of the frame; smaller blobs are not fixture slots
# solder mask: OpenCV hue 35-100 covers yellow-green to teal; blue fixtures sit at ~120
GREEN_HUE_RANGE = (35, 100)
GREEN_HUE_TOL = 10  # solder mask spans ~±8 around its peak; the fixture fringe starts ~+15
# erosion radii (px at 3000 px width) that cut the bridges between the board and the panel
BRIDGE_RADII = (30, 50, 80, 120, 170)
BG_L_WEIGHT = 0.5
SAME_ISLAND_IOU = 0.95
ANALYSIS_WIDTH = 1500  # green_board runs at this width: same result, a quarter of the work


def build_mask(image: BGRImage, spec: MaskSpec, scale: float) -> Mask:
    """``scale`` converts uploaded-image pixels (polygon vertices) to working pixels."""
    if spec.strategy is MaskStrategy.FULL_FRAME:
        mask = np.ones(image.shape[:2], np.uint8)
    elif spec.strategy is MaskStrategy.POLYGON:
        mask = polygon_mask(image.shape[:2], spec.polygon, scale)
    elif spec.strategy is MaskStrategy.GREEN_BOARD:
        mask = green_board_mask(image)
    else:
        mask = blue_fixture_mask(image)
    mask = _erode(mask, spec.margin_px)
    if mask.mean() < MIN_COVERAGE:
        raise MaskNotFoundError(
            f"inspected area covers {mask.mean():.1%} of the frame (< {MIN_COVERAGE:.0%})"
        )
    return mask


def polygon_mask(shape: tuple[int, ...], polygon: tuple[tuple[float, float], ...], scale: float) -> Mask:
    if len(polygon) < 3:
        raise MaskNotFoundError("polygon mask needs at least 3 vertices")
    mask = np.zeros(shape[:2], np.uint8)
    pts = np.round(np.asarray(polygon, dtype=np.float64) * scale).astype(np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask


def blue_fixture_mask(image: BGRImage) -> Mask:
    """Board island surrounded by blue slots of the holding fixture."""
    island = _island_inside_slots(_slot_pixels(image))
    if island is None:
        raise MaskNotFoundError("no board island surrounded by blue fixture slots was found")
    return island


def green_board_mask(image: BGRImage) -> Mask:
    """Board island surrounded by slots of any colour except the board's own green.

    The solder mask colour is measured on the image; every large non-green blob is either a slot (the
    fixture seen through it, whatever its colour) or a component. Slots border the panel, components
    border only the board: the panel is the green that reaches the frame edge once the narrow bridges
    to the board are cut by erosion. The fixture colour is then measured on the slots, so components
    merged with a slot are dropped from it. Every bridge radius gives one candidate island and the one
    found at most radii wins (ties: the larger). Wrong islands are unstable: a small radius lets the
    panel swallow the board's rim and yields just the inside of a silkscreen outline, a radius that
    misses some slots lets the island spill over them.

    Runs at ``ANALYSIS_WIDTH``; the mask is scaled back to the image.
    """
    h, w = image.shape[:2]
    small = image
    if w > ANALYSIS_WIDTH:
        size = (ANALYSIS_WIDTH, round(h * ANALYSIS_WIDTH / w))
        small = np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), np.uint8)
    mask = _green_board_island(small)
    if mask is None:
        raise MaskNotFoundError("no green board island surrounded by slots or background was found")
    if mask.shape != (h, w):
        mask = (cv2.resize(mask * 255, (w, h), interpolation=cv2.INTER_LINEAR) >= 128).astype(np.uint8)
    return mask


def _green_board_island(image: BGRImage) -> Mask | None:
    w = image.shape[1]
    barrier = _non_board_pixels(image)
    solid = (barrier == 0).astype(np.uint8)
    n_blobs, raw_blobs = cv2.connectedComponents(barrier, connectivity=8)
    blobs = np.asarray(raw_blobs, np.int32)
    on_edge = np.zeros(n_blobs, bool)  # edge-connector fingers, fixture corners: not the slot ring
    on_edge[np.unique(np.concatenate([blobs[0], blobs[-1], blobs[:, 0], blobs[:, -1]]))] = True
    depth = cv2.distanceTransform(solid, cv2.DIST_L2, 3)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    seen: dict[bytes, int | None] = {}  # slot set -> index of its island in ``islands``
    islands: list[tuple[Mask, int]] = []  # (island, votes)
    for r in BRIDGE_RADII:
        radius = max(2.0, r * w / 3000)
        panel = _panel(depth, radius) & (solid > 0)
        touch = cv2.dilate(panel.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        touch[[0, -1], :] = True  # background around a board without a panel touches the frame edge
        touch[:, [0, -1]] = True
        hit = np.zeros(n_blobs, bool)
        hit[np.unique(blobs[touch & (barrier > 0)])] = True
        hit[0] = False
        key = hit.tobytes()
        if key not in seen:
            seen[key] = _vote(islands, _slot_island(lab, blobs, hit, on_edge))
        elif (i := seen[key]) is not None:
            islands[i] = (islands[i][0], islands[i][1] + 1)
    if not islands:
        return None
    best, _ = max(islands, key=lambda iv: (iv[1], int(iv[0].sum())))
    # restoring the edges can leave slivers of panel along a slot's outer side, joined to the island at
    # the bridges; they would turn the slot into a hole and get it filled
    sliver = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    return _fill_holes(_largest(np.asarray(cv2.morphologyEx(best, cv2.MORPH_OPEN, sliver), np.uint8)))


def _largest(mask: Mask) -> Mask:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    if n <= 1:
        return mask
    return np.asarray(labels == 1 + int(np.argmax(stats[1:, 4])), np.uint8)


def _slot_island(
    lab: NDArray[np.float32], blobs: NDArray[np.int32], hit: NDArray[np.bool_], on_edge: NDArray[np.bool_]
) -> Mask | None:
    ring = hit & ~on_edge
    fixture = _fixture_colour(lab, (ring if ring[1:].any() else hit)[blobs])
    if fixture is None:
        return None
    return _island_inside_slots(_large_blobs(_close(fixture & hit[blobs])))


def _vote(islands: list[tuple[Mask, int]], island: Mask | None) -> int | None:
    """Count ``island`` for the candidate it matches (IoU), or add it; returns the candidate's index."""
    if island is None:
        return None
    for i, (other, votes) in enumerate(islands):
        union = np.count_nonzero(island | other)
        if union and np.count_nonzero(island & other) / union >= SAME_ISLAND_IOU:
            islands[i] = (other, votes + 1)
            return i
    islands.append((island, 1))
    return len(islands) - 1


def _fixture_colour(lab: NDArray[np.float32], slots: NDArray[np.bool_]) -> NDArray[np.bool_] | None:
    """Pixels of the fixture's colour, measured as the median colour of the slot blobs off the frame edge.

    A component standing right at the board edge merges with the slot next to it into one non-green
    blob; its colour differs from the fixture's, so it is dropped and stays part of the board.
    """
    px = lab[slots]
    if len(px) == 0:
        return None
    rng = np.random.default_rng(0)
    sample = px[rng.choice(len(px), min(len(px), 50000), replace=False)]
    centre = np.median(sample, axis=0)
    weights = np.array([BG_L_WEIGHT, 1.0, 1.0], np.float32)  # hatching and shading vary lightness most
    # median, not a high percentile: components merged with the slots must not widen the range
    spread = float(np.median(np.linalg.norm((sample - centre) * weights, axis=1)))
    radius = float(np.clip(spread * 2.5, 10, 35))
    return np.asarray(np.linalg.norm((lab - centre) * weights, axis=2) <= radius, bool)


def _close(pixels: NDArray[np.bool_]) -> NDArray[np.uint8]:
    """Fill the hatching of textured fixtures."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return np.asarray(cv2.morphologyEx(pixels.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel), np.uint8)


def _panel(depth: NDArray[np.float32], radius: float) -> NDArray[np.bool_]:
    """Green connected to the frame edge after an erosion by ``radius``, grown back by the same radius."""
    core = (depth > radius).astype(np.uint8)
    n, labels = cv2.connectedComponents(core, connectivity=4)
    edge = np.zeros(n, bool)
    edge[np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))] = True
    edge[0] = False
    seed = np.asarray(edge[labels], bool)
    if not seed.any():
        return seed
    grown = cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 3) <= radius + 2
    return np.asarray(grown, bool)


def _island_inside_slots(blue: NDArray[np.uint8]) -> Mask | None:
    """Bridges between the slots are closed with growing kernels until an island that does not touch
    the frame edge separates from the panel frame.
    """
    h, w = blue.shape[:2]
    best: tuple[Mask, int, int] | None = None
    # kernel sizes were tuned at 3000 px width; scale them with the image
    k_scale = w / 3000
    for k in (61, 101, 151, 201, 261, 331):
        ks = max(3, int(k * k_scale) | 1)
        closed = cv2.morphologyEx(
            blue, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
        )
        n, labels, stats, _ = cv2.connectedComponentsWithStats((closed == 0).astype(np.uint8), connectivity=4)
        for i in range(1, n):
            x, y, bw, bh, area = (int(v) for v in stats[i])
            if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
                continue  # touches the frame edge: this is the panel frame, not the board
            if area < MIN_COVERAGE * h * w:
                continue
            if best is None or area > best[1]:
                best = ((labels == i).astype(np.uint8), area, ks)
        if best is not None:
            break
    if best is None:
        return None
    # restore at least as far as the strongest closing could have eaten, whatever kernel separated the island
    return _restore_island_edges(best[0], blue, max(best[2], int(261 * k_scale) | 1))


def _slot_pixels(image: BGRImage) -> NDArray[np.uint8]:
    """Saturated blue of the fixture seen through the slots.

    Solder mask can be blue-green (hue ~100-110) and fairly saturated, so the range is kept tight and
    only large blobs survive: slots are big, stray board pixels are not.
    """
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (110, 110, 70), (135, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(blue, connectivity=8)
    keep = np.zeros(n, np.uint8)
    keep[1:] = stats[1:, 4] >= SLOT_MIN_AREA * h * w
    slots: NDArray[np.uint8] = (keep[labels] * 255).astype(np.uint8)
    return slots


def _non_board_pixels(image: BGRImage) -> NDArray[np.uint8]:
    """Everything that is not the dominant green of the solder mask, as large blobs only."""
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lo, hi = GREEN_HUE_RANGE
    vivid = (sat >= 60) & (val >= 40)
    hist = np.bincount(np.asarray(hue[vivid & (hue >= lo) & (hue <= hi)], np.int64).ravel(), minlength=180)
    if hist.sum() < MIN_COVERAGE * h * w:
        raise MaskNotFoundError("no green solder mask in the image")
    peak = int(np.argmax(hist))
    green = (np.abs(hue.astype(np.int16) - peak) <= GREEN_HUE_TOL) & (sat >= 40) & (val >= 30)
    return _large_blobs(np.where(green, 0, 255).astype(np.uint8))


def _large_blobs(pixels: NDArray[np.uint8]) -> NDArray[np.uint8]:
    h, w = pixels.shape[:2]
    pixels = np.asarray(cv2.morphologyEx(pixels, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)), np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(pixels, connectivity=8)
    keep = np.zeros(n, np.uint8)
    keep[1:] = stats[1:, 4] >= SLOT_MIN_AREA * h * w
    blobs: NDArray[np.uint8] = (keep[labels] * 255).astype(np.uint8)
    return blobs


def _fill_holes(mask: Mask) -> Mask:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled


def _restore_island_edges(island: Mask, blue: NDArray[np.uint8], ks: int) -> Mask:
    """Closing rounds the island's corners and cuts them off unevenly from photo to photo.

    Grow the rough island back up to the inner edges of the slots, but never past the ring of slots
    that surrounds it (the convex hull of the nearby slot pixels): the panel outside stays excluded,
    bridges between neighbouring slots are kept as part of the board.
    """
    h, w = island.shape
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
    near = cv2.dilate(island, grow)
    ring = np.argwhere((blue > 0) & (near > 0))
    hull = np.zeros((h, w), np.uint8)
    if len(ring) >= 3:
        cv2.fillPoly(hull, [cv2.convexHull(ring[:, ::-1].astype(np.int32))], 1)
    else:
        hull[:] = 1
    candidate = ((blue == 0) & (near > 0) & (hull > 0)).astype(np.uint8)
    n, labels = cv2.connectedComponents(candidate, connectivity=4)
    if n <= 1:
        return island
    overlap = np.bincount(labels[island > 0].ravel(), minlength=n)
    overlap[0] = 0
    restored: Mask = (np.asarray(labels) == int(np.argmax(overlap))).astype(np.uint8)
    return restored


def _erode(mask: Mask, margin_px: int) -> Mask:
    if margin_px <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))
    # a full-frame mask must shrink from the image border too
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    return cv2.erode(padded, kernel)[1:-1, 1:-1].astype(np.uint8)
