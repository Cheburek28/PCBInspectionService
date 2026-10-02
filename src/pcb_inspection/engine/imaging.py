"""Decoding and resizing images. Orientation rule: EXIF orientation is always applied on decode."""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

BGRImage = NDArray[np.uint8]

_SIGNATURES: dict[bytes, str] = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}

# decompression-bomb guard: 100 megapixels is far above any camera we expect
Image.MAX_IMAGE_PIXELS = 100_000_000


class ImageDecodeError(ValueError):
    """The bytes are not a supported, readable image."""


@dataclass(frozen=True, slots=True)
class DecodedImage:
    pixels: BGRImage

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])


def sniff_content_type(data: bytes) -> str | None:
    """Content type by magic bytes (never trust the file extension or the client header)."""
    for signature, content_type in _SIGNATURES.items():
        if data.startswith(signature):
            return content_type
    return None


def decode(data: bytes) -> DecodedImage:
    """Decode JPEG/PNG bytes into a BGR uint8 array with EXIF orientation applied."""
    if sniff_content_type(data) is None:
        raise ImageDecodeError("unsupported image format (expected JPEG or PNG)")
    try:
        with Image.open(io.BytesIO(data)) as img:
            upright = ImageOps.exif_transpose(img).convert("RGB")
            rgb = np.asarray(upright, dtype=np.uint8)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageDecodeError(f"cannot decode image: {exc}") from exc
    return DecodedImage(np.ascontiguousarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)))


def encode_jpeg(image: BGRImage, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


def encode_png(image: NDArray[np.uint8]) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG encoding failed")
    return buf.tobytes()


def resize_to_width(image: BGRImage, width: int) -> tuple[BGRImage, float]:
    """Resize keeping aspect ratio. Returns the image and the factor (new / old)."""
    h, w = image.shape[:2]
    factor = width / w
    if factor == 1.0:
        return image, 1.0
    interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (width, round(h * factor)), interpolation=interpolation)
    return resized.astype(np.uint8), factor
