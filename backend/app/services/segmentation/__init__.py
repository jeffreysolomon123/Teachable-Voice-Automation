"""Segmentation service: screenshot -> provider -> normalized element list."""
from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import Any

from ...config import Settings, get_settings
from ...errors import ApiError, ErrorCode
from ...models.segment import Element, Screen
from ..images import Screenshot
from .base import RawSegmentation, SegmentationError, SegmentationProvider

log = logging.getLogger("app.segmentation")


def build_provider(s: Settings) -> SegmentationProvider:
    params = dict(conf=s.seg_conf, iou=s.seg_iou, min_conf=s.seg_ocr_min_conf, contain=s.seg_contain)
    if s.segmentation_provider == "hf_space":
        from .hf_space import HfSpaceProvider
        return HfSpaceProvider(s.hf_space, s.hf_token, **params)
    if s.segmentation_provider == "http":
        from .http_endpoint import HttpProvider
        return HttpProvider(s.segmentation_http_url, s.segmentation_http_token, s.segmentation_timeout_seconds, **params)
    if s.segmentation_provider == "local":
        from .local import LocalProvider
        return LocalProvider(s.local_segmentation_dir, s.local_segmentation_device, **params)
    raise ValueError(f"unknown SEGMENTATION_PROVIDER {s.segmentation_provider!r}")


@lru_cache
def get_provider() -> SegmentationProvider:
    return build_provider(get_settings())


def normalize(raw: RawSegmentation) -> list[Element]:
    """Combined pipeline output -> Elements indexed in reading order (top-to-bottom, left-to-right).

    combine_results.combine() keeps CV ids and appends OCR ids, so ids are not in reading order;
    here ``index`` is the position in the returned list and ``parent`` is remapped to it.
    """
    w, h = raw.width, raw.height
    usable: list[tuple[Any, dict[str, Any], tuple[int, int, int, int]]] = []
    for e in raw.combined:
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in e["bbox"])
        except (KeyError, TypeError, ValueError):
            continue
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        usable.append((e.get("id"), e, (x1, y1, x2, y2)))
    usable.sort(key=lambda t: (t[2][1], t[2][0]))
    new_index = {orig_id: i for i, (orig_id, _, _) in enumerate(usable) if orig_id is not None}
    out = []
    for i, (_, e, bbox) in enumerate(usable):
        parent = e.get("parent", e.get("parent_id"))
        conf = e.get("confidence")
        out.append(Element(
            index=i,
            text=" ".join(str(e.get("text") or "").split()),
            type=str(e.get("type") or "unknown"),
            bbox=bbox,
            confidence=min(1.0, max(0.0, float(conf))) if isinstance(conf, (int, float)) else 0.0,
            source="ocr" if e.get("source") == "ocr" else "cv",
            parent=new_index.get(parent) if parent is not None else None,
        ))
    return out


class SegmentationService:
    def __init__(self, provider: SegmentationProvider, settings: Settings):
        self.provider, self.settings = provider, settings

    async def analyze(self, shot: Screenshot) -> Screen:
        s = self.settings
        # Remote providers get a JPEG (4x smaller than PNG, same pixel grid); local decodes anything.
        payload = shot.data if self.provider.name == "local" else shot.as_jpeg(s.seg_upload_jpeg_quality)[0]
        t0 = time.perf_counter()
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self.provider.segment, payload, shot.width, shot.height),
                                         timeout=s.segmentation_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ApiError(ErrorCode.SEGMENTATION_FAILED, f"segmentation timed out after "
                           f"{s.segmentation_timeout_seconds:.0f}s", status=504, retryable=True) from exc
        except SegmentationError as exc:
            log.warning("segmentation failed provider=%s error=%s", self.provider.name, exc)
            raise ApiError(ErrorCode.SEGMENTATION_FAILED, str(exc), status=502, retryable=True) from exc
        latency = int((time.perf_counter() - t0) * 1000)
        if (raw.width, raw.height) != (shot.width, shot.height):
            raise ApiError(ErrorCode.SEGMENTATION_FAILED, f"provider returned a {raw.width}x{raw.height} result "
                           f"for a {shot.width}x{shot.height} screenshot", status=502, retryable=True)
        elements = normalize(raw)
        log.info("segmentation provider=%s elements=%d latency_ms=%d", self.provider.name, len(elements), latency)
        return Screen(width=raw.width, height=raw.height, elements=elements, latency_ms=latency,
                      provider=self.provider.name)


def get_segmentation_service() -> SegmentationService:
    return SegmentationService(get_provider(), get_settings())
