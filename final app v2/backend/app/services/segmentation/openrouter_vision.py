"""Provider: UI segmentation + OCR in ONE vision-LLM call via OpenRouter.

Ported from backend_new/app/segmentation.py (tested in test_ui_segment_api/): the screenshot is
sent at full resolution, the model returns every element with type, verbatim text, a label for
text-less elements, a tappable flag and a box in Gemini's native box_2d format (0-1000). Google's
slow "flex" tier is skipped, output is capped at 6000 tokens, and cut-off / errored upstream
replies (about 1 in 4 in testing) are retried.

``segment_bytes`` is also used by the TEACH pipeline's stage 3.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .base import RawSegmentation, SegmentationError

log = logging.getLogger("app.segmentation.openrouter")

URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 6000
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
TYPES = ["button", "icon_button", "text_field", "tab", "chip", "checkbox", "toggle", "link",
         "image", "icon", "text", "card", "list_item", "nav_bar", "status_bar", "container"]
PROVIDER = {"sort": "throughput", "ignore": ["google-ai-studio/flex"]}

PROMPT = """You are a UI parser. Detect EVERY visible UI element in this mobile screenshot and read its text.

Include: buttons, icon buttons (back, menu, share, bookmark...), text fields, tabs, chips/filters,
checkboxes, toggles, links, images, standalone icons, every text line/label, cards, list items,
status bar and nav bar. Include both a card AND the separate elements inside it.

Return ONLY JSON: {"elements": [{"type": ..., "text": ..., "label": ..., "interactive": ..., "box_2d": ...}, ...]}
- type: one of TYPES
- text: exact visible text, verbatim (keep symbols like the rupee sign), "" if none
- label: for elements without text, a short description (e.g. "back arrow", "pizza photo"), else ""
- interactive: true if tappable
- box_2d: [ymin, xmin, ymax, xmax], integers normalized 0-1000 relative to image size, tight around the element
Order elements top-to-bottom, left-to-right.""".replace("TYPES", ", ".join(TYPES))


def _parse(content: str) -> tuple[list[dict[str, Any]], bool]:
    """(element dicts, complete). Salvages whole elements from truncated output."""
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("elements", [])
        return [r for r in data if isinstance(r, dict)], True
    except json.JSONDecodeError:
        out = []
        for piece in re.findall(r"\{[^{}]*\}", content):
            try:
                out.append(json.loads(piece))
            except json.JSONDecodeError:
                pass
        return out, False


def _contains(outer: list[int], inner: list[int], frac: float = 0.9) -> bool:
    ix = max(0, min(outer[2], inner[2]) - max(outer[0], inner[0]))
    iy = max(0, min(outer[3], inner[3]) - max(outer[1], inner[1]))
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return area > 0 and ix * iy / area >= frac


def to_elements(raw: list[dict[str, Any]], w: int, h: int) -> list[dict[str, Any]]:
    """Model boxes (box_2d 0-1000) -> pixel elements in reading order with parent/children."""
    out = []
    for r in raw:
        box = r.get("box_2d") or r.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            ymin, xmin, ymax, xmax = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0.0, min(1000.0, xmin)), max(0.0, min(1000.0, xmax))))
        y1, y2 = sorted((max(0.0, min(1000.0, ymin)), max(0.0, min(1000.0, ymax))))
        bbox = [round(x1 * w / 1000), round(y1 * h / 1000), round(x2 * w / 1000), round(y2 * h / 1000)]
        if bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
            continue
        out.append({"type": str(r.get("type") or "unknown"), "text": str(r.get("text") or ""),
                    "label": str(r.get("label") or ""), "interactive": bool(r.get("interactive", False)),
                    "bbox": bbox, "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2]})
    seen, uniq = set(), []
    for e in out:
        key = (tuple(e["bbox"]), e["text"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    uniq.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    for i, e in enumerate(uniq):
        e["id"], e["parent"], e["children"] = i, None, []
    area = lambda e: (e["bbox"][2] - e["bbox"][0]) * (e["bbox"][3] - e["bbox"][1])
    for e in uniq:
        cands = [p for p in uniq if p is not e and area(p) > area(e) and _contains(p["bbox"], e["bbox"])]
        if cands:
            p = min(cands, key=area)
            e["parent"] = p["id"]
            p["children"].append(e["id"])
    return uniq


def segment_bytes(api_key: str, model: str, retries: int, image_bytes: bytes, width: int, height: int,
                  mime: str = "image/jpeg") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One screenshot -> (elements, info). Raises SegmentationError after all attempts fail."""
    if not api_key:
        raise SegmentationError("OPENROUTER_API_KEY is not set")
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": MAX_TOKENS, "provider": PROVIDER,
        "response_format": {"type": "json_object"}, "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"}},
            {"type": "text", "text": PROMPT},
        ]}],
    }).encode()
    attempts = retries + 1
    last = "no attempt"
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(URL, data=body, headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
            if e.code not in RETRYABLE_HTTP:
                raise SegmentationError(last) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            last = f"network error: {e}"
        else:
            if "error" in resp:
                last = f"API error: {resp['error']}"
            else:
                choice = resp["choices"][0]
                finish = choice.get("finish_reason")
                raw, complete = _parse(choice["message"].get("content") or "")
                elements = to_elements(raw, width, height)
                usage = resp.get("usage") or {}
                # finish_reason=error / zero output = stream cut off upstream: never accept it.
                cut = finish == "error" or not usage.get("completion_tokens")
                if complete and elements and not cut:
                    return elements, {"model": model, "provider": resp.get("provider"), "attempts": attempt,
                                      "latency_s": round(time.time() - t0, 2), "cost": usage.get("cost")}
                last = ("upstream error" if cut else "hit max_tokens" if finish == "length" else "bad JSON") \
                    + f" (finish_reason={finish})"
        log.warning("openrouter segmentation attempt %d/%d failed: %s", attempt, attempts, last)
        if attempt < attempts:
            time.sleep(min(2 * attempt, 6))
    raise SegmentationError(f"OpenRouter segmentation failed after {attempts} attempts; last: {last}")


class OpenRouterVisionProvider:
    name = "openrouter"

    def __init__(self, api_key: str, model: str, retries: int = 4):
        self.api_key, self.model, self.retries = api_key, model, retries

    def status(self) -> str:
        return f"configured ({self.model})" if self.api_key else "not_configured (set OPENROUTER_API_KEY)"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        elements, info = segment_bytes(self.api_key, self.model, self.retries, image_bytes, width, height)
        log.info("openrouter segmentation: %d elements in %.1fs (attempt %d)", len(elements),
                 info["latency_s"], info["attempts"])
        combined = [{"id": e["id"], "type": e["type"], "text": e["text"], "label": e["label"],
                     "interactive": e["interactive"], "bbox": e["bbox"], "confidence": 0.9,
                     "source": "cv", "parent": e["parent"]} for e in elements]
        return RawSegmentation(width, height, combined)
