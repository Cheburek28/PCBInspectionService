"""Images derived from inspections: original, aligned, heatmap and difference crops."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pcb_inspection.domain.errors import InvalidState, NotFound
from pcb_inspection.engine import imaging
from pcb_inspection.engine.crops import CropKind, make_crop
from pcb_inspection.engine.geometry import BBox
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


def crop(
    ctx: ServiceContext,
    inspection_id: uuid.UUID,
    defect_id: uuid.UUID,
    kind: CropKind,
    pad: int,
    height: int,
) -> bytes:
    cache_key = f"cache/crops/{defect_id}_{kind}_{pad}_{height}.jpg"
    try:
        return ctx.storage.get(cache_key)
    except BlobNotFound:
        pass
    defect = defects.get_defect(ctx, inspection_id, defect_id)
    insp = inspections.get(ctx, inspection_id)
    if insp.aligned_storage_key is None:
        raise InvalidState("inspection has no aligned image")
    ref = references.prepared(ctx, insp.reference)
    aligned = imaging.decode(ctx.storage.get(insp.aligned_storage_key)).pixels
    bbox_work = BBox(defect.ref_x, defect.ref_y, defect.ref_w, defect.ref_h).scaled(ref.scale)
    h, w = ref.image.shape[:2]
    clipped = bbox_work.clip(w, h) or BBox(0, 0, 1, 1)
    pad_work = max(1, round(pad * ref.scale))
    img = make_crop(ref.image, aligned, clipped, kind, pad_work, height)
    data = imaging.encode_jpeg(img, 90)
    ctx.storage.put(cache_key, data, "image/jpeg")
    return data
