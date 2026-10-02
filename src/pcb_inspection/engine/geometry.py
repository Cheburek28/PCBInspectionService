"""Bounding boxes and projective transforms between image coordinate systems."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Matrix = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned box in integer pixels; (x, y) is the top-left corner."""

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if self.w <= 0 or self.h <= 0:
            raise ValueError(f"bbox must have positive size, got {self.w}x{self.h}")

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def corners(self) -> NDArray[np.float64]:
        x0, y0, x1, y1 = self.x, self.y, self.x + self.w, self.y + self.h
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)

    def clip(self, width: int, height: int) -> BBox | None:
        """Intersect with the image rectangle; None if nothing is left."""
        x0, y0 = max(self.x, 0), max(self.y, 0)
        x1, y1 = min(self.x + self.w, width), min(self.y + self.h, height)
        if x1 <= x0 or y1 <= y0:
            return None
        return BBox(x0, y0, x1 - x0, y1 - y0)

    def scaled(self, factor: float) -> BBox:
        x0, y0 = self.x * factor, self.y * factor
        x1, y1 = (self.x + self.w) * factor, (self.y + self.h) * factor
        return BBox.from_bounds(x0, y0, x1, y1)

    def iou(self, other: BBox) -> float:
        ix0, iy0 = max(self.x, other.x), max(self.y, other.y)
        ix1 = min(self.x + self.w, other.x + other.w)
        iy1 = min(self.y + self.h, other.y + other.h)
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        return inter / float(self.area + other.area - inter)

    @staticmethod
    def from_bounds(x0: float, y0: float, x1: float, y1: float) -> BBox:
        """Smallest integer box that contains the float bounds."""
        ix0, iy0 = int(np.floor(x0)), int(np.floor(y0))
        ix1, iy1 = int(np.ceil(x1)), int(np.ceil(y1))
        return BBox(ix0, iy0, max(1, ix1 - ix0), max(1, iy1 - iy0))

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def scale_matrix(factor: float) -> Matrix:
    return np.diag([factor, factor, 1.0])


def project_points(matrix: Matrix, points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Apply a 3x3 projective transform to an (N, 2) array of points."""
    homogeneous = np.hstack([points, np.ones((len(points), 1))]) @ matrix.T
    w = homogeneous[:, 2:3]
    if np.any(np.abs(w) < 1e-12):
        raise ValueError("transform maps a point to infinity")
    return homogeneous[:, :2] / w


def project_bbox(matrix: Matrix, box: BBox) -> BBox:
    """Bounding box of the projected corners of ``box``."""
    pts = project_points(matrix, box.corners)
    return BBox.from_bounds(pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())


def affine_to_matrix(affine: NDArray[np.floating]) -> Matrix:
    m = np.eye(3)
    m[:2, :] = affine
    return m


def is_plausible_board_transform(matrix: Matrix, min_scale: float, max_scale: float) -> bool:
    """Reject degenerate homographies: mirrored, strongly scaled or with strong perspective."""
    if not np.all(np.isfinite(matrix)) or abs(matrix[2, 2]) < 1e-12:
        return False
    m = matrix / matrix[2, 2]
    linear = m[:2, :2]
    if np.linalg.det(linear) <= 0:
        return False
    singular = np.linalg.svd(linear, compute_uv=False)
    if singular.min() < min_scale or singular.max() > max_scale:
        return False
    return bool(np.abs(m[2, :2]).max() < 1e-3)
