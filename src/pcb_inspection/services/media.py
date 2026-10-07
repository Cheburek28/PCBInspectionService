"""Images derived from inspections: original, aligned, heatmap and difference crops."""

from __future__ import annotations

import uuid
from enum import StrEnum

import cv2
import numpy as np

from pcb_inspection.db.models import Inspection
from pcb_inspection.domain.errors import InvalidState, NotFound
from pcb_inspection.engine import imaging
from pcb_inspection.engine.crops import CropKind, make_crop
from pcb_inspection.engine.geometry import BBox, scale_matrix
from pcb_inspection.engine.imaging import BGRImage
from pcb_inspection.services import defects, inspections, references
from pcb_inspection.services.context import ServiceContext
from pcb_inspection.storage import BlobNotFound


class ImageKind(StrEnum):
    ORIGINAL = "original"
    ALIGNED = "aligned"
    HEATMAP = "heatmap"


def inspection_image(ctx: ServiceContext, inspection_id: uuid.UUID, kind: ImageKind) -> tuple[bytes, str]:
    insp = inspections.get(ctx, inspection_id)
    if kind is ImageKind.ORIGINAL:
        return ctx.storage.get(insp.image.storage_key), insp.image.content_type
    key = insp.aligned_storage_key if kind is ImageKind.ALIGNED else insp.heatmap_storage_key
    if key is None:
        raise NotFound(f"inspection {inspection_id} has no {kind} image (status {insp.status})")
    return ctx.storage.get(key), "image/jpeg"


def reference_mask_view(ctx: ServiceContext, inspection_id: uuid.UUID) -> bytes:
    """Reference at working resolution with everything outside the inspected area darkened."""
    insp = inspections.get(ctx, inspection_id)
    ref = references.prepared(ctx, insp.reference)
    view = ref.image.copy()
    view[ref.mask == 0] //= 4
    return imaging.encode_jpeg(view, 85)


def crop(
    ctx: ServiceContext,
    inspection_id: uuid.UUID,
    defect_id: uuid.UUID,
    kind: CropKind,
    pad: int,
    height: int,
) -> bytes:
    cache_key = f"cache/crops/v2/{defect_id}_{kind}_{pad}_{height}.jpg"
    try:
        return ctx.storage.get(cache_key)
    except BlobNotFound:
        pass
    defect = defects.get_defect(ctx, inspection_id, defect_id)
    insp = inspections.get(ctx, inspection_id)
    if insp.aligned_storage_key is None:
        raise InvalidState("inspection has no aligned image")
    ref = references.prepared(ctx, insp.reference)
    aligned = photo_in_reference_frame(ctx, insp, ref.image.shape, ref.scale)
    if aligned is None:
        aligned = imaging.decode(ctx.storage.get(insp.aligned_storage_key)).pixels
    bbox_work = BBox(defect.ref_x, defect.ref_y, defect.ref_w, defect.ref_h).scaled(ref.scale)
    h, w = ref.image.shape[:2]
    clipped = bbox_work.clip(w, h) or BBox(0, 0, 1, 1)
    pad_work = max(1, round(pad * ref.scale))
    img = make_crop(ref.image, aligned, clipped, kind, pad_work, height)
    data = imaging.encode_jpeg(img, 90)
    ctx.storage.put(cache_key, data, "image/jpeg")
    return data


def photo_in_reference_frame(
    ctx: ServiceContext, insp: Inspection, shape: tuple[int, ...], ref_scale: float
) -> BGRImage | None:
    """The original photo brought into the reference frame by the board-wide transform only.

    The stored aligned image also carries the local optical-flow correction; where the flow goes wrong
    (uniform IC bodies, rows of identical leads) it bends edges that are straight on the photo. The
    operator must see the board as photographed, so crops use the global transform (homography + ECC),
    which keeps reference and photo in register without local warping.
    """
    if not insp.transform or "ref_to_test" not in insp.transform:
        return None
    ref_to_test = np.asarray(insp.transform["ref_to_test"], dtype=np.float64)
    photo = imaging.decode(ctx.storage.get(insp.image.storage_key)).pixels
    # working reference pixel -> uploaded reference pixel -> uploaded test pixel
    work_to_test = ref_to_test @ scale_matrix(1 / ref_scale)
    h, w = shape[:2]
    warped = cv2.warpPerspective(
        photo,
        work_to_test,
        (w, h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return np.asarray(warped, np.uint8)
