"""Provider: Google Gemini Flash vision-based UI element and text detector.

Extracts interactive UI elements and text directly from mobile screenshots with sub-pixel
spatial precision, outputting normalized bboxes [x1, y1, x2, y2] compatible with the
segmentation normalization pipeline.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

from .base import RawSegmentation, SegmentationError

log = logging.getLogger("app.segmentation.gemini")

PROMPT = """Detect all interactive and textual UI elements on this mobile screen (buttons, input fields, search bars, cards, text labels, icons, tabs).
For each element, return a JSON object with:
- id: integer starting at 0
- type: 'button' | 'text' | 'input' | 'icon'
- text: visible text label or placeholder inside the element (or empty string if an icon)
- bbox: [ymin, xmin, ymax, xmax] normalized on a scale of 0 to 1000

Return ONLY a strict JSON array of objects. Do not include markdown formatting or reasoning."""


def _parse_json_elements(raw: str) -> list[dict[str, Any]]:
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw or "", flags=re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[\s*\{[\s\S]*\}\s*\]", text)
        if not m:
            raise SegmentationError("Gemini reply did not contain a valid JSON array of elements")
        data = json.loads(m.group(0))

    if not isinstance(data, list):
        if isinstance(data, dict) and "elements" in data:
            data = data["elements"]
        else:
            raise SegmentationError("Gemini reply is not an element list")
    return data


class GeminiVisionProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-lite", timeout_seconds: float = 20.0):
        self.api_key = api_key
        self.model_name = model or "gemini-3.1-flash-lite"
        self.timeout_seconds = timeout_seconds
        self._client = None
        self._lock = threading.Lock()

    def status(self) -> str:
        return "configured" if self.api_key else "not_configured (set GEMINI_API_KEY)"

    def _get_client(self):
        with self._lock:
            if self._client is None:
                if not self.api_key:
                    raise SegmentationError("GEMINI_API_KEY is not set")
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            return self._client

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        from google.genai import types

        client = self._get_client()
        try:
            resp = client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    PROMPT
                ]
            )
            raw_text = resp.text or ""
        except Exception as exc:
            msg = str(exc)
            log.warning("Gemini vision segmentation failed: %s: %s", type(exc).__name__, msg)
            raise SegmentationError(f"Gemini vision call failed: {type(exc).__name__}: {msg[:200]}") from exc

        elements_raw = _parse_json_elements(raw_text)
        combined = []
        for i, item in enumerate(elements_raw):
            raw_bbox = item.get("bbox") or item.get("bbox_2d") or [0, 0, 0, 0]
            if len(raw_bbox) != 4:
                continue
            ymin, xmin, ymax, xmax = (float(v) for v in raw_bbox)
            # Map normalized 0-1000 coordinates to pixel coordinates
            x1 = max(0, min(width, int(round(xmin * width / 1000.0))))
            y1 = max(0, min(height, int(round(ymin * height / 1000.0))))
            x2 = max(0, min(width, int(round(xmax * width / 1000.0))))
            y2 = max(0, min(height, int(round(ymax * height / 1000.0))))

            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1
            if x2 - x1 < 2:
                x2 = min(width, x1 + 4)
            if y2 - y1 < 2:
                y2 = min(height, y1 + 4)

            el_type = str(item.get("type") or "text").lower()
            text_val = str(item.get("text") or "").strip()
            source = "ocr" if el_type in ("text", "input") and text_val else "cv"

            combined.append({
                "id": i,
                "type": el_type,
                "text": text_val,
                "bbox": [x1, y1, x2, y2],
                "confidence": 0.95,
                "source": source,
                "parent": None
            })

        log.info("Gemini vision detected %d elements on %dx%d screen", len(combined), width, height)
        return RawSegmentation(width, height, combined)
