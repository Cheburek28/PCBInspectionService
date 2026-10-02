"""Deterministic synthetic PCB photos for tests, demos and smoke checks.

A board is described by a ``BoardSpec`` (generated from a seed) and rendered with optional defects.
A ``Camera`` turns the rendered board into a "photo" (perspective, exposure, blur, noise) and knows
where any board point ends up, which gives exact ground truth for detected differences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.geometry import BBox, Matrix, project_bbox
from pcb_inspection.engine.imaging import BGRImage

SUPERSAMPLE = 2
BOARD_BGR = (38, 104, 30)
TRACE_BGR = (60, 150, 50)
PANEL_BGR = (160, 160, 158)
SLOT_BGR = (200, 40, 50)  # saturated blue (HSV hue ~122, like real fixtures)
COMPONENT_COLORS = {
    "ic": (25, 25, 28),
    "cap": (90, 160, 200),
    "res": (40, 40, 45),
    "led": (40, 40, 210),
}


@dataclass(frozen=True, slots=True)
class Component:
    bbox: BBox  # board pixels
    kind: str
    label: str


@dataclass(frozen=True, slots=True)
class BoardSpec:
    width: int
    height: int
    seed: int
    board_area: BBox  # where the green board is (whole image unless a fixture is drawn)
    components: tuple[Component, ...]
    traces: tuple[tuple[tuple[int, int], ...], ...]
    vias: tuple[tuple[int, int, int], ...]
    blue_fixture: bool = False


@dataclass(frozen=True, slots=True)
class Defects:
    removed: frozenset[int] = frozenset()
    shifted: dict[int, tuple[int, int]] = field(default_factory=dict)
    recolored: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    blobs: tuple[tuple[int, int, int], ...] = ()  # (x, y, radius) solder-like blobs

    def affected(self, spec: BoardSpec) -> list[BBox]:
        """Ground-truth boxes (board pixels) of every defect."""
        boxes = [spec.components[i].bbox for i in sorted(self.removed | set(self.recolored))]
        for i, (dx, dy) in sorted(self.shifted.items()):
            b = spec.components[i].bbox
            x0, y0 = min(b.x, b.x + dx), min(b.y, b.y + dy)
            boxes.append(BBox(x0, y0, b.w + abs(dx), b.h + abs(dy)))
        boxes += [BBox(x - r, y - r, 2 * r, 2 * r) for x, y, r in self.blobs]
        return boxes


@dataclass(frozen=True, slots=True)
class Camera:
    """Maps board pixels to photo pixels and simulates exposure and optics."""

    matrix: Matrix
    out_size: tuple[int, int]
    gain: float = 1.0
    offset: float = 0.0
    blur_sigma: float = 0.6
    noise_sigma: float = 1.5
    seed: int = 0

    def project(self, box: BBox) -> BBox:
        return project_bbox(self.matrix, box)


def random_board(
    seed: int,
    width: int = 1200,
    height: int = 800,
    blue_fixture: bool = False,
    grid: tuple[int, int] = (7, 5),
) -> BoardSpec:
    rng = np.random.default_rng(seed)
    if blue_fixture:
        mx, my = width // 9, height // 9
        area = BBox(mx, my, width - 2 * mx, height - 2 * my)
    else:
        area = BBox(0, 0, width, height)
    cols, rows = grid
    cw, ch = area.w / cols, area.h / rows
    components: list[Component] = []
    for r in range(rows):
        for c in range(cols):
            if rng.random() < 0.15:
                continue
            kind = str(rng.choice(list(COMPONENT_COLORS)))
            w = int(rng.uniform(0.25, 0.6) * cw)
            h = int(rng.uniform(0.2, 0.45) * ch)
            x = int(area.x + c * cw + rng.uniform(0.1, 0.9 - w / cw) * cw)
            y = int(area.y + r * ch + rng.uniform(0.3, 0.9 - h / ch) * ch)
            components.append(Component(BBox(x, y, w, h), kind, f"{kind[0].upper()}{len(components) + 1}"))
    traces = []
    for _ in range(25):
        pts = [(int(rng.uniform(area.x, area.x + area.w)), int(rng.uniform(area.y, area.y + area.h)))]
        for _ in range(3):
            x, y = pts[-1]
            if rng.random() < 0.5:
                x = int(np.clip(x + rng.uniform(-200, 200), area.x, area.x + area.w))
            else:
                y = int(np.clip(y + rng.uniform(-150, 150), area.y, area.y + area.h))
            pts.append((x, y))
        traces.append(tuple(pts))
    vias = tuple(
        (
            int(rng.uniform(area.x, area.x + area.w)),
            int(rng.uniform(area.y, area.y + area.h)),
            int(rng.integers(3, 7)),
        )
        for _ in range(60)
    )
    return BoardSpec(width, height, seed, area, tuple(components), tuple(traces), vias, blue_fixture)


def render(spec: BoardSpec, defects: Defects | None = None) -> BGRImage:
    """Board image at ``SUPERSAMPLE`` x resolution (pass it to ``photograph``)."""
    d = defects or Defects()
    s = SUPERSAMPLE
    rng = np.random.default_rng(spec.seed + 7919)
    img = np.empty((spec.height * s, spec.width * s, 3), np.uint8)
    img[:] = PANEL_BGR if spec.blue_fixture else BOARD_BGR
    a = spec.board_area
    texture = cv2.resize(rng.normal(0, 9, (a.h // 8, a.w // 8)).astype(np.float32), (a.w * s, a.h * s))
    board = np.clip(np.array(BOARD_BGR, np.float32) + texture[..., None], 0, 255).astype(np.uint8)
    img[a.y * s : (a.y + a.h) * s, a.x * s : (a.x + a.w) * s] = board
    if spec.blue_fixture:
        _draw_fixture(img, a, s)
    for pts in spec.traces:
        cv2.polylines(img, [np.array(pts, np.int32) * s], False, TRACE_BGR, 3 * s, cv2.LINE_AA)
    for x, y, r in spec.vias:
        cv2.circle(img, (x * s, y * s), r * s, (60, 170, 200), -1, cv2.LINE_AA)
        cv2.circle(img, (x * s, y * s), max(1, r * s // 2), (20, 40, 20), -1, cv2.LINE_AA)
    for i, comp in enumerate(spec.components):
        if i in d.removed:
            _draw_pads(img, comp.bbox, s)
            continue
        dx, dy = d.shifted.get(i, (0, 0))
        color = d.recolored.get(i, COMPONENT_COLORS[comp.kind])
        _draw_component(
            img,
            replace(comp, bbox=BBox(comp.bbox.x + dx, comp.bbox.y + dy, comp.bbox.w, comp.bbox.h)),
            color,
            s,
        )
    for x, y, r in d.blobs:
        cv2.circle(img, (x * s, y * s), r * s, (200, 200, 205), -1, cv2.LINE_AA)
    return img


def _draw_fixture(img: BGRImage, a: BBox, s: int) -> None:
    gap = 10
    thick = 18
    n = 4
    for side in range(4):
        horizontal = side < 2
        length = a.w if horizontal else a.h
        seg = (length - (n - 1) * gap * 3) / n
        for k in range(n):
            start = int(k * (seg + gap * 3))
            if horizontal:
                y = a.y - gap - thick if side == 0 else a.y + a.h + gap
                p0, p1 = (a.x + start, y), (a.x + start + int(seg), y + thick)
            else:
                x = a.x - gap - thick if side == 2 else a.x + a.w + gap
                p0, p1 = (x, a.y + start), (x + thick, a.y + start + int(seg))
            cv2.rectangle(img, (p0[0] * s, p0[1] * s), (p1[0] * s, p1[1] * s), SLOT_BGR, -1)


def _draw_pads(img: BGRImage, b: BBox, s: int) -> None:
    pad_w = max(2, b.w // 6)
    for x in (b.x, b.x + b.w - pad_w):
        cv2.rectangle(
            img,
            (x * s, (b.y + b.h // 4) * s),
            ((x + pad_w) * s, (b.y + 3 * b.h // 4) * s),
            (150, 170, 180),
            -1,
        )


def _draw_component(img: BGRImage, comp: Component, color: tuple[int, int, int], s: int) -> None:
    b = comp.bbox
    _draw_pads(img, b, s)
    inset = max(2, b.w // 8)
    cv2.rectangle(
        img, ((b.x + inset) * s, b.y * s), ((b.x + b.w - inset) * s, (b.y + b.h) * s), color, -1, cv2.LINE_AA
    )
    if comp.kind == "ic":
        cv2.circle(img, ((b.x + inset + 5) * s, (b.y + 5) * s), 2 * s, (90, 90, 90), -1)
        for k in range(1, 5):
            px = b.x + inset + k * (b.w - 2 * inset) // 5
            cv2.line(img, (px * s, (b.y - 4) * s), (px * s, b.y * s), (190, 190, 190), s)
            cv2.line(img, (px * s, (b.y + b.h) * s), (px * s, (b.y + b.h + 4) * s), (190, 190, 190), s)
    cv2.putText(
        img,
        comp.label,
        (b.x * s, (b.y - 8) * s),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45 * s,
        (235, 235, 235),
        s,
        cv2.LINE_AA,
    )


def camera(
    spec: BoardSpec,
    seed: int,
    rotation_deg: float = 1.5,
    shift_px: float = 12.0,
    perspective: float = 2e-5,
    out_size: tuple[int, int] | None = None,
    gain: float = 1.0,
    offset: float = 0.0,
    blur_sigma: float = 0.6,
    noise_sigma: float = 1.5,
) -> Camera:
    """Random but deterministic small pose change around the board centre."""
    rng = np.random.default_rng(seed)
    w, h = out_size or (spec.width, spec.height)
    cx, cy = spec.width / 2, spec.height / 2
    angle = math.radians(rng.uniform(-rotation_deg, rotation_deg))
    scale = (w / spec.width) * rng.uniform(0.98, 1.02)
    cos, sin = math.cos(angle) * scale, math.sin(angle) * scale
    tx, ty = rng.uniform(-shift_px, shift_px, 2)
    to_center = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], np.float64)
    rot = np.array([[cos, -sin, 0], [sin, cos, 0], [0, 0, 1]], np.float64)
    back = np.array([[1, 0, w / 2 + tx], [0, 1, h / 2 + ty], [0, 0, 1]], np.float64)
    persp = np.eye(3)
    persp[2, :2] = rng.uniform(-perspective, perspective, 2)
    matrix = back @ rot @ persp @ to_center
    return Camera(
        matrix=matrix / matrix[2, 2],
        out_size=(w, h),
        gain=gain,
        offset=offset,
        blur_sigma=blur_sigma,
        noise_sigma=noise_sigma,
        seed=seed,
    )


def identity_camera(spec: BoardSpec) -> Camera:
    return Camera(matrix=np.eye(3), out_size=(spec.width, spec.height))


def photograph(board: BGRImage, cam: Camera) -> BGRImage:
    s = SUPERSAMPLE
    up = np.diag([s, s, 1.0])
    m = up @ cam.matrix @ np.linalg.inv(up)
    w, h = cam.out_size
    big = cv2.warpPerspective(
        board, m, (w * s, h * s), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    img: NDArray[np.float32] = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
    if cam.blur_sigma > 0:
        img = np.asarray(cv2.GaussianBlur(img, (0, 0), cam.blur_sigma), dtype=np.float32)
    img = img * cam.gain + cam.offset
    if cam.noise_sigma > 0:
        img += np.random.default_rng(cam.seed + 1).normal(0, cam.noise_sigma, img.shape).astype(np.float32)
    out: NDArray[np.uint8] = np.clip(img, 0, 255).astype(np.uint8)
    return out
