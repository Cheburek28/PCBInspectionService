"""Binary blob storage behind one small interface: local filesystem or S3-compatible."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcb_inspection.storage.base import BlobNotFound, BlobStorage
from pcb_inspection.storage.local import LocalStorage

if TYPE_CHECKING:
    from pcb_inspection.settings import Settings

__all__ = ["BlobNotFound", "BlobStorage", "LocalStorage", "build_storage"]


def build_storage(settings: Settings) -> BlobStorage:
    if settings.storage_backend == "s3":
        from pcb_inspection.storage.s3 import (
            S3Storage,
        )

        return S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    return LocalStorage(settings.storage_path)
