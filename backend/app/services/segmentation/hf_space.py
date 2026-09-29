"""Provider: the ui-segmentation Gradio Space (cloud_gpu_ui_segmentation/hf_space/app.py)."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading

from .base import RawSegmentation, SegmentationError, raw_from_payload


def space_url(space: str) -> str:
    """Same rule as tap-to-flow-pipeline/3_ui_segmentation/cloud_segment.py: call the direct
    https://<user>-<name>.hf.space URL, which works even where huggingface.co is blocked."""
    if space.startswith(("http://", "https://")):
        return space.rstrip("/")
    space = space.removeprefix("https://huggingface.co/spaces/")
    return f"https://{re.sub(r'[/_.]', '-', space.lower())}.hf.space"


class HfSpaceProvider:
    name = "hf_space"

    def __init__(self, space: str, token: str, conf: float, iou: float, min_conf: float, contain: float):
        self.space, self.token = space, token or None
        self.params = dict(conf=conf, iou=iou, min_conf=min_conf, contain=contain)
        self._client = None
        self._batch_api = False
        self._lock = threading.Lock()

    def status(self) -> str:
        return "configured" if self.space else "not_configured (set HF_SPACE)"

    def _get_client(self):
        with self._lock:
            if self._client is None:
                if not self.space:
                    raise SegmentationError("HF_SPACE is not set")
                from gradio_client import Client  # imported lazily: only this provider needs it

                try:
                    client = Client(space_url(self.space), token=self.token, verbose=False, download_files=False)
                    endpoints = client.view_api(print_info=False, return_format="dict")["named_endpoints"]
                except Exception as exc:  # network / auth / Space asleep
                    raise SegmentationError(f"cannot connect to Space {self.space}: {type(exc).__name__}: {exc}") from exc
                self._batch_api = "/segment_batch" in endpoints
                self._client = client
            return self._client

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        from gradio_client import handle_file

        client = self._get_client()
        fd, path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(image_bytes)
            try:
                if self._batch_api:  # returns JSON only (no annotated image to download)
                    data = client.predict(files=[handle_file(path)], **self.params, api_name="/segment_batch")
                else:
                    data, _annotated = client.predict(image=handle_file(path), **self.params, api_name="/segment_ui")
            except Exception as exc:
                msg = str(exc)
                if "quota" in msg.lower():
                    raise SegmentationError(f"Space GPU quota exhausted: {msg[:200]}") from exc
                raise SegmentationError(f"Space call failed: {type(exc).__name__}: {msg[:200]}") from exc
            if isinstance(data, str):
                data = json.loads(data or "{}")
            return raw_from_payload(data, width, height)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
