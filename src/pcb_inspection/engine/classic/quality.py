"""Photometric measurements: sharpness, colour shift, colour matching."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.imaging import BGRImage


def sharpness(image: BGRImage, mask: NDArray[np.uint8]) -> float:
    """Mean absolute Laplacian inside the mask."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    selected = lap[mask > 0]
    return float(selected.mean()) if selected.size else 0.0


def lab_shift(test: BGRImage, ref: BGRImage, mask: NDArray[np.uint8]) -> tuple[float, float, float]:
    """Mean L, a, b of the test minus the reference inside the mask (OpenCV 8-bit Lab units)."""
    k = mask > 0
    t = cv2.cvtColor(test, cv2.COLOR_BGR2LAB)[k].astype(np.float32).mean(0)
    r = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB)[k].astype(np.float32).mean(0)
    d = t - r
    return round(float(d[0]), 2), round(float(d[1]), 2), round(float(d[2]), 2)


def color_match(image: BGRImage, ref: BGRImage, mask: NDArray[np.uint8]) -> BGRImage:
    """Match mean and spread of every Lab channel inside the mask (exposure / white balance compensation)."""
    k = mask > 0
    a = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    for c in range(3):
        ma, sa = a[..., c][k].mean(), a[..., c][k].std() + 1e-6
        mb, sb = b[..., c][k].mean(), b[..., c][k].std()
        a[..., c] = (a[..., c] - ma) * (sb / sa) + mb
    return cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.uint8)
