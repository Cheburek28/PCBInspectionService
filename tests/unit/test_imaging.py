from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from pcb_inspection.engine import imaging


def _jpeg_with_orientation(rgb: np.ndarray, orientation: int) -> bytes:
    img = Image.fromarray(rgb)
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), quality=95)
    return buf.getvalue()


def test_sniff() -> None:
    assert imaging.sniff_content_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert imaging.sniff_content_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert imaging.sniff_content_type(b"GIF89a") is None


def test_decode_rejects_unknown_format() -> None:
    with pytest.raises(imaging.ImageDecodeError, match="unsupported"):
        imaging.decode(b"not an image")


def test_decode_rejects_truncated_jpeg() -> None:
    with pytest.raises(imaging.ImageDecodeError, match="cannot decode"):
        imaging.decode(b"\xff\xd8\xff\xe0" + b"\x00" * 20)


def test_decode_png_roundtrip() -> None:
    bgr = np.zeros((10, 20, 3), np.uint8)
    bgr[..., 0] = 255  # blue in BGR
    out = imaging.decode(imaging.encode_png(bgr))
    assert (out.width, out.height) == (20, 10)
    assert out.pixels[0, 0].tolist() == [255, 0, 0]


def test_exif_orientation_is_applied() -> None:
    """Orientation=6 means 'rotate 90° clockwise to display': a 40x20 image is shown as 20x40."""
    rgb = np.zeros((20, 40, 3), np.uint8)
    rgb[:, :20] = (255, 0, 0)  # left half red
    out = imaging.decode(_jpeg_with_orientation(rgb, 6))
    assert (out.width, out.height) == (20, 40)
    # after a clockwise rotation the left half ends up on top
    top, bottom = out.pixels[5, 10], out.pixels[35, 10]
    assert top[2] > 200  # red channel (BGR)
    assert top[1] < 60
    assert bottom.max() < 60


def test_resize_to_width() -> None:
    img = np.zeros((100, 200, 3), np.uint8)
    small, f = imaging.resize_to_width(img, 100)
    assert small.shape == (50, 100, 3)
    assert f == 0.5
    same, f1 = imaging.resize_to_width(img, 200)
    assert same is img
    assert f1 == 1.0
    big, f2 = imaging.resize_to_width(img, 400)
    assert big.shape == (200, 400, 3)
    assert f2 == 2.0


def test_encode_jpeg() -> None:
    data = imaging.encode_jpeg(np.zeros((8, 8, 3), np.uint8))
    assert imaging.sniff_content_type(data) == "image/jpeg"
