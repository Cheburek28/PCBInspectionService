"""Registration of the test photo onto the reference: SIFT + RANSAC, ECC refinement, optical flow."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from pcb_inspection.engine.geometry import Matrix, affine_to_matrix, is_plausible_board_transform
from pcb_inspection.engine.imaging import BGRImage

MIN_MATCHES = 10
LOWE_RATIO = 0.75
RANSAC_REPROJ_PX = 3.0
MIN_SCALE, MAX_SCALE = 0.8, 1.25


@dataclass(slots=True)
class Features:
    keypoints: tuple[cv2.KeyPoint, ...]
    descriptors: NDArray[np.float32] | None


@dataclass(slots=True)
class Alignment:
    ok: bool
    inliers: int
    # maps working-reference pixels to working-test pixels
    ref_to_test: Matrix | None
    warped: BGRImage | None  # test in the reference frame (homography + ECC), no optical flow


def detect(image: BGRImage, n_features: int, mask: NDArray[np.uint8] | None = None) -> Features:
    """SIFT keypoints; with ``mask`` only where mask > 0."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    roi = None if mask is None else (mask > 0).astype(np.uint8) * 255
    keypoints, descriptors = cv2.SIFT.create(nfeatures=n_features).detectAndCompute(gray, roi)
    return Features(tuple(keypoints), None if descriptors is None else np.asarray(descriptors, np.float32))


def estimate_homography(ref: Features, test: Features) -> tuple[Matrix | None, int]:
    """Homography mapping test pixels to reference pixels and the number of RANSAC inliers."""
    if ref.descriptors is None or test.descriptors is None:
        return None, 0
    if len(ref.keypoints) < MIN_MATCHES or len(test.keypoints) < MIN_MATCHES:
        return None, 0
    pairs = cv2.BFMatcher().knnMatch(test.descriptors, ref.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < LOWE_RATIO * p[1].distance]
    if len(good) < MIN_MATCHES:
        return None, 0
    src = np.array([test.keypoints[g.queryIdx].pt for g in good], dtype=np.float32)
    dst = np.array([ref.keypoints[g.trainIdx].pt for g in good], dtype=np.float32)
    H, inlier_mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_REPROJ_PX)
    if H is None:
        return None, 0
    return H.astype(np.float64), int(inlier_mask.sum())


def refine_ecc(ref: BGRImage, warped: BGRImage) -> Matrix:
    """Sub-pixel affine refinement. Returns W (3x3) with warped(W·x) ≈ ref(x); identity on failure."""
    sm = 0.5
    a = cv2.resize(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), None, fx=sm, fy=sm, interpolation=cv2.INTER_AREA)
    b = cv2.resize(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY), None, fx=sm, fy=sm, interpolation=cv2.INTER_AREA)
    a = cv2.GaussianBlur(a, (0, 0), 1.5)
    b = cv2.GaussianBlur(b, (0, 0), 1.5)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
    everywhere = np.full(a.shape, 255, np.uint8)
    try:
        _, found = cv2.findTransformECC(
            a, b, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE, criteria, everywhere, 5
        )
    except cv2.error:
        return np.eye(3)
    W = np.asarray(found, np.float64)
    W[:, 2] /= sm
    return affine_to_matrix(W)


def align(ref: BGRImage, ref_features: Features, test: BGRImage, n_features: int) -> Alignment:
    H, inliers = estimate_homography(ref_features, detect(test, n_features))
    if H is None or not is_plausible_board_transform(H, MIN_SCALE, MAX_SCALE):
        return Alignment(ok=False, inliers=inliers, ref_to_test=None, warped=None)
    size = (ref.shape[1], ref.shape[0])
    warped = np.asarray(
        cv2.warpPerspective(test, H, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE), np.uint8
    )
    W = refine_ecc(ref, warped)
    if not np.allclose(W, np.eye(3)):
        warped = np.asarray(
            cv2.warpAffine(
                warped,
                W[:2],
                size,
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            ),
            np.uint8,
        )
    # ref x → warped-by-H coords (W·x) → test coords (H⁻¹·W·x)
    ref_to_test = np.linalg.inv(H) @ W
    return Alignment(ok=True, inliers=inliers, ref_to_test=ref_to_test, warped=warped)


def refine_flow(ref: BGRImage, warped: BGRImage) -> BGRImage:
    """Residual local alignment with dense optical flow.

    The flow is heavily smoothed so that real defects are not "explained away" by warping.
    """
    g1 = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (0, 0), 2)
    g2 = cv2.GaussianBlur(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY), (0, 0), 2)
    flow = cv2.calcOpticalFlowFarneback(
        g1, g2, np.zeros((*g1.shape, 2), np.float32), 0.5, 4, 41, 5, 7, 1.5, 0
    )
    flow = cv2.GaussianBlur(flow, (0, 0), 12)
    h, w = g1.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    out = cv2.remap(
        warped, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    return out.astype(np.uint8)
