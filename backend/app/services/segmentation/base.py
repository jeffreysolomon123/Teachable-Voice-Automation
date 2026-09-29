"""Segmentation provider interface.

A provider turns screenshot bytes into the *combined* element list produced by the existing
pipeline (tap-to-flow-pipeline/3_ui_segmentation/combine_results.py and the identical merge in
cloud_gpu_ui_segmentation). Adding a new backend (another Space, a GPU server, a hosted API)
means implementing ``segment`` and registering it in ``__init__.get_provider``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RawSegmentation:
    width: int
    height: int
    # Items shaped like combine_results.combine() output: id, type, text, bbox, confidence,
    # source ("cv"/"ocr"), parent.
    combined: list[dict[str, Any]]


class SegmentationError(Exception):
    """The provider could not produce a result (network, quota, bad response, ...)."""


class SegmentationProvider(Protocol):
    name: str

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        """Blocking call; runs in a worker thread. Raises SegmentationError."""
        ...

    def status(self) -> str:
        """Cheap configuration check for /health/dependencies (no network, no model load)."""
        ...


def raw_from_payload(data: Any, fallback_w: int, fallback_h: int) -> RawSegmentation:
    """Accept the JSON returned by the Space / server.py and pull out the combined elements."""
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise SegmentationError(f"unexpected segmentation payload type {type(data).__name__}")
    combined = data.get("combined", data.get("elements"))
    if not isinstance(combined, list):
        raise SegmentationError("segmentation payload has no 'combined' element list")
    return RawSegmentation(int(data.get("width") or fallback_w), int(data.get("height") or fallback_h), combined)
