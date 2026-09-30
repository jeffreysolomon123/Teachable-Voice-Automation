"""Provider: Groq Vision (Qwen 2.5/3.8 VL) UI element detector.

Provides sub-second, high-throughput (30 RPM, 14,400 RPD) vision-based UI segmentation
running on Groq LPUs without external GPU setup.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
from typing import Any

from .base import RawSegmentation, SegmentationError

log = logging.getLogger("app.segmentation.groq")

PROMPT = """Detect all interactive and textual UI elements on this mobile screen (buttons, search bars, inputs, cards, text labels, icons).
For each element, return a JSON object with:
- id: integer starting at 0
- type: 'button' | 'text' | 'input' | 'icon'
- text: visible text label or placeholder inside the element (or empty string if an icon)
- bbox: [ymin, xmin, ymax, xmax] normalized on a scale of 0 to 1000

Return ONLY a valid JSON array of objects. No markdown or explanation."""


def _parse_groq_elements(raw: str) -> list[dict[str, Any]]:
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw or "", flags=re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[\s*\{[\s\S]*\}\s*\]", text)
        if not m:
            raise SegmentationError("Groq reply did not contain a valid JSON array of elements")
        data = json.loads(m.group(0))

    if not isinstance(data, list):
        if isinstance(data, dict) and "elements" in data:
            data = data["elements"]
        else:
            raise SegmentationError("Groq reply is not an element list")
    return data


class GroqVisionProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str = "qwen/qwen3.8-27b", timeout_seconds: float = 20.0):
        self.api_key = api_key
        self.model_name = model or "qwen/qwen3.8-27b"
        self.timeout_seconds = timeout_seconds
        self._client = None
        self._lock = threading.Lock()

    def status(self) -> str:
        return "configured" if self.api_key else "not_configured (set GROQ_API_KEY)"

    def _get_client(self):
        with self._lock:
            if self._client is None:
                if not self.api_key:
                    raise SegmentationError("GROQ_API_KEY is not set")
                import groq
                self._client = groq.Client(api_key=self.api_key, timeout=self.timeout_seconds)
            return self._client

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        client = self._get_client()
        b64_image = base64.b64encode(image_bytes).decode("ascii")

        try:
            resp = client.chat.completions.create(
                model=self.model_name,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }],
                temperature=0.0,
                max_tokens=1500
            )
            raw_text = resp.choices[0].message.content or ""
        except Exception as exc:
            msg = str(exc)
            log.warning("Groq vision segmentation failed: %s: %s", type(exc).__name__, msg)
            raise SegmentationError(f"Groq vision call failed: {type(exc).__name__}: {msg[:200]}") from exc

        elements_raw = _parse_groq_elements(raw_text)
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
                "confidence": 0.90,
                "source": source,
                "parent": None
            })

        log.info("Groq vision detected %d elements on %dx%d screen", len(combined), width, height)
        return RawSegmentation(width, height, combined)
