"""Storing uploaded images (deduplicated by SHA-256) and loading them back."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pcb_inspection.db.models import Image
from pcb_inspection.domain.errors import ImageUnreadable, PayloadTooLarge, UnsupportedMediaType
from pcb_inspection.engine import imaging
from pcb_inspection.services.context import ServiceContext

_EXT = {"image/jpeg": "jpg", "image/png": "png"}


def store_upload(ctx: ServiceContext, s: Session, data: bytes) -> tuple[Image, imaging.DecodedImage]:
    if len(data) > ctx.settings.max_upload_bytes:
        raise PayloadTooLarge(f"upload is {len(data)} bytes, limit is {ctx.settings.max_upload_bytes}")
    content_type = imaging.sniff_content_type(data)
    if content_type is None:
        raise UnsupportedMediaType("only JPEG and PNG images are accepted")
    try:
        decoded = imaging.decode(data)
    except imaging.ImageDecodeError as exc:
        raise ImageUnreadable(str(exc)) from exc
    sha = hashlib.sha256(data).hexdigest()
    key = f"images/{sha[:2]}/{sha}.{_EXT[content_type]}"
    if not ctx.storage.exists(key):
        ctx.storage.put(key, data, content_type)
    s.execute(
        insert(Image)
        .values(
            sha256=sha,
            storage_key=key,
            content_type=content_type,
            width=decoded.width,
            height=decoded.height,
            bytes=len(data),
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    image = s.scalars(select(Image).where(Image.sha256 == sha)).one()
    return image, decoded


def load_pixels(ctx: ServiceContext, image: Image) -> imaging.DecodedImage:
    return imaging.decode(ctx.storage.get(image.storage_key))
