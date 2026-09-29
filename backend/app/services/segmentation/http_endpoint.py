"""Provider: any HTTP endpoint speaking cloud_gpu_ui_segmentation/server.py's /segment contract
(POST {"image": <base64>, ...} -> {"width", "height", "combined": [...]})."""
from __future__ import annotations

import base64

import httpx

from .base import RawSegmentation, SegmentationError, raw_from_payload


class HttpProvider:
    name = "http"

    def __init__(self, url: str, token: str, timeout: float, conf: float, iou: float, min_conf: float,
                 contain: float):
        self.url, self.token, self.timeout = url, token, timeout
        self.params = dict(conf=conf, iou=iou, min_conf=min_conf, contain=contain, return_annotated=False)

    def status(self) -> str:
        return "configured" if self.url else "not_configured (set SEGMENTATION_HTTP_URL)"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        if not self.url:
            raise SegmentationError("SEGMENTATION_HTTP_URL is not set")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        body = {"image": base64.b64encode(image_bytes).decode("ascii"), **self.params}
        try:
            r = httpx.post(self.url, json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise SegmentationError(f"segmentation endpoint unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise SegmentationError(f"segmentation endpoint returned HTTP {r.status_code}")
        try:
            return raw_from_payload(r.json(), width, height)
        except ValueError as exc:
            raise SegmentationError("segmentation endpoint returned invalid JSON") from exc
