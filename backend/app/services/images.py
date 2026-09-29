"""Screenshot validation. Images stay in memory; nothing is written to disk here."""
from __future__ import annotations

import io
from dataclasses import dataclass

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from ..config import Settings
from ..errors import ApiError, ErrorCode

Image.MAX_IMAGE_PIXELS = None  # we enforce our own (lower) limit below


@dataclass
class Screenshot:
    data: bytes
    width: int
    height: int
    format: str  # PNG / JPEG / WEBP

    def as_jpeg(self, quality: int, max_width: int | None = None) -> tuple[bytes, float]:
        """JPEG bytes (optionally downscaled) and the scale factor applied to coordinates."""
        with Image.open(io.BytesIO(self.data)) as im:
            im = im.convert("RGB")
            scale = 1.0
            if max_width and im.width > max_width:
                scale = max_width / im.width
                im = im.resize((max_width, max(1, round(im.height * scale))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=quality)
            return out.getvalue(), scale


async def read_screenshot(upload: UploadFile | None, settings: Settings) -> Screenshot:
    if upload is None:
        raise ApiError(ErrorCode.SCREEN_INVALID, "no screenshot uploaded (multipart field 'file' or 'screenshot')")
    ctype = (upload.content_type or "").split(";")[0].strip().lower()
    if ctype not in settings.allowed_image_type_set:
        raise ApiError(ErrorCode.SCREEN_INVALID, f"unsupported content type {ctype or '(none)'}; "
                       f"allowed: {', '.join(sorted(settings.allowed_image_type_set))}", status=415)
    data = await upload.read(settings.max_image_bytes + 1)
    if len(data) > settings.max_image_bytes:
        raise ApiError(ErrorCode.SCREEN_INVALID, f"image larger than {settings.max_image_bytes} bytes", status=413)
    return decode_screenshot(data, settings)


def decode_screenshot(data: bytes, settings: Settings) -> Screenshot:
    if not data:
        raise ApiError(ErrorCode.SCREEN_INVALID, "empty image")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, (w, h) = (im.format or "").upper(), im.size
            if w * h > settings.max_image_pixels:
                raise ApiError(ErrorCode.SCREEN_INVALID, f"image has {w * h} pixels (max {settings.max_image_pixels})", status=413)
            im.verify()  # detects truncated / corrupt files
    except ApiError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ApiError(ErrorCode.SCREEN_INVALID, f"not a valid image: {type(exc).__name__}") from exc
    if fmt not in {"PNG", "JPEG", "WEBP"}:
        raise ApiError(ErrorCode.SCREEN_INVALID, f"unsupported image format {fmt or 'unknown'}", status=415)
    if w < 10 or h < 10:
        raise ApiError(ErrorCode.SCREEN_INVALID, f"image too small ({w}x{h})")
    return Screenshot(data, w, h, fmt)
